"""SQLite persistence layer.

SQLite is used because the workload is a single daily writer with rich
relational history (vehicles -> listings -> price observations / photos /
evidence / lifecycle events) and the whole database travels as one file in the
repository's state branch, so history survives every deployment.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
PRAGMA journal_mode=DELETE;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS sellers (
  id INTEGER PRIMARY KEY,
  key TEXT UNIQUE NOT NULL,            -- normalised name|zip
  name TEXT, kind TEXT,                -- porsche_dealer | dealer | private | auction
  street TEXT, city TEXT, state TEXT, zip TEXT, phone TEXT, website TEXT,
  lat REAL, lon REAL,
  first_seen TEXT, last_seen TEXT
);

CREATE TABLE IF NOT EXISTS vehicles (
  key TEXT PRIMARY KEY,                -- VIN, or NOVIN:<source>:<id>
  vin TEXT,
  variant TEXT, generation TEXT, year INTEGER,
  first_seen TEXT NOT NULL,
  last_seen TEXT,
  baseline INTEGER NOT NULL DEFAULT 0, -- 1 = present in the initial backfill (never alerted as "new")
  status TEXT NOT NULL DEFAULT 'active',  -- active | unverified | removed | sold
  status_changed_at TEXT,
  category TEXT,                       -- confirmed | lead | near_miss | excluded
  score REAL,
  merged TEXT                          -- JSON: merged view + evaluation, rebuilt every run
);

CREATE TABLE IF NOT EXISTS listings (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  source_listing_id TEXT NOT NULL,
  vehicle_key TEXT NOT NULL REFERENCES vehicles(key),
  seller_id INTEGER REFERENCES sellers(id),
  url TEXT,
  scope TEXT,                          -- source-specific coverage unit (dealer id / query)
  status TEXT NOT NULL DEFAULT 'active',   -- active | unverified | removed
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  last_checked TEXT,
  silent_misses INTEGER NOT NULL DEFAULT 0,
  explicit_gone INTEGER NOT NULL DEFAULT 0,
  last_gone_signal TEXT,
  price REAL, mileage INTEGER,
  data TEXT NOT NULL,                  -- JSON: full normalised listing as last seen
  UNIQUE(source, source_listing_id)
);
CREATE INDEX IF NOT EXISTS idx_listings_vehicle ON listings(vehicle_key);

CREATE TABLE IF NOT EXISTS price_observations (
  id INTEGER PRIMARY KEY,
  listing_id INTEGER NOT NULL REFERENCES listings(id),
  vehicle_key TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  price REAL NOT NULL,
  kind TEXT NOT NULL DEFAULT 'asking', -- asking | asking_incl_fees | source_reported_previous
  note TEXT
);
CREATE INDEX IF NOT EXISTS idx_price_vehicle ON price_observations(vehicle_key, observed_at);

CREATE TABLE IF NOT EXISTS options (
  listing_id INTEGER NOT NULL REFERENCES listings(id),
  code TEXT, name TEXT NOT NULL, grp TEXT, kind TEXT NOT NULL DEFAULT 'factory_option',
  PRIMARY KEY (listing_id, kind, name)
);

CREATE TABLE IF NOT EXISTS photos (
  id INTEGER PRIMARY KEY,
  listing_id INTEGER NOT NULL REFERENCES listings(id),
  vehicle_key TEXT NOT NULL,
  source_image_id TEXT NOT NULL,       -- stable id from the source (dedupes resolutions)
  url TEXT NOT NULL,
  category TEXT, position INTEGER,
  aspect REAL,
  first_seen TEXT, last_seen TEXT,
  archived_path TEXT, archived_bytes INTEGER, sha256 TEXT,
  UNIQUE(listing_id, source_image_id)
);
CREATE INDEX IF NOT EXISTS idx_photos_vehicle ON photos(vehicle_key);

CREATE TABLE IF NOT EXISTS evidence (
  id INTEGER PRIMARY KEY,
  vehicle_key TEXT NOT NULL,
  field TEXT NOT NULL,                 -- lift | accident | track | weissach | seats | cpo | ...
  status TEXT NOT NULL,                -- verified | seller_stated | unknown | verified_absent | ...
  value TEXT,
  source TEXT, detail TEXT, url TEXT,
  observed_at TEXT NOT NULL,
  UNIQUE(vehicle_key, field, source, status, value)
);

CREATE TABLE IF NOT EXISTS sales (
  id INTEGER PRIMARY KEY,
  vehicle_key TEXT,                    -- may be NULL when the sold car's VIN is unknown
  vin TEXT,
  variant TEXT, generation TEXT, year INTEGER, mileage INTEGER,
  sale_price REAL NOT NULL,
  sale_date TEXT NOT NULL,
  kind TEXT NOT NULL,                  -- auction_sold | dealer_reported_sale
  venue TEXT, url TEXT, title TEXT, notes TEXT,
  recorded_at TEXT NOT NULL,
  UNIQUE(venue, url)
);

CREATE TABLE IF NOT EXISTS lifecycle_events (
  id INTEGER PRIMARY KEY,
  vehicle_key TEXT NOT NULL,
  listing_id INTEGER,
  at TEXT NOT NULL,
  event TEXT NOT NULL,                 -- discovered | price_change | unverified | removed | relisted | sold | ...
  detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_vehicle ON lifecycle_events(vehicle_key, at);

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY,
  started_at TEXT NOT NULL, finished_at TEXT,
  status TEXT NOT NULL DEFAULT 'running',   -- running | ok | partial | failed
  baseline INTEGER NOT NULL DEFAULT 0,
  summary TEXT
);

CREATE TABLE IF NOT EXISTS source_runs (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES runs(id),
  source TEXT NOT NULL,
  started_at TEXT NOT NULL, finished_at TEXT,
  status TEXT NOT NULL,                -- ok | partial | failed | skipped
  listings_seen INTEGER DEFAULT 0, listings_new INTEGER DEFAULT 0,
  requests INTEGER DEFAULT 0, errors INTEGER DEFAULT 0,
  scopes_total INTEGER DEFAULT 0, scopes_ok INTEGER DEFAULT 0,
  photos_advertised INTEGER DEFAULT 0, photos_captured INTEGER DEFAULT 0,
  detail TEXT
);

CREATE TABLE IF NOT EXISTS inventory_snapshots (
  day TEXT NOT NULL, variant TEXT NOT NULL, generation TEXT NOT NULL,
  active_count INTEGER, median_ask REAL, p25_ask REAL, p75_ask REAL, median_miles REAL,
  eligible_count INTEGER,
  PRIMARY KEY (day, variant, generation)
);

CREATE TABLE IF NOT EXISTS digests (
  digest_date TEXT PRIMARY KEY,        -- local date (America/Chicago); one email per date at most
  status TEXT NOT NULL,                -- pending | sent | failed
  created_at TEXT NOT NULL, sent_at TEXT,
  channel TEXT, message_id TEXT, attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
  vehicle_keys TEXT NOT NULL           -- JSON array
);

CREATE TABLE IF NOT EXISTS alert_log (
  vehicle_key TEXT PRIMARY KEY,        -- a vehicle is announced once, regardless of how many sites list it
  digest_date TEXT NOT NULL REFERENCES digests(digest_date),
  category TEXT, claimed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS favorites (
  vehicle_key TEXT PRIMARY KEY, added_at TEXT NOT NULL, note TEXT
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path: str | Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    con.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)", (str(SCHEMA_VERSION),))
    con.commit()
    return con


def get_meta(con, key: str, default=None):
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(con, key: str, value) -> None:
    con.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)))


def j(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def event(con, vehicle_key: str, name: str, detail=None, listing_id=None, at: str | None = None) -> None:
    con.execute("INSERT INTO lifecycle_events(vehicle_key, listing_id, at, event, detail) VALUES(?,?,?,?,?)",
                (vehicle_key, listing_id, at or now_iso(), name,
                 detail if isinstance(detail, str) or detail is None else j(detail)))

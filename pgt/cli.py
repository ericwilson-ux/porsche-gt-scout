"""Command line entry point:  python -m pgt <command>"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import alerts, config, export as exporter, ingest, photos
from . import db as D
from .http import Fetcher
from .sources import marketcheck, porsche_finder
from .sources.base import SourceResult

ROOT = Path(__file__).resolve().parent.parent


def _db(state: Path):
    return D.connect(state / "pgt.sqlite")


def _known_finder(con) -> dict:
    return {r["source_listing_id"]: {"url": r["url"], "scope": r["scope"]} for r in
            con.execute("SELECT source_listing_id, url, scope FROM listings WHERE source='porsche_finder' AND status!='removed'")}


def _mc_trims(con) -> list[str] | None:
    at = D.get_meta(con, "mc_trims_at")
    if at and datetime.now(timezone.utc) - datetime.strptime(at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) < timedelta(days=7):
        return json.loads(D.get_meta(con, "mc_trims", "[]")) or None
    return None


def build_site(con, cfg, state: Path, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    for f in (ROOT / "site").iterdir():
        if f.is_file():
            shutil.copy2(f, out / f.name)
    stats = exporter.export(con, cfg, out / "data", state)
    if (state / "photos").exists():
        shutil.copytree(state / "photos", out / "data" / "photos", dirs_exist_ok=True)
    (out / ".nojekyll").write_text("")
    return stats


def cmd_run(args) -> int:
    cfg, state = config.load_config(), config.state_dir()
    con = _db(state)
    alerts.import_receipts(con, state)
    wanted = [s.strip() for s in args.sources.split(",") if s.strip()]
    results: list[SourceResult] = []
    if "porsche_finder" in wanted:
        try:
            results.append(porsche_finder.collect(Fetcher(min_interval=args.interval), known=_known_finder(con),
                                                  max_dealers=args.max_dealers, extra_urls=args.url or None))
        except Exception as exc:            # a crashing source must not take the run (or its neighbours) down
            results.append(SourceResult("porsche_finder", status="failed", messages=[f"crash: {type(exc).__name__}: {exc}"]))
    if "marketcheck" in wanted:
        try:
            cached = _mc_trims(con)
            res = marketcheck.collect(Fetcher(min_interval=0.4, respect_robots=False), cached_trims=cached)
            if res.detail.get("trims") and not cached:
                D.set_meta(con, "mc_trims", json.dumps(res.detail["trims"]))
                D.set_meta(con, "mc_trims_at", D.now_iso())
            results.append(res)
        except Exception as exc:
            results.append(SourceResult("marketcheck", status="failed", messages=[f"crash: {type(exc).__name__}: {exc}"]))
    summary = ingest.process_results(con, cfg, results, sales_csv=ROOT / "config" / "manual_sales.csv")
    if cfg["photos"]["archive"] == "repo":
        try:
            summary["photo_archive"] = photos.archive(con, state, cfg["photos"]["archive_width"])
            ingest.rebuild_vehicles(con, cfg, D.now_iso())
        except Exception as exc:
            summary["photo_archive"] = {"error": f"{type(exc).__name__}: {exc}"}
    digest = alerts.prepare_digest(con, cfg, state)
    summary["digest"] = {"date": digest["digest_date"], "cars": len(digest["cars"])} if digest else None
    if digest and os.environ.get("EMAIL_MODE", "claude") == "smtp":
        try:
            mid = alerts.send_smtp(digest)
            alerts.mark_sent(con, state, digest["digest_date"], "smtp", mid)
            summary["digest"]["sent"] = True
        except Exception as exc:
            alerts.mark_failed(con, digest["digest_date"], f"{type(exc).__name__}: {exc}")
            summary["digest"]["error"] = str(exc)
    summary["site"] = build_site(con, cfg, state, Path(args.out))
    con.commit()
    print(json.dumps(summary, indent=1, default=str))
    for r in results:
        for msg in r.messages[:25]:
            print(f"[{r.source}] {msg}", file=sys.stderr)
    return 1 if summary["status"] == "failed" else 0


def cmd_export(args) -> int:
    cfg, state = config.load_config(), config.state_dir()
    con = _db(state)
    ingest.rebuild_vehicles(con, cfg, D.now_iso())
    con.commit()
    print(json.dumps(build_site(con, cfg, state, Path(args.out))))
    return 0


def cmd_probe(args) -> int:
    """Fetch and parse one Porsche Finder listing URL; prints the normalised record."""
    f = Fetcher(max_retries=1)
    print(f"robots.txt allows this URL for our user agent: {f.allowed(args.url)}")
    r = f.get(args.url)
    if not r.ok:
        print(f"HTTP {r.status} {r.error or ''} headers={r.headers} body={r.text[:300]!r}")
        return 1
    if "/details/" not in args.url:
        print(f"HTTP 200, {len(r.text)} bytes; starts: {r.text[:200]!r}")
        return 0
    if args.save:
        Path(args.save).write_text(r.text, encoding="utf-8")
    lst = porsche_finder.parse_detail(r.text, args.url)
    if lst is None:
        print("no listing record found in page", file=sys.stderr)
        return 1
    d = lst.to_dict()
    d["photos"] = f"{len(d['photos'])} captured / {d['photos_advertised']} advertised"
    d["standard_equipment"] = f"{len(d['standard_equipment'])} items"
    print(json.dumps(d, indent=1, default=str))
    return 0


def cmd_digest(args) -> int:
    cfg, state = config.load_config(), config.state_dir()
    con = _db(state)
    if args.action == "mark-sent":
        ok = alerts.mark_sent(con, state, args.date, args.channel, args.message_id)
        print("marked sent" if ok else "already sent or unknown digest (no change)")
        return 0
    if args.action == "show":
        row = con.execute("SELECT * FROM digests WHERE status IN ('pending','failed') ORDER BY digest_date LIMIT 1").fetchone()
        print(json.dumps(alerts.build_digest(con, cfg, row), indent=1) if row else "no pending digest")
        return 0
    return 2


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="pgt")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="collect, update the database, queue the digest, build the site")
    r.add_argument("--sources", default="porsche_finder,marketcheck")
    r.add_argument("--max-dealers", type=int, default=None, help="limit dealer sitemaps (smoke tests)")
    r.add_argument("--interval", type=float, default=1.0, help="seconds between requests to one host")
    r.add_argument("--url", action="append", help="extra Porsche Finder listing URL to include")
    r.add_argument("--out", default=str(ROOT / "_site"))
    r.set_defaults(fn=cmd_run)
    e = sub.add_parser("export", help="rebuild the site from the existing database")
    e.add_argument("--out", default=str(ROOT / "_site"))
    e.set_defaults(fn=cmd_export)
    pr = sub.add_parser("probe", help="parse a single Porsche Finder listing URL")
    pr.add_argument("url")
    pr.add_argument("--save", help="write the raw HTML here (fixture capture)")
    pr.set_defaults(fn=cmd_probe)
    d = sub.add_parser("digest")
    d.add_argument("action", choices=["show", "mark-sent"])
    d.add_argument("--date")
    d.add_argument("--channel", default="gmail")
    d.add_argument("--message-id")
    d.set_defaults(fn=cmd_digest)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())

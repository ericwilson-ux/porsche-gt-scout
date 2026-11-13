"""Optional durable photo copies (off by default; see README "Photos").

When PHOTO_ARCHIVE=repo, photos of confirmed and lead vehicles are copied once into the
state branch (state/photos/<vehicle>/<n>.jpg) and published with the site, so a gallery
survives after the listing site stops serving it.  Whether copying a seller's photos is
permitted depends on the source's terms; that decision belongs to the owner.
"""
from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

import requests

from .http import USER_AGENT


def archive(con, state: Path, width: int = 1280, max_downloads: int = 400, pause: float = 0.5) -> dict:
    rows = con.execute("""SELECT p.id, p.vehicle_key, p.url, p.position FROM photos p JOIN vehicles v ON v.key=p.vehicle_key
                          WHERE p.archived_path IS NULL AND v.category IN ('confirmed','lead') AND v.status='active'
                          ORDER BY v.score DESC, p.vehicle_key, p.position LIMIT ?""", (max_downloads,)).fetchall()
    sess = requests.Session()
    sess.headers["User-Agent"] = USER_AGENT
    done = failed = 0
    for r in rows:
        url = re.sub(r"/\d+$", f"/{width}", r["url"]) if "images.finder.porsche.com" in r["url"] else r["url"]
        try:
            resp = sess.get(url, timeout=30)
            if resp.status_code != 200 or not resp.headers.get("content-type", "").startswith("image/"):
                failed += 1
                continue
        except requests.RequestException:
            failed += 1
            continue
        ext = {"image/webp": "webp", "image/avif": "avif", "image/png": "png"}.get(resp.headers["content-type"].split(";")[0], "jpg")
        rel = Path("photos") / r["vehicle_key"].replace(":", "_") / f"{r['position']:03d}.{ext}"
        dest = state / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.content)
        con.execute("UPDATE photos SET archived_path=?, archived_bytes=?, sha256=? WHERE id=?",
                    (rel.as_posix(), len(resp.content), hashlib.sha256(resp.content).hexdigest(), r["id"]))
        done += 1
        time.sleep(pause)
    con.commit()
    return {"archived": done, "failed": failed, "remaining_after_cap": max(0, len(rows) - done - failed)}

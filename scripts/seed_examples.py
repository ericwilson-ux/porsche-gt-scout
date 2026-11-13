"""Run the pipeline offline on the three captured example listings (tests/fixtures) and build the site.

Usage: PGT_STATE=/tmp/pgt-demo python scripts/seed_examples.py [out_dir]
This never touches the network; it exists to exercise parse -> database -> evaluation -> export.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pgt import alerts, cli, config, db as D, ingest  # noqa: E402
from pgt.sources import porsche_finder as pf  # noqa: E402
from pgt.sources.base import SourceResult  # noqa: E402

cfg, state = config.load_config(), config.state_dir()
con = D.connect(state / "pgt.sqlite")
res = SourceResult("porsche_finder")
for f in sorted((ROOT / "tests" / "fixtures").glob("finder_*.html")):
    res.listings.append(pf.parse_detail(f.read_text(encoding="utf-8"), f.name))
res.scopes_ok = {l.scope for l in res.listings}
res.scopes_total = len(res.scopes_ok)
res.detail = {"note": "offline run on captured example pages"}
summary = ingest.process_results(con, cfg, [res], sales_csv=ROOT / "config" / "manual_sales.csv")
alerts.prepare_digest(con, cfg, state)
summary["site"] = cli.build_site(con, cfg, state, Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "_site"))
print(json.dumps(summary, indent=1))

import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pgt import config, db as D  # noqa: E402
from pgt.sources import porsche_finder as pf  # noqa: E402
from pgt.sources.base import Listing, SourceResult  # noqa: E402

FIX = ROOT / "tests" / "fixtures"
EXAMPLE_IDS = ["ZO80OE", "058WLN", "5007XV"]


@pytest.fixture(scope="session", autouse=True)
def _build_fixtures():
    import runpy
    mod = runpy.run_path(str(FIX / "build_fixtures.py"))
    for lid, (main, dl, text) in mod["EXAMPLES"].items():
        (FIX / f"finder_{lid}.html").write_text(mod["page"](main, dl, text), encoding="utf-8")


@pytest.fixture()
def cfg():
    c = config.load_config(ROOT / "config" / "criteria.toml")
    c["alerts"]["site_url"] = "https://example.test/gt"
    c["alerts"]["recipient"] = "buyer@example.test"
    return c


@pytest.fixture()
def con(tmp_path):
    return D.connect(tmp_path / "pgt.sqlite")


@pytest.fixture()
def state(tmp_path):
    return tmp_path


def example(lid: str) -> Listing:
    html = (FIX / f"finder_{lid}.html").read_text(encoding="utf-8")
    return pf.parse_detail(html, f"https://finder.porsche.com/us/en-US/details/x-preowned-{lid}")


@pytest.fixture()
def examples():
    return [example(i) for i in EXAMPLE_IDS]


def finder_result(listings, status="ok", **kw) -> SourceResult:
    r = SourceResult("porsche_finder", status=status, listings=list(listings))
    r.scopes_ok = {l.scope for l in listings} | set(kw.pop("scopes_ok", set()))
    r.scopes_total = len(r.scopes_ok)
    for k, v in kw.items():
        setattr(r, k, v)
    return r


def clone(lst: Listing, **changes) -> Listing:
    c = copy.deepcopy(lst)
    for k, v in changes.items():
        setattr(c, k, v)
    return c


# A second, synthetic vehicle used to exercise "new car" paths.  The VIN has a valid check digit.
def synthetic(lst: Listing, vin: str, lid: str, **changes) -> Listing:
    return clone(lst, vin=vin, source_listing_id=lid, url=lst.url.replace(lst.source_listing_id, lid), **changes)

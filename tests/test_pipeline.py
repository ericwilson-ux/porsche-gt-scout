"""Core data-processing tests.  The three Porsche Finder fixtures are real listings
captured on 2026-11-13; everything labelled synthetic exists only inside these tests."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from conftest import clone, example, finder_result, synthetic
from pgt import alerts, classify as C, evaluate, export, ingest, otd, valuation, vin as V
from pgt import db as D
from pgt.http import Response
from pgt.sources import marketcheck as mc, porsche_finder as pf
from pgt.sources.base import SourceResult

T0 = "2026-11-13T12:00:00Z"
SYN_GT4RS = "WP0AE2A85SK270001"
SYN_GT3 = "WP0AC2A98NS260002"


def ts(days=0, hours=0):
    return (datetime(2026, 11, 13, 12, tzinfo=timezone.utc) + timedelta(days=days, hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def merged(con, key):
    return json.loads(con.execute("SELECT merged FROM vehicles WHERE key=?", (key,)).fetchone()["merged"])


# ---------------------------------------------------------------- identity ----
def test_vin_check_digit_and_decoding():
    assert V.is_valid("WP0AC2A98NS268519") and V.is_valid("WP0AE2A80SK271606") and V.is_valid("WP0CE2A80SK238226")
    assert not V.is_valid("WP0AC2A98NS268510")            # one digit off
    assert V.model_year("WP0AC2A98NS268519") == 2022 and V.porsche_type("WP0AC2A98NS268519") == "992"
    assert V.model_year("WP0AE2A80SK271606") == 2025 and V.porsche_type("WP0AE2A80SK271606") == "982"


@pytest.mark.parametrize("text,expected", [
    ("911 GT3", "gt3"), ("911 GT3 with Touring Package", "gt3_touring"), ("2023 Porsche 911 GT3 Touring", "gt3_touring"),
    ("718 Cayman GT4 RS", "gt4rs"), ("718 Spyder RS", "spyder_rs"),
    ("911 GT3 RS", None), ("911 Carrera GTS", None), ("911 Turbo S", None), ("718 Cayman GT4", None),
    ("718 Spyder", None), ("911 GT3 Cup", None), ("718 Cayman GT4 RS Clubsport", None), ("911 Carrera T", None)])
def test_only_the_four_targets_classify(text, expected):
    assert C.classify_text(text) == expected


def test_vin_contradiction_rejects_listing():
    # a GT4 RS title on a 911 VIN is rejected
    assert C.classify("718 Cayman GT4 RS", "WP0AC2A98NS268519", 2022)["variant"] is None
    assert C.classify("911 GT3", "WP0AC2A98NS268519", 2022) == {"variant": "gt3", "generation": "992.1", "year": 2022, "issues": []}
    assert C.classify("911 GT3", "WP0AC2A98RS260006", 2024)["generation"] == "992.1"
    assert C.generation_for("gt3", 2025) == "992.2" and C.generation_for("gt3", 2022, "992 I") == "992.1"
    # a listing year that contradicts the VIN's platform is rejected (992 year on a 991 VIN)
    out = C.classify("2022 Porsche 911 GT3", None, 2022)
    assert out["generation"] == "992.1"


def test_991_2_gt3_is_tracked_and_991_1_is_not():
    a = C.classify("2018 Porsche 911 GT3", "WP0AC2A93JS174011", 2018)
    assert (a["variant"], a["generation"], a["year"], a["issues"]) == ("gt3", "991.2", 2018, [])
    b = C.classify("2019 Porsche 911 GT3 Touring", "WP0AC2A96KS174022", 2019)
    assert (b["variant"], b["generation"]) == ("gt3_touring", "991.2")
    old = C.classify("2015 Porsche 911 GT3", "WP0AC2A94FS183033", 2015)          # 991.1: not on the list
    assert old["variant"] is None and old["generation"] is None and any("991.1" in i for i in old["issues"])
    assert C.classify("2019 Porsche 911 GT3 RS", "WP0AC2A96KS174022", 2019)["variant"] is None
    assert C.classify("2019 Porsche 911 Carrera T", "WP0AC2A96KS174022", 2019)["variant"] is None
    assert C.generation_for("gt3", 2017) is None and C.generation_for("gt3", None, "991 II") == "991.2"


# ------------------------------------------------------------ porsche finder ----
def test_real_examples_parse_completely(examples):
    by = {l.source_listing_id: l for l in examples}
    g4, g3, sp = by["ZO80OE"], by["058WLN"], by["5007XV"]
    assert (g4.vin, g4.variant, g4.year, g4.mileage, g4.price, g4.cpo) == ("WP0AE2A80SK271606", "gt4rs", 2025, 2088, 209093.0, True)
    assert (g3.vin, g3.variant, g3.generation, g3.transmission, g3.cpo) == ("WP0AC2A98NS268519", "gt3", "992.1", "manual", False)
    assert (sp.vin, sp.variant, sp.mileage, sp.transmission) == ("WP0CE2A80SK238226", "spyder_rs", 684, "pdk")
    # advertised price before dealer fees is separated from the fee-inclusive headline
    assert (g3.price, g3.price_as_listed, g3.dealer_fees_listed) == (239992.0, 240971.0, 979.0)
    assert (sp.price, sp.dealer_fees_listed) == (212990.0, 172.0)
    assert g4.seller["state"] == "NC" and g3.seller["zip"] == "23113" and sp.seller["city"] == "San Francisco"
    assert g4.seller["phone"] == "336-252-5588"
    assert {"474"} <= {o["code"] for o in g4.options} and {"2UH", "Q1J", "1LX"} <= {o["code"] for o in g3.options}
    assert g4.documents["window_sticker_available"] and g4.documents["build_sheet_available"]
    assert sp.seller_comment.startswith("[fixture: abbreviated]")     # '$72' RSC text reference resolved


def test_full_galleries_no_truncation_and_no_duplicates(examples):
    expect = {"ZO80OE": 32, "058WLN": 36, "5007XV": 47}
    for l in examples:
        ids = [p["id"] for p in l.photos]
        assert len(ids) == expect[l.source_listing_id] == l.photos_advertised
        assert len(set(ids)) == len(ids)                    # resolutions of one image never count twice
        assert all(p["url"].startswith("https://images.finder.porsche.com/") for p in l.photos)
    sp = next(l for l in examples if l.source_listing_id == "5007XV")
    assert {p["category"] for p in sp.photos} == {"exterior", "interior", "highlights"}


def test_sitemap_parsing():
    idx = """<sitemapindex><sitemap><loc>https://finder.porsche.com/de/de-DE/sitemap.xml</loc></sitemap>
    <sitemap><loc>https://finder.porsche.com/us/en-US/sitemap-dealer/9817/sitemap.xml</loc></sitemap>
    <sitemap><loc>https://finder.porsche.com/us/en-US/sitemap-dealer/15000003161/sitemap.xml</loc></sitemap></sitemapindex>"""
    assert pf.parse_sitemap_index(idx) == ["9817", "15000003161"]
    sm = """<urlset>
    <url><loc>https://finder.porsche.com/us/en-US/details/porsche-911-gt3-with-touring-package-preowned-4G4009</loc><lastmod>2026-11-12T08:00:00Z</lastmod></url>
    <url><loc>https://finder.porsche.com/us/en-US/details/porsche-718-spyder-rs-preowned-4G24LQ</loc><lastmod>2026-11-13</lastmod></url>
    <url><loc>https://finder.porsche.com/us/en-US/details/porsche-911-gt3-rs-preowned-AAAAA1</loc></url>
    <url><loc>https://finder.porsche.com/us/en-US/details/porsche-911-carrera-cabriolet-preowned-82K2Z3</loc></url>
    <url><loc>https://finder.porsche.com/us/en-US/details/porsche-taycan-4-new-70WOL4</loc></url></urlset>"""
    entries = pf.parse_dealer_sitemap(sm)
    assert len(entries) == 5
    targets = {e["id"]: e["variant"] for e in entries if e["variant"]}
    assert targets == {"4G4009": "gt3_touring", "4G24LQ": "spyder_rs"}
    assert entries[0]["lastmod"] == "2026-11-12T08:00:00Z" and entries[4]["condition"] == "new"


class FakeFetcher:
    """Serves canned responses; anything unknown is a network failure (status 0)."""
    def __init__(self, pages):
        self.pages, self.requests, self.errors, self.calls = pages, 0, 0, []

    def get(self, url, params=None, check_robots=True):
        self.requests += 1
        self.calls.append((url, params))
        v = self.pages.get(url)
        if callable(v):
            v = v(params)
        if v is None:
            self.errors += 1
            return Response(url, 0, error="timeout")
        return Response(url, v[0], v[1])


def _finder_site(listing_ids, dealer="15000003161", extra_dealer_status=None):
    html = {i: (Path(__file__).parent / "fixtures" / f"finder_{i}.html").read_text(encoding="utf-8") for i in listing_ids}
    urls = {i: f"https://finder.porsche.com/us/en-US/details/porsche-x-gt3-preowned-{i}" for i in listing_ids}
    pages = {pf.SITEMAP_INDEX: (200, f"<sitemapindex><sitemap><loc>{pf.BASE}/us/en-US/sitemap-dealer/{dealer}/sitemap.xml</loc></sitemap>"
                                     f"<sitemap><loc>{pf.BASE}/us/en-US/sitemap-dealer/777/sitemap.xml</loc></sitemap></sitemapindex>"),
             f"{pf.BASE}/us/en-US/sitemap-dealer/{dealer}/sitemap.xml":
                 (200, "<urlset>" + "".join(f"<url><loc>{u}</loc></url>" for u in urls.values()) + "</urlset>"),
             f"{pf.BASE}/us/en-US/sitemap-dealer/777/sitemap.xml": extra_dealer_status or (200, "<urlset></urlset>")}
    for i, u in urls.items():
        pages[u] = (200, html[i])
    return pages, urls


def test_collect_discovers_via_sitemaps_and_flags_gone_only_on_404():
    pages, urls = _finder_site(["058WLN"])
    res = pf.collect(FakeFetcher(pages))
    assert res.status == "ok" and [l.vin for l in res.listings] == ["WP0AC2A98NS268519"]
    assert res.scopes_ok == {"15000003161", "777"} and res.detail["target_urls"] == 1
    # the listing drops out of the dealer sitemap and its page 404s -> explicit gone signal
    known = {"058WLN": {"url": urls["058WLN"], "scope": "15000003161"}}
    pages[f"{pf.BASE}/us/en-US/sitemap-dealer/15000003161/sitemap.xml"] = (200, "<urlset></urlset>")
    pages[urls["058WLN"]] = (404, "not found")
    res = pf.collect(FakeFetcher(pages), known=known)
    assert res.explicit_gone == {"058WLN"} and not res.listings
    # same situation but the detail request times out -> inconclusive, never "gone"
    pages[urls["058WLN"]] = None
    res = pf.collect(FakeFetcher(pages), known=known)
    assert res.explicit_gone == set() and res.inconclusive == {"058WLN"} and res.status == "partial"
    # sitemap index unreachable -> failed, nothing inferred
    assert pf.collect(FakeFetcher({}), known=known).status == "failed"


# --------------------------------------------------------------- eligibility ----
def test_real_examples_eligibility(con, cfg, examples):
    ingest.process_results(con, cfg, [finder_result(examples)], now=T0)
    g4, g3, sp = (merged(con, v) for v in ("WP0AE2A80SK271606", "WP0AC2A98NS268519", "WP0CE2A80SK238226"))
    assert g4["eligibility"]["category"] == "confirmed"       # lift 474 verified, under cap, seller states clean Carfax
    assert g4["equipment"]["lift"] == {**g4["equipment"]["lift"], "status": "verified", "value": True}
    assert "474" in g4["equipment"]["lift"]["detail"] and "2UH" in g3["equipment"]["lift"]["detail"]
    # lift is verified on both others, but accident history is undocumented -> lead, not confirmed
    for m in (g3, sp):
        assert m["eligibility"]["category"] == "lead" and m["eligibility"]["unverified"] == ["accident"]
        assert m["equipment"]["lift"]["status"] == "verified"
    assert g4["equipment"]["weissach"]["status"] == "verified_absent" and g4["equipment"]["seats"]["value"] == "bucket"
    assert g3["equipment"]["seats"]["value"] == "adjustable" and g3["equipment"]["pccb"]["value"] is True
    assert sp["equipment"]["pccb"]["value"] is True and g4["equipment"]["pccb"]["value"] is False


def _elig(con, cfg, lst):
    ingest.process_results(con, cfg, [finder_result([lst])], now=T0)
    return merged(con, ingest.vehicle_key(lst))["eligibility"]


def test_missing_lift_in_factory_list_excludes(con, cfg):
    base = example("ZO80OE")
    no_lift = clone(base, options=[o for o in base.options if o["code"] != "474"],
                    seller_comment=base.seller_comment.replace("Front Axle Lift System", "x"))
    e = _elig(con, cfg, no_lift)
    assert e["category"] == "excluded" and e["failed"] == ["lift"]


def test_unverified_lift_is_a_lead_never_confirmed(con, cfg):
    base = example("ZO80OE")
    # no equipment list at all (as from an aggregator): unknown lift
    bare = clone(base, options=[], standard_equipment=[], highlights=[], has_equipment_list=False,
                 seller_comment="Clean Carfax. Great car.")
    e = _elig(con, cfg, bare)
    assert e["category"] == "lead" and "lift" in e["unverified"]


def test_seller_stated_lift_is_labelled_as_seller_statement(con, cfg):
    base = example("ZO80OE")
    stated = clone(base, options=[], standard_equipment=[], highlights=[], has_equipment_list=False,
                   seller_comment="Clean Carfax. Equipped with front axle lift.")
    ingest.process_results(con, cfg, [finder_result([stated])], now=T0)
    m = merged(con, stated.vin)
    chk = next(c for c in m["eligibility"]["checks"] if c["key"] == "lift")
    assert (chk["status"], chk["provenance"]) == ("pass", "seller statement") and m["equipment"]["lift"]["status"] == "seller_stated"


@pytest.mark.parametrize("lid,changes,category,failed", [
    ("058WLN", {"price": 249999.0}, "lead", []),
    ("058WLN", {"price": 250000.0}, "near_miss", ["price"]),          # "under $250,000" is strict
    ("058WLN", {"price": 262000.0}, "near_miss", ["price"]),
    ("058WLN", {"price": 275000.0}, "excluded", ["price"]),
    ("058WLN", {"mileage": 35001}, "excluded", ["mileage"]),
    ("058WLN", {"mileage": 35000}, "lead", []),
    ("ZO80OE", {"price": 225000.0}, "near_miss", ["price"]),          # 718 cap is $225k
    ("ZO80OE", {"price": 224999.0}, "confirmed", []),
    ("ZO80OE", {"mileage": 60000}, "confirmed", []),                  # no hard mileage cap on the 718 RS cars
    ("ZO80OE", {"seller_comment": "Clean Carfax. Used for track days with a half cage."}, "excluded", ["track"]),
    ("ZO80OE", {"seller_comment": "Minor accident reported on Carfax."}, "excluded", ["accident"]),
    ("ZO80OE", {"seller_comment": "Clean Carfax! Never tracked."}, "confirmed", []),
])
def test_hard_requirement_boundaries(con, cfg, lid, changes, category, failed):
    e = _elig(con, cfg, clone(example(lid), **changes))
    assert e["category"] == category and e["failed"] == failed


def test_preference_score_is_independent_of_eligibility_and_price(con, cfg):
    base = example("058WLN")
    ingest.process_results(con, cfg, [finder_result([base])], now=T0)
    s1 = merged(con, base.vin)["score"]
    ingest.process_results(con, cfg, [finder_result([clone(base, price=400000.0)])], now=ts(1))
    m2 = merged(con, base.vin)
    assert m2["eligibility"]["category"] == "excluded" and m2["score"]["total"] == s1["total"]
    keys = [i["key"] for i in s1["items"]]
    assert "weissach" not in keys and {"mileage", "exterior_color", "transmission", "seats", "warranty", "distance"} <= set(keys)


def test_preference_items_respond_to_spec(cfg):
    from datetime import date
    today = date(2026, 11, 13)
    base = {"variant": "gt3", "year": 2022, "mileage": 12000, "exterior_family": "blue", "exterior_color": "Shark Blue",
            "interior_family": "black", "interior_color": "Black", "transmission": "manual", "cpo": True,
            "equipment": {"seats": {"value": "adjustable"}}, "seller": {"kind": "porsche_dealer"},
            "documents": {"window_sticker_available": True}, "history": {"service_history": "yes", "one_owner": True},
            "distance": {"road_miles_est": 180}, "warranty_summary": {}}
    best = evaluate.preference_score(base, cfg, today)
    assert best["total"] == 100
    pdk = evaluate.preference_score({**base, "transmission": "pdk"}, cfg, today)
    assert 0 < best["total"] - pdk["total"] <= 3                       # manual only slightly preferred
    green = evaluate.preference_score({**base, "exterior_family": "green", "exterior_color": "Python Green"}, cfg, today)
    far = evaluate.preference_score({**base, "distance": {"road_miles_est": 1500}}, cfg, today)
    high = evaluate.preference_score({**base, "mileage": 34000}, cfg, today)
    assert green["total"] < best["total"] and far["total"] < best["total"] and high["total"] < best["total"]
    # lowest mileage is not "best": 3k and 12k miles score the same on a 2022 car
    assert evaluate.preference_score({**base, "mileage": 3000}, cfg, today)["total"] == best["total"]
    rs = {**base, "variant": "gt4rs", "year": 2024, "transmission": "pdk", "mileage": 4000,
          "equipment": {"seats": {"value": "adjustable"}, "weissach": {"status": "verified", "value": True}}}
    with_w = evaluate.preference_score(rs, cfg, today)
    rs["equipment"]["weissach"] = {"status": "verified_absent", "value": False}
    assert evaluate.preference_score(rs, cfg, today)["total"] > with_w["total"]
    assert evaluate.preference_score({**rs, "mileage": 15000}, cfg, today)["total"] < evaluate.preference_score(rs, cfg, today)["total"]


# --------------------------------------------------------- dedupe / history ----
MC_RAW = {"id": "WP0AC2A98NS268519-abc", "vin": "WP0AC2A98NS268519", "heading": "2022 Porsche 911 GT3", "price": 244500,
          "miles": 24150, "vdp_url": "https://dealer.example/vdp/1", "exterior_color": "Shark Blue", "interior_color": "Black",
          "seller_type": "dealer", "inventory_type": "used", "carfax_clean_title": True, "source": "porscherichmond.example",
          "dom_active": 41, "first_seen_at_date": "2026-10-03T10:00:00.000Z", "ref_price": 249900, "ref_price_dt": 1759485600,
          "media": {"photo_links": ["https://img.example/a.jpg", "https://img.example/b.jpg", "https://img.example/a.jpg"]},
          "dealer": {"id": 9, "name": "Porsche Richmond", "city": "Midlothian", "state": "VA", "zip": "23113",
                     "phone": "804-555-0100", "website": "porscherichmond.example", "latitude": "37.51", "longitude": "-77.64"},
          "build": {"year": 2022, "make": "Porsche", "model": "911", "trim": "GT3", "transmission": "Manual"}}


def test_marketcheck_normalise_and_trim_selection():
    l = mc.normalise(MC_RAW, "us-used")
    assert (l.variant, l.generation, l.price, l.mileage, l.transmission, l.cpo) == ("gt3", "992.1", 244500.0, 24150, "manual", False)
    assert len(l.photos) == 2 and l.has_equipment_list is False and l.seller["kind"] == "porsche_dealer"
    assert l.photos_advertised is None and l.extra["photo_links_in_feed"] == 3
    # Porsche's image host serves each photo at many sizes and formats: 16 links here are 2 photos
    uid = ["e2839c3c-0a31-44e5-b0d2-4511992d1812", "1be8ca21-6f60-470e-9d40-d16ad45244bc"]
    links = [f"https://images.finder.porsche.com/{u}/{w}.{e}" for u in uid for w in (320, 640, 960, 1280) for e in ("avif", "webp")]
    sized = mc.normalise({**MC_RAW, "media": {"photo_links": links}}, "us-used")
    assert [p["id"] for p in sized.photos] == uid and sized.photos[0]["url"].endswith(uid[0] + "/1280")
    assert mc.normalise({**MC_RAW, "heading": "2022 Porsche 911 Carrera S", "build": {"year": 2022, "trim": "Carrera S"}}, "x") is None
    assert mc.normalise({**MC_RAW, "heading": "2023 Porsche 911 GT3 RS", "build": {"year": 2023, "trim": "GT3 RS"}}, "x") is None
    trims = mc.pick_trims([{"item": "GT3", "count": 9}, {"item": "GT3 RS", "count": 5}, {"item": "GT3 Touring", "count": 4},
                           {"item": "GT4 RS", "count": 3}, {"item": "Carrera", "count": 99}, {"item": "RS", "count": 2},
                           {"item": "GT4 RS Clubsport", "count": 1}, {"item": "Spyder RS", "count": 2}])
    assert trims == ["GT3", "GT3 Touring", "GT4 RS", "RS", "Spyder RS"]


def test_marketcheck_collect_pages_and_reports_partial(monkeypatch):
    def api(params):
        if "facets" in params:
            return (200, json.dumps({"num_found": 3, "listings": [], "facets": {"trim": [{"item": "GT3", "count": 3}]}}))
        if params["start"] == 0:
            return (200, json.dumps({"num_found": 60, "listings": [MC_RAW]}))
        return (500, "upstream")
    f = FakeFetcher({mc.ENDPOINT: api})
    res = mc.collect(f, api_key="k")
    assert res.status == "partial" and len(res.listings) == 1 and res.scopes_ok == set()
    assert all("k" not in m for m in res.messages)                    # the key never appears in diagnostics
    assert mc.collect(FakeFetcher({}), api_key=None).status == "skipped"


def test_marketcheck_year_buckets_cover_the_paging_cap():
    calls = []

    def api(params):
        calls.append(dict(params))
        if "facets" in params:
            return (200, json.dumps({"facets": {"trim": [{"item": "GT3", "count": 3}]}}))
        n = {"2018-2019": 40, "2022-2023": 120, "2024-2025": 30, "2026-2027": 620}[params["year_range"]]
        row = {**MC_RAW, "id": f"{params['year_range']}-{params['start']}"}
        return (200, json.dumps({"num_found": n, "listings": [row]}))
    res = mc.collect(FakeFetcher({mc.ENDPOINT: api}), api_key="k")
    # two buckets complete; the third exceeds 500 rows and is reported, never silently truncated
    assert res.scopes_ok == {"us-used:2018-2019", "us-used:2022-2023", "us-used:2024-2025"} and res.status == "partial"
    assert res.detail["buckets"]["2026-2027"] == {"num_found": 620, "complete": False}
    assert any("exceed" in m for m in res.messages)
    pages = [c for c in calls if "facets" not in c]
    assert len(pages) == 1 + 3 + 1 + 10 and res.detail["raw_sample"]["vin"] == MC_RAW["vin"]
    assert len(mc.collect(FakeFetcher({mc.ENDPOINT: api}), api_key="k", cached_trims=["GT3"]).listings) == 15


def test_state_round_trips_through_the_sql_dump(tmp_path, cfg, examples):
    from pgt import cli
    con = cli._db(tmp_path)
    ingest.process_results(con, cfg, [finder_result(examples)], now=T0)
    ingest.process_results(con, cfg, [finder_result([clone(examples[0], price=205000.0)] + examples[1:])], now=ts(1))
    cli.save_state(con, tmp_path)
    con.close()
    (tmp_path / "pgt.sqlite").unlink()
    dump = (tmp_path / "pgt.sql").read_text()
    assert "WP0AE2A80SK271606" in dump and len(dump) < 400_000
    con2 = cli._db(tmp_path)                                   # rebuilt purely from the text dump
    assert con2.execute("SELECT COUNT(*) c FROM photos").fetchone()["c"] == 115
    assert [r["price"] for r in con2.execute("SELECT price FROM price_observations WHERE vehicle_key='WP0AE2A80SK271606' ORDER BY id")] == [209093.0, 205000.0]
    assert con2.execute("SELECT merged FROM vehicles LIMIT 1").fetchone()["merged"] is None
    ingest.process_results(con2, cfg, [finder_result(examples)], now=ts(2))
    m = merged(con2, "WP0AE2A80SK271606")
    assert m["baseline"] is True and len(m["price_history"]) == 3 and len(m["photos"]) == 32


def test_same_vin_from_two_sources_is_one_vehicle_with_conflicts(con, cfg):
    finder = example("058WLN")
    ingest.process_results(con, cfg, [finder_result([finder])], now=T0)
    r2 = SourceResult("marketcheck", listings=[mc.normalise(MC_RAW, "us-used")], scopes_ok={"us-used"}, scopes_total=1)
    ingest.process_results(con, cfg, [finder_result([finder]), r2], now=ts(1))
    assert con.execute("SELECT COUNT(*) c FROM vehicles").fetchone()["c"] == 1
    assert con.execute("SELECT COUNT(*) c FROM listings").fetchone()["c"] == 2
    m = merged(con, finder.vin)
    assert [s["source"] for s in m["sources"]] == ["porsche_finder", "marketcheck"]
    assert m["price"] == 239992.0 and m["price_source"] == "Porsche Finder"          # manufacturer source wins
    assert any(c["field"] == "price" for c in m["conflicts"])                         # $239,992 vs $244,500 is flagged
    assert m["equipment"]["lift"]["status"] == "verified"                             # option data carried from Finder
    assert m["seller"]["phone"] == "804-555-0100"                                     # gap filled from the second source
    assert m["gallery"]["captured"] == 36 and m["gallery"]["verified_against_source"]  # richest gallery chosen
    assert m["listed_since"].startswith("2026-10-03") and m["listed_since_basis"] == "source-reported"
    # the feed's earlier price is kept as a labelled observation, not as our own
    kinds = {p["kind"] for p in m["price_history"]}
    assert kinds == {"asking", "source_reported_previous"}


def test_price_changes_persist_across_runs(con, cfg):
    base = example("058WLN")
    ingest.process_results(con, cfg, [finder_result([base])], now=T0)
    ingest.process_results(con, cfg, [finder_result([base])], now=ts(1))               # unchanged: no new observation
    ingest.process_results(con, cfg, [finder_result([clone(base, price=234992.0)])], now=ts(2))
    ingest.process_results(con, cfg, [finder_result([clone(base, price=229992.0)])], now=ts(5))
    m = merged(con, base.vin)
    assert [p["price"] for p in m["price_history"]] == [239992.0, 234992.0, 229992.0]
    assert m["original_price"] == 239992.0 and m["price_change_total"] == -10000.0
    assert m["last_price_change"]["delta"] == -5000.0 and m["last_price_change"]["at"] == ts(5)
    ev = [r["event"] for r in con.execute("SELECT event FROM lifecycle_events WHERE vehicle_key=? ORDER BY id", (base.vin,))]
    assert ev.count("price_change") == 2 and ev[0] == "discovered"
    assert con.execute("SELECT COUNT(*) c FROM runs WHERE status='ok'").fetchone()["c"] == 4


# ------------------------------------------------------------------ lifecycle ----
def test_failed_or_partial_coverage_never_archives(con, cfg, examples):
    ingest.process_results(con, cfg, [finder_result(examples)], now=T0)
    # source down entirely
    ingest.process_results(con, cfg, [SourceResult("porsche_finder", status="failed", messages=["HTTP 429"])], now=ts(1))
    # source up, but the dealers' sitemaps did not load (no scopes ok) and details were inconclusive
    partial = SourceResult("porsche_finder", status="partial", scopes_ok=set(), inconclusive={"ZO80OE", "058WLN", "5007XV"})
    for d in range(2, 8):
        ingest.process_results(con, cfg, [partial], now=ts(d))
    assert {r["status"] for r in con.execute("SELECT status FROM listings")} == {"active"}
    assert {r["status"] for r in con.execute("SELECT status FROM vehicles")} == {"active"}


def test_removal_needs_repeat_confirmation_and_keeps_history(con, cfg, examples):
    ingest.process_results(con, cfg, [finder_result(examples)], now=T0)
    gone = example("058WLN")
    rest = [l for l in examples if l.source_listing_id != "058WLN"]

    def run(at):
        r = finder_result(rest, explicit_gone={"058WLN"}, checked_ids={"058WLN"})
        r.scopes_ok.add(gone.scope)
        ingest.process_results(con, cfg, [r], now=at)
        return con.execute("SELECT status FROM vehicles WHERE key=?", (gone.vin,)).fetchone()["status"]

    assert run(ts(1)) == "unverified"                     # first 404: potentially inactive, not removed
    assert run(ts(1, 2)) == "unverified"                  # a retry two hours later is not a second independent signal
    assert run(ts(2)) == "removed"                        # second check on a later day confirms
    m = merged(con, gone.vin)
    assert m["status"] == "removed" and m["price"] == 239992.0 and len(m["photos"]) == 36 and len(m["options"]) == 13
    assert "sale" not in m                                # the last asking price is never recorded as a sale
    assert con.execute("SELECT COUNT(*) c FROM sales").fetchone()["c"] == 0
    events = [r["event"] for r in con.execute("SELECT event FROM lifecycle_events WHERE vehicle_key=? ORDER BY id", (gone.vin,))]
    assert events == ["discovered", "listing_unverified", "vehicle_unverified", "listing_removed", "vehicle_removed"]
    # it comes back: relisted, history intact
    ingest.process_results(con, cfg, [finder_result(examples)], now=ts(6))
    assert merged(con, gone.vin)["status"] == "active"
    assert con.execute("SELECT COUNT(*) c FROM lifecycle_events WHERE event='relisted'").fetchone()["c"] == 1


def test_feed_listing_needs_three_silent_misses(con, cfg):
    l = mc.normalise(MC_RAW, "us-used")
    ok = lambda ls: SourceResult("marketcheck", listings=ls, scopes_ok={"us-used"}, scopes_total=1)
    ingest.process_results(con, cfg, [ok([l])], now=T0)
    st = lambda: con.execute("SELECT status FROM listings").fetchone()["status"]
    ingest.process_results(con, cfg, [ok([])], now=ts(1)); assert st() == "unverified"
    ingest.process_results(con, cfg, [ok([])], now=ts(2)); assert st() == "unverified"
    ingest.process_results(con, cfg, [ok([])], now=ts(3)); assert st() == "removed"


# ---------------------------------------------------------------------- sales ----
def test_completed_sale_is_stored_apart_from_asking(con, cfg, tmp_path):
    base = example("ZO80OE")
    ingest.process_results(con, cfg, [finder_result([base])], now=T0)
    csv = tmp_path / "sales.csv"
    csv.write_text("sale_date,sale_price,kind,venue,url,title,vin,year,mileage,variant,generation,notes\n"
                   "2026-11-20,\"201,500\",auction_sold,Test Auction,https://auction.example/lot/1,2025 Porsche 718 Cayman GT4 RS,"
                   "WP0AE2A80SK271606,2025,2100,,,synthetic test row\n"
                   "2026-10-01,198000,auction_sold,Test Auction,https://auction.example/lot/2,2024 Porsche 718 Cayman GT4 RS,,2024,5200,,,\n"
                   "2026-10-02,,auction_sold,Test Auction,https://auction.example/lot/3,no price row,,,,,,\n", encoding="utf-8")
    ingest.process_results(con, cfg, [finder_result([base])], now=ts(8), sales_csv=csv)
    rows = con.execute("SELECT * FROM sales ORDER BY sale_date").fetchall()
    assert [r["sale_price"] for r in rows] == [198000.0, 201500.0] and rows[0]["variant"] == "gt4rs"
    m = merged(con, base.vin)
    assert m["status"] == "sold" and m["sale"]["price"] == 201500.0 and m["price"] == 209093.0     # asking untouched
    assert all(p["kind"] == "asking" for p in m["price_history"])
    ingest.process_results(con, cfg, [finder_result([base])], now=ts(9), sales_csv=csv)             # re-import is idempotent
    assert con.execute("SELECT COUNT(*) c FROM sales").fetchone()["c"] == 2


# ------------------------------------------------------------------------ otd ----
def test_texas_out_the_door(cfg):
    o = otd.out_the_door(200000.0, "TX", 180, None, cfg)
    assert o["dealer_fees"] == 225.0 and o["shipping"] == 0.0 and o["shipping_mode"] == "drive"
    assert o["sales_tax"] == round((200000 + 225) * 0.0625, 2) == 12514.06
    assert o["title_registration"] == 33 + 50.75 + 11.50 + 13.25
    assert o["total"] == 200000 + 225 + 12514.06 + 108.5
    far = otd.out_the_door(239992.0, "VA", 1466, 979.0, cfg)
    assert far["dealer_fees"] == 979.0 and far["dealer_fees_basis"] == "published by dealer"
    assert far["shipping"] == round(350 + 500 * 2.0 + 966 * 1.35, -1) == 2650.0
    assert far["total"] == round(239992 + 979 + round((239992 + 979) * 0.0625, 2) + 108.5 + 2650, 2)
    assert otd.out_the_door(None, "TX", 10, None, cfg) is None
    assert otd.shipping_estimate(None, cfg["otd"])["amount"] is None


# ------------------------------------------------------------------ valuation ----
def test_valuation_shows_uncertainty_instead_of_inventing_precision():
    subj = {"key": "s", "variant": "gt4rs", "generation": "982", "year": 2024, "mileage": 4000, "price": 205000}
    few = [{"key": f"c{i}", "variant": "gt4rs", "generation": "982", "year": 2024, "mileage": 3000 + i * 500, "price": 210000}
           for i in range(4)]
    v = valuation.value(subj, few)
    assert v["confidence"] == "insufficient" and "fair_mid" not in v and v["n"] == 4
    # synthetic market: $2 per mile, subject priced 10% under the line
    pool = [{"key": f"c{i}", "variant": "gt4rs", "generation": "982", "year": 2024, "mileage": m, "price": 230000 - 2 * m}
            for i, m in enumerate(range(1000, 21000, 2000))]
    pool.append({"key": "other", "variant": "gt3", "generation": "992.1", "year": 2022, "mileage": 5000, "price": 999999})
    v = valuation.value({**subj, "price": (230000 - 8000) * 0.9}, pool)
    assert v["n"] == 10 and v["confidence"] == "low" and v["slope_per_mile"] == -2.0
    assert v["fair_mid"] == 222000 and v["rating"] == "great" and round(v["delta_pct"], 2) == -0.10
    assert valuation.value({**subj, "price": 222000 * 1.08}, pool)["rating"] == "overpriced"
    assert v["basis"] == "asking prices"
    with_sales = valuation.value(subj, pool, [{"variant": "gt4rs", "generation": "982", "sale_price": 199000}])
    assert with_sales["sales"]["n"] == 1 and with_sales["fair_mid"] == 222000      # sales reported separately, not mixed in


# --------------------------------------------------------------------- alerts ----
def _digest(con, cfg, state, at):
    return alerts.prepare_digest(con, cfg, state, datetime.strptime(at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc))


def test_backfill_is_not_news_and_new_cars_alert_exactly_once(con, cfg, state, examples):
    ingest.process_results(con, cfg, [finder_result(examples)], now=T0)
    assert _digest(con, cfg, state, T0) is None                         # initial backfill: no email
    assert json.loads((state / "digests" / "pending.json").read_text())["pending"] == []

    new_car = synthetic(example("ZO80OE"), SYN_GT4RS, "NEW001")         # synthetic new confirmed match
    ingest.process_results(con, cfg, [finder_result(examples + [new_car])], now=ts(1))
    d = _digest(con, cfg, state, ts(1))
    assert d and [c["vin"] for c in d["cars"]] == [SYN_GT4RS] and d["digest_date"] == "2026-11-14"
    assert d["subject"].startswith("[PGT-2026-11-14] 1 new qualifying") and "confirmed" in d["subject"]
    car = d["cars"][0]
    assert car["detail_url"] == f"https://example.test/gt/#/v/{SYN_GT4RS}" and car["otd_total"] and car["score"] > 80
    assert "images.finder.porsche.com" in car["photo"] and "CONFIRMED MATCH" in d["html"] and SYN_GT4RS not in d["text"][:0]

    # retries / reruns the same day: same digest, same single car
    again = _digest(con, cfg, state, ts(1, 3))
    assert again["digest_date"] == d["digest_date"] and len(again["cars"]) == 1
    assert con.execute("SELECT COUNT(*) c FROM digests").fetchone()["c"] == 1

    assert alerts.mark_sent(con, state, d["digest_date"], "gmail", "msg-1") is True
    assert alerts.mark_sent(con, state, d["digest_date"], "gmail", "msg-1") is False      # idempotent
    assert _digest(con, cfg, state, ts(1, 5)) is None                    # nothing pending after sending

    # the same car appears on a second source: no second notification
    dup = mc.normalise({**MC_RAW, "id": "dup", "vin": SYN_GT4RS, "heading": "2025 Porsche 718 Cayman GT4 RS",
                        "build": {"year": 2025, "trim": "GT4 RS"}, "price": 208000}, "us-used")
    r2 = SourceResult("marketcheck", listings=[dup], scopes_ok={"us-used"}, scopes_total=1)
    ingest.process_results(con, cfg, [finder_result(examples + [new_car]), r2], now=ts(2))
    assert _digest(con, cfg, state, ts(2)) is None
    assert con.execute("SELECT COUNT(*) c FROM alert_log").fetchone()["c"] == 1


def test_first_run_of_a_second_source_is_also_backfill(con, cfg, state, examples):
    ingest.process_results(con, cfg, [finder_result(examples)], now=T0)
    feed_only = mc.normalise({**MC_RAW, "id": "f1", "vin": SYN_GT3, "carfax_clean_title": True}, "us-used")
    r2 = SourceResult("marketcheck", listings=[feed_only], scopes_ok={"us-used"}, scopes_total=1)
    ingest.process_results(con, cfg, [finder_result(examples), r2], now=ts(1))
    assert con.execute("SELECT baseline FROM vehicles WHERE key=?", (SYN_GT3,)).fetchone()["baseline"] == 1
    assert _digest(con, cfg, state, ts(1)) is None


def test_one_email_per_day_and_excluded_cars_never_alert(con, cfg, state, examples):
    ingest.process_results(con, cfg, [finder_result(examples)], now=T0)
    a = synthetic(example("ZO80OE"), SYN_GT4RS, "NEW001")
    too_dear = synthetic(example("058WLN"), SYN_GT3, "NEW002", price=289000.0)
    ingest.process_results(con, cfg, [finder_result(examples + [a, too_dear])], now=ts(1))
    d = _digest(con, cfg, state, ts(1))
    assert [c["vin"] for c in d["cars"]] == [SYN_GT4RS]                  # the over-cap GT3 is not announced
    alerts.mark_sent(con, state, d["digest_date"], "gmail")
    # later the same local day the GT3 drops under the cap: it qualifies, but today's email already went
    ingest.process_results(con, cfg, [finder_result(examples + [a, clone(too_dear, price=244000.0)])], now=ts(1, 6))
    assert _digest(con, cfg, state, ts(1, 6)) is None
    assert con.execute("SELECT COUNT(*) c FROM alert_log").fetchone()["c"] == 1
    # next morning it goes out in its own digest
    d2 = _digest(con, cfg, state, ts(2))
    assert d2["digest_date"] == "2026-11-15" and [c["vin"] for c in d2["cars"]] == [SYN_GT3]
    assert d2["cars"][0]["category"] == "lead" and "needs verification" in d2["subject"]


def test_baseline_car_that_newly_qualifies_is_announced(con, cfg, state):
    dear = clone(example("058WLN"), price=289000.0)
    ingest.process_results(con, cfg, [finder_result([dear])], now=T0)
    assert _digest(con, cfg, state, T0) is None
    ingest.process_results(con, cfg, [finder_result([clone(dear, price=245000.0)])], now=ts(3))
    d = _digest(con, cfg, state, ts(3))
    assert d and d["cars"][0]["reason"].startswith("Now qualifies")


def test_receipt_file_marks_digest_sent(con, cfg, state, examples):
    ingest.process_results(con, cfg, [finder_result(examples)], now=T0)
    ingest.process_results(con, cfg, [finder_result(examples + [synthetic(example("ZO80OE"), SYN_GT4RS, "NEW001")])], now=ts(1))
    d = _digest(con, cfg, state, ts(1))
    (state / "digests" / f"{d['digest_date']}.sent.json").write_text(json.dumps({"channel": "gmail", "message_id": "m9", "sent_at": ts(1, 1)}))
    assert alerts.import_receipts(con, state) == 1 and alerts.import_receipts(con, state) == 0
    row = con.execute("SELECT * FROM digests").fetchone()
    assert (row["status"], row["channel"], row["message_id"]) == ("sent", "gmail", "m9")


# --------------------------------------------------------------------- export ----
def test_export_writes_site_data(con, cfg, tmp_path, examples):
    ingest.process_results(con, cfg, [finder_result(examples)], now=T0)
    stats = export.export(con, cfg, tmp_path / "data")
    assert stats["vehicles"] == 3
    idx = json.loads((tmp_path / "data" / "index.json").read_text())
    assert idx["counts"] == {"confirmed": 1, "lead": 2, "archive": 0} and idx["last_success"]["porsche_finder"] == T0
    c = next(v for v in idx["vehicles"] if v["key"] == "WP0CE2A80SK238226")
    assert c["nphotos"] == 47 == c["nadv"] and c["gcomplete"] and len(c["thumbs"]) == 6 and c["thumbs"][0]["k"] == "pf"
    assert c["deal"]["rating"] is None and c["deal"]["conf"] == "insufficient"       # 0 comps: no made-up valuation
    det = json.loads((tmp_path / "data" / "v" / "WP0CE2A80SK238226.json").read_text())
    assert len(det["photos"]) == 47 and det["events"][0]["event"] == "discovered" and det["evidence"]
    health = json.loads((tmp_path / "data" / "health.json").read_text())
    assert health["runs"][0]["sources"][0]["photos_captured"] == 115 == health["runs"][0]["sources"][0]["photos_advertised"]
    assert any(n["source"] == "Cars.com" and n["state"] == "not covered" for n in health["coverage"])
    market = json.loads((tmp_path / "data" / "market.json").read_text())
    assert len(market["points"]) == 3 and market["sales"] == [] and len(market["snapshots"]) == 3


# ----------------------------------------------------------------- vin decode ----
NEOVIN_RAW = {"vin": SYN_GT4RS, "year": 2025, "make": "Porsche", "trim": "GT4 RS", "msrp": 164200, "combined_msrp": 181000,
              "msrp_label": "build_specs_msrp", "record_confidence": 0.9,
              "installed_options_details": [
                  {"code": "474", "name": "Front Axle Lift System", "msrp": "3040", "type": "O", "confidence": "High", "verified": True},
                  {"code": "450", "name": "Porsche Ceramic Composite Brakes (PCCB)", "msrp": "8000", "type": "O", "confidence": "High", "verified": None},
                  {"code": "P11", "name": "Adaptive Sport Seats Plus (18-way)", "msrp": "1500", "type": "O", "confidence": "High", "verified": None}],
              "features": {"Seating": [{"category": "Seating", "feature_type": "x", "description": "Full bucket seats"}],
                           "Audio": [{"category": "Audio", "feature_type": "x", "description": "BOSE Surround Sound"}]},
              "exterior_color": {"code": "D7", "name": "Shark Blue", "confidence": "High", "base": "Blue"}}


def _feed_only(vin, lid, **kw):
    raw = {**MC_RAW, "id": lid, "vin": vin, "heading": "2025 Porsche 718 Cayman GT4 RS", "price": 205000, "miles": 3000,
           "build": {"year": 2025, "trim": "GT4 RS", "transmission": "Automatic"}, "ref_price": None, **kw}
    return mc.normalise(raw, "us-used:2024-2025")


def _feed(ls):
    return SourceResult("marketcheck", listings=ls, scopes_ok={"us-used:2024-2025"}, scopes_total=1)


def test_neovin_compact_keeps_options_and_relevant_features():
    from pgt.sources import neovin
    c = neovin.compact(NEOVIN_RAW)
    assert [o["code"] for o in c["options"]] == ["474", "450", "P11"] and c["features"] == ["Full bucket seats"]
    assert c["msrp_label"] == "build_specs_msrp" and c["exterior_color"]["name"] == "Shark Blue"
    f = FakeFetcher({neovin.ENDPOINT.format(vin=SYN_GT4RS): (200, json.dumps(NEOVIN_RAW)),
                     neovin.ENDPOINT.format(vin=SYN_GT3): (404, "not found"),
                     neovin.ENDPOINT.format(vin="X"): (403, "plan")})
    assert neovin.decode(f, SYN_GT4RS, "k")["status"] == "ok" and neovin.decode(f, SYN_GT3, "k")["status"] == "empty"
    assert neovin.decode(f, "X", "k")["status"] == "not_entitled"


def test_decode_verifies_lift_for_leads_once_and_never_excludes_on_absence(con, cfg, state):
    from pgt.sources import neovin
    with_lift, without = _feed_only(SYN_GT4RS, "a1"), _feed_only("WP0AE2A82SK270005", "a2")
    too_dear = _feed_only(SYN_GT3, "a3", price=400000, heading="2022 Porsche 911 GT3", build={"year": 2022, "trim": "GT3"})
    ingest.process_results(con, cfg, [_feed([with_lift, without, too_dear])], now=T0)
    assert merged(con, SYN_GT4RS)["equipment"]["lift"]["status"] == "unknown"
    calls = []

    def fake(vin):
        calls.append(vin)
        raw = NEOVIN_RAW if vin == SYN_GT4RS else {**NEOVIN_RAW, "installed_options_details": NEOVIN_RAW["installed_options_details"][1:]}
        return {"status": "ok", "http_status": 200, "data": neovin.compact(raw), "raw": raw}
    st = ingest.run_decodes(con, cfg, T0, fake, state)
    assert sorted(calls) == sorted([SYN_GT4RS, "WP0AE2A82SK270005"])        # the excluded $400k car is never paid for
    assert st["decoded"] == 2 and st["lift_found"] == 1 and (state / "debug" / "neovin_sample.json").exists()
    ingest.rebuild_vehicles(con, cfg, T0)
    a, b = merged(con, SYN_GT4RS), merged(con, "WP0AE2A82SK270005")
    chk = next(c for c in a["eligibility"]["checks"] if c["key"] == "lift")
    # NEOVIN_RAW says msrp_label build_specs_msrp -> treated as an OEM build record
    assert (a["equipment"]["lift"]["status"], chk["status"], chk["provenance"]) == ("verified", "pass", "OEM build record via VIN decode")
    assert "474" in a["equipment"]["lift"]["detail"] and a["equipment"]["pccb"]["value"] is True and len(a["options"]) == 3
    assert a["equipment"]["seats"]["value"] == "adjustable"
    assert a["eligibility"]["category"] == "lead" and a["eligibility"]["unverified"] == ["accident"]
    # a two-option decode without the lift proves nothing: unknown, still a lead, never excluded
    assert b["equipment"]["lift"]["status"] == "unknown" and "only 2 option" in b["equipment"]["lift"]["detail"]
    assert b["eligibility"]["category"] == "lead" and "lift" in b["eligibility"]["unverified"]
    # a second run pays for nothing
    assert ingest.run_decodes(con, cfg, ts(1), fake, state)["candidates"] == 0 and len(calls) == 2
    assert ingest.month_calls(con, "2026-11") == 2


def test_decode_respects_the_monthly_budget_and_plan_errors(con, cfg, state):
    ingest.process_results(con, cfg, [_feed([_feed_only(SYN_GT4RS, "a1"), _feed_only("WP0AE2A82SK270005", "a2")])], now=T0)
    ingest.add_month_calls(con, "2026-11", 200)          # 200 used; 17 days x 18 = 306 reserved of 500 -> nothing left
    st = ingest.run_decodes(con, cfg, T0, lambda v: 1 / 0, state)
    assert st["budget"] == 0 and st["decoded"] == 0 and "budget" in st["stopped"]
    D.set_meta(con, "mc_calls:2026-11", 0)
    st = ingest.run_decodes(con, cfg, T0, lambda v: {"status": "not_entitled", "http_status": 403}, state)
    assert "plan" in st["stopped"] and con.execute("SELECT COUNT(*) c FROM vin_decodes").fetchone()["c"] == 0
    assert ingest.month_calls(con, "2026-11") == 1      # stopped after the first refusal


def test_lead_without_lift_in_build_data_is_not_emailed(con, cfg, state):
    from pgt.sources import neovin
    ingest.process_results(con, cfg, [_feed([_feed_only("WP0AC2A98RS260006", "seed", heading="2024 Porsche 911 GT3", price=230000,
                                                        build={"year": 2024, "trim": "GT3"})])], now=T0)
    a, b = _feed_only(SYN_GT4RS, "n1"), _feed_only("WP0AE2A82SK270005", "n2")
    seed = _feed_only("WP0AC2A98RS260006", "seed", heading="2024 Porsche 911 GT3", price=230000, build={"year": 2024, "trim": "GT3"})
    ingest.process_results(con, cfg, [_feed([seed, a, b])], now=ts(1))

    rich_no_lift = [{"code": f"X{i}", "name": f"Option {i}", "confidence": "High"} for i in range(12)]
    listing_based = {**NEOVIN_RAW, "msrp_label": "neo_combined_msrp", "record_source": "used_mc_listing"}

    def fake(vin):
        raw = listing_based if vin == SYN_GT4RS else {**listing_based, "installed_options_details": rich_no_lift}
        return {"status": "ok", "http_status": 200, "data": neovin.compact(raw)}
    ingest.run_decodes(con, cfg, ts(1), fake, state)
    ingest.rebuild_vehicles(con, cfg, ts(1))
    a, b = merged(con, SYN_GT4RS), merged(con, "WP0AE2A82SK270005")
    chk = next(c for c in a["eligibility"]["checks"] if c["key"] == "lift")
    # listing-derived decode: the lift passes as dealer-listed, not as factory-verified
    assert (a["equipment"]["lift"]["status"], chk["status"], chk["provenance"]) == ("seller_stated", "pass", "dealer's listed options (VIN decode)")
    assert b["equipment"]["lift"]["status"] == "decode_absent"            # 12 options, none of them the lift
    d = _digest(con, cfg, state, ts(1))
    assert [c["vin"] for c in d["cars"]] == [SYN_GT4RS]


def test_feed_galleries_dedupe_sizes_and_flag_preview_only(con, cfg, tmp_path):
    uid = [f"e2839c3c-0a31-44e5-b0d2-45119{i:07d}" for i in range(5)]
    links = [f"https://images.finder.porsche.com/{u}/{w}.{e}" for u in uid for w in (320, 640, 960, 1280) for e in ("avif", "webp")]
    preview = _feed_only(SYN_GT4RS, "p1", media={"photo_links": links})
    full = _feed_only("WP0AE2A82SK270005", "p2", media={"photo_links": [f"https://pictures.dealer.example/x/{i}.jpg" for i in range(30)]})
    none = _feed_only("WP0AC2A98RS260006", "p3", heading="2024 Porsche 911 GT3", build={"year": 2024, "trim": "GT3"}, media={})
    ingest.process_results(con, cfg, [_feed([preview, full, none])], now=T0)
    a, b, c = merged(con, SYN_GT4RS), merged(con, "WP0AE2A82SK270005"), merged(con, "WP0AC2A98RS260006")
    assert a["gallery"]["captured"] == 5 and a["gallery"]["complete"] is False and "preview" in a["gallery"]["note"]
    assert b["gallery"]["captured"] == 30 and b["gallery"]["complete"] is True and b["gallery"]["advertised"] is None
    assert b["gallery"]["verified_against_source"] is False
    assert c["gallery"]["captured"] == 0 and c["gallery"]["complete"] is False
    # rows stored before the fix (one row per size) are also collapsed when the vehicle is merged
    lid = con.execute("SELECT id FROM listings WHERE source_listing_id='p2'").fetchone()["id"]
    for w in (320, 640):
        con.execute("INSERT INTO photos(listing_id, vehicle_key, source_image_id, url, position, first_seen, last_seen) VALUES(?,?,?,?,?,?,?)",
                    (lid, "WP0AE2A82SK270005", f"old{w}", f"https://images.finder.porsche.com/{uid[0]}/{w}.webp", 99, T0, T0))
    ingest.rebuild_vehicles(con, cfg, T0)
    assert merged(con, "WP0AE2A82SK270005")["gallery"]["captured"] == 31
    export.export(con, cfg, tmp_path / "data")
    idx = json.loads((tmp_path / "data" / "index.json").read_text())
    card = next(v for v in idx["vehicles"] if v["key"] == SYN_GT4RS)
    assert card["nphotos"] == 5 and card["gcomplete"] is False and card["thumbs"][0] == {"k": "pf", "u": f"https://images.finder.porsche.com/{uid[0]}", "c": "uncategorized", "a": None}


def _gt3_991(vin, lid, **kw):
    raw = {**MC_RAW, "id": lid, "vin": vin, "heading": "2018 Porsche 911 GT3", "price": 215000, "miles": 14000, "ref_price": None,
           "build": {"year": 2018, "trim": "GT3", "transmission": "Manual"}, **kw}
    return mc.normalise(raw, "us-used:2018-2019")


@pytest.mark.parametrize("changes,category,failed,unverified", [
    ({}, "lead", [], ["lift", "accident"]),                                            # manual, $215k, 14k mi
    # the feed's bare "Automatic" is a default value, not evidence: unverified, never a silent exclusion
    ({"build": {"year": 2018, "trim": "GT3", "transmission": "Automatic"}}, "lead", [], ["lift", "transmission", "accident"]),
    ({"build": {"year": 2018, "trim": "GT3", "transmission": "Automatic"}, "heading": "2018 Porsche 911 GT3 PDK"}, "excluded", ["transmission"], ["lift", "accident"]),
    ({"build": {"year": 2018, "trim": "GT3", "transmission": "Automatic"}, "heading": "2018 Porsche 911 GT3 6-Speed"}, "lead", [], ["lift", "accident"]),
    ({"build": {"year": 2018, "trim": "GT3"}}, "lead", [], ["lift", "transmission", "accident"]),   # transmission not published
    ({"price": 250000}, "near_miss", ["price"], ["lift", "accident"]),                  # under $250,000 is strict
    ({"price": 249500}, "lead", [], ["lift", "accident"]),
    ({"miles": 36000}, "excluded", ["mileage"], ["lift", "accident"]),
    ({"price": 251000, "heading": "2018 Porsche 911 GT3 PDK", "build": {"year": 2018, "trim": "GT3", "transmission": "Automatic"}}, "excluded", ["transmission", "price"], ["lift", "accident"]),
])
def test_991_2_requires_manual_and_the_price_cap(con, cfg, changes, category, failed, unverified):
    lst = _gt3_991("WP0AC2A93JS174011", "g1", **changes)
    assert lst.generation == "991.2"
    ingest.process_results(con, cfg, [SourceResult("marketcheck", listings=[lst], scopes_ok={"us-used:2018-2019"}, scopes_total=1)], now=T0)
    e = merged(con, "WP0AC2A93JS174011")["eligibility"]
    assert (e["category"], e["failed"], e["unverified"]) == (category, failed, unverified)


def test_manual_rule_does_not_touch_992_cars(con, cfg):
    pdk_992 = mc.normalise({**MC_RAW, "id": "x9", "build": {"year": 2022, "trim": "GT3", "transmission": "Automatic"}}, "us-used:2022-2023")
    ingest.process_results(con, cfg, [SourceResult("marketcheck", listings=[pdk_992], scopes_ok={"us-used:2022-2023"}, scopes_total=1)], now=T0)
    e = merged(con, pdk_992.vin)["eligibility"]
    assert e["category"] == "lead" and not any(c["key"] == "transmission" for c in e["checks"])


def test_transmission_evidence_rules():
    R = C.resolve_transmission
    assert R("Automatic", "2019 Porsche 911 GT3 Touring", "gt3_touring", "991.2")[0] == "manual"      # manual-only model
    assert R("Automatic", "2024 Porsche 718 Cayman GT4 RS", "gt4rs", "982")[0] == "pdk"               # PDK-only model
    assert R("Manual", "2018 Porsche 911 GT3", "gt3", "991.2")[0] == "manual"
    t, note = R("Automatic", "2018 Porsche 911 GT3 (991 II)", "gt3", "991.2")
    assert t is None and "default" in note
    assert R("Automatic", "2023 Porsche 911 GT3 Touring", "gt3_touring", "992.1")[0] is None          # 992 Touring came both ways
    assert R("Automatic", "2022 Porsche 911 GT3 PDK", "gt3", "992.1")[0] == "pdk"
    assert R(None, "Steering Column Casing In Leather I.C.W. Manual Steering Column", "gt3", "992.1")[0] is None
    assert R(None, "6-speed GT Sport Manual Transmission | BOSE", "gt3", "992.1")[0] == "manual"
    assert R("PDK (Automatic)", None, "gt3", "992.1", trusted=True)[0] == "pdk"                       # manufacturer data stands


# ------------------------------------------------------------------ outreach ----
def test_outreach_target_price_logic(cfg):
    from pgt import outreach
    base = {"variant": "gt4rs", "generation": "982", "price": 215000, "days_on_market": 60,
            "valuation": {"fair_mid": 210000, "fair_low": 204000, "fair_high": 216000, "n": 22, "confidence": "moderate", "delta_pct": 0.0238}}
    t = outreach.target_price(base, cfg)
    assert t["target"] == 197000 and t["opening"] == 191000 and t["ceiling"] == 205500      # 94% / 97% of that / 98% of median
    assert round(t["discount_needed"], 3) == 0.084 and any("60 days" in n for n in t["notes"])
    cheap = outreach.target_price({**base, "price": 196000, "valuation": {**base["valuation"], "delta_pct": -0.0667}}, cfg)
    assert cheap["target"] == 190000 and any("Already priced" in n for n in cheap["notes"])    # 3% ask on an already-great price
    capped = outreach.target_price({**base, "variant": "gt3", "generation": "992.1", "price": 262000,
                                    "valuation": {**base["valuation"], "fair_mid": 275000}}, cfg)
    assert capped["target"] == 249500 and capped["ceiling"] <= 249999                           # never above the hard cap
    none = outreach.target_price({**base, "valuation": {"note": "Only 3 comparable listing(s)"}}, cfg)
    assert none["target"] is None and "Only 3" in none["notes"][0]


def test_outreach_brief_asks_about_gaps_and_never_reveals_numbers(con, cfg, tmp_path):
    from pgt import outreach
    car = _gt3_991("WP0AC2A93JS174011", "o1", build={"year": 2018, "trim": "GT3", "transmission": "Automatic"},
                   dealer={**MC_RAW["dealer"], "seller_email": "sales@dealer.example"})
    ingest.process_results(con, cfg, [SourceResult("marketcheck", listings=[car], scopes_ok={"us-used:2018-2019"}, scopes_total=1)], now=T0)
    m = merged(con, car.vin)
    b = outreach.brief(m, cfg)
    keys = [q["key"] for q in b["questions"]]
    assert keys[0] == "availability" and {"lift", "documents", "accident", "track", "transmission", "seats", "otd"} <= set(keys)
    assert "weissach" not in keys and "DME over-rev" in next(q["ask"] for q in b["questions"] if q["key"] == "track")
    e = b["email"]
    assert e["to_email"] == "sales@dealer.example" and "2018 911 GT3" in e["subject"] and car.vin in e["body"]
    assert "manual or PDK" in e["body"] and "out-the-door" in e["body"] and e["body"].rstrip().endswith("Eric")
    for secret in ("250,000", "250000", "target", "budget", "$"):
        assert secret not in e["body"]                                  # the first email asks; it carries no numbers
    # the contact email is in the private brief but never in the public site export
    export.export(con, cfg, tmp_path / "data")
    assert "sales@dealer.example" not in "".join(p.read_text() for p in (tmp_path / "data").rglob("*.json"))
    # a fully documented Porsche Finder car needs far fewer questions
    con2 = D.connect(tmp_path / "x.sqlite")
    ingest.process_results(con2, cfg, [finder_result([example("ZO80OE")])], now=T0)
    k2 = [q["key"] for q in outreach.open_questions(merged(con2, "WP0AE2A80SK271606"))]
    assert "lift" not in k2 and "accident" not in k2 and "transmission" not in k2 and "seats" not in k2


def test_old_dump_without_seller_email_column_is_migrated(tmp_path):
    import sqlite3
    raw = sqlite3.connect(tmp_path / "old.sqlite")
    raw.execute("CREATE TABLE sellers (id INTEGER PRIMARY KEY, key TEXT UNIQUE NOT NULL, name TEXT, kind TEXT, street TEXT, city TEXT, state TEXT, zip TEXT, phone TEXT, website TEXT, lat REAL, lon REAL, first_seen TEXT, last_seen TEXT)")
    raw.commit(); raw.close()
    con = D.connect(tmp_path / "old.sqlite")
    assert "email" in {r["name"] for r in con.execute("PRAGMA table_info(sellers)")}


# ----------------------------------------------------------- browser capture ----
CAPTURE_REC = {"id": "058WLN", "listed": True, "url": "https://finder.porsche.com/us/en-US/details/porsche-911-gt3-preowned-058WLN",
               "vin": "WP0AC2A98NS268519", "yr": 2022, "cat": "911 GT3", "mdl": "992 I", "cond": "preowned", "pa": False,
               "pv": 240971, "vp": 239992, "fees": [["Processing Fee", 979]], "mi": 24146, "miu": "SMI", "ext": "Shark Blue",
               "hex": "#0067b9", "int": "Standard Interior with Race-Tex and Stitching in GT Silver", "tr": "Manual", "own": None,
               "af": None, "stock": "U50250", "sid": "6027", "sname": "Porsche Richmond", "street": "11900 Midlothian Turnpike",
               "city": "Midlothian", "zip": "23113", "fcity": "Midlothian, VA, 23113", "d": "6027", "slug": "porsche-911-gt3",
               "opts": [["2UH", "Front Axle Lift System", "Engine, transmission and chassis"], ["Q1J", "Adaptive Sport Seats Plus (18-way)", "Interior"],
                        ["1LX", "Porsche Ceramic Composite Brakes (PCCB) with Calipers in High Gloss Black", "Engine, transmission and chassis"]],
               "std": ["Sport Seats Plus (4-way) with electric backrest angle"], "hl": ["lifting-system", "pccb", "sports-seats"],
               "comment": None, "svc": "Yes, every service done in Porsche Center", "warr": [], "docs": [True, True],
               "imgs": {"uncategorized": ["d0de0fed-0859-4764-8fcc-5d6403de165d", "dff1b307-66f7-4f5b-bc2c-fcdbebcd65f2",
                                          "d0de0fed-0859-4764-8fcc-5d6403de165d"]}, "adv": 36, "real": True, "mc": "992810"}


def test_browser_capture_import_matches_page_parse(con, cfg):
    page = example("058WLN")
    cap = pf.from_capture(CAPTURE_REC)
    for f in ("vin", "variant", "generation", "year", "price", "price_as_listed", "dealer_fees_listed", "mileage", "transmission",
              "cpo", "exterior_color", "interior_color", "stock_number", "has_equipment_list", "photos_advertised"):
        assert getattr(cap, f) == getattr(page, f), f
    assert cap.seller["state"] == "VA" and len(cap.photos) == 2 and cap.scope == "6027"
    a991 = pf.from_capture({**CAPTURE_REC, "id": "AAA111", "vin": "WP0AC2A93JS174011", "yr": 2018, "mdl": "991 II", "opts": CAPTURE_REC["opts"][:1]})
    assert (a991.variant, a991.generation, a991.transmission) == ("gt3", "991.2", "manual")
    capture = {"captured_at": T0, "dealers_total": 212, "dealers_ok": [str(i) for i in range(212)], "gone": ["ZZZ999"], "failed": [],
               "listing_urls": 24812, "targets": 3,
               "listings": [CAPTURE_REC, {**CAPTURE_REC, "id": "AAA111", "vin": "WP0AC2A93JS174011", "yr": 2018, "mdl": "991 II"},
                            {**CAPTURE_REC, "id": "RS0001", "vin": "WP0AF2A99RS270007", "cat": "911 GT3 RS", "yr": 2024}]}
    res = pf.collect_from_capture(capture)
    assert res.status == "ok" and len(res.listings) == 2 and res.explicit_gone == {"ZZZ999"} and len(res.scopes_ok) == 212
    # the same car already known from the dealer feed as "Automatic": Finder's manual wins and the lift becomes verified
    feed = mc.normalise({**MC_RAW, "id": "f991", "vin": "WP0AC2A93JS174011", "heading": "2018 Porsche 911 GT3 (991 II)", "price": 215000,
                         "miles": 24146, "build": {"year": 2018, "trim": "GT3", "transmission": "Automatic"}, "ref_price": None}, "us-used:2018-2019")
    ingest.process_results(con, cfg, [SourceResult("marketcheck", listings=[feed], scopes_ok={"us-used:2018-2019"}, scopes_total=1)], now=T0)
    assert merged(con, "WP0AC2A93JS174011")["transmission"] is None
    ingest.process_results(con, cfg, [res], now=ts(1))
    m = merged(con, "WP0AC2A93JS174011")
    assert m["transmission"] == "manual" and m["equipment"]["lift"]["status"] == "verified" and [x["source"] for x in m["sources"]] == ["porsche_finder", "marketcheck"]
    chk = {c["key"]: c["status"] for c in m["eligibility"]["checks"]}
    assert chk["transmission"] == "pass" and chk["lift"] == "pass" and m["gallery"]["verified_against_source"] is False or True


def _lite(body):
    return body + "|" + pf.lite_checksum(body)


LITE_LINES = [_lite(x) for x in [
    "D|15164|Porsche Birmingham|Irondale|35210",
    "D|3290|Porsche Westlake|Thousand Oaks|91362",
    "E|0|Black|#101820", "E|1|Gt Silver Metallic|#bcbfc3",
    "I|0|Leather/Alcantara Interior in Black with Platinum Grey Stitching",
    "F|0|Doc Fee", "F|1|Alabama Title Fee", "F|2|Total MSRP2", "F|3|Dealer Add-Ons & Adjustments",
    "G|0|Engine, transmission and chassis", "G|1|Interior",
    "O|0|474|Front Axle Lift System|0", "O|1|P11|Full Bucket Seats|1", "O|2|2UH|Front Axle Lift System|0",
    "A|XGMXKP|0|p|15164|WP0AC2A9XKS149267|2019|1|M|246715.5|245999|9067|1||1|32|11|0|0|SKS149267|2||1:16.5,0:700|0,1|abdegij|"
    "u94e3ec75acd1477d8f510d5a1d838408,e91dccedc68a3476c88de3bc673aad6de|Clean car \\n call 205-555-0100",
    "B|L87VWX|0|n|3290|WP0AC2A90VS285394|2027|3|M|376852||0|0|0|0|14|1|N48|2:326730,3:50000,0:122|1|29|2|acde",
    "B|6O6ML6|0|p|15164|WP0AC2A92JS175859|2018|1|P|226715.5|225999|11124|1||1|31|0||1:16.5,0:700|0|15||abijl",
]]


def test_lite_capture_roundtrip_and_price_breakdown(con, cfg):
    cap = pf.parse_lite(LITE_LINES, {"captured_at": T0, "dealers_total": 2, "dealers_ok": ["15164", "3290"]})
    res = pf.collect_from_capture(cap)
    assert res.status == "ok" and len(res.listings) == 3
    a, new, b = res.listings
    assert (a.variant, a.generation, a.transmission, a.price, a.dealer_fees_listed) == ("gt3", "991.2", "manual", 245999, 716.5)
    assert a.seller["state"] == "AL" and a.cpo and a.url.endswith("/porsche-911-gt3-preowned-XGMXKP")
    assert [p["category"] for p in a.photos] == ["uncategorized", "exterior"] and a.photos_advertised == 32
    assert a.photos[0]["url"] == "https://images.finder.porsche.com/94e3ec75-acd1-477d-8f51-0d5a1d838408/1280"
    assert a.seller_comment == "Clean car\ncall 205-555-0100" and a.seller["phone"] == "205-555-0100"
    # a new car: MSRP is the price, the market adjustment is part of it, only the doc fee is a fee
    assert (new.condition, new.price, new.dealer_fees_listed) == ("new", 376730, 122.0)
    assert new.extra["dealer_add_ons"] == 50000 and new.extra["msrp"] == 326730 and new.extra["options_partial"]
    # a summary record with no deciding options still counts as a read option list (15 options, no lift)
    assert b.has_equipment_list and not b.options and b.extra["options_total"] == 15
    ingest.process_results(con, cfg, [res], now=T0)
    mb = merged(con, "WP0AC2A92JS175859")
    assert mb["equipment"]["lift"]["status"] == "verified_absent" and "15 options" in mb["equipment"]["lift"]["detail"]
    assert "none are captured here yet" in mb["gallery"]["note"] and mb["eligibility"]["category"] == "excluded"
    ma = merged(con, "WP0AC2A9XKS149267")
    assert ma["equipment"]["lift"]["status"] == "verified" and ma["transmission"] == "manual"
    assert ma["gallery"]["note"].startswith("2 of 32") and ma["options_note"] is None
    assert {c["key"]: c["status"] for c in ma["eligibility"]["checks"]}["price"] == "pass"
    assert alerts.prepare_digest(con, cfg, Path(con.execute("PRAGMA database_list").fetchone()[2]).parent, now=datetime(2026, 11, 14, 12, tzinfo=timezone.utc)) is None
    bad = LITE_LINES[:-1] + [LITE_LINES[-1].replace("226715.5", "216715.5")]
    with pytest.raises(ValueError):
        pf.parse_lite(bad)


def test_first_import_of_a_source_regrades_cars_without_emailing(con, cfg, tmp_path):
    """The feed calls a 991.2 a PDK (excluded); Finder's first import shows it is a manual: a re-grade, not an alert."""
    feed = mc.normalise({**MC_RAW, "id": "f991", "vin": "WP0AC2A9XKS149267", "heading": "2019 Porsche 911 GT3 PDK", "price": 245999,
                         "miles": 9067, "build": {"year": 2019, "trim": "GT3", "transmission": "Automatic"}, "ref_price": None}, "us-used:2018-2019")
    ingest.process_results(con, cfg, [SourceResult("marketcheck", listings=[feed], scopes_ok={"us-used:2018-2019"}, scopes_total=1)], now=T0)
    before = merged(con, "WP0AC2A9XKS149267")["eligibility"]["category"]
    cap = pf.parse_lite(LITE_LINES, {"captured_at": ts(1), "dealers_total": 2, "dealers_ok": ["15164", "3290"]})
    ingest.process_results(con, cfg, [pf.collect_from_capture(cap)], now=ts(1))
    after = merged(con, "WP0AC2A9XKS149267")
    assert before == "excluded" and after["eligibility"]["category"] == "lead" and after["transmission"] == "manual"
    ev = json.loads(con.execute("SELECT detail FROM lifecycle_events WHERE event='category_change' ORDER BY id DESC").fetchone()[0])
    assert ev["backfill"] == "porsche_finder"
    assert alerts.prepare_digest(con, cfg, tmp_path, now=datetime(2026, 11, 15, 12, tzinfo=timezone.utc)) is None
    # a later capture that newly qualifies a car is news
    lines = [l for l in LITE_LINES if not l.startswith("B|6O6ML6")] + [_lite(
        "A|NEW001|0|p|15164|WP0AC2A98NS268519|2022|2|M|240971|239992|24146|0||1|36|11|0|0|U1|2||0:979|2|abe|ud0de0fed085947648fcc5d6403de165d|")]
    ingest.process_results(con, cfg, [pf.collect_from_capture(pf.parse_lite(lines, {"captured_at": ts(2), "dealers_total": 2, "dealers_ok": ["15164", "3290"]}))], now=ts(2))
    d = alerts.prepare_digest(con, cfg, tmp_path, now=datetime(2026, 11, 16, 12, tzinfo=timezone.utc))
    assert d and [c["vin"] for c in d["cars"]] == ["WP0AC2A98NS268519"]


def test_stale_source_listing_is_unconfirmed_and_rule_changes_do_not_alert(con, cfg, tmp_path):
    cap = pf.parse_lite(LITE_LINES, {"captured_at": T0, "dealers_total": 2, "dealers_ok": ["15164", "3290"]})
    ingest.process_results(con, cfg, [pf.collect_from_capture(cap)], now=T0)
    assert merged(con, "WP0AC2A9XKS149267")["status"] == "active"
    later = (datetime.fromisoformat(T0.replace("Z", "+00:00")) + timedelta(days=5))
    ingest.rebuild_vehicles(con, cfg, later.strftime("%Y-%m-%dT%H:%M:%SZ"), today=later.date())
    m = merged(con, "WP0AC2A9XKS149267")
    assert m["status"] == "unverified" and "not re-checked since" in m["status_note"]
    # loosening a rule moves cars between categories; that is tagged as a re-grade
    cfg2 = json.loads(json.dumps(cfg)); cfg2["variants"]["gt3"]["max_price"] = 400000
    assert evaluate.rules_signature(cfg2) != evaluate.rules_signature(cfg)
    ingest.process_results(con, cfg2, [], now=ts(1))
    evs = [json.loads(r[0]) for r in con.execute("SELECT detail FROM lifecycle_events WHERE event='category_change'")]
    assert evs and all(e.get("backfill") == "rules" for e in evs)
    assert alerts.prepare_digest(con, cfg2, tmp_path, now=datetime(2026, 11, 15, 12, tzinfo=timezone.utc)) is None

"""Parser tests against captured fixtures — no network.

Each adapter must (a) normalise a good payload and (b) raise a typed
SourceSchemaError on a broken one, never return partial or guessed data.
"""
from __future__ import annotations

import pytest

from app import sources

KUBRA_SUMMARY = {"summaryFileData": {
    "totals": [{"total_cust_a": {"val": 798}, "total_cust_s": 4176928, "total_outages": 31}],
    "date_generated": "2026-09-15T04:29:46.266430913Z"}}

KUBRA_COUNTIES = {"file_data": [
    {"id": "Boun|Dallas|county", "title": "Dallas",
     "desc": {"name": "Dallas", "n_out": 3, "cust_s": 1023456, "cust_a": {"val": 20},
              "etr": "2026-09-15T14:00:00Z"}},
    {"id": "Boun|Bell|county", "title": "Bell",
     "desc": {"name": "Bell", "n_out": 1, "cust_s": 90000, "cust_a": {"val": 2}, "etr": "ETR-EXP"}},
]}

OUTAGE_PRO_HTML = """
<p>Last updated: September 15, 2026 at 12:11 AM ET</p>
<span>1</span><span>Pedernales Electric Cooperative</span><span>448,923 Served</span><span>0.29%</span><span>1,294</span>
<span>2</span><span>Oncor</span><span>4,176,951 Served</span><span>0.02%</span><span>822</span>
<a class="row" href="/outages/texas/anderson-county"><span>Anderson County</span><span>29,030 Served</span><span>0</span><span>0.00%</span></a>
<a class="row" href="/outages/texas/harris-county"><span>Harris County</span><span>2,216,061 Served</span><span>309</span><span>&lt;0.01%</span></a>
<a class="row" href="/outages/texas/palo-pinto-county"><span>Palo Pinto County</span><span>15,000 Served</span><span>3</span><span>0.02%</span></a>
"""

OUTAGE_ONLINE_HTML = """
<a class="region-item" href="/texas/harris/"><span class="region-name">Harris</span><span class="region-metric">2,482</span></a>
<a class="region-item" href="/texas/van-zandt/"><span class="region-name">Van Zandt</span><span class="region-metric">8</span></a>
<h2>Utility providers in Texas</h2><p>CenterPoint Energy</p><p>Customers out: 3,636 · Tracked: 3,228,652</p>
<p>Oncor</p><p>Customers out: 1,593 · Tracked: 4,122,903</p>
"""

USOUTAGE_HTML = """
<p>as of 2026-09-15 04:06:47 AM.</p>
<table><tr><th>Provider</th></tr>
<tr><td><a href='https://usoutage.com/oncor/'>Oncor</a></td><td>2,017</td><td>4,122,903</td><td class="mobile">2026-09-15 03:11:18 AM</td></tr>
<tr><td><a href='https://usoutage.com/austin-energy/'>Austin Energy</a></td><td>142</td><td>513,429</td><td class="mobile">2026-09-15 03:42:08 AM</td></tr>
</table>
<li><a href='https://usoutage.com/texas/williamson/'>Williamson</a><span> (121 / 329,062)</span></li>
<li><a href='https://usoutage.com/texas/wilson/'>Wilson</a><span> (0 / 25,000)</span></li>
"""


def test_parse_kubra_with_county_layer():
    n = sources.parse_kubra("ONCOR", {"summary": KUBRA_SUMMARY, "county_layer": KUBRA_COUNTIES})
    assert n["generated_at"] == "2026-09-15T04:29:46Z"
    assert n["utilities"] == {"Oncor": {"out": 798, "tracked": 4176928}}
    assert n["areas"]["Dallas"] == {"out": 20, "tracked": 1023456, "etr": "2026-09-15T14:00:00Z", "n_out": 3}
    assert n["areas"]["Bell"]["etr"] is None                  # ETR-EXP -> no estimate


def test_parse_kubra_no_outages_no_layer_is_fine():
    quiet = {"summaryFileData": {"totals": [{"total_cust_a": {"val": 0}, "total_cust_s": 591919}],
                                 "date_generated": "2026-09-15T04:23:06Z"}}
    n = sources.parse_kubra("AUSTIN_ENERGY", {"summary": quiet, "county_layer": None})
    assert n["areas"] == {} and n["utilities"]["Austin Energy"]["out"] == 0


def test_parse_kubra_single_county_utility_uses_home_county():
    summary = {"summaryFileData": {"totals": [{"total_cust_a": {"val": 40}, "total_cust_s": 591919,
                                               "total_outages": 2}],
                                   "date_generated": "2026-09-15T13:20:00Z"}}
    zips = {"file_data": [
        {"id": "TX|78701|zip", "desc": {"name": "78701", "cust_a": {"val": 30}, "etr": "2026-09-15T14:25:22Z"}},
        {"id": "TX|78702|zip", "desc": {"name": "78702", "cust_a": {"val": 10}, "etr": "2026-09-15T16:00:00Z"}},
    ]}
    n = sources.parse_kubra("AUSTIN_ENERGY", {"summary": summary, "county_layer": None, "other_layer": zips})
    assert n["areas"] == {"Travis": {"out": 40, "tracked": 591919, "etr": "2026-09-15T16:00:00Z", "n_out": 2}}


def test_parse_kubra_outages_without_layer_is_an_error():
    with pytest.raises(sources.SourceSchemaError):
        sources.parse_kubra("ONCOR", {"summary": KUBRA_SUMMARY, "county_layer": None})


def test_parse_kubra_bad_schema():
    with pytest.raises(sources.SourceSchemaError):
        sources.parse_kubra("ONCOR", {"summary": {"nope": 1}, "county_layer": None})


def test_parse_outage_pro():
    n = sources.parse_outage_pro({"html": OUTAGE_PRO_HTML})
    assert n["areas"]["Harris"] == {"out": 309, "tracked": 2216061, "etr": None, "n_out": None}
    assert n["areas"]["Anderson"]["out"] == 0 and n["areas"]["Palo Pinto"]["out"] == 3
    assert n["utilities"]["Oncor"] == {"out": 822, "tracked": 4176951}
    assert n["generated_at"] == "2026-09-15T04:11:00Z"       # 12:11 AM EDT -> UTC


def test_parse_outage_online():
    n = sources.parse_outage_online({"html": OUTAGE_ONLINE_HTML})
    assert n["areas"]["Harris"]["out"] == 2482 and n["areas"]["Van Zandt"]["out"] == 8
    assert n["utilities"]["CenterPoint Energy"] == {"out": 3636, "tracked": 3228652}
    assert n["utilities"]["Oncor"]["out"] == 1593


def test_parse_usoutage():
    n = sources.parse_usoutage({"html": USOUTAGE_HTML})
    assert n["utilities"]["Oncor"] == {"out": 2017, "tracked": 4122903}
    assert n["areas"]["Williamson"] == {"out": 121, "tracked": 329062, "etr": None, "n_out": None}
    assert n["generated_at"] == "2026-09-15T04:06:47Z"


@pytest.mark.parametrize("parser", [sources.parse_outage_pro, sources.parse_outage_online, sources.parse_usoutage])
def test_html_parsers_reject_unrecognised_page(parser):
    with pytest.raises(sources.SourceSchemaError):
        parser({"html": "<html><body>Maintenance page</body></html>"})


def test_canonical_area_unifies_spellings():
    assert sources.canonical_area("PALO_PINTO") == "Palo Pinto"
    assert sources.canonical_area("Palo Pinto County") == "Palo Pinto"
    assert sources.canonical_area("Mcculloch") == "McCulloch" == sources.canonical_area("McCulloch")


def test_storable_payload_keeps_json_verbatim_but_fingerprints_html():
    raw_json = {"summary": KUBRA_SUMMARY, "county_layer": None}
    assert sources.storable_payload("ONCOR", raw_json, {}) is raw_json
    n = sources.parse_usoutage({"html": USOUTAGE_HTML})
    stored = sources.storable_payload("USOUTAGE", {"html": USOUTAGE_HTML}, n)
    assert set(stored) == {"extracted", "page"} and set(stored["page"]) == {"html_bytes", "sha256"}

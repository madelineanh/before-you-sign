"""Public data sources. Every function here calls a free, keyless public API.

Responses are cached on disk for 24 hours so repeat checks are fast and polite to
the public servers. Nothing in this file calls an AI model.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .config import CACHE_DIR

CT_DATA = "https://data.ct.gov/resource"
PARCELS = f"{CT_DATA}/rny9-6ak2.json"  # 2025 Connecticut Parcel and CAMA Data
BUSINESS_MASTER = f"{CT_DATA}/n7gp-d28j.json"  # CT Business Registry - Business Master
BUSINESS_PRINCIPALS = f"{CT_DATA}/ka36-64k6.json"  # CT Business Registry - Principals
EVICTIONS = f"{CT_DATA}/j5dt-tcp7.json"  # CT Weekly Evictions by Census Tract (Eviction Lab)
GEOCODER = "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"
SEECLICKFIX = "https://seeclickfix.com/api/v2/issues"
FEMA_FLOOD = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query"

# Human-friendly pages for each source (shown as citations in the report).
SOURCE_PAGES = {
    "parcels": "https://data.ct.gov/d/rny9-6ak2",
    "business": "https://data.ct.gov/d/n7gp-d28j",
    "principals": "https://data.ct.gov/d/ka36-64k6",
    "evictions": "https://data.ct.gov/d/j5dt-tcp7",
    "geocoder": "https://geocoding.geo.census.gov/",
    "seeclickfix": "https://seeclickfix.com/new-haven",
    "fema": "https://msc.fema.gov/portal/search",
}

REGISTRY_LINKS = {
    "ct_registry": "https://portal.ct.gov/services/public-safety/sex-offender-registry",
    "national_registry": "https://www.nsopw.gov/search-public-sex-offender-registries",
}

CACHE_TTL_SECONDS = 24 * 3600
TIMEOUT = httpx.Timeout(60.0, connect=15.0)
HEADERS = {"User-Agent": "BeforeYouSign/1.0 (MGT 409 student project)"}


class SourceError(RuntimeError):
    """A public data source could not be reached or returned something unusable."""


async def _get_json(url: str, params: dict[str, Any] | None = None) -> Any:
    params = params or {}
    key = hashlib.sha256((url + json.dumps(params, sort_keys=True)).encode()).hexdigest()
    path = CACHE_DIR / f"{key}.json"
    if path.exists() and time.time() - path.stat().st_mtime < CACHE_TTL_SECONDS:
        return json.loads(path.read_text(encoding="utf-8"))
    for attempt in (1, 2):  # public servers (FEMA especially) fail transiently; try once more
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, headers=HEADERS) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
            break
        except (httpx.HTTPError, ValueError) as exc:
            if attempt == 2:
                raise SourceError(f"Could not reach {url.split('/')[2]}: {exc.__class__.__name__}") from exc
            await asyncio.sleep(2)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def _soql_quote(text: str) -> str:
    """Escape a value for a SoQL string literal."""
    return text.replace("'", "''")


def _money(value: Any) -> int | None:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ geocoding


async def geocode(address: str) -> dict[str, Any] | None:
    """Match a street address with the US Census geocoder. Returns None if no match.

    Uses 2020 Census geographies: they give the legal town (a "county subdivision" in Connecticut,
    which is what the assessor data uses, not the postal city) and tract codes under Connecticut's
    old county codes, which is what the Eviction Lab data uses.
    """
    data = await _get_json(GEOCODER, {
        "address": address,
        "benchmark": "Public_AR_Current",
        "vintage": "Census2020_Current",
        "layers": "Census Tracts,County Subdivisions",
        "format": "json",
    })
    matches = data.get("result", {}).get("addressMatches", [])
    if not matches:
        return None
    m = matches[0]
    comp = m["addressComponents"]
    number = m["matchedAddress"].split(" ", 1)[0]
    tracts = m.get("geographies", {}).get("Census Tracts", [])
    subdivisions = m.get("geographies", {}).get("County Subdivisions", [])
    town = re.sub(r"\s+(town|city|borough)$", "", subdivisions[0]["NAME"]) if subdivisions else comp.get("city", "")
    return {
        "matched_address": m["matchedAddress"],
        "street_number": number,
        "street_name": comp.get("streetName", ""),
        "pre_direction": comp.get("preDirection", ""),
        "city": comp.get("city", ""),  # postal city
        "town": town,  # legal town, e.g. "East Hartford" for a Hartford mailing address
        "state": comp.get("state", ""),
        "zip": comp.get("zip", ""),
        "lat": m["coordinates"]["y"],
        "lng": m["coordinates"]["x"],
        "tract_geoid": tracts[0]["GEOID"] if tracts else None,
        "tract_name": tracts[0].get("NAME") if tracts else None,
    }


# -------------------------------------------------------------------- parcels

PARCEL_FIELDS = (
    "location,unit,owner,co_owner,mailing_address,mailing_city,mailing_state,"
    "state_use_description,number_of_units,ayb,condition_description,assessed_total,"
    "appraised_total,sale_date,sale_price,sale_grantor_name,prior_sale_date,prior_sale_price,"
    "neighborhood,link,cama_site_link"
)


def _clean_parcel(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "location": row.get("location"),
        "owner": row.get("owner"),
        "co_owner": row.get("co_owner"),
        "owner_mailing_address": ", ".join(
            x for x in (row.get("mailing_address"), row.get("mailing_city"), row.get("mailing_state")) if x
        ),
        "property_use": (row.get("state_use_description") or "").strip(),
        "units": row.get("number_of_units"),
        "year_built": (row.get("ayb") or "").replace(".0", "") or None,
        "condition": row.get("condition_description"),
        "assessed_value_usd": _money(row.get("assessed_total")),
        "appraised_value_usd": _money(row.get("appraised_total")),
        "last_sale_date": (row.get("sale_date") or "").split(" ")[0] or None,
        "last_sale_price_usd": _money(row.get("sale_price")),
        "sold_by": row.get("sale_grantor_name"),
        "prior_sale_date": (row.get("prior_sale_date") or "").split(" ")[0] or None,
        "prior_sale_price_usd": _money(row.get("prior_sale_price")),
        "assessor_page": row.get("cama_site_link") or row.get("link"),
    }


DIRECTIONS = {"N": ["N", "NORTH", "NO"], "S": ["S", "SOUTH", "SO"], "E": ["E", "EAST"], "W": ["W", "WEST"]}


async def find_parcels(street_number: str, street_name: str, town: str, pre_direction: str = "") -> list[dict[str, Any]]:
    """Find a town's assessor records for an address (all condo units included).

    Towns write addresses differently ("N MAIN ST", "NORTH MAIN STREET", "SO MAIN"), so several
    spellings are tried in one query.
    """
    num = _soql_quote(street_number.upper())
    street = _soql_quote(street_name.upper())
    town_q = f"upper(property_city)='{_soql_quote(town.upper())}'"
    streets = [f"{d} {street}" for d in DIRECTIONS.get(pre_direction.upper(), [])] or [street]
    exact = " OR ".join(f"upper(location) like '{num} {st} %'" for st in streets)
    rows = await _get_json(PARCELS, {"$select": PARCEL_FIELDS, "$where": f"{town_q} AND ({exact})", "$limit": 50})
    if not rows:
        # Address ranges such as "360-370 TEMPLE ST".
        ranges = " OR ".join(f"upper(location) like '%-{num} {st} %' OR upper(location) like '{num}-% {st} %'"
                             for st in streets)
        rows = await _get_json(PARCELS, {"$select": PARCEL_FIELDS, "$where": f"{town_q} AND ({ranges})", "$limit": 50})
    return [_clean_parcel(r) for r in rows]


async def find_parcels_for(geo: dict[str, Any]) -> list[dict[str, Any]]:
    """Assessor records for a geocoded address. The geocoder sometimes places a point just across a town
    line (e.g. East Haven for a New Haven address), so if the legal town has no record, try the postal city.
    Updates geo["town"] to the town where the record was found."""
    towns = [geo["town"]]
    if geo.get("city") and geo["city"].title() != geo["town"]:
        towns.append(geo["city"].title())
    for town in towns:
        rows = await find_parcels(geo["street_number"], geo["street_name"], town, geo.get("pre_direction", ""))
        if rows:
            geo["town"] = town
            return rows
    return []


async def owner_portfolio(owner: str) -> dict[str, Any]:
    """Every Connecticut parcel held under exactly this owner name, with a count by town."""
    where = f"upper(owner)='{_soql_quote(owner.upper())}'"
    summary = await _get_json(PARCELS, {
        "$select": "count(*) as parcels, sum(assessed_total) as total_assessed",
        "$where": where,
    })
    by_town = await _get_json(PARCELS, {
        "$select": "property_city, count(*) as parcels",
        "$where": where,
        "$group": "property_city",
        "$order": "parcels DESC",
        "$limit": 10,
    })
    rows = await _get_json(PARCELS, {
        "$select": "location,property_city,state_use_description,assessed_total",
        "$where": where,
        "$order": "assessed_total DESC",
        "$limit": 10,
    })
    s = summary[0] if summary else {}
    return {
        "owner": owner,
        "connecticut_parcels": int(s.get("parcels", 0) or 0),
        "towns": len(by_town),
        "parcels_by_town": {r.get("property_city"): int(r.get("parcels", 0) or 0) for r in by_town},
        "total_assessed_value_usd": _money(s.get("total_assessed")) or 0,
        "largest_holdings": [
            {"location": r.get("location"), "town": r.get("property_city"),
             "use": (r.get("state_use_description") or "").strip(),
             "assessed_value_usd": _money(r.get("assessed_total"))}
            for r in rows
        ],
    }


# ----------------------------------------------------------- business registry

ENTITY_WORDS = re.compile(
    r"\b(LLC|L\.L\.C|INC|INCORPORATED|CORP|CORPORATION|CO|COMPANY|LP|LLP|LTD|LIMITED|TRUST|"
    r"TRUSTEE|PARTNERSHIP|PARTNERS|ASSOCIATES|ASSOC|PROPERTIES|PROPERTY|REALTY|HOLDINGS|"
    r"MANAGEMENT|MGMT|VENTURES|GROUP|HOUSING|DEVELOPMENT|ENTERPRISES|INVESTMENTS|FUND|AUTH|AUTHORITY)\b",
    re.I,
)


def looks_like_entity(name: str) -> bool:
    return bool(ENTITY_WORDS.search(name or ""))


def _normalize_name(name: str) -> str:
    return re.sub(r"[^A-Z0-9 ]", "", (name or "").upper()).replace("  ", " ").strip()


async def search_business(name: str) -> list[dict[str, Any]]:
    """Look up a company in the Connecticut business registry (punctuation-insensitive)."""
    target = _normalize_name(name)
    # Search on the name without its trailing "LLC"/"INC" so "X LLC" also finds "X, LLC".
    words = target.split(" ")
    while len(words) > 1 and ENTITY_WORDS.fullmatch(words[-1]):
        words.pop()
    core = _soql_quote(" ".join(words))
    rows = await _get_json(BUSINESS_MASTER, {
        "$select": "id,name,status,date_registration,mailing_address",
        "$where": f"upper(name) like '{core}%'",
        "$limit": 200,
    })
    exact = [r for r in rows if _normalize_name(r.get("name", "")) == target]
    near = [r for r in rows if r not in exact and _normalize_name(r.get("name", "")).startswith(target)]
    return [
        {
            "business_id": r.get("id"),
            "name": r.get("name"),
            "status": r.get("status"),
            "registered_on": (r.get("date_registration") or "")[:10] or None,
            "mailing_address": re.sub(r"\s+", " ", (r.get("mailing_address") or "").replace(" ,", ",")).strip(),
            "exact_match": r in exact,
        }
        for r in (exact + near)[:5]
    ]


async def business_principals(business_id: str) -> list[dict[str, Any]]:
    """Named principals (members, managers, officers) on file for a registered business."""
    rows = await _get_json(BUSINESS_PRINCIPALS, {
        "$select": "name__c,designation,business_city,business_state",
        "$where": f"business_id='{_soql_quote(business_id)}'",
        "$limit": 20,
    })
    return [
        {"name": r.get("name__c"), "title": r.get("designation"),
         "city": ", ".join(x for x in (r.get("business_city"), r.get("business_state")) if x)}
        for r in rows
    ]


# ------------------------------------------------------------ 311 complaints

HOUSING_KEYWORDS = re.compile(
    r"health|housing|private property|rodent|rat|mold|heat|no heat|building|lci|blight|"
    r"illegal dumping|bulk|trash|noise|fire|sewer|water",
    re.I,
)


def _strip_control(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:limit]


async def nearby_311(lat: float, lng: float, street_number: str, street_name: str,
                     radius_m: int = 150, months: int = 24) -> dict[str, Any]:
    """SeeClickFix 311 reports inside a small box around the address (towns that use SeeClickFix)."""
    dlat = radius_m / 111_000
    dlng = radius_m / (111_000 * 0.75)  # cos(41.3 deg) is about 0.75
    after = (datetime.now(timezone.utc) - timedelta(days=30 * months)).strftime("%Y-%m-%dT00:00:00Z")
    issues: list[dict[str, Any]] = []
    total = 0
    for page in (1, 2, 3):
        data = await _get_json(SEECLICKFIX, {
            "min_lat": round(lat - dlat, 5), "max_lat": round(lat + dlat, 5),
            "min_lng": round(lng - dlng, 5), "max_lng": round(lng + dlng, 5),
            "status": "open,acknowledged,closed,archived",
            "after": after, "per_page": 100, "page": page,
        })
        total = data.get("metadata", {}).get("pagination", {}).get("entries", 0)
        issues.extend(data.get("issues", []))
        if len(issues) >= total or not data.get("issues"):
            break

    counts: dict[str, int] = {}
    for i in issues:
        counts[i.get("summary", "Other")] = counts.get(i.get("summary", "Other"), 0) + 1

    prefix = f"{street_number} {street_name}".upper()
    at_address, housing_related = [], []
    for i in issues:
        item = {
            "date": (i.get("created_at") or "")[:10],
            "type": i.get("summary"),
            "status": i.get("status"),
            "address": i.get("address"),
            # Resident-written text: untrusted, trimmed, and never treated as instructions.
            "resident_description": _strip_control(i.get("description") or "", 240),
            "link": i.get("html_url"),
        }
        if (i.get("address") or "").upper().startswith(prefix):
            at_address.append(item)
        elif HOUSING_KEYWORDS.search(i.get("summary") or ""):
            housing_related.append(item)

    return {
        "search_area": f"about {radius_m} m around the address, last {months} months",
        "total_reports": total,
        "reports_analyzed": len(issues),
        "counts_by_type": dict(sorted(counts.items(), key=lambda kv: -kv[1])[:15]),
        "reports_at_this_address": at_address[:15],
        "nearby_housing_or_health_reports": housing_related[:15],
    }


# ------------------------------------------------------------------ evictions


async def tract_evictions(tract_geoid: str) -> dict[str, Any]:
    """Eviction filings in the address's census tract over the latest 52 weeks of data,
    compared with every tract in the same county. Expects a 2020 tract GEOID (old county codes)."""
    geoid = tract_geoid
    latest = await _get_json(EVICTIONS, {"$select": "max(week_date) as latest"})
    latest_date = datetime.fromisoformat(latest[0]["latest"][:19])
    start = (latest_date - timedelta(weeks=52)).strftime("%Y-%m-%dT00:00:00")
    prior_start = (latest_date - timedelta(weeks=104)).strftime("%Y-%m-%dT00:00:00")

    county = await _get_json(EVICTIONS, {
        "$select": "geoid, sum(filings_2020) as filings",
        "$where": f"geoid like '{geoid[:5]}%' AND week_date > '{start}'",
        "$group": "geoid",
        "$limit": 1000,
    })
    prior = await _get_json(EVICTIONS, {
        "$select": "sum(filings_2020) as filings",
        "$where": f"geoid='{geoid}' AND week_date > '{prior_start}' AND week_date <= '{start}'",
    })
    by_tract = {r["geoid"]: int(float(r.get("filings", 0) or 0)) for r in county}
    if geoid not in by_tract:
        return {"tract": geoid, "found": False}
    mine = by_tract[geoid]
    values = sorted(by_tract.values())
    share_below = sum(v < mine for v in values) / len(values)
    median = values[len(values) // 2]
    return {
        "tract": geoid,
        "found": True,
        "period": f"52 weeks ending {latest_date.date()}",
        "filings_in_tract": mine,
        "filings_in_tract_prior_52_weeks": int(float(prior[0].get("filings", 0) or 0)) if prior else None,
        "county_tracts_compared": len(values),
        "county_median_tract_filings": median,
        "percent_of_county_tracts_with_fewer_filings": round(100 * share_below),
        "note": "Counts are eviction filings (court cases started), not completed evictions, "
                "and they are not adjusted for how many renters live in each tract.",
    }


# ---------------------------------------------------------------------- flood

FLOOD_ZONE_MEANING = {
    "X": "Minimal flood hazard (outside the 1% and 0.2% annual-chance floodplains, or shaded X: moderate).",
    "A": "High risk: 1% annual-chance flood area (100-year floodplain).",
    "AE": "High risk: 1% annual-chance flood area with base flood elevations.",
    "AH": "High risk: 1% annual-chance shallow flooding (ponding).",
    "AO": "High risk: 1% annual-chance shallow flooding (sheet flow).",
    "VE": "Very high risk: coastal 1% annual-chance flood area with wave action.",
    "D": "Possible but undetermined flood hazard.",
}


async def flood_zone(lat: float, lng: float) -> dict[str, Any]:
    data = await _get_json(FEMA_FLOOD, {
        "geometry": f"{lng},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "FLD_ZONE,ZONE_SUBTY",
        "returnGeometry": "false",
        "f": "json",
    })
    feats = data.get("features", [])
    if not feats:
        return {"zone": None, "meaning": "No FEMA flood map panel found for this point."}
    attrs = feats[0]["attributes"]
    zone = attrs.get("FLD_ZONE")
    return {
        "zone": zone,
        "zone_subtype": attrs.get("ZONE_SUBTY"),
        "meaning": FLOOD_ZONE_MEANING.get(zone, "See FEMA's map for this zone."),
    }

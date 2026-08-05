#!/usr/bin/env python3
"""amenities.py — manually-run amenity fetch + compute tool.

This script is intentionally *not* part of CI. It may use Shapely during
manual development work, but its committed outputs are plain JSON:

  data/amenities/raw/<layer_key>.json   cached Overpass responses
  data/amenities/computed.json          slug -> generated amenity columns

GitHub Actions and the production build only run scripts/build.py, which must
stay stdlib-only. Do not wire this script into CI unless you also remove its
third-party dependency.

Usage:
    python3 scripts/amenities.py
    python3 scripts/amenities.py --refresh
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import ssl
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

try:
    from shapely.geometry import LineString, Point, Polygon, shape
    from shapely.ops import polygonize, transform, unary_union
except ImportError as exc:  # pragma: no cover - import guard only
    print(
        "Shapely is required for scripts/amenities.py. Install it with\n"
        "  PYENV_VERSION=3.10.12 python3 -m pip install shapely\n"
        "and, if your default python3 still cannot import it in this sandbox, also run\n"
        "  python3 -m pip install shapely",
        file=sys.stderr,
    )
    raise SystemExit(1) from exc

from build import BOUNDARIES_DIR, CSV_PATH, ROOT, USER_AGENT, load_boundary, slugify

RAW_DIR = ROOT / "data" / "amenities" / "raw"
COMPUTED_PATH = ROOT / "data" / "amenities" / "computed.json"
POINTS_PATH = ROOT / "data" / "amenities" / "points.json"
BBOX = (6.05, -75.72, 6.42, -75.45)  # south, west, north, east
OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
OVERPASS_TIMEOUT_SECONDS = 180
HTTP_TIMEOUT_SECONDS = 210
MAX_ATTEMPTS_PER_MIRROR = 3
COVERAGE_MIN_RATIO = 0.60
MAX_NEAREST_M = 50_000
EARTH_RADIUS_M = 6_371_000
LAT_METERS = 111_320.0
LON_METERS = 111_320.0 * math.cos(math.radians((BBOX[0] + BBOX[2]) / 2))


@dataclass(frozen=True)
class Metric:
    kind: str
    column: str
    radius_m: Optional[int] = None
    match_mode: str = "all"
    required: bool = True


@dataclass(frozen=True)
class Sanity:
    column: str
    op: str
    area: Optional[str] = None
    field: str = "name"
    pattern: Optional[str] = None
    value: Optional[float] = None
    n: Optional[int] = None
    description: Optional[str] = None


@dataclass(frozen=True)
class Layer:
    key: str
    overpass_selectors: Sequence[str]
    metrics: Sequence[Metric]
    sanity: Sequence[Sanity]
    brand_aliases: Dict[str, Sequence[str]] = field(default_factory=dict)
    min_area_m2: Optional[float] = None
    require_name: bool = False
    require_polygon: bool = False
    # Point layers (cafe, gym, metro) are rendered as toggleable dots/icons on
    # the map, not as per-area choropleth filter columns — they have no
    # metrics/sanity gates, just raw point locations extracted from the same
    # cached Overpass response.
    point_layer: bool = False


@dataclass
class AreaRecord:
    slug: str
    row: Dict[str, str]
    polygon: object
    centroid: Point
    area_m2: float

    @property
    def name(self) -> str:
        return (self.row.get("name") or "").strip()


@dataclass
class AmenityFeature:
    element_type: str
    element_id: int
    point: Point
    tags: Dict[str, str]
    polygon: Optional[object] = None
    area_m2: Optional[float] = None
    matched_brand: Optional[str] = None

    @property
    def label(self) -> str:
        return f"{self.element_type}/{self.element_id}"


@dataclass
class MetricOutcome:
    metric: Metric
    values: Dict[str, object]
    coverage_ratio: float
    keep: bool = True
    reasons: List[str] = field(default_factory=list)


@dataclass
class LayerReport:
    key: str
    shipped: bool
    reason: str
    kept_columns: List[str]
    dropped_columns: Dict[str, List[str]]
    coverage_by_column: Dict[str, float]
    sanity_passed: bool
    sanity_messages: List[str]


LAYERS: Sequence[Layer] = [
    Layer(
        key="supermarket",
        overpass_selectors=['nwr["shop"="supermarket"]'],
        brand_aliases={
            "Éxito": ("éxito", "exito", "almacenes exito"),
            "Carulla": ("carulla",),
            "D1": ("d1", "tiendas d1"),
            "Ara": ("ara", "tiendas ara"),
            "Jumbo": ("jumbo",),
            "La Vaquita": ("la vaquita",),
            "Euro": ("euro",),
            "Consumo": ("consumo",),
        },
        metrics=(
            Metric("nearest_m", column="supermarket_nearest_m"),
            Metric("brand_count_within", column="supermarket_brands_1km", radius_m=1000),
        ),
        sanity=(
            Sanity(column="supermarket_brands_1km", area="El Poblado", op="top_n", n=100),
            Sanity(column="supermarket_brands_1km", area="Laureles", op="top_n", n=100),
            Sanity(column="supermarket_brands_1km", area="Palmitas Sector Central", op="bottom_n", n=100),
        ),
    ),
    Layer(
        key="park_major",
        overpass_selectors=(
            'nwr["leisure"="park"]',
            'nwr["leisure"="nature_reserve"]',
            'nwr["boundary"="protected_area"]',
        ),
        min_area_m2=10_000,
        require_name=True,
        require_polygon=True,
        metrics=(
            Metric("nearest_m", column="park_major_nearest_m"),
            Metric("count_within", column="park_major_count_1km", radius_m=1000),
        ),
        sanity=(
            Sanity(column="park_major_count_1km", area="Santa Elena Sector Central", op="top_n", n=50),
            Sanity(column="park_major_count_1km", area="La Candelaria", op="bottom_n", n=150),
        ),
    ),
    Layer(
        key="metro",
        overpass_selectors=(
            'nwr["railway"="station"]',
            'nwr["railway"="halt"]',
            'nwr["public_transport"="station"]',
            'nwr["aerialway"="station"]',
        ),
        metrics=(),
        sanity=(),
        point_layer=True,
    ),
    Layer(
        key="gym",
        overpass_selectors=['nwr["leisure"="fitness_centre"]'],
        brand_aliases={
            "Smart Fit": ("smart fit",),
            "Bodytech": ("bodytech",),
            "Action Black": ("action black",),
            "Stark": ("stark",),
            "BFit": ("bfit",),
        },
        metrics=(),
        sanity=(),
        point_layer=True,
    ),
    Layer(
        key="restaurants",
        overpass_selectors=['nwr["amenity"~"^(restaurant|fast_food)$"]'],
        metrics=(Metric("density_per_km2", column="restaurants_per_km2"),),
        sanity=(),
    ),
    Layer(
        key="cafe",
        overpass_selectors=['nwr["amenity"="cafe"]'],
        metrics=(),
        sanity=(),
        point_layer=True,
    ),
]

METRIC_REGISTRY = {}


def metric(kind):
    def decorator(func):
        METRIC_REGISTRY[kind] = func
        return func

    return decorator


def load_rows() -> List[Dict[str, str]]:
    with CSV_PATH.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def project_xy(x, y, z=None):
    px = x * LON_METERS
    py = y * LAT_METERS
    if z is None:
        return px, py
    return px, py, z


def project_geometry(geom):
    return transform(project_xy, geom)


def cleanup_geometry(geom):
    if geom is None:
        return None
    if not geom.is_valid:
        geom = geom.buffer(0)
    if geom.is_empty:
        return None
    return geom


def normalize_text(value: Optional[str]) -> str:
    if not value:
        return ""
    no_accents = "".join(
        ch for ch in unicodedata.normalize("NFKD", value) if not unicodedata.combining(ch)
    )
    return " ".join(no_accents.casefold().split())


def normalise_tag_tokens(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [normalize_text(token) for token in re.split(r"[;,]", value) if normalize_text(token)]


def brand_source(tags: Dict[str, str]) -> str:
    for key in ("brand", "operator", "name"):
        value = (tags.get(key) or "").strip()
        if value:
            return value
    return ""


def match_brand(tags: Dict[str, str], aliases: Dict[str, Sequence[str]]) -> Optional[str]:
    label = normalize_text(brand_source(tags))
    if not label:
        return None
    for canonical, variants in aliases.items():
        for alias in variants:
            if label.startswith(normalize_text(alias)):
                return canonical
    return None


def is_metro_station(tags: Dict[str, str]) -> bool:
    operator = normalize_text(tags.get("operator"))
    network = normalize_text(tags.get("network"))
    station = normalize_text(tags.get("station"))
    aerialway = normalize_text(tags.get("aerialway"))
    railway = normalize_text(tags.get("railway"))
    return any((
        operator in {"etmva", "metro de medellin"},
        network in {"metro de medellin", "metrocable"},
        station == "subway",
        aerialway == "station" and network == "metrocable",
        railway == "tram stop" and operator in {"etmva", "metro de medellin"},
    ))


def haversine_m(point_a: Point, point_b: Point) -> float:
    lat1 = math.radians(point_a.y)
    lon1 = math.radians(point_a.x)
    lat2 = math.radians(point_b.y)
    lon2 = math.radians(point_b.x)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def load_areas() -> List[AreaRecord]:
    rows = load_rows()
    areas: List[AreaRecord] = []
    missing = []
    for row in rows:
        name = (row.get("name") or "").strip()
        if not name:
            continue
        slug = slugify(name)
        geometry_dict = load_boundary(name)
        if geometry_dict is None:
            missing.append(name)
            continue
        geom = cleanup_geometry(shape(geometry_dict))
        if geom is None:
            missing.append(name)
            continue
        projected = project_geometry(geom)
        areas.append(
            AreaRecord(
                slug=slug,
                row=row,
                polygon=geom,
                centroid=geom.centroid,
                area_m2=float(projected.area),
            )
        )
    if missing:
        raise SystemExit(f"Missing or invalid boundaries for: {', '.join(missing)}")
    return areas


def build_query(layer: Layer) -> str:
    south, west, north, east = BBOX
    selectors = "\n".join(f"  {selector}({south},{west},{north},{east});" for selector in layer.overpass_selectors)
    return (
        f"[out:json][timeout:{OVERPASS_TIMEOUT_SECONDS}];\n"
        "(\n"
        f"{selectors}\n"
        ");\n"
        "out body center geom qt;\n"
    )


def fetch_overpass(layer: Layer, refresh: bool) -> dict:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"{layer.key}.json"
    if path.exists() and not refresh:
        print(f"[{layer.key}] using cached raw data ({path.relative_to(ROOT)})")
        return json.loads(path.read_text(encoding="utf-8"))

    query = build_query(layer)
    last_error = "unknown error"
    for mirror in OVERPASS_MIRRORS:
        for attempt in range(1, MAX_ATTEMPTS_PER_MIRROR + 1):
            print(f"[{layer.key}] fetch attempt {attempt}/{MAX_ATTEMPTS_PER_MIRROR} via {mirror}")
            req = urllib.request.Request(
                mirror,
                data=query.encode("utf-8"),
                headers={"User-Agent": f"{USER_AGENT} amenities-fetch", "Content-Type": "text/plain"},
                method="POST",
            )
            try:
                try:
                    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
                        body = resp.read().decode("utf-8")
                except urllib.error.URLError as exc:
                    if "CERTIFICATE_VERIFY_FAILED" not in str(exc):
                        raise
                    print(f"[{layer.key}] {mirror} certificate validation failed; retrying with an unverified SSL context")
                    with urllib.request.urlopen(
                        req,
                        timeout=HTTP_TIMEOUT_SECONDS,
                        context=ssl._create_unverified_context(),
                    ) as resp:
                        body = resp.read().decode("utf-8")
                payload = json.loads(body)
                elements = payload.get("elements")
                if not isinstance(elements, list):
                    raise ValueError("response had no elements array")
                path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
                print(f"[{layer.key}] cached {len(elements)} elements -> {path.relative_to(ROOT)}")
                return payload
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
                last_error = str(exc)
                wait_seconds = 5 * (2 ** (attempt - 1))
                print(f"[{layer.key}] {mirror} failed: {exc} (backing off {wait_seconds}s)")
                time.sleep(wait_seconds)
    raise RuntimeError(f"[{layer.key}] failed to fetch after all mirrors: {last_error}")


def way_polygon_from_geometry(points: Sequence[Dict[str, float]]):
    if len(points) < 3:
        return None
    coords = [(pt["lon"], pt["lat"]) for pt in points]
    if coords[0] != coords[-1]:
        coords.append(coords[0])
    geom = cleanup_geometry(Polygon(coords))
    if geom is None or geom.geom_type not in ("Polygon", "MultiPolygon"):
        return None
    return geom


def relation_polygon_from_members(members: Sequence[Dict[str, object]]):
    outer_lines = []
    inner_lines = []
    for member in members:
        geometry = member.get("geometry")
        if not geometry or len(geometry) < 2:
            continue
        line = LineString([(pt["lon"], pt["lat"]) for pt in geometry])
        role = (member.get("role") or "").strip()
        if role == "inner":
            inner_lines.append(line)
        else:
            outer_lines.append(line)
    if not outer_lines:
        return None
    outer_polys = list(polygonize(outer_lines))
    if not outer_polys:
        outer_polys = list(polygonize(outer_lines + inner_lines))
    if not outer_polys:
        return None
    geom = cleanup_geometry(unary_union(outer_polys))
    if geom is None:
        return None
    if inner_lines:
        holes = list(polygonize(inner_lines))
        if holes:
            geom = cleanup_geometry(geom.difference(unary_union(holes)))
    return geom if geom is not None and geom.geom_type in ("Polygon", "MultiPolygon") else None


def element_polygon(element: Dict[str, object]):
    if element.get("type") == "way" and isinstance(element.get("geometry"), list):
        return way_polygon_from_geometry(element["geometry"])
    if element.get("type") == "relation" and isinstance(element.get("members"), list):
        return relation_polygon_from_members(element["members"])
    return None


def element_point(element: Dict[str, object], polygon=None):
    if element.get("type") == "node" and "lat" in element and "lon" in element:
        return Point(float(element["lon"]), float(element["lat"]))
    center = element.get("center")
    if isinstance(center, dict) and "lat" in center and "lon" in center:
        return Point(float(center["lon"]), float(center["lat"]))
    geometry = element.get("geometry")
    if isinstance(geometry, list) and geometry:
        lon = sum(pt["lon"] for pt in geometry) / len(geometry)
        lat = sum(pt["lat"] for pt in geometry) / len(geometry)
        return Point(lon, lat)
    if polygon is not None:
        return polygon.centroid
    return None


def parse_features(layer: Layer, payload: dict) -> List[AmenityFeature]:
    features: List[AmenityFeature] = []
    for element in payload.get("elements", []):
        tags = {k: str(v) for k, v in (element.get("tags") or {}).items()}
        polygon = element_polygon(element) if layer.require_polygon or layer.min_area_m2 else None
        point = element_point(element, polygon=polygon)
        if point is None:
            continue
        if layer.key == "metro" and not is_metro_station(tags):
            continue
        if layer.require_name and not (tags.get("name") or "").strip():
            continue
        area_m2 = None
        if polygon is not None:
            area_m2 = float(project_geometry(polygon).area)
            if layer.min_area_m2 is not None and area_m2 < layer.min_area_m2:
                continue
        elif layer.require_polygon:
            continue
        feature = AmenityFeature(
            element_type=str(element.get("type")),
            element_id=int(element.get("id")),
            point=point,
            tags=tags,
            polygon=polygon,
            area_m2=area_m2,
        )
        if layer.brand_aliases:
            feature.matched_brand = match_brand(tags, layer.brand_aliases)
        features.append(feature)
    return features


def metric_candidates(layer: Layer, metric: Metric, features: Sequence[AmenityFeature]) -> List[AmenityFeature]:
    if metric.match_mode == "brand":
        return [feature for feature in features if feature.matched_brand]
    return list(features)


def format_value(metric: Metric, value: float):
    if metric.kind in {"nearest_m", "count_within", "brand_count_within"}:
        return int(round(value))
    if metric.kind == "density_per_km2":
        return round(value, 2)
    return value


@metric("nearest_m")
def compute_nearest(area: AreaRecord, layer: Layer, metric: Metric, features: Sequence[AmenityFeature]):
    candidates = metric_candidates(layer, metric, features)
    if not candidates:
        return ""
    nearest = min(haversine_m(area.centroid, feature.point) for feature in candidates)
    return format_value(metric, nearest)


@metric("count_within")
def compute_count_within(area: AreaRecord, layer: Layer, metric: Metric, features: Sequence[AmenityFeature]):
    candidates = metric_candidates(layer, metric, features)
    if not candidates:
        return ""
    count = sum(1 for feature in candidates if haversine_m(area.centroid, feature.point) <= (metric.radius_m or 0))
    return format_value(metric, count)


@metric("brand_count_within")
def compute_brand_count_within(area: AreaRecord, layer: Layer, metric: Metric, features: Sequence[AmenityFeature]):
    candidates = [feature for feature in metric_candidates(layer, metric, features) if feature.matched_brand]
    if not candidates:
        return ""
    brands = {
        feature.matched_brand
        for feature in candidates
        if haversine_m(area.centroid, feature.point) <= (metric.radius_m or 0) and feature.matched_brand
    }
    count = len(brands)
    return format_value(metric, count)


@metric("density_per_km2")
def compute_density_per_km2(area: AreaRecord, layer: Layer, metric: Metric, features: Sequence[AmenityFeature]):
    candidates = metric_candidates(layer, metric, features)
    if not candidates or area.area_m2 <= 0:
        return ""
    count = sum(1 for feature in candidates if area.polygon.covers(feature.point))
    density = count / (area.area_m2 / 1_000_000)
    return format_value(metric, density)


def compute_metric_values(areas: Sequence[AreaRecord], layer: Layer, metric: Metric, features: Sequence[AmenityFeature]) -> Dict[str, object]:
    calculator = METRIC_REGISTRY[metric.kind]
    return {area.slug: calculator(area, layer, metric, features) for area in areas}


def coverage_ratio(values: Dict[str, object]) -> float:
    if not values:
        return 0.0
    populated = sum(1 for value in values.values() if value != "")
    return populated / len(values)


def evaluate_global_metric_gates(metric: Metric, values: Dict[str, object]) -> List[str]:
    reasons = []
    coverage = coverage_ratio(values)
    if coverage < COVERAGE_MIN_RATIO:
        reasons.append(
            f"coverage {coverage * 100:.1f}% < required {COVERAGE_MIN_RATIO * 100:.0f}%"
        )
    if metric.kind == "nearest_m":
        invalid = [value for value in values.values() if value != "" and (float(value) < 0 or float(value) > MAX_NEAREST_M)]
        if invalid:
            reasons.append(
                f"nearest self-consistency failed ({len(invalid)} value(s) outside 0..{MAX_NEAREST_M})"
            )
    return reasons


def nth_value(values: Dict[str, object], n: int, reverse: bool) -> Optional[float]:
    ranked = sorted(float(value) for value in values.values() if value != "")
    if not ranked:
        return None
    if reverse:
        ranked = list(reversed(ranked))
    index = min(max(n, 1), len(ranked)) - 1
    return ranked[index]


def matching_areas(sanity: Sanity, areas: Sequence[AreaRecord]) -> List[AreaRecord]:
    if sanity.area:
        return [area for area in areas if area.name == sanity.area]
    if sanity.pattern:
        regex = re.compile(sanity.pattern)
        return [area for area in areas if regex.search(area.row.get(sanity.field, "") or "")]
    return []


def evaluate_sanity(layer: Layer, sanity: Sanity, values: Dict[str, object], areas: Sequence[AreaRecord]) -> (bool, str):
    matches = matching_areas(sanity, areas)
    if not matches:
        return False, sanity.description or f"no area matched sanity gate for {sanity.column}"
    if sanity.op == "is_empty":
        failed = [area.name for area in matches if values.get(area.slug, "") != ""]
        ok = not failed
        return ok, (sanity.description or f"expected empty values") + ("" if ok else f"; got data for {', '.join(failed)}")
    if sanity.op in {"lt", "gt", "lt_all"}:
        comparator = (lambda value: value < sanity.value) if sanity.op in {"lt", "lt_all"} else (lambda value: value > sanity.value)
        failed = []
        for area in matches:
            raw = values.get(area.slug, "")
            if raw == "" or not comparator(float(raw)):
                failed.append(f"{area.name}={raw!r}")
        ok = not failed
        expected = "<" if sanity.op in {"lt", "lt_all"} else ">"
        message = sanity.description or f"{sanity.column} {expected} {sanity.value}"
        return ok, message + ("" if ok else f"; failed: {', '.join(failed)}")
    if sanity.op in {"top_n", "bottom_n"}:
        reverse = sanity.op == "top_n"
        cutoff = nth_value(values, sanity.n or 0, reverse=reverse)
        failed = []
        for area in matches:
            raw = values.get(area.slug, "")
            if raw == "" or cutoff is None:
                failed.append(f"{area.name} value={raw!r}")
                continue
            value = float(raw)
            passed = value >= cutoff if reverse else value <= cutoff
            if not passed:
                failed.append(f"{area.name} value={value} cutoff={cutoff}")
        ok = not failed
        edge = "top" if sanity.op == "top_n" else "bottom"
        message = sanity.description or f"{sanity.column} in {edge} {sanity.n}"
        return ok, message + ("" if ok else f"; failed: {', '.join(failed)}")
    raise ValueError(f"Unsupported sanity op: {sanity.op}")


def compute_layer(areas: Sequence[AreaRecord], layer: Layer, refresh: bool) -> (Dict[str, Dict[str, object]], LayerReport):
    payload = fetch_overpass(layer, refresh=refresh)
    features = parse_features(layer, payload)
    print(f"[{layer.key}] usable features after local filtering: {len(features)}")

    outcomes: Dict[str, MetricOutcome] = {}
    metric_by_column = {metric.column: metric for metric in layer.metrics}
    dropped_columns: Dict[str, List[str]] = {}
    whole_layer_drop = False
    layer_reasons: List[str] = []

    for metric in layer.metrics:
        values = compute_metric_values(areas, layer, metric, features)
        reasons = evaluate_global_metric_gates(metric, values)
        outcome = MetricOutcome(
            metric=metric,
            values=values,
            coverage_ratio=coverage_ratio(values),
            keep=not reasons,
            reasons=reasons,
        )
        outcomes[metric.column] = outcome
        if reasons:
            dropped_columns[metric.column] = reasons.copy()
            if metric.required:
                whole_layer_drop = True
                layer_reasons.extend([f"{metric.column}: {reason}" for reason in reasons])

    kept_columns = [column for column, outcome in outcomes.items() if outcome.keep]
    sanity_messages: List[str] = []
    sanity_passed = True
    if not whole_layer_drop:
        for sanity in layer.sanity:
            outcome = outcomes.get(sanity.column)
            metric = metric_by_column[sanity.column]
            if outcome is None or not outcome.keep:
                if metric.required:
                    whole_layer_drop = True
                    reason = f"{sanity.column}: dropped before sanity gate"
                    layer_reasons.append(reason)
                continue
            ok, message = evaluate_sanity(layer, sanity, outcome.values, areas)
            sanity_messages.append(("PASS: " if ok else "FAIL: ") + message)
            if not ok:
                if metric.required:
                    whole_layer_drop = True
                    layer_reasons.append(f"{sanity.column}: {message}")
                else:
                    outcome.keep = False
                    dropped_columns.setdefault(sanity.column, []).append(message)

    if whole_layer_drop:
        for column, outcome in outcomes.items():
            if outcome.keep:
                dropped_columns.setdefault(column, []).append("layer dropped because a required metric or sanity gate failed")
        report = LayerReport(
            key=layer.key,
            shipped=False,
            reason="; ".join(layer_reasons) or "required metric failed",
            kept_columns=[],
            dropped_columns=dropped_columns,
            coverage_by_column={column: round(outcome.coverage_ratio * 100, 1) for column, outcome in outcomes.items()},
            sanity_passed=False,
            sanity_messages=sanity_messages,
        )
        return {}, report

    kept_columns = [column for column, outcome in outcomes.items() if outcome.keep]
    layer_values = {area.slug: {} for area in areas}
    for column in kept_columns:
        for slug, value in outcomes[column].values.items():
            layer_values[slug][column] = value

    report = LayerReport(
        key=layer.key,
        shipped=bool(kept_columns),
        reason="shipped" if kept_columns else "all metrics dropped",
        kept_columns=kept_columns,
        dropped_columns=dropped_columns,
        coverage_by_column={column: round(outcome.coverage_ratio * 100, 1) for column, outcome in outcomes.items()},
        sanity_passed=all(msg.startswith("PASS") for msg in sanity_messages) if sanity_messages else True,
        sanity_messages=sanity_messages,
    )
    return layer_values, report


def merge_layer_values(destination: Dict[str, Dict[str, object]], source: Dict[str, Dict[str, object]]):
    for slug, values in source.items():
        destination.setdefault(slug, {}).update(values)


def write_computed_json(areas: Sequence[AreaRecord], computed: Dict[str, Dict[str, object]]):
    serialisable = {}
    for area in sorted(areas, key=lambda area: area.slug):
        if area.slug in computed:
            serialisable[area.slug] = dict(sorted(computed[area.slug].items()))
    COMPUTED_PATH.parent.mkdir(parents=True, exist_ok=True)
    COMPUTED_PATH.write_text(json.dumps(serialisable, indent=2, ensure_ascii=False), encoding="utf-8")


def extract_points(layer: Layer, refresh: bool) -> List[Dict[str, object]]:
    """Point-layer variant of compute_layer: no per-area metrics, just the
    raw {lat, lon, name?, brand?} of every matched feature, for the frontend
    to render as toggleable map markers. Reuses the same cached Overpass
    response as a metric layer would (no extra network calls)."""
    payload = fetch_overpass(layer, refresh=refresh)
    features = parse_features(layer, payload)
    points: List[Dict[str, object]] = []
    for feature in features:
        name = (feature.tags.get("name") or "").strip()
        entry: Dict[str, object] = {"lat": round(feature.point.y, 6), "lon": round(feature.point.x, 6)}
        if name:
            entry["name"] = name
        if feature.matched_brand:
            entry["brand"] = feature.matched_brand
        points.append(entry)
    return points


def write_points_json(points_by_layer: Dict[str, List[Dict[str, object]]]):
    serialisable = {
        key: sorted(items, key=lambda e: (e.get("name") or "", e["lat"], e["lon"]))
        for key, items in points_by_layer.items()
    }
    POINTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    POINTS_PATH.write_text(json.dumps(serialisable, indent=2, ensure_ascii=False), encoding="utf-8")


def print_report(reports: Sequence[LayerReport]):
    print("\nAmenity layer summary")
    print("=====================")
    for report in reports:
        status = "SHIPPED" if report.shipped else "DROPPED"
        print(f"- {report.key}: {status} — {report.reason}")
        for column, coverage in report.coverage_by_column.items():
            print(f"    {column}: coverage {coverage:.1f}%")
        if report.kept_columns:
            print(f"    kept: {', '.join(report.kept_columns)}")
        if report.dropped_columns:
            for column, reasons in report.dropped_columns.items():
                print(f"    dropped {column}: {'; '.join(reasons)}")
        for message in report.sanity_messages:
            print(f"    {message}")


def print_point_layer_report(points_by_layer: Dict[str, List[Dict[str, object]]]):
    print("\nAmenity point-layer summary (map markers, not filters)")
    print("========================================================")
    for key, points in points_by_layer.items():
        print(f"- {key}: {len(points)} point(s) extracted")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch and compute LivLens amenity layers.")
    parser.add_argument("--refresh", action="store_true", help="Refetch raw Overpass caches even if cached JSON already exists.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    areas = load_areas()
    computed: Dict[str, Dict[str, object]] = {area.slug: {} for area in areas}
    reports: List[LayerReport] = []
    points_by_layer: Dict[str, List[Dict[str, object]]] = {}
    for layer in LAYERS:
        if layer.point_layer:
            points_by_layer[layer.key] = extract_points(layer, refresh=args.refresh)
            continue
        layer_values, report = compute_layer(areas, layer, refresh=args.refresh)
        merge_layer_values(computed, layer_values)
        reports.append(report)
    write_computed_json(areas, computed)
    write_points_json(points_by_layer)
    print_report(reports)
    print_point_layer_report(points_by_layer)
    print(f"\nWrote computed amenities to {COMPUTED_PATH.relative_to(ROOT)}")
    print(f"Wrote amenity map points to {POINTS_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

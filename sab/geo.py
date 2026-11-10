"""Geometry helpers for the Land / Survey Map: reading KML / KMZ / GeoJSON / Shapefile,
UTM <-> WGS84 conversion and survey-number field detection. Only the standard library
plus `pyshp` (shapefiles) is needed here; area / intersection work lives in landmap.py (shapely)."""
import io
import json
import math
import re
import zipfile
import xml.etree.ElementTree as ET

# Ayodhya, Basti, Siddharthnagar and Balrampur all lie between 81.5 and 83.5 deg E -> UTM zone 44 North.
DEFAULT_UTM_ZONE = 44

DISTRICTS = ["Ayodhya", "Basti", "Siddharthnagar", "Balrampur"]

# property names that usually hold the survey / khasra / gata number (lower-case, compared without _ - space .)
SURVEY_KEYS = ["gatano", "gatanumber", "gata", "khasrano", "khasranumber", "khasra", "khno", "kh", "surveyno",
               "surveynumber", "survey", "svno", "sno", "plotno", "plotnumber", "plot", "parcelno", "parcel",
               "kidno", "kid", "number", "no", "name"]
VILLAGE_KEYS = ["village", "villagename", "vill", "villname", "gram", "gaon", "mauza", "mouza", "revvillage"]
TEHSIL_KEYS = ["tehsil", "tahsil", "tehsilname", "taluka", "subdistrict"]
DISTRICT_KEYS = ["district", "districtname", "dist", "zila", "jila"]
AREA_KEYS = ["area", "areaha", "areahec", "areahectare", "rakba", "areasqm"]
OWNER_KEYS = ["owner", "ownername", "khatedar", "name_owner", "khatauni", "khatano", "khatauninumber"]


def _norm(key):
    return re.sub(r"[\s_\-.()/]", "", str(key).lower())


def find_key(props_keys, candidates):
    """Return the first property name (original spelling) that matches one of the candidate names."""
    normed = {_norm(k): k for k in props_keys}
    for c in candidates:
        if c in normed:
            return normed[c]
    for c in candidates:
        if len(c) >= 4:
            for nk, k in normed.items():
                if c in nk:
                    return k
    return None


# ---------------------------------------------------------------- UTM (WGS84 ellipsoid)
_A = 6378137.0
_F = 1 / 298.257223563
_E2 = _F * (2 - _F)
_EP2 = _E2 / (1 - _E2)
_K0 = 0.9996


def utm_zone(lon):
    return int((lon + 180) // 6) + 1


def to_utm(lon, lat, zone=DEFAULT_UTM_ZONE):
    """WGS84 lon/lat (deg) -> UTM easting, northing (m), northern hemisphere."""
    lon0 = math.radians((zone - 1) * 6 - 180 + 3)
    phi, lam = math.radians(lat), math.radians(lon)
    n = _A / math.sqrt(1 - _E2 * math.sin(phi) ** 2)
    t = math.tan(phi) ** 2
    c = _EP2 * math.cos(phi) ** 2
    a = math.cos(phi) * (lam - lon0)
    e4, e6 = _E2 ** 2, _E2 ** 3
    m = _A * ((1 - _E2 / 4 - 3 * e4 / 64 - 5 * e6 / 256) * phi
              - (3 * _E2 / 8 + 3 * e4 / 32 + 45 * e6 / 1024) * math.sin(2 * phi)
              + (15 * e4 / 256 + 45 * e6 / 1024) * math.sin(4 * phi)
              - (35 * e6 / 3072) * math.sin(6 * phi))
    x = _K0 * n * (a + (1 - t + c) * a ** 3 / 6 + (5 - 18 * t + t * t + 72 * c - 58 * _EP2) * a ** 5 / 120) + 500000.0
    y = _K0 * (m + n * math.tan(phi) * (a * a / 2 + (5 - t + 9 * c + 4 * c * c) * a ** 4 / 24
                                        + (61 - 58 * t + t * t + 600 * c - 330 * _EP2) * a ** 6 / 720))
    return x, y


def from_utm(x, y, zone=DEFAULT_UTM_ZONE):
    """UTM easting, northing (m, northern hemisphere) -> WGS84 lon, lat (deg)."""
    lon0 = math.radians((zone - 1) * 6 - 180 + 3)
    x -= 500000.0
    m = y / _K0
    mu = m / (_A * (1 - _E2 / 4 - 3 * _E2 ** 2 / 64 - 5 * _E2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - _E2)) / (1 + math.sqrt(1 - _E2))
    phi1 = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
            + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
            + (151 * e1 ** 3 / 96) * math.sin(6 * mu) + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))
    n1 = _A / math.sqrt(1 - _E2 * math.sin(phi1) ** 2)
    t1 = math.tan(phi1) ** 2
    c1 = _EP2 * math.cos(phi1) ** 2
    r1 = _A * (1 - _E2) / (1 - _E2 * math.sin(phi1) ** 2) ** 1.5
    d = x / (n1 * _K0)
    lat = phi1 - (n1 * math.tan(phi1) / r1) * (d * d / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1 * c1 - 9 * _EP2) * d ** 4 / 24
                                               + (61 + 90 * t1 + 298 * c1 + 45 * t1 * t1 - 252 * _EP2 - 3 * c1 * c1) * d ** 6 / 720)
    lon = lon0 + (d - (1 + 2 * t1 + c1) * d ** 3 / 6
                  + (5 - 2 * c1 + 28 * t1 - 3 * c1 * c1 + 8 * _EP2 + 24 * t1 * t1) * d ** 5 / 120) / math.cos(phi1)
    return math.degrees(lon), math.degrees(lat)


def map_coords(geom, fn):
    """Apply fn(x, y) -> (x, y) to every coordinate of a GeoJSON geometry (returns a new geometry)."""
    def walk(c):
        if c and isinstance(c[0], (int, float)):
            return list(fn(c[0], c[1]))
        return [walk(p) for p in c]
    if geom["type"] == "GeometryCollection":
        return {"type": "GeometryCollection", "geometries": [map_coords(g, fn) for g in geom["geometries"]]}
    return {"type": geom["type"], "coordinates": walk(geom["coordinates"])}


def first_coord(geom):
    c = geom["geometries"][0] if geom["type"] == "GeometryCollection" else geom
    c = c["coordinates"]
    while c and not isinstance(c[0], (int, float)):
        c = c[0]
    return c


def bbox(geom):
    xs, ys = [], []

    def walk(c):
        if c and isinstance(c[0], (int, float)):
            xs.append(c[0]); ys.append(c[1])
        else:
            for p in c:
                walk(p)
    for g in (geom["geometries"] if geom["type"] == "GeometryCollection" else [geom]):
        walk(g["coordinates"])
    return min(xs), min(ys), max(xs), max(ys)


def normalise_crs(features, crs="auto", zone=DEFAULT_UTM_ZONE):
    """Make sure every feature is WGS84 lon/lat. crs: 'auto' | 'wgs84' | 'utm'. Returns (features, note)."""
    if not features:
        return features, ""
    x, y = first_coord(features[0]["geometry"])[:2]
    looks_projected = abs(x) > 180 or abs(y) > 90
    if crs == "wgs84" or (crs == "auto" and not looks_projected):
        # lat/lon swapped (y ~ 81-84, x ~ 26-28) is a common export mistake for this region
        if 20 < x < 35 and 75 < y < 90:
            for f in features:
                f["geometry"] = map_coords(f["geometry"], lambda a, b: (b, a))
            return features, "Latitude/longitude were swapped in the file - corrected."
        return features, ""
    for f in features:
        f["geometry"] = map_coords(f["geometry"], lambda a, b: from_utm(a, b, zone))
    return features, f"Coordinates converted from UTM zone {zone}N to WGS84."


# ---------------------------------------------------------------- readers -> list of GeoJSON features
def read_any(filename, data):
    """Read a KML, KMZ, GeoJSON/JSON or zipped Shapefile into a list of GeoJSON features (raw CRS)."""
    name = filename.lower()
    if name.endswith(".kml"):
        return read_kml(data)
    if name.endswith((".geojson", ".json")):
        return read_geojson(data)
    if name.endswith((".kmz", ".zip")):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.namelist()
            kmls = [n for n in names if n.lower().endswith(".kml")]
            shps = [n for n in names if n.lower().endswith(".shp")]
            gjs = [n for n in names if n.lower().endswith((".geojson", ".json"))]
            feats = []
            for n in kmls:
                feats += read_kml(z.read(n))
            for n in gjs:
                feats += read_geojson(z.read(n))
            for n in shps:
                base = n[:-4]
                parts = {ext: (z.read(base + ext) if base + ext in names else
                               z.read(base + ext.upper()) if base + ext.upper() in names else None)
                         for ext in (".shp", ".shx", ".dbf", ".prj")}
                feats += read_shapefile(parts[".shp"], parts[".shx"], parts[".dbf"], parts[".prj"])
            if not (kmls or shps or gjs):
                raise ValueError("The zip has no .kml, .shp or .geojson file inside.")
            return feats
    if name.endswith(".shp"):
        raise ValueError("Upload a shapefile as a .zip containing the .shp, .shx, .dbf (and .prj) files together.")
    raise ValueError("Unsupported file type. Use KML, KMZ, GeoJSON or a zipped Shapefile. "
                     "(DXF/DWG: export to KML or Shapefile in QGIS / AutoCAD Map first.)")


def read_geojson(data):
    obj = json.loads(data.decode("utf-8-sig") if isinstance(data, bytes) else data)
    if obj.get("type") == "FeatureCollection":
        feats = obj.get("features") or []
    elif obj.get("type") == "Feature":
        feats = [obj]
    else:
        feats = [{"type": "Feature", "properties": {}, "geometry": obj}]
    return [{"type": "Feature", "properties": f.get("properties") or {}, "geometry": f["geometry"]}
            for f in feats if f.get("geometry")]


def read_shapefile(shp, shx, dbf, prj=None):
    import shapefile  # pyshp
    if shp is None or dbf is None:
        raise ValueError("The shapefile zip must contain the .shp and .dbf files.")
    r = shapefile.Reader(shp=io.BytesIO(shp), shx=io.BytesIO(shx) if shx else None, dbf=io.BytesIO(dbf),
                         encoding="utf-8", encodingErrors="replace")
    fields = [f[0] for f in r.fields[1:]]
    out = []
    for sr in r.iterShapeRecords():
        if sr.shape.shapeType == 0:
            continue
        geom = sr.shape.__geo_interface__
        out.append({"type": "Feature", "properties": dict(zip(fields, list(sr.record))), "geometry": geom})
    return out


def _strip_ns(root):
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def _coords(text):
    pts = []
    for tok in (text or "").replace("\n", " ").replace("\t", " ").split():
        parts = tok.split(",")
        if len(parts) >= 2:
            try:
                pts.append([float(parts[0]), float(parts[1])])
            except ValueError:
                pass
    return pts


def _kml_geoms(el):
    """Yield GeoJSON geometries found under a KML element (handles MultiGeometry)."""
    for child in el:
        tag = child.tag
        if tag == "Point":
            c = _coords(child.findtext("coordinates"))
            if c:
                yield {"type": "Point", "coordinates": c[0]}
        elif tag == "LineString":
            c = _coords(child.findtext("coordinates"))
            if len(c) >= 2:
                yield {"type": "LineString", "coordinates": c}
        elif tag == "Polygon":
            outer = _coords(child.findtext("outerBoundaryIs/LinearRing/coordinates"))
            if len(outer) >= 3:
                rings = [outer] + [_coords(ib.findtext("LinearRing/coordinates"))
                                   for ib in child.findall("innerBoundaryIs")]
                yield {"type": "Polygon", "coordinates": [r for r in rings if len(r) >= 3]}
        elif tag in ("MultiGeometry", "MultiTrack"):
            yield from _kml_geoms(child)
        elif tag == "Track":  # gx:Track
            c = [[float(v) for v in w.text.split()[:2]] for w in child.findall("coord") if w.text]
            if len(c) >= 2:
                yield {"type": "LineString", "coordinates": c}


def _description_props(text):
    """Pull key/value pairs out of an HTML table description (common in KML exported from QGIS / GIS tools)."""
    props = {}
    if not text:
        return props
    for k, v in re.findall(r"<t[dh][^>]*>\s*([^<]{1,60}?)\s*</t[dh]>\s*<td[^>]*>\s*([^<]*?)\s*</td>", text, re.I):
        props.setdefault(k.strip(), v.strip())
    return props


def read_kml(data):
    try:
        root = _strip_ns(ET.fromstring(data))
    except ET.ParseError as e:
        raise ValueError(f"Could not read the KML file: {e}")
    feats = []
    for pm in root.iter("Placemark"):
        props = {}
        name = pm.findtext("name")
        if name:
            props["name"] = name.strip()
        desc = pm.findtext("description")
        props.update({k: v for k, v in _description_props(desc).items() if k not in props})
        for d in pm.iter("Data"):
            props[d.get("name")] = (d.findtext("value") or "").strip()
        for sd in pm.iter("SimpleData"):
            props[sd.get("name")] = (sd.text or "").strip()
        if desc and not _description_props(desc) and len(desc) < 300:
            props.setdefault("description", re.sub(r"<[^>]+>", " ", desc).strip())
        geoms = list(_kml_geoms(pm))
        if not geoms:
            continue
        polys = [g for g in geoms if g["type"] == "Polygon"]
        if len(geoms) == 1:
            geom = geoms[0]
        elif polys and len(polys) == len(geoms):
            geom = {"type": "MultiPolygon", "coordinates": [p["coordinates"] for p in polys]}
        else:
            for g in geoms:  # mixed MultiGeometry: keep each part as its own feature
                feats.append({"type": "Feature", "properties": dict(props), "geometry": g})
            continue
        feats.append({"type": "Feature", "properties": props, "geometry": geom})
    return feats

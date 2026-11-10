"""Land / Survey Map: explore survey (gata / khasra) numbers of every imported village of
Ayodhya, Basti, Siddharthnagar and Balrampur, overlay highway alignment KML files and list
the survey numbers the alignment / ROW passes through (land schedule for 3A / 3D)."""
import csv
import io
import json
import os
from datetime import datetime
from xml.sax.saxutils import escape

from flask import Blueprint, Response, abort, flash, jsonify, redirect, render_template, request, url_for

from . import db as store
from . import geo

bp = Blueprint("landmap", __name__, url_prefix="/landmap")

GEO_EXT = (".kml", ".kmz", ".geojson", ".json", ".zip")
MAX_BBOX_FEATURES = 8000


def ensure_tables(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS land_villages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, district TEXT, tehsil TEXT, village TEXT, source_file TEXT,
        parcel_count INTEGER DEFAULT 0, minx REAL, miny REAL, maxx REAL, maxy REAL, imported_at TEXT);
    CREATE UNIQUE INDEX IF NOT EXISTS ix_land_villages ON land_villages(district, tehsil, village);
    CREATE TABLE IF NOT EXISTS land_parcels (
        id INTEGER PRIMARY KEY AUTOINCREMENT, village_id INTEGER NOT NULL, survey_no TEXT, area_ha REAL,
        props TEXT, geometry TEXT, minx REAL, miny REAL, maxx REAL, maxy REAL);
    CREATE INDEX IF NOT EXISTS ix_land_parcels_v ON land_parcels(village_id);
    CREATE INDEX IF NOT EXISTS ix_land_parcels_s ON land_parcels(survey_no);
    CREATE INDEX IF NOT EXISTS ix_land_parcels_bb ON land_parcels(minx, maxx, miny, maxy);
    CREATE TABLE IF NOT EXISTS land_alignments (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, filename TEXT, geojson TEXT, color TEXT,
        row_width REAL, start_chainage REAL, created_at TEXT);
    """)
    db.commit()


def _shapely():
    try:
        import shapely  # noqa: F401
        from shapely.geometry import shape, mapping
        from shapely import ops
        return shape, mapping, ops
    except ImportError:
        abort(500, "The 'shapely' package is needed for the Land Map. Run: pip3 install -r requirements.txt")


def _to_utm(g):
    """WGS84 -> UTM 44N metres (areas, buffers and chainages are worked out in metres)."""
    import shapely
    return shapely.transform(g, lambda xy: [geo.to_utm(x, y) for x, y in xy])


def _to_wgs(g):
    import shapely
    return shapely.transform(g, lambda xy: [geo.from_utm(x, y) for x, y in xy])


def chainage_text(m):
    if m is None:
        return ""
    km, rest = divmod(round(m), 1000)
    return f"{int(km)}+{int(rest):03d}"


def _clean(v):
    if v is None:
        return ""
    if isinstance(v, float) and v == int(v):
        v = int(v)
    return str(v).strip()


# ---------------------------------------------------------------- import of cadastral (survey number) maps
def import_parcels(db, filename, data, district, tehsil="", village="", survey_field="", village_field="",
                   tehsil_field="", crs="auto", replace=True, replaced=None):
    """Store the parcels of one file. Returns a summary dict. Polygons only; points/lines are ignored."""
    shape, _mapping, _ops = _shapely()
    feats = geo.read_any(filename, data)
    feats = [f for f in feats if f["geometry"]["type"] in ("Polygon", "MultiPolygon")]
    if not feats:
        raise ValueError("No polygons found in the file - a cadastral map must have one polygon per survey number.")
    feats, crs_note = geo.normalise_crs(feats, crs)
    keys = set()
    for f in feats[:200]:
        keys.update(f["properties"].keys())
    survey_field = survey_field or geo.find_key(keys, geo.SURVEY_KEYS)
    if not village:
        village_field = village_field or geo.find_key(keys, geo.VILLAGE_KEYS)
        if not village_field:
            raise ValueError("Type the village name, or give the name of the village column in the file. "
                             f"Columns found: {', '.join(sorted(map(str, keys))) or 'none'}")
    if not tehsil and not tehsil_field:
        tehsil_field = geo.find_key(keys, geo.TEHSIL_KEYS) or ""
    district_field = geo.find_key(keys, geo.DISTRICT_KEYS) if not district else None

    groups = {}
    for f in feats:
        p = f["properties"]
        d = district or _clean(p.get(district_field)) or "Unknown"
        t = tehsil or (_clean(p.get(tehsil_field)) if tehsil_field else "")
        v = village or _clean(p.get(village_field)) or "Unknown"
        groups.setdefault((d.strip(), (t or "").strip(), v.strip()), []).append(f)

    stamp = datetime.now().isoformat(timespec="seconds")
    total, villages = 0, []
    for (d, t, v), items in groups.items():
        row = db.execute("SELECT id FROM land_villages WHERE district=? AND tehsil=? AND village=?", (d, t, v)).fetchone()
        if row:
            vid = row["id"]
            if replace and (replaced is None or vid not in replaced):
                db.execute("DELETE FROM land_parcels WHERE village_id=?", (vid,))
        else:
            vid = db.execute("INSERT INTO land_villages (district, tehsil, village, source_file, imported_at) "
                             "VALUES (?,?,?,?,?)", (d, t, v, filename, stamp)).lastrowid
        if replaced is not None:
            replaced.add(vid)
        rows = []
        for i, f in enumerate(items, 1):
            g = shape(f["geometry"])
            if not g.is_valid:
                g = g.buffer(0)
            if g.is_empty:
                continue
            area_ha = _to_utm(g).area / 10000.0
            sno = _clean(f["properties"].get(survey_field)) if survey_field else ""
            minx, miny, maxx, maxy = g.bounds
            props = {str(k): _clean(val) for k, val in f["properties"].items()}
            rows.append((vid, sno or f"#{i}", round(area_ha, 4), json.dumps(props, ensure_ascii=False),
                         json.dumps(f["geometry"]), minx, miny, maxx, maxy))
        db.executemany("INSERT INTO land_parcels (village_id, survey_no, area_ha, props, geometry, minx, miny, maxx, maxy)"
                       " VALUES (?,?,?,?,?,?,?,?,?)", rows)
        _refresh_village(db, vid, filename, stamp)
        total += len(rows)
        villages.append(f"{v} ({len(rows)})")
    db.commit()
    return {"parcels": total, "villages": villages, "survey_field": survey_field, "crs_note": crs_note,
            "columns": sorted(map(str, keys))}


def _refresh_village(db, vid, filename, stamp):
    r = db.execute("SELECT COUNT(*) n, MIN(minx) a, MIN(miny) b, MAX(maxx) c, MAX(maxy) d FROM land_parcels "
                   "WHERE village_id=?", (vid,)).fetchone()
    db.execute("UPDATE land_villages SET parcel_count=?, minx=?, miny=?, maxx=?, maxy=?, source_file=?, imported_at=? "
               "WHERE id=?", (r["n"], r["a"], r["b"], r["c"], r["d"], filename, stamp, vid))


def _feature(row, village=None):
    props = json.loads(row["props"] or "{}")
    out = {"id": row["id"], "survey_no": row["survey_no"], "area_ha": row["area_ha"], "village_id": row["village_id"],
           "attrs": props}
    if village is not None:
        out.update(district=village["district"], tehsil=village["tehsil"], village=village["village"])
    return {"type": "Feature", "id": row["id"], "properties": out, "geometry": json.loads(row["geometry"])}


def _villages(db):
    return {r["id"]: r for r in db.execute("SELECT * FROM land_villages")}


# ---------------------------------------------------------------- alignment analysis
def load_alignment(db, aid):
    a = db.execute("SELECT * FROM land_alignments WHERE id=?", (aid,)).fetchone()
    if not a:
        abort(404)
    return a


def analyse(db, aid, width=None, start=None, mode="auto"):
    """Survey numbers touched by the alignment. width = total ROW width (m) used to buffer the centre line."""
    shape, _mapping, ops = _shapely()
    from shapely.geometry import MultiLineString, Point
    from shapely.prepared import prep
    a = load_alignment(db, aid)
    width = float(width if width not in (None, "") else (a["row_width"] or 60))
    start = float(start if start not in (None, "") else (a["start_chainage"] or 0))
    fc = json.loads(a["geojson"])
    lines, polys = [], []
    for f in fc["features"]:
        g = _to_utm(shape(f["geometry"]))
        if g.geom_type in ("LineString", "MultiLineString"):
            lines.append(g)
        elif g.geom_type in ("Polygon", "MultiPolygon"):
            polys.append(g if g.is_valid else g.buffer(0))
    if mode == "auto":
        mode = "polygon" if polys else "buffer"
    if mode == "polygon" and not polys:
        raise ValueError("This KML has no polygons (ROW boundary). Use 'Buffer centre line' instead.")
    if mode == "buffer" and not lines:
        raise ValueError("This KML has no line (centre line). Use 'ROW polygons in the KML' instead.")

    centre = None
    if lines:
        merged = ops.linemerge(MultiLineString([p for g in lines for p in getattr(g, "geoms", [g])]))
        parts = list(getattr(merged, "geoms", [merged]))
        centre = max(parts, key=lambda p: p.length)
    corridor = ops.unary_union(polys) if mode == "polygon" else ops.unary_union(lines).buffer(width / 2.0, cap_style=2)
    left = centre.buffer(5000, single_sided=True, cap_style=2) if centre is not None else None

    minx, miny, maxx, maxy = _to_wgs(corridor).bounds
    pad = 0.0005
    rows = db.execute("SELECT * FROM land_parcels WHERE maxx>=? AND minx<=? AND maxy>=? AND miny<=?",
                      (minx - pad, maxx + pad, miny - pad, maxy + pad)).fetchall()
    vmap = _villages(db)
    pc = prep(corridor)
    out = []
    for r in rows:
        g = _to_utm(shape(json.loads(r["geometry"])))
        if not g.is_valid:
            g = g.buffer(0)
        if not pc.intersects(g):
            continue
        inter = corridor.intersection(g)
        if inter.area < 0.5:  # ignore slivers below half a square metre (digitising noise)
            continue
        v = vmap.get(r["village_id"])
        props = json.loads(r["props"] or "{}")
        rec = {"parcel_id": r["id"], "district": v["district"], "tehsil": v["tehsil"], "village": v["village"],
               "survey_no": r["survey_no"], "total_ha": round(g.area / 10000, 4),
               "affected_sqm": round(inter.area, 1), "affected_ha": round(inter.area / 10000, 4),
               "pct": round(100 * inter.area / g.area, 1) if g.area else 0,
               "owner": _clean(props.get(geo.find_key(props.keys(), geo.OWNER_KEYS) or "")),
               "ch_from": None, "ch_to": None, "side": ""}
        if centre is not None:
            ds = [centre.project(Point(c[0], c[1])) for c in _all_coords(inter)]
            rec["ch_from"], rec["ch_to"] = start + min(ds), start + max(ds)
            la = inter.intersection(left).area
            ra = inter.area - la
            rec["side"] = "Both" if la > 1 and ra > 1 else ("LHS" if la >= ra else "RHS")
        out.append(rec)
    out.sort(key=lambda x: (x["ch_from"] if x["ch_from"] is not None else 0, x["village"], _skey(x["survey_no"])))
    for i, rec in enumerate(out, 1):
        rec["sno"] = i
        rec["ch_from_txt"], rec["ch_to_txt"] = chainage_text(rec["ch_from"]), chainage_text(rec["ch_to"])
    by_village = {}
    for rec in out:
        k = (rec["district"], rec["tehsil"], rec["village"])
        s = by_village.setdefault(k, {"district": k[0], "tehsil": k[1], "village": k[2], "count": 0, "affected_ha": 0})
        s["count"] += 1
        s["affected_ha"] = round(s["affected_ha"] + rec["affected_ha"], 4)
    return {"alignment": {"id": a["id"], "name": a["name"]}, "mode": mode, "width": width, "start": start,
            "length_km": round(centre.length / 1000, 3) if centre is not None else None,
            "corridor_ha": round(corridor.area / 10000, 4),
            "corridor": json.loads(json.dumps(_mapping(_to_wgs(corridor)))),
            "parcels": out, "villages": list(by_village.values()),
            "total_affected_ha": round(sum(r["affected_ha"] for r in out), 4)}


def _all_coords(g):
    for part in getattr(g, "geoms", [g]):
        if part.geom_type == "Polygon":
            yield from part.exterior.coords
        elif hasattr(part, "coords"):
            yield from part.coords
        else:
            yield from _all_coords(part)


def _skey(s):
    import re
    m = re.match(r"\D*(\d+)", s or "")
    return (int(m.group(1)) if m else 10 ** 9, s or "")


# ---------------------------------------------------------------- routes
@bp.route("/")
def page():
    db = store.get_db()
    n = db.execute("SELECT COUNT(*) FROM land_parcels").fetchone()[0]
    return render_template("landmap.html", districts=geo.DISTRICTS, parcel_total=n)


@bp.route("/import", methods=["POST"])
def import_view():
    db = store.get_db()
    files = [f for f in request.files.getlist("files") if f and f.filename]
    if not files:
        flash("Choose at least one KML / KMZ / GeoJSON / zipped Shapefile.", "error")
        return redirect(url_for("landmap.page"))
    district = (request.form.get("district_other") or request.form.get("district") or "").strip()
    replaced = set()
    for f in files:
        if not f.filename.lower().endswith(GEO_EXT):
            flash(f"{f.filename}: unsupported type (use KML, KMZ, GeoJSON or zipped Shapefile).", "error")
            continue
        village = request.form.get("village", "").strip()
        if request.form.get("village_from_filename"):
            village = os.path.splitext(os.path.basename(f.filename))[0].replace("_", " ").strip()
        try:
            res = import_parcels(db, f.filename, f.read(), district, request.form.get("tehsil", "").strip(),
                                 village, request.form.get("survey_field", "").strip(),
                                 request.form.get("village_field", "").strip(), "", request.form.get("crs", "auto"),
                                 replace=request.form.get("existing", "replace") == "replace", replaced=replaced)
        except Exception as e:  # report and carry on with the next file
            flash(f"{f.filename}: {e}", "error")
            continue
        msg = (f"{f.filename}: {res['parcels']} survey numbers imported into {', '.join(res['villages'])}. "
               f"Survey no. read from column '{res['survey_field'] or '- none found, numbered #1, #2…'}'. "
               f"{res['crs_note']}")
        if not res["survey_field"]:
            msg += f" Columns in file: {', '.join(res['columns'])} - re-import with the right column name."
        flash(msg, "ok")
    return redirect(url_for("landmap.page"))


@bp.route("/village/<int:vid>/delete", methods=["POST"])
def delete_village(vid):
    db = store.get_db()
    db.execute("DELETE FROM land_parcels WHERE village_id=?", (vid,))
    db.execute("DELETE FROM land_villages WHERE id=?", (vid,))
    db.commit()
    return jsonify(ok=True)


@bp.route("/api/tree")
def api_tree():
    db = store.get_db()
    tree = {}
    for r in db.execute("SELECT * FROM land_villages ORDER BY district, tehsil, village"):
        tree.setdefault(r["district"], {}).setdefault(r["tehsil"] or "-", []).append(
            {"id": r["id"], "village": r["village"], "count": r["parcel_count"],
             "bbox": [r["minx"], r["miny"], r["maxx"], r["maxy"]], "source": r["source_file"]})
    return jsonify(tree)


@bp.route("/api/village/<int:vid>")
def api_village(vid):
    db = store.get_db()
    v = db.execute("SELECT * FROM land_villages WHERE id=?", (vid,)).fetchone()
    if not v:
        abort(404)
    rows = db.execute("SELECT * FROM land_parcels WHERE village_id=?", (vid,)).fetchall()
    return jsonify({"type": "FeatureCollection", "features": [_feature(r, v) for r in rows]})


@bp.route("/api/parcels")
def api_parcels():
    try:
        minx, miny, maxx, maxy = [float(x) for x in request.args["bbox"].split(",")]
    except (KeyError, ValueError):
        abort(400, "bbox=minLon,minLat,maxLon,maxLat required")
    db = store.get_db()
    rows = db.execute("SELECT * FROM land_parcels WHERE maxx>=? AND minx<=? AND maxy>=? AND miny<=? LIMIT ?",
                      (minx, maxx, miny, maxy, MAX_BBOX_FEATURES + 1)).fetchall()
    vmap = _villages(db)
    return jsonify({"type": "FeatureCollection", "truncated": len(rows) > MAX_BBOX_FEATURES,
                    "features": [_feature(r, vmap.get(r["village_id"])) for r in rows[:MAX_BBOX_FEATURES]]})


@bp.route("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    db = store.get_db()
    sql = ("SELECT p.id, p.survey_no, p.area_ha, p.minx, p.miny, p.maxx, p.maxy, v.district, v.tehsil, v.village "
           "FROM land_parcels p JOIN land_villages v ON v.id=p.village_id WHERE (p.survey_no=? OR p.survey_no LIKE ?)")
    params = [q, f"{q}%"]
    if request.args.get("village_id"):
        sql += " AND p.village_id=?"
        params.append(int(request.args["village_id"]))
    elif request.args.get("district"):
        sql += " AND v.district=?"
        params.append(request.args["district"])
    sql += " ORDER BY (p.survey_no<>?), v.village, p.survey_no LIMIT 200"
    params.append(q)
    return jsonify([dict(r) for r in db.execute(sql, params)])


@bp.route("/alignments", methods=["POST"])
def add_alignment():
    db = store.get_db()
    shape, mapping, _ops = _shapely()
    files = [f for f in request.files.getlist("files") if f and f.filename]
    if not files:
        flash("Choose the alignment KML / KMZ file.", "error")
        return redirect(url_for("landmap.page"))
    colors = ["#e3170a", "#7b2cbf", "#ff8800", "#0077b6", "#2b9348", "#d6336c"]
    for f in files:
        try:
            feats = geo.read_any(f.filename, f.read())
            feats = [x for x in feats if x["geometry"]["type"] != "GeometryCollection"]
            feats, _note = geo.normalise_crs(feats, request.form.get("crs", "auto"))
            if not feats:
                raise ValueError("no lines or polygons found")
            for x in feats:  # drop Z values, keep it light
                x["geometry"] = mapping(shape(x["geometry"]))
                x["geometry"] = json.loads(json.dumps(x["geometry"]))
        except Exception as e:
            flash(f"{f.filename}: {e}", "error")
            continue
        n = db.execute("SELECT COUNT(*) FROM land_alignments").fetchone()[0]
        name = request.form.get("name", "").strip() if len(files) == 1 else ""
        db.execute("INSERT INTO land_alignments (name, filename, geojson, color, row_width, start_chainage, created_at) "
                   "VALUES (?,?,?,?,?,?,?)",
                   (name or os.path.splitext(f.filename)[0], f.filename,
                    json.dumps({"type": "FeatureCollection", "features": feats}), colors[n % len(colors)],
                    float(request.form.get("row_width") or 60), float(request.form.get("start_chainage") or 0),
                    store.now()))
        db.commit()
        kinds = sorted({x["geometry"]["type"] for x in feats})
        flash(f"Alignment '{f.filename}' added ({len(feats)} features: {', '.join(kinds)}).", "ok")
    return redirect(url_for("landmap.page"))


@bp.route("/api/alignments")
def api_alignments():
    db = store.get_db()
    return jsonify([{"id": r["id"], "name": r["name"], "filename": r["filename"], "color": r["color"],
                     "row_width": r["row_width"], "start_chainage": r["start_chainage"],
                     "geojson": json.loads(r["geojson"])}
                    for r in db.execute("SELECT * FROM land_alignments ORDER BY id")])


@bp.route("/alignments/<int:aid>/delete", methods=["POST"])
def delete_alignment(aid):
    db = store.get_db()
    db.execute("DELETE FROM land_alignments WHERE id=?", (aid,))
    db.commit()
    return jsonify(ok=True)


def _analyse_args(aid):
    return analyse(store.get_db(), aid, request.args.get("width"), request.args.get("start"),
                   request.args.get("mode", "auto"))


@bp.route("/api/analyse/<int:aid>")
def api_analyse(aid):
    try:
        return jsonify(_analyse_args(aid))
    except ValueError as e:
        return jsonify(error=str(e)), 400


@bp.route("/analyse/<int:aid>.csv")
def analyse_csv(aid):
    res = _analyse_args(aid)
    buf = io.StringIO()
    w = csv.writer(buf)
    method = ("ROW polygons from the KML" if res["mode"] == "polygon"
              else "centre line buffered, ROW width %g m" % res["width"])
    w.writerow([f"Survey numbers affected by: {res['alignment']['name']}", f"Method: {method}"])
    w.writerow(["S.No", "District", "Tehsil", "Village", "Survey / Gata No", "Total area (ha)", "Affected area (ha)",
                "Affected area (sqm)", "% of plot", "Chainage from", "Chainage to", "Side", "Owner / Khata (from file)"])
    for r in res["parcels"]:
        w.writerow([r["sno"], r["district"], r["tehsil"], r["village"], r["survey_no"], r["total_ha"], r["affected_ha"],
                    r["affected_sqm"], r["pct"], r["ch_from_txt"], r["ch_to_txt"], r["side"], r["owner"]])
    w.writerow([])
    w.writerow(["", "", "", "TOTAL", len(res["parcels"]), "", res["total_affected_ha"]])
    fname = f"affected_survey_numbers_{res['alignment']['name']}.csv".replace(" ", "_")
    return Response("﻿" + buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@bp.route("/analyse/<int:aid>.kml")
def analyse_kml(aid):
    """Affected survey numbers as KML (open in Google Earth with the alignment)."""
    res = _analyse_args(aid)
    db = store.get_db()
    pms = []
    for r in res["parcels"]:
        g = json.loads(db.execute("SELECT geometry FROM land_parcels WHERE id=?", (r["parcel_id"],)).fetchone()[0])
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        geom = "".join(
            "<Polygon>" + "".join(
                f"<{'outerBoundaryIs' if i == 0 else 'innerBoundaryIs'}><LinearRing><coordinates>"
                + " ".join(f"{c[0]},{c[1]}" for c in ring)
                + f"</coordinates></LinearRing></{'outerBoundaryIs' if i == 0 else 'innerBoundaryIs'}>"
                for i, ring in enumerate(p)) + "</Polygon>" for p in polys)
        data = "".join(f'<Data name="{k}"><value>{escape(str(r[k]))}</value></Data>'
                       for k in ("village", "tehsil", "district", "survey_no", "total_ha", "affected_ha", "pct",
                                 "ch_from_txt", "ch_to_txt", "side"))
        pms.append(f"<Placemark><name>{escape(r['village'])} - {escape(r['survey_no'])}</name><styleUrl>#aff</styleUrl>"
                   f"<ExtendedData>{data}</ExtendedData><MultiGeometry>{geom}</MultiGeometry></Placemark>")
    kml = ('<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
           f"<name>Affected survey numbers - {escape(res['alignment']['name'])}</name>"
           '<Style id="aff"><LineStyle><color>ff0000ff</color><width>2</width></LineStyle>'
           '<PolyStyle><color>550000ff</color></PolyStyle></Style>' + "".join(pms) + "</Document></kml>")
    fname = f"affected_survey_numbers_{res['alignment']['name']}.kml".replace(" ", "_")
    return Response(kml, mimetype="application/vnd.google-earth.kml+xml",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})

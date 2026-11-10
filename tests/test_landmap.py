import io
import json
import zipfile

import pytest

from sab import geo
from sab.app import create_app

# synthetic village near Ayodhya: 10 x 10 grid of 100 m x 100 m plots (1 ha each) in UTM 44N
E0, N0 = 700000.0, 2965000.0


def plot_ring(i, j):
    pts = [(E0 + i * 100, N0 + j * 100), (E0 + (i + 1) * 100, N0 + j * 100),
           (E0 + (i + 1) * 100, N0 + (j + 1) * 100), (E0 + i * 100, N0 + (j + 1) * 100), (E0 + i * 100, N0 + j * 100)]
    return [geo.from_utm(x, y) for x, y in pts]


def village_kml():
    pms = []
    for i in range(10):
        for j in range(10):
            coords = " ".join(f"{x},{y},0" for x, y in plot_ring(i, j))
            pms.append(f"<Placemark><name>{j * 10 + i + 1}</name><ExtendedData><SchemaData>"
                       f"<SimpleData name=\"Khasra_No\">{j * 10 + i + 1}</SimpleData>"
                       f"<SimpleData name=\"Owner\">Owner {i}-{j}</SimpleData></SchemaData></ExtendedData>"
                       f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{coords}</coordinates></LinearRing>"
                       f"</outerBoundaryIs></Polygon></Placemark>")
    return ('<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
            + "".join(pms) + "</Document></kml>").encode()


def alignment_kml():
    # east-west centre line through the middle of row j=5 (northing N0+550), across the whole village
    a = geo.from_utm(E0 - 200, N0 + 550)
    b = geo.from_utm(E0 + 1200, N0 + 550)
    return (f'<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark><name>CL</name>'
            f"<LineString><coordinates>{a[0]},{a[1]},0 {b[0]},{b[1]},0</coordinates></LineString>"
            f"</Placemark></Document></kml>").encode()


@pytest.fixture
def client(tmp_path):
    app = create_app({"DATABASE": str(tmp_path / "t.sqlite3"), "UPLOAD_FOLDER": str(tmp_path / "up"), "TESTING": True})
    with app.test_client() as c:
        yield c


def test_utm_roundtrip():
    x, y = geo.to_utm(82.2, 26.79)
    assert geo.utm_zone(82.2) == 44
    lon, lat = geo.from_utm(x, y)
    assert abs(lon - 82.2) < 1e-8 and abs(lat - 26.79) < 1e-8


def test_import_explore_and_analyse(client):
    assert client.get("/landmap/").status_code == 200
    r = client.post("/landmap/import", data={"files": (io.BytesIO(village_kml()), "Majha.kml"), "district": "Ayodhya",
                                             "tehsil": "Sadar", "village": "Majha"},
                    content_type="multipart/form-data", follow_redirects=True)
    assert b"100 survey numbers imported" in r.data and b"Khasra_No" in r.data
    tree = client.get("/landmap/api/tree").get_json()
    v = tree["Ayodhya"]["Sadar"][0]
    assert v["village"] == "Majha" and v["count"] == 100
    fc = client.get(f"/landmap/api/village/{v['id']}").get_json()
    assert abs(fc["features"][0]["properties"]["area_ha"] - 1.0) < 0.001
    s = client.get("/landmap/api/search?q=55").get_json()
    assert s[0]["survey_no"] == "55" and s[0]["village"] == "Majha"
    b = v["bbox"]
    assert len(client.get(f"/landmap/api/parcels?bbox={b[0]},{b[1]},{b[2]},{b[3]}").get_json()["features"]) == 100

    client.post("/landmap/alignments", data={"files": (io.BytesIO(alignment_kml()), "bypass.kml"), "row_width": "60",
                                             "start_chainage": "10000"}, content_type="multipart/form-data")
    al = client.get("/landmap/api/alignments").get_json()
    assert al[0]["name"] == "bypass"
    res = client.get(f"/landmap/api/analyse/{al[0]['id']}").get_json()
    # 60 m corridor centred in row j=5 -> only plots 51..60, each 100 m x 60 m = 0.6 ha
    assert sorted(int(p["survey_no"]) for p in res["parcels"]) == list(range(51, 61))
    assert all(abs(p["affected_ha"] - 0.6) < 0.002 and p["side"] == "Both" for p in res["parcels"])
    assert abs(res["total_affected_ha"] - 6.0) < 0.02
    first = res["parcels"][0]
    assert first["survey_no"] == "51" and first["ch_from_txt"] == "10+200" and first["ch_to_txt"] == "10+300"
    assert first["owner"] == "Owner 0-5"
    # wider ROW (240 m: 430-670 m) also takes 70 m of the neighbouring rows j=4 and j=6
    res = client.get(f"/landmap/api/analyse/{al[0]['id']}?width=240").get_json()
    assert len(res["parcels"]) == 30
    side = {p["survey_no"]: (p["side"], round(p["affected_ha"], 2)) for p in res["parcels"]}
    assert side["45"] == ("RHS", 0.7) and side["65"] == ("LHS", 0.7) and side["55"] == ("Both", 1.0)
    csv = client.get(f"/landmap/analyse/{al[0]['id']}.csv").data.decode("utf-8-sig")
    assert "Majha" in csv and "10+200" in csv
    kml = client.get(f"/landmap/analyse/{al[0]['id']}.kml").data
    assert kml.count(b"<Placemark>") == 10
    client.post(f"/landmap/village/{v['id']}/delete")
    assert client.get("/landmap/api/tree").get_json() == {}


def test_shapefile_zip_utm_and_village_column(client):
    import shapefile
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    w = shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shapefile.POLYGON)
    w.field("GATA_NO", "C")
    w.field("VILLAGE", "C")
    for k, vill in enumerate(["Rampur", "Rampur", "Shivpur"]):
        x = E0 + k * 100
        w.poly([[(x, N0), (x, N0 + 100), (x + 100, N0 + 100), (x + 100, N0), (x, N0)]])
        w.record(str(k + 1), vill)
    w.close()
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("v.shp", shp.getvalue()); zf.writestr("v.shx", shx.getvalue()); zf.writestr("v.dbf", dbf.getvalue())
    r = client.post("/landmap/import", data={"files": (io.BytesIO(z.getvalue()), "basti.zip"), "district": "Basti"},
                    content_type="multipart/form-data", follow_redirects=True)
    assert b"converted from UTM" in r.data and b"GATA_NO" in r.data
    tree = client.get("/landmap/api/tree").get_json()
    names = {v["village"]: v["count"] for v in tree["Basti"]["-"]}
    assert names == {"Rampur": 2, "Shivpur": 1}


def test_bad_file_reported(client):
    r = client.post("/landmap/import", data={"files": (io.BytesIO(b"not xml"), "x.kml"), "district": "Ayodhya",
                                             "village": "X"}, content_type="multipart/form-data", follow_redirects=True)
    assert b"Could not read the KML" in r.data


def test_kml_reader_description_table():
    kml = (b'<kml xmlns="http://www.opengis.net/kml/2.2"><Placemark><description><![CDATA[<table><tr><td>KHASRA</td>'
           b'<td>12/3</td></tr></table>]]></description><Polygon><outerBoundaryIs><LinearRing><coordinates>'
           b'82,26 82.001,26 82.001,26.001 82,26</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark></kml>')
    f = geo.read_kml(kml)
    assert f[0]["properties"]["KHASRA"] == "12/3"
    assert geo.find_key(f[0]["properties"].keys(), geo.SURVEY_KEYS) == "KHASRA"

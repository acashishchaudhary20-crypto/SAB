import io

import pytest

from sab import db as store
from sab.app import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app({"DATABASE": str(tmp_path / "t.sqlite3"), "UPLOAD_FOLDER": str(tmp_path / "up"), "TESTING": True})
    (tmp_path / "up").mkdir(exist_ok=True)
    with app.test_client() as c:
        c.application = app
        yield c


def test_all_pages_load(client):
    for url in ["/", "/m/letters", "/m/dpr", "/m/mpr", "/m/drb", "/m/documents", "/settings",
                "/mpr/generate?month=2026-11", "/search?q=x", "/m/letters/new", "/m/drb/new"]:
        assert client.get(url).status_code == 200, url


def test_letter_reply_flow(client):
    r = client.post("/m/letters/new", data={
        "ref_no": "AE/1", "letter_date": "2026-11-01", "direction": "Incoming", "from_party": "Authority's Engineer",
        "to_party": "Contractor", "subject": "Delay in GSB", "category": "EOT (Extension of Time)",
        "reply_required": "Yes", "reply_due": "2026-11-05", "status": "Open",
        "attachment": (io.BytesIO(b"%PDF-1.4"), "letter.pdf")}, content_type="multipart/form-data",
        follow_redirects=True)
    assert b"Delay in GSB" in r.data and b"letter.pdf" in r.data
    r = client.post("/draft/letters/1", data={"action": "template", "kind": "Reply"})
    assert b"Your Letter No. AE/1" in r.data and b"critical path" in r.data
    client.post("/draft/letters/1", data={"action": "save", "kind": "Reply", "source": "template", "content": "Dear Sir, ..."})
    r = client.post("/reply/1/issue", data={"ref_no": "OUT/9", "letter_date": "2026-11-03"}, follow_redirects=True)
    assert b"OUT/9" in r.data
    with client.application.app_context():
        assert store.get_record("letters", 1)["status"] == "Replied"
        assert store.get_record("letters", 2)["ref_letters"] == "AE/1"


def test_required_fields(client):
    r = client.post("/m/letters/new", data={"subject": "x"}, follow_redirects=True)
    assert b"Required" in r.data


def test_drb_and_timeline(client):
    client.post("/m/letters/new", data={"ref_no": "L-7", "letter_date": "2026-01-01", "subject": "ROW"})
    client.post("/m/drb/new", data={"dispute_no": "DRB-1", "title": "EOT claim", "amount_claimed": "12.5",
                                    "related_letters": "L-7", "status": "Hearing"})
    client.post("/m/drb_entries/new", data={"dispute_id": "1", "entry_date": "2026-02-01", "entry_type": "Hearing"})
    r = client.get("/m/drb/1")
    assert b"L-7" in r.data and b"Hearing" in r.data
    r = client.post("/draft/drb/1", data={"action": "template", "kind": "Statement of Defence"})
    assert b"STATEMENT OF DEFENCE" in r.data and b"L-7" in r.data
    client.post("/m/drb/1/delete")
    with client.application.app_context():
        assert store.get_db().execute("SELECT COUNT(*) FROM drb_entries").fetchone()[0] == 0


def test_mpr_from_dpr(client):
    for day, qty in (("2026-10-01", 100), ("2026-10-02", 150), ("2026-09-30", 999)):
        client.post("/m/dpr/new", data={"report_date": day, "activity": "GSB", "unit": "cum",
                                        "qty_planned": "200", "qty_achieved": str(qty), "hindrances": "rain"})
    r = client.get("/mpr/generate?month=2026-10")
    assert b"250" in r.data and b"999" not in r.data
    client.post("/m/mpr/new", data={"month": "2026-10", "physical_planned": "40", "physical_actual": "35"})
    r = client.get("/mpr/1/print")
    assert r.status_code == 200 and b"Monthly Progress Report" in r.data


def test_csv_and_search(client):
    client.post("/m/documents/new", data={"title": "GAD of ROB at Ch 10+200", "doc_type": "Drawing"})
    assert b"GAD of ROB" in client.get("/m/documents/export.csv").data
    assert b"GAD of ROB" in client.get("/search?q=ROB").data


def test_ai_disabled_falls_back(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client.post("/m/letters/new", data={"ref_no": "A", "letter_date": "2026-11-01", "subject": "S"})
    r = client.post("/draft/letters/1", data={"action": "ai", "kind": "Reply"})
    assert r.status_code == 200 and b"Your Letter No. A" in r.data


def test_ai_draft_uses_claude(client, monkeypatch):
    import types

    import anthropic

    captured = {}

    class FakeMessages:
        def create(self, **kw):
            captured.update(kw)
            return types.SimpleNamespace(stop_reason="end_turn",
                                         content=[types.SimpleNamespace(type="text", text="AI DRAFT BODY")])

    class FakeClient:
        def __init__(self, *a, **k):
            self.beta = types.SimpleNamespace(messages=FakeMessages())

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    client.post("/m/letters/new", data={"ref_no": "A", "letter_date": "2026-11-01", "subject": "Unique subject"})
    r = client.post("/draft/letters/1", data={"action": "ai", "kind": "Reply", "instructions": "be firm"})
    assert b"AI DRAFT BODY" in r.data
    assert captured["model"] == "claude-opus-5-5"
    assert "Unique subject" in captured["messages"][0]["content"] and "be firm" in captured["messages"][0]["content"]


def test_classify_rules():
    from sab.importer import classify, find_date
    m, d = classify("IE Letter No. 1056 (Sub EOT) dated 14.09.2026.pdf", "")
    assert m == "letters" and d["letter_date"] == "2026-09-14" and d["from_party"] == "Authority's Engineer"
    assert d["category"] == "EOT (Extension of Time)" and d["ref_no"].startswith("1056")
    m, d = classify("GAD of ROB at Ch 10+200.pdf", "")
    assert m == "documents" and d["doc_type"] == "Drawing"
    m, d = classify("Minutes of meeting 2026-10-05.docx", "")
    assert m == "documents" and d["doc_type"] == "Minutes of Meeting" and d["doc_date"] == "2026-10-05"
    assert classify("anything.pdf", "", force="letters")[0] == "letters"
    assert find_date("dated 5th October, 2026") == "2026-10-05"


def test_bulk_import(client):
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", "<w:p>Subject: Shifting of POWERGRID line at Ch 12+300</w:p>")
    files = [(io.BytesIO(buf.getvalue()), "SAB/Letters/Letter to AE 01.11.2026.docx"),
             (io.BytesIO(b"x"), "SAB/Drawings/GAD culvert.pdf"),
             (io.BytesIO(b"x"), "SAB/.DS_Store"),
             (io.BytesIO(b"x"), "SAB/tool.exe")]
    r = client.post("/import", data={"files": files, "ajax": "1"}, content_type="multipart/form-data")
    res = r.get_json()["results"]
    assert [x["status"] for x in res] == ["imported", "imported", "skipped", "skipped"]
    assert res[0]["module"] == "letters" and res[1]["module"] == "documents"
    with client.application.app_context():
        letter = store.get_record("letters", res[0]["id"])
        assert "POWERGRID" in letter["subject"] and letter["category"] == "Utility Shifting"
        assert letter["letter_date"] == "2026-11-01" and letter["attachment"]
    assert b"Letter to AE" in client.get("/search?q=POWERGRID").data
    # importing the same content again is skipped
    r = client.post("/import", data={"files": [(io.BytesIO(b"x"), "again.pdf")], "ajax": "1"},
                    content_type="multipart/form-data")
    assert r.get_json()["results"][0]["note"] == "already imported"
    assert client.get("/import").status_code == 200


def _seed_compose(client):
    client.post("/m/letters/new", data={"ref_no": "IE/1056", "letter_date": "2026-09-14", "subject": "EOT-2 recommendation",
                                        "body": "IE recommends provisional EOT of 120 days for ROW delay at Ch 12+300",
                                        "from_party": "Authority's Engineer"})
    client.post("/m/letters/new", data={"ref_no": "PIU/702", "letter_date": "2026-08-01", "subject": "ROW handover",
                                        "body": "ROW at Ch 12+300 handed over"})
    client.post("/m/documents/new", data={"title": "Bitumen test report", "doc_type": "Test Report"})


def test_compose_find_and_template(client):
    _seed_compose(client)
    r = client.post("/compose", data={"kind": "note", "request_text": "Note for approval of EOT-2 for ROW delay", "action": "find"})
    assert b"IE/1056" in r.data and b"PIU/702" in r.data and b"Bitumen" not in r.data
    r = client.post("/compose", data={"kind": "note", "request_text": "Note for approval of EOT-2 for ROW delay",
                                      "action": "generate", "engine": "template", "src": ["letters:1", "letters:2"]})
    body = r.data.decode().split('name="content"')[1]
    assert "Submitted for kind perusal" in body and body.index("PIU/702") < body.index("IE/1056")  # chronological
    r = client.post("/compose", data={"kind": "note", "request_text": "EOT-2 note", "action": "save",
                                      "content": "NOTE\nSub: EOT-2", "subject": "EOT-2 note"}, follow_redirects=True)
    assert b"Note Sheet" in r.data
    r = client.post("/compose/docx", data={"content": "NOTE\nSub: EOT-2", "subject": "EOT-2 note"})
    assert r.status_code == 200 and r.data[:2] == b"PK"


def test_compose_letter_ai(client, monkeypatch):
    import types

    import anthropic
    captured = {}

    class FakeMessages:
        def create(self, **kw):
            captured.update(kw)
            return types.SimpleNamespace(stop_reason="end_turn", content=[types.SimpleNamespace(type="text", text="LETTER BODY")])

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: types.SimpleNamespace(beta=types.SimpleNamespace(messages=FakeMessages())))
    _seed_compose(client)
    r = client.post("/compose", data={"kind": "letter", "request_text": "Letter to contractor on EOT-2", "addressee": "M/s ABC",
                                      "action": "generate", "engine": "ai", "src": ["letters:1"]})
    assert b"LETTER BODY" in r.data
    text = captured["messages"][0]["content"][-1]["text"]
    assert "IE/1056" in text and "M/s ABC" in text and "PIU/702" not in text
    assert "SOURCE RECORDS" in text and "Never invent" in captured["system"]
    r = client.post("/compose", data={"kind": "letter", "request_text": "Letter to contractor on EOT-2", "action": "save",
                                      "content": "LETTER BODY", "subject": "EOT-2", "ref_letters": "IE/1056"}, follow_redirects=True)
    assert b"Draft" in r.data and b"IE/1056" in r.data


def test_local_folder_import(client, tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "Desktop" / "SAB Files" / "Letters").mkdir(parents=True)
    (home / "Desktop" / "SAB Files" / "Letters" / "Letter no 702 dated 01.10.2026.pdf").write_bytes(b"%PDF")
    (home / "Desktop" / "SAB Files" / "MoM site meeting.txt").write_text("minutes")
    monkeypatch.setenv("HOME", str(home))
    d = client.get("/import/folders").get_json()
    assert "Desktop" in [x["name"] for x in d["dirs"]]
    d = client.get("/import/folders", query_string={"path": str(home / "Desktop")}).get_json()
    assert d["dirs"][0]["name"] == "SAB Files"
    root = d["dirs"][0]["path"]
    files = client.post("/import/list", data={"path": root}).get_json()["files"]
    assert len(files) == 2
    for fpath in files:
        r = client.post("/import", data={"local_path": fpath, "root": root, "ajax": "1"}).get_json()
        assert r["results"][0]["status"] == "imported"
    # outside home or from another computer is refused
    assert client.get("/import/folders", query_string={"path": "/etc"}).status_code == 400
    assert client.get("/import/folders", environ_base={"REMOTE_ADDR": "192.168.1.9"}).status_code == 403

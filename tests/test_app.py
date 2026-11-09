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

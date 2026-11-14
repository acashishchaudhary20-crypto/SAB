import os
import sys
import time
import zipfile
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dak"))
import dak_courier as dc  # noqa: E402

OLD = time.time() - 3600  # files must be "settled" (not changed in the last 10 minutes)


def make(path, data=b"%PDF-1.4 scan", mtime=OLD):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    os.utime(path, (mtime, mtime))
    return path


def make_docx(path, text, mtime=OLD):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", "<w:document><w:p><w:t>%s</w:t></w:p></w:document>" % text)
    os.utime(path, (mtime, mtime))
    return path


@pytest.fixture
def env(tmp_path):
    drive = tmp_path / "GoogleDrive-x" / "My Drive"
    (drive / "Dak Inbox ").mkdir(parents=True)  # the real Drive folder has a space at the end
    docs = tmp_path / "Documents"
    docs.mkdir()
    base = tmp_path / "state"
    base.mkdir()
    cfg = dict(dc.DEFAULTS, folders=[str(docs)])
    return {"cfg": cfg, "base": str(base), "docs": str(docs), "roots": [str(drive)],
            "sub": str(drive / "Dak Inbox " / "Mac folders")}


def run(env, **kw):
    return dc.run_once(env["cfg"], env["base"], roots=env["roots"], **kw)


@pytest.mark.parametrize("name,expected", [
    ("Letter no. 136 dated. 24.09.2026 PIU Reg. MPR June 2026.pdf", True),
    ("2387 dated 09.05.2026 (1).pdf", True),
    ("3921dated 29..10.2026 Contractor.pdf", True),
    ("Lr.No.839 Dated 01.11.2026.pdf", True),
    ("Letter_no_158_dated_12_10_2026_Contractor_compressed.pdf", True),
    ("Medical Claim Circular 261.123 29.01.24.pdf", True),
    ("3942 30.10.2026 IE.pdf", True),
    ("18.142.pdf", False),
    ("Screenshot 2026-11-14 at 10.30.12.png", False),
    ("IMG_2041.jpg", False),
    ("holiday 12.10.2026.jpg", False),
    ("notice to contractor.jpg", True),
    ("bank statement.pdf", False),
])
def test_name_says_letter(name, expected):
    assert dc.name_says_letter(name) is expected


def test_text_says_letter():
    assert dc.text_says_letter("To, The Project Director\nSub: EOT\nRef: letter no. 12\nSir,")
    assert dc.text_says_letter("पत्रांक 45  विषय: भूमि अधिग्रहण  महोदय,")
    assert not dc.text_says_letter("Chapter 1. Sir Isaac Newton was born in 1642.")


def test_sends_letters_once_and_leaves_the_rest(env):
    make(env["docs"] + "/EOT file/3942 dated 30.10.2026 IE.pdf", b"%PDF letter A")
    make(env["docs"] + "/copy/3942 dated 30.10.2026 IE (1).pdf", b"%PDF letter A")  # same content
    make(env["docs"] + "/IMG_2041.jpg", b"photo")
    make(env["docs"] + "/budget.xlsx", b"xlsx")
    make(env["docs"] + "/~$draft letter.docx", b"lock")
    make(env["docs"] + "/SAB-Data/uploads/letter dated 01.01.2026.pdf", b"%PDF app copy")
    make_docx(env["docs"] + "/untitled.docx", "Sub: Delay in work. Ref: our letter no 5. Sir, kindly note")
    make_docx(env["docs"] + "/recipe.docx", "Take two cups of flour")
    s = run(env)
    sent = sorted(os.listdir(env["sub"]))
    assert sent == ["3942 dated 30.10.2026 IE.pdf", "untitled.docx"]  # one copy of the duplicate pair
    assert len(s["sent"]) == 2 and s["not_letters"] == 2 and not s["errors"]
    # originals untouched
    assert os.path.isfile(env["docs"] + "/EOT file/3942 dated 30.10.2026 IE.pdf")
    # second pass: nothing new
    assert run(env)["sent"] == []
    assert sorted(os.listdir(env["sub"])) == sent
    assert os.path.isfile(env["base"] + "/sent-log.csv")


def test_dry_run_copies_nothing(env):
    make(env["docs"] + "/notice dated 02.11.2026.pdf")
    s = run(env, dry_run=True)
    assert len(s["sent"]) == 1 and not os.path.exists(env["sub"])
    assert len(run(env)["sent"]) == 1  # the real pass still sends it


def test_age_limit_and_backfill(env):
    old = time.time() - 90 * 86400
    make(env["docs"] + "/letter dated 01.08.2026.pdf", b"%PDF old", mtime=old)
    make(env["docs"] + "/letter dated 01.11.2026.pdf", b"%PDF new")
    assert len(run(env)["sent"]) == 1
    assert len(run(env, backfill=True)["sent"]) == 1
    assert len(os.listdir(env["sub"])) == 2


def test_file_still_being_written_waits(env):
    path = make(env["docs"] + "/letter dated 05.11.2026.pdf", mtime=time.time())
    assert run(env)["sent"] == []
    os.utime(path, (OLD, OLD))
    assert len(run(env)["sent"]) == 1


def test_daily_limit_carries_over(env):
    env["cfg"]["max_per_day"] = 2
    for i in range(5):
        make(env["docs"] + "/letter %d dated 0%d.11.2026.pdf" % (i, i + 1), b"%%PDF %d" % i, mtime=OLD + i)
    s = run(env)
    assert len(s["sent"]) == 2 and s["waiting"] == 3
    assert sorted(os.listdir(env["sub"])) == ["letter 3 dated 04.11.2026.pdf", "letter 4 dated 05.11.2026.pdf"]  # newest first
    assert run(env)["sent"] == []  # still within the same 24 hours
    tomorrow = datetime.now() + timedelta(hours=25)
    assert len(run(env, now=tomorrow)["sent"]) == 2


def test_same_name_different_letter_keeps_both(env):
    make(env["docs"] + "/a/reply dated 03.11.2026.pdf", b"%PDF one")
    make(env["docs"] + "/b/reply dated 03.11.2026.pdf", b"%PDF two")
    run(env)
    names = sorted(os.listdir(env["sub"]))
    assert len(names) == 2 and "reply dated 03.11.2026.pdf" in names
    other = [n for n in names if n != "reply dated 03.11.2026.pdf"][0]
    assert other.startswith("reply dated 03.11.2026 [") and "(1)" not in other


def test_changed_pdf_is_sent_again_but_word_file_once_a_day(env):
    pdf = make(env["docs"] + "/letter dated 06.11.2026.pdf", b"%PDF v1")
    doc = make_docx(env["docs"] + "/letter to IE dated 06.11.2026.docx", "v1")
    assert len(run(env)["sent"]) == 2
    make(pdf, b"%PDF v2 signed", mtime=OLD + 60)
    make_docx(doc, "v2", mtime=OLD + 60)
    s = run(env)
    assert [os.path.basename(x[0]) for x in s["sent"]] == ["letter dated 06.11.2026.pdf"] and s["waiting"] == 1
    assert len(run(env, now=datetime.now() + timedelta(hours=25))["sent"]) == 1


def test_take_everything_folder_and_scanned_setting(env, tmp_path):
    scans = tmp_path / "Scanner"
    make(str(scans / "Scan_0001.pdf"), b"%PDF scan 1")
    make(env["docs"] + "/Scan_0002.pdf", b"%PDF scan 2")
    env["cfg"]["folders_take_everything"] = [str(scans)]
    s = run(env)
    assert [os.path.basename(x[0]) for x in s["sent"]] == ["Scan_0001.pdf"] and s["not_letters"] == 1


def test_inbox_fallbacks(env, tmp_path, monkeypatch):
    assert dc.find_inbox(env["cfg"], roots=env["roots"])[0].endswith("Dak Inbox ")
    assert dc.find_inbox(dict(env["cfg"], inbox=str(tmp_path / "x")), roots=[])[0] == os.path.realpath(str(tmp_path / "x"))
    monkeypatch.setattr(dc, "HOME", str(tmp_path))
    assert dc.find_inbox(env["cfg"], roots=[]) [0] is None
    (tmp_path / "Desktop").mkdir()
    path, note = dc.find_inbox(env["cfg"], roots=[])
    assert path == str(tmp_path / "Desktop" / "Letters") and "Desktop/Letters" in note
    s = dc.run_once(env["cfg"], env["base"], roots=[])
    assert s["inbox"] == path


def test_inbox_inside_watched_folder_is_not_recopied(env, tmp_path):
    inbox = env["docs"] + "/Dak Inbox"
    env["cfg"]["inbox"] = inbox
    make(env["docs"] + "/letter dated 07.11.2026.pdf")
    assert len(run(env)["sent"]) == 1
    assert run(env)["sent"] == [] and len(os.listdir(inbox + "/Mac folders")) == 1


def test_command_line(env, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DAK_COURIER_DIR", env["base"])
    extra = tmp_path / "EOT file"
    extra.mkdir()
    assert dc.main(["--add", str(extra), "--everything", "--inbox", str(tmp_path / "inbox")]) == 0
    cfg = dc.load_config(env["base"])
    assert cfg["folders_take_everything"] == [str(extra)] and cfg["inbox"] == str(tmp_path / "inbox")
    make(str(extra / "Scan_9.pdf"))
    assert dc.main([]) == 0
    assert "Sent 1 letter(s)" in capsys.readouterr().out
    assert dc.main(["--remove", str(extra)]) == 0
    assert dc.load_config(env["base"])["folders_take_everything"] == []
    assert dc.main(["--status"]) == 0 and "Sent so far   : 1" in capsys.readouterr().out
    assert dc.main(["--add", str(tmp_path / "missing")]) == 1

# Southern Bypass Project – Project Management & Contracts System (SAB)

A single web application that keeps **every record of the Southern Bypass Project in one place**
and helps you **answer letters and DRB disputes**.

| Register | What it holds |
|---|---|
| ✉ **Correspondence / Letters** | Every incoming & outgoing letter: ref. no., date, parties, category (EOT, IPC, ROW, Utility, Variation, Claim…), text, scan, reply-due date, status, cross-references to earlier letters |
| ▦ **Daily Progress Reports (DPR)** | Date, chainage, side, activity (Earthwork, GSB, WMM, DBM, BC, structures…), planned vs achieved qty, manpower, machinery, weather, hindrances, photos |
| ▤ **Monthly Progress Reports (MPR)** | Physical & financial progress (planned vs actual), IPC status, achievements, issues, next-month plan; **auto-generated from DPRs** and printable / save-as-PDF |
| ⚖ **DRB Disputes** | Dispute no., parties, clauses, notice/referral dates, amount & EOT claimed, status, facts, both sides' cases, DRB recommendation, **proceedings timeline** (SoC, SoD, rejoinder, hearings, recommendation…), linked letters |
| 🗀 **Document Repository** | Contract agreement, schedules, drawings, MoMs, approvals, BGs, insurance, test reports… with revisions and files |

## Answering letters and DRB disputes

On any letter or dispute click **Answer** / **Draft DRB submission**:

1. Choose the document type – *Reply, Rejoinder, Reminder* for letters; *Statement of Claim, Statement of Defence, Rejoinder,
   Written Submission, Reply to DRB Query, Notice of Dissatisfaction, Hearing notes* for disputes.
2. Optionally type the key points you want to make.
3. **Generate template draft** (works offline) – a properly structured letter / DRB submission pre-filled with the
   project details, the letter or dispute data, the chronology and all linked correspondence.
   **or** **Draft with AI (Claude)** – a complete first draft written from the same context (needs an API key, see below).
4. Edit, **save the draft to the record**, then **Register as issued letter** – this creates the outgoing letter in
   the register, links it to the original and marks the original *Replied*.

Drafts are a starting point: check every fact, date, amount and clause before issue.

## Other features
- Dashboard: overdue replies (highlighted), letters awaiting reply, active disputes and next hearing dates, open claim
  value, latest DPR and MPR progress bars.
- Global search across all registers and saved drafts.
- Filters on every register; **CSV export** (opens in Excel) for every register.
- File attachments (PDF, Word, Excel, images, DWG, ZIP…) on every record.
- Project settings (project name, contract no., parties, our role, signatory) used in all drafts and reports.

## Install & run

Requires Python 3.10+.

```bash
pip install -r requirements.txt
python seed_demo.py          # optional: load sample data to explore
python run.py                # open http://127.0.0.1:5000
```

Share with your team on the site-office network: `python run.py --host 0.0.0.0` (or, for production,
`waitress-serve --port=8000 wsgi:app`). There is no login yet, so only run it on a trusted network.

### Enable AI drafting (optional)

```bash
export ANTHROPIC_API_KEY=sk-ant-...      # Windows: set ANTHROPIC_API_KEY=sk-ant-...
python run.py
```

The model defaults to `claude-opus-5-5`; override with `SAB_CLAUDE_MODEL`. Without a key the app works fully and
uses template drafts.

### Where the data lives
Everything is in the `data/` folder (`data/sab.sqlite3` + `data/uploads/`). **Back up this folder regularly.**
Change the location with `SAB_DATA_DIR`. Set `SAB_SECRET_KEY` to a random value in production.

## Customising
All registers are defined in `sab/modules.py`. To add a field, category, activity or dispute status, add one line
there – the database column, forms, lists, CSV export and search update automatically.

## Tests
```bash
python -m pytest -q
```

## Project layout
```
run.py / wsgi.py        start the app
seed_demo.py            sample data
sab/modules.py          register definitions (fields, categories, activities, statuses)
sab/db.py               SQLite storage
sab/app.py              web routes (registers, dashboard, MPR generation, drafting, search, settings)
sab/drafting.py         letter / DRB template drafts and Claude AI drafting
sab/templates/          HTML pages
tests/                  automated tests
```

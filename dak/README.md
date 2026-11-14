# Dak Courier

Carries letters from **any folder of the Mac or office PC** to the Google Drive folder **Dak Inbox**, so the
scheduled Claude task *Dak register sync* files them in the Claude **Dak Register** and **Inward Outward Register**.

## How it works

```
 Phone downloads ─────────────► Drive "Dak Inbox" ─────┐
 Mac / PC Desktop, Downloads ─► Drive "Computers" ─────┤   Dak register sync          Claude Dak Register
 Gmail / Outlook attachments ──────────────────────────┼─► (scheduled Claude task, ─► Inward Outward Register
 Mac / PC other folders ──► DAK COURIER ──► Dak Inbox ─┤    3 times a day)            Outward Letters sheet
 Letters used in a chat ──► dak-register skill ────────┘                                     │
                                                                                             ▼
                                              any new chat or task looks letters up here (dak-register skill)
```

The first three lines already work. This folder adds the fourth: folders that Google Drive does not sync
(Documents, a scanner folder, a project folder on another disk) were invisible to the sync.

Every 15 minutes the courier:

1. looks through the folders you have named, sub-folders included;
2. keeps only PDF, Word, e-mail and scan files, and of those only the ones that are letters. A file counts as a
   letter when its **name** says so (`letter`, `ltr`, `Lr.No`, `dated`, `notice`, `reply`, `reminder`, `circular`,
   `office order`, `minutes`, `MoM`, `NHAI`, `PIU`, `NOC`, or a date such as `30.10.2026`), or when the **text
   inside** has at least two marks of a letter (Sub:, Ref:, Sir, Yours faithfully, Copy to, NHAI / MoRTH,
   Circular / Office Memorandum, or the Hindi equivalents);
3. copies each such file **once** into `Dak Inbox/Mac folders` (or `PC folders`). It remembers every file by
   its content, so a letter saved in two places, renamed, or downloaded twice as `name (1).pdf` goes only once;
4. leaves your own folders exactly as they were. Nothing is moved, renamed or deleted.

Google Drive for desktop uploads the copy, and the next run of the sync (about 08:56, 13:56 and 18:56) reads
it and files it. So a letter saved on the Mac reaches the registers within a few hours without any action.

Safeguards:

| Setting | Default | Why |
|---|---|---|
| `since_days` | 30 | The first run does not flood the sync with years of old files. `--backfill` lifts it. |
| `max_per_day` | 60 | At most 60 files in any 24 hours; the rest wait and go the next day, newest first. |
| `settle_minutes` | 10 | A file still downloading or being typed is left until it stops changing. |
| Word files | once a day | A `.docx` being edited is copied at most once in 24 hours. A changed PDF goes again at once. |
| `scanned_pdfs` | `skip` | A scanned PDF whose name says nothing (`Scan_0012.pdf`) is left alone, so personal scans in Documents do not reach the office register. Set `send` to take them. |
| Skipped always | | Excel, PowerPoint, ZIP, KMZ, drawings, `~$` lock files, hidden folders, `Library`, `SAB-Data`. |

## Run it

Needs Python 3.8 or later and Google Drive for desktop (already on the Mac and the PC, since Desktop and
Downloads sync through it).

**Mac:** double-click `Start Dak Courier.command` (first time: right-click → Open → Open). A Terminal window opens,
shows the folders and the Dak Inbox it found, and keeps running. macOS asks once whether Terminal may read
Documents: allow it.

**Office PC:** double-click `Start Dak Courier (Windows).bat`.

**Start automatically:** Mac → System Settings → General → Login Items → `+` → choose `Start Dak Courier.command`.
PC → press Win+R, type `shell:startup`, put a shortcut to the `.bat` file there.

From Terminal, in this folder:

```bash
python3 dak_courier.py --dry-run
```

shows what would be sent without copying anything. Run this first.

```bash
python3 dak_courier.py --add "~/Documents/EOT file"
```

adds one more folder (letters only).

```bash
python3 dak_courier.py --add "~/Documents/Scanner" --everything
```

adds a folder from which every PDF, Word file and scan is taken, whatever its name.

```bash
python3 dak_courier.py --status
```

shows the folders, the Dak Inbox, the last files sent and the last files left alone with the reason.

```bash
python3 dak_courier.py --backfill
```

one pass that also takes files older than 30 days (still 60 a day; run it daily until nothing is waiting).

Do **not** add Desktop or Downloads: Google Drive already syncs them and the sync already reads them.

## Settings and records

All in `Documents/Dak-Courier/`:

- `config.json` – folders and the settings in the table above. Edit it in TextEdit; the running courier picks
  up the change at its next pass.
- `sent-log.csv` – date, original file, copy made, why it was taken. Opens in Excel.
- `courier.sqlite3` – the courier's memory of what it has sent. Deleting it makes everything go again.

If the courier cannot find *Dak Inbox* (Google Drive's "My Drive" is not shown on the computer), it uses
`Desktop/Letters`, which Drive already syncs. To set the folder by hand:

```bash
python3 dak_courier.py --inbox "/path/to/Dak Inbox"
```

## Letters used in chats, and using the registers in a new chat

That part is not code on the computer. It is the Claude skill **dak-register**, installed once in the Claude
account. It tells every new chat where the registers are, to look letters up there before asking for them, and
to put any signed letter used in the chat into Dak Inbox / the registers. The skill file is kept outside this
repository because it names the private registers.

## Tests

```bash
python -m pytest tests/test_dak_courier.py -q
```

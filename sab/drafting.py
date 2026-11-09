"""Drafting of letter replies and DRB submissions.

Two engines:
  * template - always available, offline, produces a structured draft from the
    record and its linked letters for you to complete.
  * ai       - uses Claude (Anthropic API) when ANTHROPIC_API_KEY is set; writes a
    full draft grounded in the record, the linked correspondence and the
    proceedings timeline.

Every draft must be reviewed by the responsible engineer / contracts manager
before issue: it is a starting point, not legal advice.
"""
import os
from datetime import date

LETTER_KINDS = ["Reply", "Rejoinder / Further Reply", "Reminder", "Acknowledgement"]
DRB_KINDS = ["Statement of Claim", "Statement of Defence", "Rejoinder",
             "Written Submission", "Reply to DRB Query", "Notice of Dissatisfaction",
             "Hearing Notes / Oral Arguments"]

CATEGORY_POINTS = {
    "EOT (Extension of Time)": [
        "the event(s) causing delay and the date each commenced / ended",
        "the notice(s) given within the period stipulated in the Contract",
        "the effect on the critical path, supported by the updated programme",
        "contemporary records (DPRs, hindrance register, site photographs)",
    ],
    "Payment / IPC": [
        "the IPC number, amount certified and amount paid",
        "the contractual time-limit for certification / payment",
        "the specific deductions disputed and the basis for each",
        "interest / financing charges for delayed payment, if applicable",
    ],
    "Variation / Change of Scope": [
        "the instruction or drawing that changed the scope",
        "why the work is outside the original Scope / Specifications",
        "the valuation basis (contract rates, analogous rates or cost-plus)",
        "time implications of the change",
    ],
    "Claim": [
        "the contractual entitlement (clauses relied upon)",
        "compliance with notice requirements",
        "quantum with supporting calculations",
        "contemporary records and correspondence relied upon",
    ],
    "Land / ROW": [
        "the stretches (chainage-wise) not yet handed over",
        "the obligations of the Authority under the Contract for ROW handing over",
        "the impact on progress and resources deployed",
        "the action requested and the date by which it is required",
    ],
    "Utility Shifting": [
        "the utilities (chainage, type, owning agency) obstructing work",
        "status of estimates / approvals / shifting",
        "the impact on progress",
        "the action requested",
    ],
    "Design / Drawings": [
        "drawing numbers, revisions and submission dates",
        "the review period permitted by the Contract",
        "observations received and our compliance",
        "the approval / action requested",
    ],
}


def _today():
    return date.today().strftime("%d.%m.%Y")


def _fmt_date(d):
    if d and len(d) == 10 and d[4] == "-":
        return f"{d[8:10]}.{d[5:7]}.{d[0:4]}"
    return d or "________"


def _signature(s):
    return (f"\nYours faithfully,\n\n\n{s.get('signatory_name') or '[Name]'}\n"
            f"{s.get('signatory_designation') or '[Designation]'}\n"
            f"For {_our_name(s)}\n")


def _our_name(s):
    role = s.get("our_role", "Contractor")
    if role.startswith("Authority /"):
        return s.get("authority") or "the Authority"
    if role.startswith("Authority's"):
        return s.get("engineer") or "the Authority's Engineer"
    return s.get("contractor") or "the Contractor"


def template_letter_reply(letter, linked, settings, kind="Reply"):
    s = settings
    pts = CATEGORY_POINTS.get(letter["category"] or "", [
        "the facts as recorded at site",
        "the relevant provisions of the Contract",
        "our position on each point raised",
        "the action we request / will take, with dates",
    ])
    refs = [f"{i + 2}. Letter No. {l['ref_no']} dated {_fmt_date(l['letter_date'])}" for i, l in enumerate(linked)]
    lines = [
        f"Ref. No.: {s.get('project_name', '')}/____/____",
        f"Date: {_today()}",
        "",
        "To,",
        f"{letter['from_party'] or '[Addressee]'}",
        "",
        f"Project: {s.get('project_name', '')}"
        + (f" (Contract No. {s['contract_no']})" if s.get("contract_no") else ""),
        "",
        f"Subject: {kind} - {letter['subject']}",
        "",
        "Reference:",
        f"1. Your Letter No. {letter['ref_no']} dated {_fmt_date(letter['letter_date'])}",
        *refs,
        "",
        "Dear Sir,",
        "",
        f"1. We acknowledge receipt of your letter under reference (1) regarding "
        f"\"{letter['subject']}\".",
        "",
        "2. [Restate briefly the points raised in the referred letter.]",
        "",
        f"3. In this regard, we submit the following, addressing:",
        *[f"   ({chr(97 + i)}) {p};" for i, p in enumerate(pts)],
        "",
        f"4. [State our position point-wise, citing the relevant clauses of the {s.get('contract_form') or 'Contract'}.]",
        "",
        "5. All our rights under the Contract are hereby reserved, and this letter is "
        "issued without prejudice to any claim, notice or entitlement already raised or "
        "to be raised.",
        "",
        "6. [Specific request / action required, with a date.]",
        "",
        "This is for your kind information and necessary action please.",
        _signature(s),
        "Copy to: [ ]",
    ]
    return "\n".join(lines)


def template_drb(dispute, entries, linked, settings, kind):
    s = settings
    head = [
        f"BEFORE THE DISPUTE REVIEW BOARD",
        f"{s.get('project_name', '')}" + (f" - Contract No. {s['contract_no']}" if s.get("contract_no") else ""),
        "",
        f"Dispute No. {dispute['dispute_no']}: {dispute['title']}",
        f"{dispute['claimant'] or 'Claimant'}  ...  Claimant",
        "versus",
        f"{dispute['respondent'] or 'Respondent'}  ...  Respondent",
        "",
        kind.upper(),
        f"Submitted by {_our_name(s)} on {_today()}",
        "",
    ]
    timeline = [f"   - {_fmt_date(e['entry_date'])}: {e['entry_type']} ({e['party'] or ''}) - {e['summary'] or ''}"
                for e in entries]
    letters = [f"   - {l['ref_no']} dated {_fmt_date(l['letter_date'])} ({l['from_party']} to {l['to_party']}): {l['subject']}"
               for l in linked]
    body = [
        "1. INTRODUCTION",
        f"   1.1 This {kind} is submitted in respect of the above dispute concerning "
        f"\"{dispute['title']}\".",
        f"   1.2 Contract provisions invoked: {dispute['contract_clauses'] or '[clauses]'}.",
        "",
        "2. BACKGROUND / FACTS",
        f"   {dispute['facts'] or '[Chronological facts]'}",
        "",
        "3. CHRONOLOGY OF EVENTS",
        *(timeline or ["   - [Add proceedings in the dispute timeline]"]),
        "",
        "4. CORRESPONDENCE RELIED UPON",
        *(letters or ["   - [Link letters via 'Related Letter Refs']"]),
        "",
        "5. THE OTHER PARTY'S CASE" if kind != "Statement of Claim" else "5. ISSUES FOR DETERMINATION",
        f"   {dispute['claimant_case'] or '[Summary]'}",
        "",
        "6. OUR SUBMISSIONS",
        f"   {dispute['our_position'] or '[Point-wise submissions with clause references]'}",
        "   6.1 Entitlement under the Contract: [ ]",
        "   6.2 Compliance with notice and time-bar provisions: [ ]",
        "   6.3 Causation and contemporary records: [ ]",
        f"   6.4 Quantum: amount claimed ₹ {dispute['amount_claimed'] or '___'} Cr; "
        f"EOT claimed {dispute['eot_claimed_days'] or '___'} days. [Analysis]",
        "",
        "7. RELIEF SOUGHT / PRAYER",
        "   In view of the above, the DRB is respectfully requested to:",
        "   (a) [ ]",
        "   (b) [ ]",
        "   (c) grant any other relief deemed fit.",
        "",
        "8. RESERVATION OF RIGHTS",
        "   The party reserves its right to amend, supplement or add to these submissions "
        "and to produce further documents and evidence.",
        "",
        "LIST OF ANNEXURES",
        "   Annexure-1: [ ]",
        _signature(s),
    ]
    return "\n".join(head + body)


# --------------------------------------------------------------------------- AI

SYSTEM_PROMPT = """You are a senior contracts and claims manager for a highway construction project in India (a bypass road built under the contract form named in the project details). You draft formal project correspondence and Dispute Review Board (DRB) submissions.

Write in formal Indian infrastructure-contract English. Use the project details, the record, the linked correspondence and the proceedings provided. Cite the contract clauses that are given; where a clause number is needed but not provided, write [Clause __] rather than inventing one. Never invent facts, dates, amounts or letter numbers - use [square-bracket placeholders] for anything missing. Protect the drafting party's contractual position: reserve rights, address notice and time-bar issues, and rebut each point of the other side specifically. Output only the document text, ready to paste into a letterhead, with no commentary before or after."""


def ai_available():
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def _record_text(record):
    return "\n".join(f"{k}: {record[k]}" for k in record.keys()
                     if record[k] not in (None, "") and k not in ("attachment", "created_at", "updated_at"))


def build_prompt(module, record, linked, entries, settings, kind, instructions=""):
    parts = ["PROJECT DETAILS", "\n".join(f"{k}: {v}" for k, v in settings.items() if v), ""]
    parts += [f"We are the: {settings.get('our_role')}", ""]
    if module == "letters":
        parts += [f"TASK: Draft a '{kind}' to the letter below on our behalf.", "", "LETTER RECEIVED", _record_text(record)]
    else:
        parts += [f"TASK: Draft our '{kind}' for the DRB dispute below.", "", "DISPUTE", _record_text(record)]
        if entries:
            parts += ["", "PROCEEDINGS SO FAR"] + [_record_text(e) for e in entries]
    if linked:
        parts += ["", "LINKED CORRESPONDENCE"]
        for l in linked:
            parts += ["---", _record_text(l)]
    if instructions:
        parts += ["", "ADDITIONAL INSTRUCTIONS FROM THE USER", instructions]
    return "\n".join(parts)


def ai_draft(prompt, system=SYSTEM_PROMPT):
    """Return (text, error). Uses Claude via the Anthropic SDK.

    `prompt` is a string or a list of content blocks (text and PDF documents).
    """
    if not ai_available():
        return None, "AI drafting is not configured (set ANTHROPIC_API_KEY)."
    try:
        import anthropic
    except ImportError:
        return None, "The 'anthropic' package is not installed (pip install anthropic)."
    client = anthropic.Anthropic()
    try:
        response = client.beta.messages.create(
            model=os.environ.get("SAB_CLAUDE_MODEL", "claude-opus-5-5"),
            max_tokens=16000,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.AuthenticationError:
        return None, "Anthropic API key was rejected - check ANTHROPIC_API_KEY."
    except anthropic.RateLimitError:
        return None, "Rate limited by the Anthropic API - try again in a minute."
    except anthropic.APIStatusError as e:
        return None, f"Anthropic API error {e.status_code}: {e.message}"
    except anthropic.APIConnectionError:
        return None, "Could not reach the Anthropic API (network)."
    except Exception as e:  # e.g. credentials could not be resolved
        return None, f"AI drafting failed: {e}"
    if response.stop_reason == "refusal":
        return None, "The model declined this request. Use the template draft instead."
    text = "\n".join(b.text for b in response.content if getattr(b, "type", "") == "text").strip()
    if response.stop_reason == "max_tokens":
        text += "\n\n[Draft truncated - generate again or shorten the inputs.]"
    return text, None

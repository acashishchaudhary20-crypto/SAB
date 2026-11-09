"""Register definitions for the Southern Bypass Project Management System.

Every register (Letters, DPR, MPR, DRB Disputes, Documents, ...) is described
here once.  The database schema, list pages, forms, CSV export and search are
all generated from these definitions, so adding a field is a one-line change.

Field tuple: (name, label, type, options)
  type: text | textarea | date | number | select | month | file
"""

LETTER_CATEGORIES = [
    "Contractual", "Technical", "Design / Drawings", "EOT (Extension of Time)",
    "Payment / IPC", "Variation / Change of Scope", "Claim", "Land / ROW",
    "Utility Shifting", "Forest / Environment", "Quality / QA-QC",
    "Safety / HSE", "Traffic / Diversion", "Insurance / Guarantees",
    "Meeting / MoM", "General",
]

PARTIES = ["Authority / Employer", "Authority's Engineer", "Contractor",
           "Sub-contractor", "Govt. Department", "Other"]

ACTIVITIES = [
    "Clearing & Grubbing", "Earthwork - Embankment", "Earthwork - Subgrade",
    "GSB", "WMM", "DBM", "BC", "PQC", "DLC", "Kerb / Median",
    "Box Culvert", "Pipe Culvert", "Minor Bridge", "Major Bridge", "ROB / RUB",
    "Flyover / VUP / PUP", "Retaining Wall / RE Wall", "Drain",
    "Road Furniture / Signage", "Utility Shifting", "Plantation", "Other",
]

UNITS = ["cum", "sqm", "rmt", "km", "MT", "nos", "%", "LS"]

DRB_STATUSES = [
    "Notice of Claim", "Under Negotiation", "Referred to DRB",
    "Statement of Claim Filed", "Statement of Defence Filed", "Hearing",
    "Recommendation Issued", "Recommendation Accepted",
    "Notice of Dissatisfaction", "Conciliation", "Arbitration", "Settled",
    "Withdrawn",
]

DRB_ENTRY_TYPES = [
    "Notice of Claim", "Referral to DRB", "Statement of Claim",
    "Statement of Defence", "Rejoinder", "Sur-rejoinder", "Written Submission",
    "Site Visit", "Hearing", "Minutes of Hearing", "DRB Recommendation",
    "Notice of Dissatisfaction", "Correspondence", "Other",
]

DOC_TYPES = [
    "Contract Agreement", "GCC / PCC / Schedules", "Drawing", "Design Report",
    "Method Statement", "Minutes of Meeting", "Approval", "IPC / Invoice",
    "EOT", "Variation / COS", "Bank Guarantee", "Insurance", "Test Report",
    "Photograph", "Note Sheet", "Other",
]

MODULES = {
    "letters": {
        "title": "Correspondence / Letters",
        "singular": "Letter",
        "icon": "✉",
        "order": "letter_date DESC, id DESC",
        "fields": [
            ("ref_no", "Letter Ref. No.", "text", None),
            ("letter_date", "Letter Date", "date", None),
            ("direction", "Direction", "select", ["Incoming", "Outgoing"]),
            ("from_party", "From", "select", PARTIES),
            ("to_party", "To", "select", PARTIES),
            ("subject", "Subject", "text", None),
            ("category", "Category", "select", LETTER_CATEGORIES),
            ("ref_letters", "References (earlier letter refs, comma-separated)", "text", None),
            ("body", "Letter Text / Summary", "textarea", None),
            ("reply_required", "Reply Required?", "select", ["No", "Yes"]),
            ("reply_due", "Reply Due By", "date", None),
            ("status", "Status", "select", ["Draft", "Open", "Replied", "Closed", "For Information"]),
            ("attachment", "Attachment (PDF / scan)", "file", None),
        ],
        "list": ["ref_no", "letter_date", "direction", "from_party", "subject",
                 "category", "status", "reply_due"],
        "required": ["ref_no", "letter_date", "subject"],
        "can_reply": True,
    },
    "dpr": {
        "title": "Daily Progress Reports (DPR)",
        "singular": "DPR Entry",
        "icon": "▦",
        "order": "report_date DESC, id DESC",
        "fields": [
            ("report_date", "Date", "date", None),
            ("chainage_from", "Chainage From (km)", "text", None),
            ("chainage_to", "Chainage To (km)", "text", None),
            ("side", "Side", "select", ["LHS", "RHS", "Both", "Median", "NA"]),
            ("activity", "Activity", "select", ACTIVITIES),
            ("unit", "Unit", "select", UNITS),
            ("qty_planned", "Qty Planned (day)", "number", None),
            ("qty_achieved", "Qty Achieved (day)", "number", None),
            ("manpower", "Manpower (nos)", "number", None),
            ("machinery", "Machinery Deployed", "text", None),
            ("weather", "Weather", "select", ["Clear", "Cloudy", "Rain", "Heavy Rain", "Fog", "Extreme Heat"]),
            ("hindrances", "Hindrances / Issues", "textarea", None),
            ("remarks", "Remarks", "textarea", None),
            ("attachment", "Photo / Sheet", "file", None),
        ],
        "list": ["report_date", "chainage_from", "chainage_to", "activity",
                 "unit", "qty_planned", "qty_achieved", "hindrances"],
        "required": ["report_date", "activity"],
    },
    "mpr": {
        "title": "Monthly Progress Reports (MPR)",
        "singular": "MPR",
        "icon": "▤",
        "order": "month DESC, id DESC",
        "fields": [
            ("month", "Month", "month", None),
            ("physical_planned", "Physical Progress Planned (cum. %)", "number", None),
            ("physical_actual", "Physical Progress Actual (cum. %)", "number", None),
            ("financial_planned", "Financial Progress Planned (cum. ₹ Cr)", "number", None),
            ("financial_actual", "Financial Progress Actual (cum. ₹ Cr)", "number", None),
            ("ipc_status", "IPC / Payment Status", "textarea", None),
            ("key_achievements", "Key Achievements", "textarea", None),
            ("issues", "Issues / Hindrances (Land, Utility, Forest, etc.)", "textarea", None),
            ("next_month_plan", "Plan for Next Month", "textarea", None),
            ("attachment", "Final MPR (PDF)", "file", None),
        ],
        "list": ["month", "physical_planned", "physical_actual",
                 "financial_planned", "financial_actual"],
        "required": ["month"],
    },
    "drb": {
        "title": "DRB Disputes",
        "singular": "Dispute",
        "icon": "⚖",
        "order": "id DESC",
        "fields": [
            ("dispute_no", "Dispute No.", "text", None),
            ("title", "Dispute Title", "text", None),
            ("claimant", "Claimant", "select", PARTIES),
            ("respondent", "Respondent", "select", PARTIES),
            ("contract_clauses", "Contract Clauses Invoked", "text", None),
            ("notice_date", "Notice of Claim Date", "date", None),
            ("referral_date", "Referral to DRB Date", "date", None),
            ("amount_claimed", "Amount Claimed (₹ Cr)", "number", None),
            ("eot_claimed_days", "EOT Claimed (days)", "number", None),
            ("status", "Status", "select", DRB_STATUSES),
            ("next_hearing", "Next Hearing / Deadline", "date", None),
            ("facts", "Facts / Background", "textarea", None),
            ("claimant_case", "Claimant's Case", "textarea", None),
            ("our_position", "Our Position / Defence", "textarea", None),
            ("related_letters", "Related Letter Refs (comma-separated)", "text", None),
            ("drb_recommendation", "DRB Recommendation", "textarea", None),
            ("attachment", "Main Document", "file", None),
        ],
        "list": ["dispute_no", "title", "claimant", "amount_claimed", "status",
                 "next_hearing"],
        "required": ["dispute_no", "title"],
        "can_reply": True,
        "children": "drb_entries",
    },
    "drb_entries": {
        "title": "DRB Proceedings / Timeline",
        "singular": "Proceeding",
        "icon": "⏱",
        "order": "entry_date ASC, id ASC",
        "hidden": True,
        "parent": "drb",
        "fields": [
            ("dispute_id", "Dispute", "hidden", None),
            ("entry_date", "Date", "date", None),
            ("entry_type", "Type", "select", DRB_ENTRY_TYPES),
            ("party", "Filed / Issued By", "select", PARTIES + ["DRB"]),
            ("summary", "Summary", "textarea", None),
            ("attachment", "Document", "file", None),
        ],
        "list": ["entry_date", "entry_type", "party", "summary"],
        "required": ["entry_date", "entry_type"],
    },
    "documents": {
        "title": "Document Repository",
        "singular": "Document",
        "icon": "🗀",
        "order": "doc_date DESC, id DESC",
        "fields": [
            ("doc_no", "Document No.", "text", None),
            ("title", "Title", "text", None),
            ("doc_type", "Type", "select", DOC_TYPES),
            ("revision", "Revision", "text", None),
            ("doc_date", "Date", "date", None),
            ("description", "Description / Notes", "textarea", None),
            ("attachment", "File", "file", None),
        ],
        "list": ["doc_no", "title", "doc_type", "revision", "doc_date"],
        "required": ["title"],
    },
}

SETTINGS_FIELDS = [
    ("project_name", "Project Name", "Southern Bypass Project"),
    ("contract_no", "Contract / Agreement No.", ""),
    ("contract_form", "Form of Contract (e.g. EPC Agreement / HAM / FIDIC)", "EPC Agreement"),
    ("authority", "Authority / Employer", ""),
    ("engineer", "Authority's Engineer", ""),
    ("contractor", "Contractor", ""),
    ("our_role", "We are the", "Contractor"),
    ("appointed_date", "Appointed Date", ""),
    ("scheduled_completion", "Scheduled Completion Date", ""),
    ("project_length_km", "Project Length (km)", ""),
    ("contract_price_cr", "Contract Price (₹ Cr)", ""),
    ("signatory_name", "Signatory Name", ""),
    ("signatory_designation", "Signatory Designation", "Project Manager"),
    ("sync_minutes", "Re-check connected folders every (minutes)", "10"),
]

ROLES = ["Contractor", "Authority / Employer", "Authority's Engineer"]


def field_map(module):
    return {f[0]: f for f in MODULES[module]["fields"]}

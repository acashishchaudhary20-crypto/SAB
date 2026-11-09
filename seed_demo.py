"""Load a few sample records so you can explore the system.  python seed_demo.py"""
from datetime import date, timedelta

from sab import db as store
from sab.app import create_app

app = create_app()
with app.app_context():
    d = date.today()
    iso = lambda days: (d - timedelta(days=days)).isoformat()
    store.save_settings({"project_name": "Southern Bypass Project (Demo)", "contract_no": "SBP/EPC/01",
                         "authority": "Demo Highways Authority", "engineer": "Demo AE Consultants",
                         "contractor": "Demo Infra Ltd.", "our_role": "Contractor",
                         "signatory_name": "A. Kumar", "project_length_km": "28.5"})
    store.insert_record("letters", dict(
        ref_no="AE/SBP/2026/101", letter_date=iso(20), direction="Incoming", from_party="Authority's Engineer",
        to_party="Contractor", subject="Slow progress of embankment between Ch. 12+000 and 14+500",
        category="Contractual", body="The AE observes progress is behind schedule and asks for a catch-up plan.",
        reply_required="Yes", reply_due=iso(5), status="Open"))
    store.insert_record("letters", dict(
        ref_no="SBP/DIL/2026/045", letter_date=iso(60), direction="Outgoing", from_party="Contractor",
        to_party="Authority / Employer", subject="Non-handover of ROW between Ch. 12+300 and 13+100",
        category="Land / ROW", reply_required="No", status="Closed"))
    store.insert_record("letters", dict(
        ref_no="AE/SBP/2026/115", letter_date=iso(3), direction="Incoming", from_party="Authority's Engineer",
        to_party="Contractor", subject="Deduction in IPC-14", category="Payment / IPC",
        ref_letters="SBP/DIL/2026/045", reply_required="Yes", reply_due=iso(-7), status="Open"))
    for i in range(10):
        store.insert_record("dpr", dict(report_date=iso(i), chainage_from="12+000", chainage_to="12+500", side="Both",
                                        activity="Earthwork - Embankment", unit="cum", qty_planned=1500,
                                        qty_achieved=1100 + i * 20, manpower=45, weather="Rain" if i == 3 else "Clear",
                                        hindrances="ROW not available at Ch. 12+300" if i % 4 == 0 else None))
        store.insert_record("dpr", dict(report_date=iso(i), chainage_from="5+000", chainage_to="5+800", side="LHS",
                                        activity="WMM", unit="cum", qty_planned=400, qty_achieved=380, manpower=20,
                                        weather="Clear"))
    did = store.insert_record("drb", dict(
        dispute_no="DRB-01", title="EOT-2 and compensation for delay in ROW handover", claimant="Contractor",
        respondent="Authority / Employer", contract_clauses="[Clause __] (EOT), [Clause __] (Authority's obligations)",
        notice_date=iso(200), referral_date=iso(90), amount_claimed=18.6, eot_claimed_days=210,
        status="Statement of Defence Filed", next_hearing=iso(-21),
        facts="ROW for 0.8 km was handed over 14 months after Appointed Date.",
        claimant_case="Delay attributable to Authority; EOT of 210 days and idle cost claimed.",
        our_position="Notices issued in time; contemporaneous hindrance register maintained.",
        related_letters="SBP/DIL/2026/045"))
    store.insert_record("drb_entries", dict(dispute_id=did, entry_date=iso(90), entry_type="Referral to DRB",
                                            party="Contractor", summary="Dispute referred to DRB."))
    store.insert_record("drb_entries", dict(dispute_id=did, entry_date=iso(30), entry_type="Statement of Defence",
                                            party="Authority / Employer", summary="Authority denies liability."))
    store.insert_record("documents", dict(doc_no="SBP-CA-001", title="EPC Agreement", doc_type="Contract Agreement",
                                          revision="R0", doc_date=iso(800)))
    print("Demo data loaded.")

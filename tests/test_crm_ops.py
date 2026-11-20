import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import crm
import crm_ops
from security_schema import migrate


class CrmOpsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "crm.sqlite3"
        with crm.connect(self.db):
            pass
        migrate(self.db, "tenant-a")
        self.contact = crm.add_contact({"business_name": "Example Garden Centre", "location": "Port Alfred",
                                        "town": "Port Alfred", "email": "owner@example.test",
                                        "phone": "+27460000000", "website_url": "https://example.test"},
                                       self.db, "tenant-a")

    def test_activity_is_tenant_scoped_and_templates_are_personalised(self):
        activity_id = crm_ops.log_activity(self.contact, "call", "Discussed website", "Asked for a quote",
                                           "Phone", "outbound", self.db, "tenant-a")
        rows = crm_ops.list_activities(self.contact, self.db, "tenant-a")
        self.assertEqual(rows[0]["id"], activity_id)
        self.assertEqual(rows[0]["outcome"], "Asked for a quote")
        with self.assertRaises(LookupError):
            crm_ops.list_activities(self.contact, self.db, "tenant-b")
        templates = crm_ops.personalised_templates({"business_name": "Example Garden Centre"}, "Broken mobile menu")
        self.assertIn("Example Garden Centre", templates["whatsapp_audit"])
        self.assertIn("Broken mobile menu", templates["email_audit_body"])

    def test_next_action_snooze_complete_reopen_and_history_are_tenant_scoped(self):
        key = f"followup:{self.contact}:{date.today().isoformat()}"
        snoozed = crm_ops.set_next_action_state(key, "Synthetic follow-up", "snooze", 7, self.db, "tenant-a")
        self.assertEqual(snoozed["snoozed_until"], (date.today() + timedelta(days=7)).isoformat())
        completed = crm_ops.set_next_action_state(key, "Synthetic follow-up", "complete", db_path=self.db, tenant_id="tenant-a")
        self.assertEqual(completed["status"], "completed")
        reopened = crm_ops.set_next_action_state(key, "Synthetic follow-up", "reopen", db_path=self.db, tenant_id="tenant-a")
        self.assertEqual(reopened["status"], "open")
        history = crm_ops.list_next_action_history(self.db, "tenant-a")
        self.assertEqual([x["action"] for x in history], ["reopened", "completed", "snoozed"])
        self.assertEqual(crm_ops.list_next_action_history(self.db, "tenant-b"), [])
        with self.assertRaisesRegex(ValueError, "1, 3, 7, 14 or 30"):
            crm_ops.set_next_action_state(key, "Synthetic follow-up", "snooze", 2, self.db, "tenant-a")

    def test_invoice_deposit_and_balance_are_reconciled_without_overpayment(self):
        invoice_id = crm_ops.create_invoice(self.contact, "Website fixes", 1000, "2026-12-15", db_path=self.db, tenant_id="tenant-a")
        self.assertEqual(crm_ops.invoice_details(invoice_id, self.db, "tenant-a")["invoice_number"], "0048")
        next_id = crm_ops.create_invoice(self.contact, "Follow-up work", 250, "2026-12-20", db_path=self.db, tenant_id="tenant-a")
        self.assertEqual(crm_ops.invoice_details(next_id, self.db, "tenant-a")["invoice_number"], "0049")
        crm_ops.record_payment(self.contact, 300, "2026-11-15", invoice_id, db_path=self.db, tenant_id="tenant-a")
        self.assertEqual(crm_ops.invoice_details(invoice_id, self.db, "tenant-a")["status"], "part_paid")
        with self.assertRaisesRegex(ValueError, "exceed"):
            crm_ops.record_payment(self.contact, 701, "2026-11-15", invoice_id, db_path=self.db, tenant_id="tenant-a")
        crm_ops.record_payment(self.contact, 700, "2026-11-16", invoice_id, db_path=self.db, tenant_id="tenant-a")
        self.assertEqual(crm_ops.invoice_details(invoice_id, self.db, "tenant-a")["status"], "paid")

    def test_branded_invoice_and_monthly_report_render_from_synthetic_data(self):
        invoice_id = crm_ops.create_invoice(self.contact, "Fix <script> & layout", 1250, "2026-12-15", db_path=self.db, tenant_id="tenant-a")
        invoice = crm_ops.create_invoice_pdf(invoice_id, self.root / "invoice-pdfs", self.db, "tenant-a")
        self.assertTrue(invoice.is_file())
        self.assertTrue(invoice.read_bytes().startswith(b"%PDF-"))
        report_dir = self.root / "audit-pdfs"
        report_dir.mkdir()
        def audit(score, filename):
            return {"business_name": "Example Garden Centre", "website": "https://example.test", "scorecard": {"assessed_score": score, "assessment_coverage_percent": 50},
                    "confidence": {"level": "medium"}, "external_research": {"google_places": {}},
                    "package_suggestion": {"estimated_price_zar": None},
                    "opportunities": [{"title": "Improve <mobile menu>", "action": "Make the contact link easier to find & use."}],
                    "pdf_report_path": str(report_dir / filename)}
        first_id = crm.save_audit_result(self.contact, audit(42, "before.pdf"), self.db, "tenant-a")
        with crm.connect(self.db) as conn:
            conn.execute("UPDATE audit_history SET audit_date='2026-10-15T12:00:00+00:00' WHERE id=?", (first_id,))
        crm.save_audit_result(self.contact, audit(61, "after.pdf"), self.db, "tenant-a")
        monthly = crm_ops.create_monthly_report_pdf(self.contact, "2026-11", self.root / "monthly", self.db, "tenant-a")
        self.assertTrue(monthly.is_file())
        self.assertTrue(monthly.read_bytes().startswith(b"%PDF-"))

    def test_unpaid_invoice_can_be_deleted_but_paid_invoice_is_protected(self):
        unpaid = crm_ops.create_invoice(self.contact, "Test invoice", 100, "2026-12-15", db_path=self.db, tenant_id="tenant-a")
        self.assertEqual(crm_ops.delete_invoice(unpaid, self.db, "tenant-a"), self.contact)
        with self.assertRaisesRegex(LookupError, "Invoice not found"):
            crm_ops.invoice_details(unpaid, self.db, "tenant-a")

        paid = crm_ops.create_invoice(self.contact, "Paid test invoice", 100, "2026-12-15", db_path=self.db, tenant_id="tenant-a")
        crm_ops.record_payment(self.contact, 20, "2026-11-18", paid, db_path=self.db, tenant_id="tenant-a")
        with self.assertRaisesRegex(ValueError, "recorded payments"):
            crm_ops.delete_invoice(paid, self.db, "tenant-a")
        self.assertEqual(crm_ops.invoice_details(paid, self.db, "tenant-a")["status"], "part_paid")

    def test_google_maps_sweep_adds_lead_and_repeat_merges_by_place_id(self):
        import town_sweep
        lead = town_sweep.lead_from_maps({
            "title": "Synthetic Google Listing", "type": "plumber", "phone": "+27 46 000 0000",
            "address": "1 Sample Road, Port Alfred, South Africa", "place_id": "test-place-id-001",
            "website": "", "rating": 4.5, "reviews": 12,
            "gps_coordinates": {"latitude": -33.59, "longitude": 26.89},
        }, "plumber", "Port Alfred")
        payload = {"town": "Port Alfred", "leads": [lead]}

        first = crm.import_sweep_payload(payload, self.db, "tenant-a")
        self.assertEqual(first["new_rows"], 1)
        contact = crm.get_contact(first["ids"][0], self.db, "tenant-a")
        self.assertEqual(contact["found_via"], "google_maps_serpapi")
        self.assertEqual(contact["town"], "Port Alfred")
        self.assertFalse(contact["outreach_allowed"])

        second = crm.import_sweep_payload(payload, self.db, "tenant-a")
        self.assertEqual(second["new_rows"], 0)
        self.assertEqual(second["merged_existing"], 1)

    def test_website_enquiry_is_idempotent_and_tracked_as_website_source(self):
        fields = {"business_name": "New Website Lead", "email": "lead@example.test", "message": "Please call me", "form_type": "contact", "consentGiven": True, "consentTextVersion": "privacy-2026-11", "consentTimestamp": "2026-11-15T01:00:00Z", "source": "/contact"}
        first = crm_ops.save_website_enquiry("submission-1", fields, "2026-11-15T01:00:00Z", self.db, "tenant-a")
        lead_before = crm.get_contact(first[0], self.db, "tenant-a")
        crm.save_contact_fields(first[0], {**lead_before, "pipeline_stage": "contacted"}, self.db, "tenant-a")
        second = crm_ops.save_website_enquiry("submission-1", fields, "2026-11-15T01:00:00Z", self.db, "tenant-a")
        self.assertTrue(first[2])
        self.assertFalse(second[2])
        self.assertEqual(first[:2], second[:2])
        lead = crm.get_contact(first[0], self.db, "tenant-a")
        self.assertEqual(lead["found_via"], "website")
        self.assertEqual(lead["pipeline_stage"], "contacted")
        self.assertEqual(lead["town"], "other")
        self.assertEqual(lead["location"], "Not provided")
        self.assertFalse(lead["outreach_allowed"])
        with crm.connect(self.db) as conn:
            enquiry = conn.execute("SELECT consent_given,consent_text_version,consent_timestamp,source FROM crm_website_enquiries WHERE external_id=?", ("submission-1",)).fetchone()
        self.assertEqual(tuple(enquiry), (1, "privacy-2026-11", "2026-11-15T01:00:00Z", "/contact"))
        self.assertEqual(len(crm_ops.list_activities(first[0], self.db, "tenant-a")), 1)

    def test_backup_is_valid_and_erasure_retains_only_suppression_hash(self):
        backup = crm_ops.create_database_backup(self.db, self.root / "backups")
        conn = sqlite3.connect(backup)
        try:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            conn.close()
        with self.assertRaisesRegex(ValueError, "Type DELETE"):
            crm_ops.erase_contact(self.contact, "no", self.db, "tenant-a")
        result = crm_ops.erase_contact(self.contact, "DELETE", self.db, "tenant-a")
        self.assertTrue(result["deleted"])
        self.assertIsNone(crm.get_contact(self.contact, self.db, "tenant-a"))
        with crm.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM suppression_list WHERE tenant_id='tenant-a'").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT count(*) FROM suppression_list WHERE tenant_id='tenant-a' AND identity_hash LIKE '%owner@example.test%'").fetchone()[0], 0)

    def test_project_tracking_and_analytics(self):
        crm_ops.save_project(self.contact, {"status": "in_design", "domain": "example.test", "started_on": "2026-11-01",
                                           "next_milestone": "Homepage review", "next_milestone_on": "2026-11-20"}, self.db, "tenant-a")
        with crm.connect(self.db) as conn:
            row = conn.execute("SELECT status,domain,next_milestone FROM crm_projects WHERE contact_id=? AND tenant_id='tenant-a'", (self.contact,)).fetchone()
        self.assertEqual(tuple(row), ("in_design", "example.test", "Homepage review"))
        metrics = crm_ops.sales_metrics(self.db, "tenant-a")
        self.assertEqual(metrics["total"], 1)
        self.assertEqual(metrics["stages"]["new"], 1)

    def test_sales_metrics_do_not_double_count_hosting_in_bundled_retainer(self):
        with crm.connect(self.db) as conn:
            conn.execute(
                """UPDATE contacts SET pipeline_stage='live',monthly_fee=699,
                   hosting_enabled=1,retainer_name='Active Growth' WHERE id=? AND tenant_id=?""",
                (self.contact, "tenant-a"),
            )
        configured = {
            "hosting": 129,
            "retainers": [{"name": "Active Growth", "includes_hosting": True}],
        }
        with patch("crm_ops.pricing_from_config", return_value=configured):
            metrics = crm_ops.sales_metrics(self.db, "tenant-a")
        self.assertEqual(metrics["monthly_live_zar"], 699)

        with crm.connect(self.db) as conn:
            conn.execute(
                "UPDATE contacts SET retainer_name='Custom plan' WHERE id=? AND tenant_id=?",
                (self.contact, "tenant-a"),
            )
        with patch("crm_ops.pricing_from_config", return_value=configured):
            metrics = crm_ops.sales_metrics(self.db, "tenant-a")
        self.assertEqual(metrics["monthly_live_zar"], 828)


if __name__ == "__main__":
    unittest.main()

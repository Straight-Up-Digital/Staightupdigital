import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

import crm
import crm_ops
import crm_pages
import crm_tools
import crm_web
from security_schema import migrate


class CrmPageRenderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "crm.sqlite3"
        with crm.connect(self.db):
            pass
        migrate(self.db, "tenant-pages")
        self.contact = crm.add_contact({
            "business_name": "Synthetic Garden Studio",
            "location": "Port Alfred",
            "town": "Port Alfred",
            "sector": "Hospitality",
            "phone": "046 123 4567",
            "email": "synthetic@example.test",
            "website_url": "https://garden.example.test",
        }, self.db, "tenant-pages")
        self.old_db_path = crm_web.db_path
        self.old_tenant = crm_web.tenant_id
        crm_web.db_path = lambda: self.db
        crm_web.tenant_id = lambda: "tenant-pages"
        self.addCleanup(setattr, crm_web, "db_path", self.old_db_path)
        self.addCleanup(setattr, crm_web, "tenant_id", self.old_tenant)

    def test_profile_renders_owner_workflows_without_changing_audit_engine(self):
        response, refresh = crm_web.profile_page(self.contact)
        html = response.decode("utf-8")
        self.assertEqual(refresh, 0)
        for phrase in ("Contact timeline", "Open WhatsApp draft", "They contacted me",
                       "Quotes, invoices & payments", "Create branded invoice",
                       "Monthly client progress", "Erase this contact and its saved reports"):
            self.assertIn(phrase, html)
        self.assertIn("Synthetic Garden Studio", html)
        self.assertIn("/monthly/report", html)

    def test_pricing_reference_uses_current_hosting_and_bundled_retainer_tiers(self):
        html = crm_web.main_page({}).decode("utf-8")
        for phrase in (
            "Starter Brand", "R129/month", "R1,290/year",
            "Business Pro", "R249/month", "Team Unlimited", "R449/month",
            "Basic Care", "R249 a month · hosting included",
            "Active Growth", "R699 a month · hosting included",
            "Full Digital Desk", "R1,549 a month · hosting included",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, html)

    def test_next_actions_aggregates_due_workflows_with_history_controls(self):
        today = date.today().isoformat()
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with crm.connect(self.db) as conn:
            conn.execute("UPDATE contacts SET next_follow_up_date=? WHERE id=? AND tenant_id=?",
                         (today, self.contact, "tenant-pages"))
            conn.execute("INSERT INTO audit_jobs(tenant_id,contact_id,job_id,status,error_log,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                         ("tenant-pages", self.contact, "synthetic-failed-job", "failed", "synthetic failure", now, now))
            conn.execute("INSERT INTO crm_invoices(tenant_id,contact_id,invoice_number,description,amount,issued_on,due_on,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                         ("tenant-pages", self.contact, "TEST-0048", "Synthetic invoice", 500, today, today, "issued", now))
            conn.execute("INSERT INTO crm_recurring_items(tenant_id,contact_id,item_type,description,amount,frequency,next_due_on,last_paid_on,active,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                         ("tenant-pages", self.contact, "hosting", "Synthetic hosting", 99, "monthly", today, "", 1, now, now))
            conn.execute("INSERT INTO crm_projects(tenant_id,contact_id,status,next_milestone,next_milestone_on,updated_at) VALUES(?,?,?,?,?,?)",
                         ("tenant-pages", self.contact, "in_build", "Synthetic review", today, now))
            conn.execute("INSERT INTO crm_website_enquiries(tenant_id,external_id,business_name,contact_name,email,phone,message,form_type,received_at,contact_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
                         ("tenant-pages", "synthetic-report-response", "Synthetic Garden Studio", "Test", "synthetic@example.test", "", "Synthetic response", "report_response", now, self.contact))

        html = crm_pages.next_actions_page().decode("utf-8")
        for phrase in ("Next actions", "Website enquiries awaiting a logged follow-up", "report response",
                       "Lead follow-ups due", "Audit jobs needing attention", "Overdue or due invoices",
                       "Renewals due within 14 days", "Project milestones due within 14 days",
                       "Synthetic Garden Studio", "Completing or snoozing dismisses only this reminder"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase.casefold(), html.casefold())
        self.assertIn('name="action" value="complete"', html)
        self.assertIn('name="action" value="snooze"', html)
        self.assertIn('name="snooze_days"', html)
        action_forms = html.count('action="/next-actions/action"')
        protected_html = crm_tools.inject_csrf(html.encode("utf-8"), "synthetic-session-token").decode("utf-8")
        self.assertGreater(action_forms, 0)
        self.assertGreaterEqual(protected_html.count('name="_csrf"'), action_forms)
        self.assertIn('href="/next-actions" class="on"', html)
        self.assertIn("/contact/", html)
        self.assertIn("/queue", html)

        key = f"followup:{self.contact}:{today}"
        crm_ops.set_next_action_state(key, "Synthetic Garden Studio follow-up", "complete",
                                      db_path=self.db, tenant_id="tenant-pages")
        completed_html = crm_pages.next_actions_page().decode("utf-8")
        self.assertIn("Completed reminder", completed_html)
        self.assertIn("Reopen reminder", completed_html)
        with crm.connect(self.db) as conn:
            saved = conn.execute("SELECT next_follow_up_date FROM contacts WHERE id=? AND tenant_id=?",
                                 (self.contact, "tenant-pages")).fetchone()
        self.assertEqual(saved["next_follow_up_date"], today)

    def test_operations_pages_render_and_remain_local_workflows(self):
        cases = [
            (crm_pages.audit_queue_page(), "Audit queue"),
            (crm_pages.finance_page(), "Sales &amp; billing"),
            (crm_pages.projects_page(), "Client projects"),
            (crm_pages.insights_page(), "Sales insights"),
            (crm_pages.bulk_page(), "CSV import with duplicate review"),
            (crm_pages.bulk_page(), "Sync website enquiries now"),
            (crm_pages.compliance_page(), "Privacy &amp; backups"),
        ]
        for payload, expected in cases:
            with self.subTest(page=expected):
                self.assertIn(expected, payload.decode("utf-8"))

    def test_csv_file_picker_reads_into_local_preview_textarea(self):
        html = crm_pages.bulk_page().decode("utf-8")
        self.assertIn('data-csv-file', crm_web.JS)
        self.assertIn('id="csv-text"', html)
        self.assertIn('accept=".csv,text/csv"', html)


if __name__ == "__main__":
    unittest.main()

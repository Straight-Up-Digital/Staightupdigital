import tempfile
import unittest
from pathlib import Path

import crm
import crm_pages
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

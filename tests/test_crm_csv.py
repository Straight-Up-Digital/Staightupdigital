import tempfile
import unittest
from pathlib import Path

import crm
from crm_csv import commit_csv, preview_csv
from security_schema import migrate


class CrmCsvTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "crm.sqlite3"
        with crm.connect(self.db):
            pass
        migrate(self.db, "tenant-csv")
        self.existing = crm.add_contact({"business_name": "Existing Studio", "location": "Port Alfred",
                                         "town": "Port Alfred", "email": "owner@example.test",
                                         "phone": "", "website_url": ""}, self.db, "tenant-csv")

    def test_duplicate_match_is_reviewed_then_merge_only_fills_blank_values(self):
        preview = preview_csv("business_name,email,phone,website_url,town,sector\nExisting Studio,owner@example.test,0461234567,https://studio.example.test,Port Alfred,Design\n",
                              self.db, "tenant-csv")
        self.assertEqual(len(preview), 1)
        self.assertEqual(preview[0]["duplicates"][0]["id"], self.existing)
        result = commit_csv(preview, {"0": f"merge:{self.existing}"}, self.db, "tenant-csv")
        self.assertEqual(result["merged"], 1)
        lead = crm.get_contact(self.existing, self.db, "tenant-csv")
        self.assertEqual(lead["phone"], "0461234567")
        self.assertEqual(lead["email"], "owner@example.test")
        self.assertEqual(lead["website_url"], "https://studio.example.test")
        self.assertFalse(lead["outreach_allowed"])

    def test_new_lead_is_unverified_and_contact_is_not_enabled_by_import(self):
        rows = preview_csv("business_name,email,town\nNew Lead,hello@example.test,Bathurst\n", self.db, "tenant-csv")
        result = commit_csv(rows, {"0": "new"}, self.db, "tenant-csv")
        self.assertEqual(result["added"], 1)
        lead = crm.get_contact(result.get("contact_id", -1), self.db, "tenant-csv")
        with crm.connect(self.db) as conn:
            row = conn.execute("SELECT details_verified,outreach_allowed,marketing_consent,found_via FROM contacts WHERE lower(email)=lower(?) AND tenant_id=?",
                               ("hello@example.test", "tenant-csv")).fetchone()
        self.assertEqual(tuple(row), (0, 0, 0, "csv_import"))

    def test_import_limits_and_missing_name_are_safe(self):
        with self.assertRaisesRegex(ValueError, "250 KB"):
            preview_csv("x" * 250_001, self.db, "tenant-csv")
        rows = preview_csv("business_name,email\n,none@example.test\n", self.db, "tenant-csv")
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()

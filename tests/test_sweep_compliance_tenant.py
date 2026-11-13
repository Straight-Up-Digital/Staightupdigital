import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from sweep_compliance import filter_payload


class SweepTenantMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "legacy.sqlite3"

    def create_legacy_contacts(self, include_tenant_id=False):
        connection = sqlite3.connect(self.db_path)
        try:
            tenant_column = ", tenant_id TEXT NOT NULL DEFAULT 'straight-up-digital'" if include_tenant_id else ""
            connection.execute(
                f"CREATE TABLE contacts (id INTEGER PRIMARY KEY, business_name TEXT NOT NULL{tenant_column})"
            )
            if include_tenant_id:
                connection.execute(
                    "INSERT INTO contacts(business_name, tenant_id) VALUES(?, ?)",
                    ("test-only business", "tenant-a"),
                )
            else:
                connection.execute(
                    "INSERT INTO contacts(business_name) VALUES(?)",
                    ("test-only business",),
                )
            connection.commit()
        finally:
            connection.close()

    def test_legacy_contacts_are_assigned_to_the_selected_tenant(self):
        self.create_legacy_contacts()

        filter_payload({"leads": []}, self.db_path, tenant_id="tenant-b")

        with closing(sqlite3.connect(self.db_path)) as connection:
            tenant = connection.execute("SELECT tenant_id FROM contacts").fetchone()[0]
        self.assertEqual(tenant, "tenant-b")

    def test_existing_tenant_assignments_are_not_rewritten(self):
        self.create_legacy_contacts(include_tenant_id=True)

        filter_payload({"leads": []}, self.db_path, tenant_id="tenant-b")

        with closing(sqlite3.connect(self.db_path)) as connection:
            tenant = connection.execute("SELECT tenant_id FROM contacts").fetchone()[0]
        self.assertEqual(tenant, "tenant-a")

    def test_missing_tenant_is_rejected_before_migration(self):
        self.create_legacy_contacts()

        with patch("sweep_compliance.env", return_value=""):
            with self.assertRaisesRegex(RuntimeError, "CRM_TENANT_ID required"):
                filter_payload({"leads": []}, self.db_path)

        with closing(sqlite3.connect(self.db_path)) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(contacts)")}
        self.assertNotIn("tenant_id", columns)


if __name__ == "__main__":
    unittest.main()

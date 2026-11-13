import sqlite3
import unittest
from unittest.mock import patch

import security
from security_schema import add_suppression, eligible_for_crm


class PhoneSuppressionTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute(
            """create table suppression_list(
                id integer primary key autoincrement,
                tenant_id text not null,
                identity_hash text not null,
                identity_type text not null,
                reason text not null default '',
                created_at text not null,
                unique(tenant_id, identity_hash)
            )"""
        )
        self.addCleanup(self.connection.close)
        self.env_patch = patch("security.env", return_value="test-only-suppression-key")
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def test_phone_hash_ignores_formatting(self):
        self.assertEqual(
            security.suppression_hash("phone", "+1 (555) 010-1234"),
            security.suppression_hash("phone", "+15550101234"),
        )

    def test_south_african_country_code_matches_local_format(self):
        self.assertEqual(
            security.suppression_hash("phone", "+27 82 123 4567"),
            security.suppression_hash("phone", "0821234567"),
        )

    def test_new_phone_suppression_matches_differently_formatted_lead(self):
        add_suppression(
            self.connection,
            "phone",
            "+1 (555) 010-1234",
            tenant_id="tenant-a",
        )

        self.assertFalse(
            eligible_for_crm(
                self.connection,
                {"phone": "+15550101234"},
                tenant_id="tenant-a",
            )
        )

    def test_reentering_legacy_phone_upgrades_its_hash_without_adding_a_row(self):
        value = "+1 (555) 010-1234"
        legacy_hash = security._legacy_suppression_hash("phone", value)
        self.connection.execute(
            "insert into suppression_list(tenant_id,identity_hash,identity_type,created_at) values(?,?,?,?)",
            ("tenant-a", legacy_hash, "phone", "2024-01-01T00:00:00+00:00"),
        )

        add_suppression(self.connection, "phone", value, tenant_id="tenant-a")

        rows = self.connection.execute(
            "select identity_hash from suppression_list where tenant_id=?",
            ("tenant-a",),
        ).fetchall()
        self.assertEqual(rows, [(security.suppression_hash("phone", value),)])
        self.assertFalse(
            eligible_for_crm(
                self.connection,
                {"phone": "+15550101234"},
                tenant_id="tenant-a",
            )
        )

    def test_legacy_phone_hash_still_matches_same_format(self):
        value = "+1 (555) 010-1234"
        legacy_hash = security._legacy_suppression_hash("phone", value)
        self.connection.execute(
            "insert into suppression_list(tenant_id,identity_hash,identity_type,created_at) values(?,?,?,?)",
            ("tenant-a", legacy_hash, "phone", "2024-01-01T00:00:00+00:00"),
        )

        self.assertFalse(
            eligible_for_crm(
                self.connection,
                {"phone": value},
                tenant_id="tenant-a",
            )
        )

    def test_non_phone_hash_behavior_is_unchanged(self):
        value = "  CUSTOMER@EXAMPLE.COM "
        self.assertEqual(
            security.suppression_hash("email", value),
            security._legacy_suppression_hash("email", value),
        )


if __name__ == "__main__":
    unittest.main()

import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import crm
import crm_web
from security_schema import migrate


class PdfTenantIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "crm.sqlite3"
        self.reports_dir = self.root / "reports"
        self.reports_dir.mkdir()

        with crm.connect(self.db_path):
            pass
        migrate(self.db_path, "tenant-a")

        with crm.connect(self.db_path) as conn:
            contact_ids = {}
            for tenant in ("tenant-a", "tenant-b"):
                cursor = conn.execute(
                    """INSERT INTO contacts
                       (tenant_id, business_name, location, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (tenant, f"{tenant} business", "Town", "2026-11-13", "2026-11-13"),
                )
                contact_ids[tenant] = cursor.lastrowid

                pdf_path = self.reports_dir / f"{tenant}.pdf"
                pdf_path.write_bytes(f"{tenant} report".encode("utf-8"))
                cursor = conn.execute(
                    """INSERT INTO audit_history
                       (contact_id, audit_date, pdf_path, tenant_id)
                       VALUES (?, ?, ?, ?)""",
                    (contact_ids[tenant], "2026-11-13", str(pdf_path), tenant),
                )
                if tenant == "tenant-a":
                    self.tenant_a_history_id = cursor.lastrowid
                else:
                    self.tenant_b_contact_id = contact_ids[tenant]
                    self.tenant_b_history_id = cursor.lastrowid

        patchers = (
            patch.object(crm_web, "db_path", return_value=self.db_path),
            patch.object(crm_web, "tenant_id", return_value="tenant-a"),
            patch.object(crm_web, "PROJECT_DIR", self.root),
        )
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), crm_web.Handler)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.addCleanup(self._close_server)

    def _close_server(self):
        self.server.shutdown()
        self.server_thread.join()
        self.server.server_close()

    def _get_pdf(self, history_id):
        url = f"http://127.0.0.1:{self.server.server_port}/pdf/{history_id}"
        try:
            with urllib.request.urlopen(url) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            try:
                return error.code, error.read()
            finally:
                error.close()

    def test_active_tenant_can_download_own_pdf_but_not_another_tenants(self):
        own_status, own_body = self._get_pdf(self.tenant_a_history_id)
        other_status, other_body = self._get_pdf(self.tenant_b_history_id)

        self.assertEqual(own_status, 200)
        self.assertEqual(own_body, b"tenant-a report")
        self.assertEqual(other_status, 404)
        self.assertNotIn(b"tenant-b report", other_body)

    def test_pdf_is_denied_when_audit_and_contact_tenants_disagree(self):
        with crm.connect(self.db_path) as conn:
            cursor = conn.execute(
                """INSERT INTO audit_history(contact_id, audit_date, pdf_path, tenant_id)
                   VALUES (?, ?, ?, ?)""",
                (
                    self.tenant_b_contact_id,
                    "2026-11-13",
                    str(self.reports_dir / "tenant-b.pdf"),
                    "tenant-a",
                ),
            )
            history_id = cursor.lastrowid

        status, body = self._get_pdf(history_id)

        self.assertEqual(status, 404)
        self.assertNotIn(b"tenant-b report", body)


if __name__ == "__main__":
    unittest.main()

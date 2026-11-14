import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import business_auditor
import crm_web


ROOT = Path(__file__).resolve().parents[1]


class BrandAssetTests(unittest.TestCase):
    def test_crm_shell_uses_official_brand_and_self_hosted_fonts(self):
        html = crm_web.layout("Leads", "<p>Test</p>").decode("utf-8")
        self.assertIn("/brand-logo", html)
        self.assertIn("/brand-favicon", html)
        self.assertIn("/brand-font/inter-regular.ttf", html)
        self.assertIn("Montserrat", html)
        self.assertIn("#A8FF65", html)
        self.assertIn("INTELLIGENCE. VISIBILITY. GROWTH.", html)
        self.assertTrue(crm_web.LOCKUP.is_file())
        self.assertEqual(crm_web.config().get("crm_logo_path"), "assets/brand/straight-up-digital-logo-dark.png")

    def test_brand_asset_routes_serve_expected_file_types(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), crm_web.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.shutdown)

        checks = {
            "/brand-logo": ("image/png", b"\x89PNG\r\n\x1a\n"),
            "/brand-favicon": ("image/png", b"\x89PNG\r\n\x1a\n"),
            "/brand-font/inter-regular.ttf": ("font/ttf", b"\x00\x01\x00\x00"),
        }
        for route, (content_type, signature) in checks.items():
            with self.subTest(route=route), urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_port}{route}", timeout=5
            ) as response:
                self.assertEqual(response.status, 200)
                self.assertTrue(response.headers.get("Content-Type", "").startswith(content_type))
                self.assertTrue(response.read().startswith(signature))

    @unittest.skipUnless(business_auditor.REPORTLAB_AVAILABLE, "ReportLab is not installed")
    def test_branded_pdf_builds_with_embedded_brand_fonts_and_portable_logo(self):
        body_font, bold_font, heading_font = business_auditor._register_brand_pdf_fonts()
        self.assertEqual((body_font, bold_font, heading_font), ("Inter", "Inter-Bold", "Montserrat"))

        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            report_dir = temp_path / "reports"
            report_dir.mkdir()
            missing_config = temp_path / "no-local-config.json"
            sample = {
                "business_name": "Brand Test Business",
                "website": "https://example.test",
                "location": "Port Alfred",
                "industry": "Retail",
                "generated_at": "2026-11-14",
                "executive_summary": "This sample confirms branded PDF generation without using live client data.",
                "scorecard": {"assessed_score": 72, "assessment_coverage_percent": 80, "categories": []},
                "website_research": {"pages_researched": 1, "pages": []},
                "external_research": {},
                "keyword_research": {},
                "opportunities": [],
                "strategy": {},
                "website_redesign_brief": {},
                "package_suggestion": {
                    "monthly_retainer_options": {
                        "plans": [], "audit_based_priorities": [],
                        "selection_note": "", "larger_scope_note": ""
                    }
                },
                "limitations": [],
            }
            with patch.object(business_auditor, "REPORT_DIR", report_dir), \
                 patch.object(business_auditor, "CONFIG_PATH", missing_config), \
                 patch.dict("os.environ", {"CRM_PDF_LOGO_PATH": ""}):
                output = Path(business_auditor.make_pdf(sample))
            self.assertTrue(output.is_file())
            pdf = output.read_bytes()
            self.assertTrue(pdf.startswith(b"%PDF-"))
            self.assertGreater(len(pdf), 5000)


if __name__ == "__main__":
    unittest.main()

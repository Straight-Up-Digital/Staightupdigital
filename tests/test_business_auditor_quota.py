import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Callable
from unittest.mock import patch

import business_auditor


class FakeExecutor:
    def submit(self, *_args: object, **_kwargs: object) -> None:
        return None


class DailyAuditQuotaTests(unittest.TestCase):
    def test_concurrent_audit_entry_points_cannot_exceed_daily_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            usage_path = Path(directory) / "usage.json"
            job_dir = Path(directory) / "jobs"
            job_dir.mkdir()
            barrier = threading.Barrier(2)

            def synchronized_usage_get(kind: str, period: str) -> int:
                if usage_path.exists():
                    data = json.loads(usage_path.read_text(encoding="utf-8"))
                    value = int(data.get(kind, {}).get(period, 0))
                else:
                    value = 0
                barrier.wait(timeout=5)
                return value

            calls: list[Callable[[], str]] = [
                lambda: business_auditor.start_audit("https://example.com"),
                lambda: business_auditor.audit_business("https://example.com"),
            ]
            jobs: dict[str, dict[str, object]] = {}
            with (
                patch.object(business_auditor, "USAGE_PATH", usage_path),
                patch.object(business_auditor, "JOB_DIR", job_dir),
                patch.object(business_auditor, "load_config", return_value={"max_audits_per_day": 1}),
                patch.object(
                    business_auditor,
                    "normalize_input_url",
                    return_value="https://example.com/",
                ),
                patch.object(business_auditor, "_executor", return_value=FakeExecutor()),
                patch.object(business_auditor, "_JOBS", jobs),
                patch.object(business_auditor, "run_audit", return_value={}),
                patch.object(business_auditor, "summary_view", return_value={"summary": "test"}),
                patch.object(business_auditor, "sanitize_for_model", return_value={"summary": "test"}),
                patch.object(business_auditor, "usage_get", side_effect=synchronized_usage_get),
            ):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = [executor.submit(call) for call in calls]
                    results = [future.result() for future in futures]
                recorded_usage = json.loads(usage_path.read_text(encoding="utf-8"))

            payloads = [json.loads(result) for result in results]
            successful = sum(payload.get("success") is True for payload in payloads)
            self.assertEqual(successful, 1)
            self.assertEqual(sum(recorded_usage.get("audits", {}).values()), 1)

    def test_corrupt_usage_data_fails_closed_without_starting_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            usage_path = Path(directory) / "usage.json"
            job_dir = Path(directory) / "jobs"
            job_dir.mkdir()
            usage_path.write_text("{", encoding="utf-8")
            jobs: dict[str, dict[str, object]] = {}
            with (
                patch.object(business_auditor, "USAGE_PATH", usage_path),
                patch.object(business_auditor, "JOB_DIR", job_dir),
                patch.object(business_auditor, "load_config", return_value={"max_audits_per_day": 1}),
                patch.object(
                    business_auditor,
                    "normalize_input_url",
                    return_value="https://example.com/",
                ),
                patch.object(business_auditor, "_executor", return_value=FakeExecutor()) as executor,
                patch.object(business_auditor, "_JOBS", jobs),
            ):
                result = json.loads(business_auditor.start_audit("https://example.com"))

        self.assertFalse(result["success"])
        self.assertIn("could not be safely enforced", result["error"])
        executor.assert_not_called()


class MonthlySerpApiQuotaTests(unittest.TestCase):
    def test_concurrent_lookups_cannot_exceed_monthly_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            usage_path = Path(directory) / "usage.json"
            barrier = threading.Barrier(2)
            original_usage_get = business_auditor.usage_get

            def synchronized_usage_get(kind: str, period: str) -> int:
                used = original_usage_get(kind, period)
                barrier.wait(timeout=5)
                return used

            with (
                patch.object(business_auditor, "USAGE_PATH", usage_path),
                patch.object(business_auditor, "env", return_value="test-key"),
                patch.object(business_auditor, "load_config", return_value={"monthly_search_budget": 1}),
                patch.object(business_auditor, "cache_get", return_value=None),
                patch.object(business_auditor, "cache_put"),
                patch.object(
                    business_auditor,
                    "_serpapi_raw",
                    return_value={"status": "not_found", "matched": False},
                ),
                patch.object(business_auditor, "usage_get", side_effect=synchronized_usage_get),
            ):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = [
                        executor.submit(
                            business_auditor.serpapi_google_listing,
                            "Example Co",
                            "Port Alfred",
                            "example.com",
                        )
                        for _ in range(2)
                    ]
                    results = [future.result() for future in futures]
                recorded_usage = json.loads(usage_path.read_text(encoding="utf-8"))

        statuses = [result["status"] for result in results]
        self.assertEqual(statuses.count("not_found"), 1)
        self.assertEqual(statuses.count("budget_reached"), 1)
        self.assertEqual(sum(recorded_usage["serpapi"].values()), 1)

    def test_failed_lookup_keeps_its_reserved_monthly_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            usage_path = Path(directory) / "usage.json"
            with (
                patch.object(business_auditor, "USAGE_PATH", usage_path),
                patch.object(business_auditor, "env", return_value="test-key"),
                patch.object(business_auditor, "load_config", return_value={"monthly_search_budget": 1}),
                patch.object(business_auditor, "cache_get", return_value=None),
                patch.object(business_auditor, "_serpapi_raw", return_value={"status": "api_error"}),
            ):
                first = business_auditor.serpapi_google_listing("Example Co", "Port Alfred", "example.com")
                second = business_auditor.serpapi_google_listing("Example Co", "Port Alfred", "example.com")
                recorded_usage = json.loads(usage_path.read_text(encoding="utf-8"))

        self.assertEqual(first["status"], "api_error")
        self.assertEqual(second["status"], "budget_reached")
        self.assertEqual(sum(recorded_usage["serpapi"].values()), 1)

    def test_corrupt_usage_data_fails_closed_without_calling_serpapi(self):
        with tempfile.TemporaryDirectory() as directory:
            usage_path = Path(directory) / "usage.json"
            usage_path.write_text("{", encoding="utf-8")
            with (
                patch.object(business_auditor, "USAGE_PATH", usage_path),
                patch.object(business_auditor, "env", return_value="test-key"),
                patch.object(business_auditor, "load_config", return_value={"monthly_search_budget": 1}),
                patch.object(business_auditor, "cache_get", return_value=None),
                patch.object(business_auditor, "_serpapi_raw") as raw_lookup,
            ):
                result = business_auditor.serpapi_google_listing("Example Co", "Port Alfred", "example.com")

        self.assertEqual(result["status"], "budget_unavailable")
        raw_lookup.assert_not_called()


if __name__ == "__main__":
    unittest.main()

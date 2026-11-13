import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import business_auditor


class AuditJobRetentionTests(unittest.TestCase):
    def test_expired_records_are_removed_but_active_jobs_and_unrelated_files_remain(self):
        with tempfile.TemporaryDirectory() as directory:
            job_dir = Path(directory)
            now = 1_700_000_000.0
            expired_at = now - business_auditor.JOB_RETENTION_DAYS * 24 * 60 * 60 - 1
            jobs = {
                "aaaaaaaaaa": {"status": "done", "finished_at": expired_at},
                "bbbbbbbbbb": {"status": "done", "finished_at": now - 60},
                "cccccccccc": {"status": "running", "started": expired_at},
            }
            results = {job_id: {"job_id": job_id} for job_id in jobs}

            for job_id, modified_at in (("aaaaaaaaaa", expired_at), ("bbbbbbbbbb", now - 60),
                                        ("cccccccccc", now - 60)):
                path = job_dir / f"{job_id}.json"
                path.write_text("{}", encoding="utf-8")
                os.utime(path, (modified_at, modified_at))
            unrelated_json = job_dir / "manual.json"
            unrelated_json.write_text("{}", encoding="utf-8")
            unrelated_pdf = job_dir / "report.pdf"
            unrelated_pdf.write_bytes(b"pdf")

            with (
                patch.object(business_auditor, "JOB_DIR", job_dir),
                patch.object(business_auditor, "_JOBS", jobs),
                patch.object(business_auditor, "_RESULTS", results),
            ):
                business_auditor._prune_jobs(now=now)

            self.assertNotIn("aaaaaaaaaa", jobs)
            self.assertNotIn("aaaaaaaaaa", results)
            self.assertIn("bbbbbbbbbb", jobs)
            self.assertIn("cccccccccc", jobs)
            self.assertFalse((job_dir / "aaaaaaaaaa.json").exists())
            self.assertTrue((job_dir / "bbbbbbbbbb.json").exists())
            self.assertTrue((job_dir / "cccccccccc.json").exists())
            self.assertTrue(unrelated_json.exists())
            self.assertTrue(unrelated_pdf.exists())

    def test_oldest_completed_jobs_are_evicted_to_respect_count_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            job_dir = Path(directory)
            now = 1_700_000_000.0
            job_ids = ("aaaaaaaaaa", "bbbbbbbbbb", "cccccccccc")
            jobs = {
                job_id: {"status": "done", "finished_at": now - age}
                for job_id, age in zip(job_ids, (300, 200, 100))
            }
            results = {job_id: {"job_id": job_id} for job_id in job_ids}
            for job_id, age in zip(job_ids, (300, 200, 100)):
                path = job_dir / f"{job_id}.json"
                path.write_text("{}", encoding="utf-8")
                modified_at = now - age
                os.utime(path, (modified_at, modified_at))

            with (
                patch.object(business_auditor, "JOB_DIR", job_dir),
                patch.object(business_auditor, "MAX_SAVED_JOBS", 2),
                patch.object(business_auditor, "_JOBS", jobs),
                patch.object(business_auditor, "_RESULTS", results),
            ):
                business_auditor._prune_jobs(now=now)

            self.assertNotIn(job_ids[0], jobs)
            self.assertNotIn(job_ids[0], results)
            for job_id in job_ids[1:]:
                self.assertIn(job_id, jobs)
                self.assertIn(job_id, results)
                self.assertTrue((job_dir / f"{job_id}.json").exists())
            self.assertFalse((job_dir / f"{job_ids[0]}.json").exists())

    def test_status_and_result_report_expired_job_as_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            job_dir = Path(directory)
            job_id = "aaaaaaaaaa"
            expired_job = {"status": "done", "finished_at": 1.0}
            result_path = job_dir / f"{job_id}.json"
            result_path.write_text(json.dumps({"job_id": job_id}), encoding="utf-8")
            os.utime(result_path, (1.0, 1.0))
            jobs = {job_id: expired_job}
            results = {job_id: {"job_id": job_id}}

            with (
                patch.object(business_auditor, "JOB_DIR", job_dir),
                patch.object(business_auditor, "_JOBS", jobs),
                patch.object(business_auditor, "_RESULTS", results),
            ):
                status = json.loads(business_auditor.audit_status(job_id))
                result = json.loads(business_auditor.audit_result(job_id))

            self.assertIn("expired", status["error"].lower())
            self.assertIn("expired or been evicted", result["error"])
            self.assertFalse(result_path.exists())


if __name__ == "__main__":
    unittest.main()

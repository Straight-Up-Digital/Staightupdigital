import json
import multiprocessing
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from unittest.mock import patch

import town_sweep


class FakeResponse:
    status_code = 200

    def json(self) -> dict:
        return {"local_results": []}


class FakeClient:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.search_calls = 0

    def get(self, *_args: object, **_kwargs: object) -> FakeResponse:
        with self._lock:
            self.search_calls += 1
        return FakeResponse()

    def close(self) -> None:
        return None


def _reserve_from_process(usage_path: str, barrier: Any, results: Any) -> None:
    with patch.object(town_sweep, "USAGE_PATH", Path(usage_path)):
        barrier.wait(timeout=10)
        results.put(town_sweep.reserve_daily_search("Port Alfred", 1, 1))


class TownSweepQuotaTests(unittest.TestCase):
    def test_concurrent_sweeps_cannot_exceed_daily_search_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            usage_path = Path(directory) / "town_sweep_usage.json"
            barrier = threading.Barrier(2)
            client = FakeClient()

            def synchronized_town_usage(_town: str) -> int:
                barrier.wait(timeout=5)
                return 0

            def fake_env(name: str, default: str = "") -> str:
                values = {
                    "SERPAPI_API_KEY": "test-key",
                    "CRM_SWEEP_MAX_NEW_PER_TOWN_PER_DAY": "10",
                    "CRM_SWEEP_MAX_SEARCHES_PER_DAY": "1",
                    "SWEEP_MIN_DELAY_SECONDS": "0",
                    "SWEEP_MAX_DELAY_SECONDS": "0",
                }
                return values.get(name, default)

            with (
                patch.object(town_sweep, "USAGE_PATH", usage_path),
                patch.object(town_sweep, "env", side_effect=fake_env),
                patch.object(town_sweep, "daily_usage_get", side_effect=synchronized_town_usage),
                patch.object(town_sweep, "daily_usage_total", return_value=0),
                patch.object(town_sweep, "cache_get", return_value=None),
                patch.object(town_sweep, "searches_left", return_value=10),
                patch.object(town_sweep.httpx, "Client", return_value=client),
                patch.object(town_sweep, "write_outputs", return_value={"ok": True}),
            ):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = [
                        executor.submit(
                            town_sweep.run_sweep,
                            "Port Alfred",
                            [trade],
                            1,
                            1,
                            False,
                            False,
                            lambda _message: None,
                        )
                        for trade in ("plumber", "electrician")
                    ]
                    [future.result() for future in futures]
                usage = json.loads(usage_path.read_text(encoding="utf-8"))

        self.assertEqual(client.search_calls, 1)
        today = town_sweep.datetime.now().strftime("%Y-%m-%d")
        self.assertEqual(sum(usage["daily"][today].values()), 1)

    def test_corrupt_usage_data_fails_closed_before_provider_request(self):
        with tempfile.TemporaryDirectory() as directory:
            usage_path = Path(directory) / "town_sweep_usage.json"
            usage_path.write_text("{", encoding="utf-8")
            client = FakeClient()
            with (
                patch.object(town_sweep, "USAGE_PATH", usage_path),
                patch.object(town_sweep, "cache_get", return_value=None),
            ):
                result = town_sweep.maps_search(
                    client,
                    "test-key",
                    "plumber",
                    "Port Alfred",
                    per_town_cap=10,
                    total_cap=1,
                )

        self.assertEqual(result["status"], "budget_unavailable")
        self.assertEqual(client.search_calls, 0)

    def test_per_town_cap_blocks_query_before_provider_request(self):
        with tempfile.TemporaryDirectory() as directory:
            usage_path = Path(directory) / "town_sweep_usage.json"
            today = town_sweep.datetime.now().strftime("%Y-%m-%d")
            usage_path.write_text(
                json.dumps({"daily": {today: {"port alfred": 1}}}),
                encoding="utf-8",
            )
            client = FakeClient()
            with (
                patch.object(town_sweep, "USAGE_PATH", usage_path),
                patch.object(town_sweep, "cache_get", return_value=None),
            ):
                result = town_sweep.maps_search(
                    client,
                    "test-key",
                    "plumber",
                    "Port Alfred",
                    per_town_cap=1,
                    total_cap=10,
                )

        self.assertEqual(result["status"], "budget_reached")
        self.assertEqual(client.search_calls, 0)

    def test_daily_reservation_is_safe_across_processes(self):
        with tempfile.TemporaryDirectory() as directory:
            usage_path = Path(directory) / "town_sweep_usage.json"
            context = multiprocessing.get_context("spawn")
            barrier = context.Barrier(2)
            results = context.Queue()
            processes = [
                context.Process(
                    target=_reserve_from_process,
                    args=(str(usage_path), barrier, results),
                )
                for _ in range(2)
            ]

            for process in processes:
                process.start()
            for process in processes:
                process.join(timeout=15)
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join()

            self.assertEqual([process.exitcode for process in processes], [0, 0])
            reservations = [results.get(timeout=5) for _ in processes]
            with patch.object(town_sweep, "USAGE_PATH", usage_path):
                town_sweep.usage_bump()
                usage = json.loads(usage_path.read_text(encoding="utf-8"))

        self.assertEqual(reservations.count(True), 1)
        self.assertEqual(reservations.count(False), 1)
        today = town_sweep.datetime.now().strftime("%Y-%m-%d")
        self.assertEqual(sum(usage["daily"][today].values()), 1)
        self.assertEqual(usage[town_sweep.month()], 1)


if __name__ == "__main__":
    unittest.main()

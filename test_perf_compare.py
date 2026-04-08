import unittest
from unittest.mock import MagicMock, patch

from backend import perf_compare


class TestPerfCompare(unittest.TestCase):
    def test_local_dict_benchmark_returns_positive_duration(self):
        duration = perf_compare.local_dict_benchmark(iterations=100)
        self.assertGreater(duration, 0)

    @patch("backend.perf_compare.requests.Session")
    def test_http_store_benchmark_calls_post_get_delete_per_iteration(self, mock_session):
        ok_response = MagicMock()
        ok_response.raise_for_status.return_value = None

        session_obj = MagicMock()
        session_obj.get.return_value = ok_response
        session_obj.post.return_value = ok_response
        session_obj.delete.return_value = ok_response

        # requests.Session() is used as a context manager.
        mock_session.return_value.__enter__.return_value = session_obj
        mock_session.return_value.__exit__.return_value = None

        duration = perf_compare.http_store_benchmark(
            server="http://127.0.0.1:8000",
            iterations=3,
            timeout=1.0,
        )

        self.assertGreater(duration, 0)
        self.assertEqual(session_obj.post.call_count, 3)
        self.assertEqual(session_obj.get.call_count, 4)
        self.assertEqual(session_obj.delete.call_count, 4)

    def test_format_report_contains_expected_fields(self):
        summary = {
            "server": "http://127.0.0.1:8000",
            "dict_iterations": 100,
            "http_iterations": 10,
            "dict_seconds": 0.5,
            "http_seconds": 2.0,
            "dict_total_ops": 300,
            "http_total_ops": 30,
            "dict_ops_per_sec": 600.0,
            "http_ops_per_sec": 15.0,
            "slowdown_ratio": 40.0,
        }

        report = perf_compare.format_report(summary)

        self.assertIn("Performance Report", report)
        self.assertIn("Dict runtime", report)
        self.assertIn("HTTP runtime", report)
        self.assertIn("Slowdown ratio", report)


if __name__ == "__main__":
    unittest.main()

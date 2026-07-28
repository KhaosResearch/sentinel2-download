import argparse
import unittest
from datetime import datetime
from unittest.mock import patch

from main_script_dask import _chunks, _positive_int, process_season


class DaskThrottleTests(unittest.TestCase):
    def test_chunks_tiles_by_max_in_flight_limit(self):
        self.assertEqual([["a", "b"], ["c", "d"], ["e"]], list(_chunks(["a", "b", "c", "d", "e"], 2)))

    def test_positive_int_rejects_zero(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            _positive_int("0")


class ProcessSeasonTests(unittest.TestCase):
    def test_returns_failed_status_with_error(self):
        with (
            patch("main_script_dask.seasonal_composite_exists", return_value=False),
            patch("main_script_dask.download_product_using_sentinel_api", side_effect=KeyError("value")),
            patch("main_script_dask.create_composite_by_tile_and_date"),
        ):
            result = process_season(
                2021,
                "spring",
                datetime(2021, 3, 1),
                datetime(2021, 4, 16),
                "28RFQ",
                cleanup_products=True,
            )

        self.assertEqual("failed", result["status"])
        self.assertEqual("28RFQ", result["tile"])
        self.assertIn("value", result["error"])

    def test_returns_finished_status(self):
        with (
            patch("main_script_dask.seasonal_composite_exists", return_value=False),
            patch("main_script_dask.download_product_using_sentinel_api"),
            patch("main_script_dask.create_composite_by_tile_and_date"),
        ):
            result = process_season(
                2021,
                "spring",
                datetime(2021, 3, 1),
                datetime(2021, 4, 16),
                "28RFQ",
                cleanup_products=True,
            )

        self.assertEqual(
            {"status": "finished", "tile": "28RFQ", "year": 2021, "season": "spring"},
            result,
        )


if __name__ == "__main__":
    unittest.main()

import unittest
from datetime import datetime
from unittest.mock import patch

from main_script_dask import process_season


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

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from main_script_europe import process_tile_month


class FakeMongoConnection:
    def get_collection_object(self):
        return object()

    def get_composite_collection_object(self):
        return object()


class FakeMinioConnection:
    bucket_name = "test-bucket"


class ProcessTileMonthTmpTest(unittest.TestCase):
    def test_restores_worker_tmp_dir_after_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker_tmp_dir = Path(tmp) / "worker-1"
            worker_tmp_dir.mkdir()

            with (
                patch.dict(os.environ, {"TMP_DIR": str(worker_tmp_dir)}),
                patch("main_script_europe.load_dotenv"),
                patch("main_script_europe.MongoConnection", return_value=FakeMongoConnection()),
                patch("main_script_europe.MinioConnection", return_value=FakeMinioConnection()),
                patch("main_script_europe.missing_indexes_for_month", return_value=[]),
            ):
                result = process_tile_month("26WPT", 2024, 1, ["NDWI"])

                self.assertEqual(result, "Skipped 26WPT 2024-01: monthly indexes already exist")
                self.assertEqual(os.environ["TMP_DIR"], str(worker_tmp_dir))
                self.assertEqual(list(worker_tmp_dir.glob("dask_*")), [])

    def test_restores_worker_tmp_dir_when_client_setup_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker_tmp_dir = Path(tmp) / "worker-1"
            worker_tmp_dir.mkdir()

            with (
                patch.dict(os.environ, {"TMP_DIR": str(worker_tmp_dir)}),
                patch("main_script_europe.load_dotenv"),
                patch("main_script_europe.MongoConnection", side_effect=RuntimeError("mongo unavailable")),
            ):
                with self.assertRaisesRegex(RuntimeError, "mongo unavailable"):
                    process_tile_month("26WPT", 2024, 1, ["NDWI"])

                self.assertEqual(os.environ["TMP_DIR"], str(worker_tmp_dir))
                self.assertEqual(list(worker_tmp_dir.glob("dask_*")), [])


if __name__ == "__main__":
    unittest.main()

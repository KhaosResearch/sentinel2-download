import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import rasterio
from rasterio.transform import from_origin

from ds_download.compute_composite import (
    _cleanup_month_folders_from_minio,
    seasonal_composite_exists,
    _write_composite_raster,
)


class SeasonalCompositeExistsTests(unittest.TestCase):
    def test_checks_seasonal_storage_prefix(self):
        composite_collection = Mock()
        composite_collection.find_one.return_value = {"title": "existing"}
        mongo_connection = Mock()
        mongo_connection.get_composite_collection_object.return_value = composite_collection

        with patch("ds_download.compute_composite.MongoConnection", return_value=mongo_connection):
            exists = seasonal_composite_exists("30STF", 2021, "Spring")

        self.assertTrue(exists)
        composite_collection.find_one.assert_called_once_with(
            {"S3BandsPrefix": {"$regex": "^30STF/2021/Spring/composites/"}}
        )


class CleanupMonthFoldersFromMinioTests(unittest.TestCase):
    def test_deletes_month_prefixes_in_date_range(self):
        minio_client = Mock()
        minio_client.bucket_name = "rasters"
        minio_client.list_objects.side_effect = [
            [Mock(object_name="30STF/2021/March/products/a.jp2")],
            [Mock(object_name="30STF/2021/April/composites/b.tif")],
        ]

        _cleanup_month_folders_from_minio(
            "30STF",
            datetime(2021, 3, 1),
            datetime(2021, 5, 1),
            minio_client,
        )

        self.assertEqual(
            [call.kwargs["prefix"] for call in minio_client.list_objects.call_args_list],
            ["30STF/2021/March/", "30STF/2021/April/"],
        )
        self.assertEqual(minio_client.remove_object.call_count, 2)


class WriteCompositeRasterTests(unittest.TestCase):
    def test_writes_masked_median_by_window(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            band_1 = temp_path / "band_1.tif"
            band_2 = temp_path / "band_2.tif"
            mask_1 = temp_path / "mask_1.tif"
            mask_2 = temp_path / "mask_2.tif"
            output = temp_path / "composite.tif"

            profile = {
                "driver": "GTiff",
                "height": 2,
                "width": 3,
                "count": 1,
                "dtype": "float32",
                "transform": from_origin(0, 2, 1, 1),
            }
            self._write_raster(band_1, [[1, 2, 0], [4, 5, 6]], profile)
            self._write_raster(band_2, [[3, 4, 7], [8, 10, 12]], profile)
            self._write_raster(mask_1, [[0, 1, 0], [0, 0, 0]], profile)
            self._write_raster(mask_2, [[0, 0, 0], [1, 0, 0]], profile)

            raster_mean = _write_composite_raster(
                [str(band_1), str(band_2)],
                output,
                method="median",
                cloud_mask_paths=[str(mask_1), str(mask_2)],
            )

            with rasterio.open(output) as raster:
                composite = raster.read()

            expected = np.array([[[2, 4, 7], [4, 7.5, 9]]], dtype=np.float32)
            np.testing.assert_allclose(composite, expected)
            self.assertAlmostEqual(float(np.nanmean(expected)), raster_mean, places=6)
            self.assertFalse(band_1.exists())
            self.assertFalse(band_2.exists())

    @staticmethod
    def _write_raster(path, values, profile):
        with rasterio.open(path, "w", **profile) as raster:
            raster.write(np.array([values], dtype=np.float32))


if __name__ == "__main__":
    unittest.main()

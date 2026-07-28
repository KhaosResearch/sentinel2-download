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
    _cleanup_composite_temp_paths,
    create_composite_by_tile_and_date,
    get_season_date_ranges,
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


class GetSeasonDateRangesTests(unittest.TestCase):
    def test_reads_seasons_from_app_data_json(self):
        loaded_ranges = [("spring", datetime(2021, 3, 1), datetime(2021, 4, 16))]

        with patch("ds_download.compute_composite._load_season_date_ranges", return_value=loaded_ranges) as load_ranges:
            self.assertEqual(loaded_ranges, get_season_date_ranges(2021))

        load_ranges.assert_called_once_with("app_data/seasons.json")


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


class CleanupCompositeTempPathsTests(unittest.TestCase):
    def test_removes_temp_files_and_product_dirs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            product_dir = temp_path / "S2A_MSIL2A_20210301T000000_NXXX_RXXX_T30STF_20210301T000000"
            composite_dir = temp_path / "S2S_MSIL2A_20210301T000000_NXXX_RXXX_T30STF_20210301T000000"
            product_file = product_dir / "raw" / "B04_10m.jp2"
            composite_file = composite_dir / "raw" / "B04_10m.tif"
            product_file.parent.mkdir(parents=True)
            composite_file.parent.mkdir(parents=True)
            product_file.write_text("band")
            composite_file.write_text("composite")

            _cleanup_composite_temp_paths(
                temp_dir,
                [product_dir.name],
                composite_dir.name,
                [product_file, composite_file],
            )

            self.assertFalse(product_dir.exists())
            self.assertFalse(composite_dir.exists())


class CreateCompositeByTileAndDateTests(unittest.TestCase):
    def test_calculates_product_indexes_after_max_product_selection(self):
        products = [
            {"title": f"S2A_MSIL2A_2021030{i}T000000_NXXX_RXXX_T30STF_2021030{i}T000000"}
            for i in range(1, 4)
        ]

        with (
            patch.dict("os.environ", {"MAX_PRODUCTS_COMPOSITE": "2"}),
            patch("ds_download.compute_composite.get_products_by_tile_and_date", return_value=products),
            patch("ds_download.compute_composite._get_composite", return_value=None),
            patch("ds_download.compute_composite._create_composite", return_value={"title": "composite"}),
            patch("ds_download.compute_composite.calculate_raw_index") as calculate_raw_index,
        ):
            create_composite_by_tile_and_date(
                calculate_raw_indexes=False,
                calculate_intermediate_products=False,
                tile="30STF",
                start_date=datetime(2021, 3, 1),
                end_date=datetime(2021, 6, 1),
                min_useful_data_percentage=30,
                include_product_indexes=True,
                period="seasonal",
            )

        self.assertEqual(
            [call.kwargs["product_title"] for call in calculate_raw_index.call_args_list],
            [products[0]["title"], products[1]["title"]],
        )


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

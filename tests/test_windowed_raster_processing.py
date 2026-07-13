import tempfile
import unittest
import unittest.mock
from datetime import datetime
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

from ds_download.band_arithmetic import ndvi, ndwi
from main_script_geojson import create_monthly_geojson_index_mean
from main_script_europe import build_monthly_composite_metadata, upsert_monthly_composite_metadata, write_windowed_mean


class FakeCompositeCollection:
    def __init__(self):
        self.calls = []

    def update_one(self, query, update, upsert=False):
        self.calls.append((query, update, upsert))


class FakeMinioClient:
    bucket_name = "test-bucket"

    def __init__(self, objects):
        self.objects = objects
        self.uploads = []

    def fget_object(self, bucket_name, object_name, file_path):
        Path(file_path).write_bytes(Path(self.objects[object_name]).read_bytes())

    def fput_object(self, bucket_name, object_name, file_path, content_type=None):
        self.uploads.append((bucket_name, object_name, Path(file_path), content_type))


class WindowedRasterProcessingTest(unittest.TestCase):
    def _write_float_raster(self, path: Path, data: np.ndarray) -> None:
        metadata = {
            "driver": "GTiff",
            "height": data.shape[0],
            "width": data.shape[1],
            "count": 1,
            "dtype": rasterio.float32,
            "crs": "EPSG:4326",
            "transform": from_origin(0, data.shape[0], 1, 1),
            "nodata": 0,
        }
        with rasterio.open(path, "w", **metadata) as dst:
            dst.write(data.astype(np.float32), 1)

    def test_write_windowed_mean_matches_nanmean_and_scaled_int_nodata(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            transform = from_origin(0, 4, 1, 1)
            metadata = {
                "driver": "GTiff",
                "height": 4,
                "width": 4,
                "count": 1,
                "dtype": rasterio.int16,
                "crs": "EPSG:4326",
                "transform": transform,
                "nodata": -32768,
            }

            source_1 = np.array(
                [
                    [1000, 2000, -32768, 4000],
                    [5000, -32768, 7000, 8000],
                    [9000, 10000, 11000, 12000],
                    [-32768, 14000, 15000, 16000],
                ],
                dtype=np.int16,
            )
            source_2 = np.array(
                [
                    [3000, -32768, 5000, 6000],
                    [7000, 8000, 9000, -32768],
                    [11000, 12000, -32768, 14000],
                    [15000, 16000, 17000, 18000],
                ],
                dtype=np.int16,
            )

            source_paths = []
            for idx, data in enumerate([source_1, source_2], start=1):
                source_path = tmp_path / f"source_{idx}.tif"
                with rasterio.open(source_path, "w", **metadata) as dst:
                    dst.write(data, 1)
                source_paths.append(source_path)

            output_path = tmp_path / "mean.tif"
            output_metadata = write_windowed_mean(source_paths, output_path, block_size=2)

            expected_sources = []
            for data in [source_1, source_2]:
                scaled = data.astype(np.float32)
                scaled[scaled == -32768] = np.nan
                expected_sources.append(scaled / 10000)
            expected = np.nanmean(np.stack(expected_sources), axis=0).astype(np.float32)

            with rasterio.open(output_path) as src:
                actual = src.read(1)

            self.assertEqual(output_metadata["dtype"], rasterio.float32)
            np.testing.assert_allclose(actual, expected, equal_nan=True)

    def test_ndvi_writes_windowed_output_and_returns_mean(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            red = np.array([[1, 2, 0], [4, 5, 6]], dtype=np.float32)
            nir = np.array([[3, 2, 7], [8, 0, 12]], dtype=np.float32)
            red_path = tmp_path / "red.tif"
            nir_path = tmp_path / "nir.tif"
            output_path = tmp_path / "ndvi.tif"
            self._write_float_raster(red_path, red)
            self._write_float_raster(nir_path, nir)

            mean_value = ndvi(red_path, nir_path, output=output_path)

            red_expected = red.copy()
            nir_expected = nir.copy()
            red_expected[red_expected == 0] = np.nan
            nir_expected[nir_expected == 0] = np.nan
            expected = (nir_expected - red_expected) / (nir_expected + red_expected)

            with rasterio.open(output_path) as src:
                actual = src.read(1)

            np.testing.assert_allclose(actual, expected, equal_nan=True)
            self.assertAlmostEqual(mean_value, float(np.nanmean(expected)))

    def test_ndwi_writes_windowed_output_and_returns_mean(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            green = np.array([[4, 0, 8], [2, 6, 10]], dtype=np.float32)
            nir = np.array([[2, 3, 8], [0, 2, 5]], dtype=np.float32)
            green_path = tmp_path / "green.tif"
            nir_path = tmp_path / "nir.tif"
            output_path = tmp_path / "ndwi.tif"
            self._write_float_raster(green_path, green)
            self._write_float_raster(nir_path, nir)

            mean_value = ndwi(green_path, nir_path, output=output_path)

            green_expected = green.copy()
            nir_expected = nir.copy()
            green_expected[green_expected == 0] = np.nan
            nir_expected[nir_expected == 0] = np.nan
            expected = (green_expected - nir_expected) / (green_expected + nir_expected)

            with rasterio.open(output_path) as src:
                actual = src.read(1)

            np.testing.assert_allclose(actual, expected, equal_nan=True)
            self.assertAlmostEqual(mean_value, float(np.nanmean(expected)))

    def test_monthly_composite_metadata_matches_original_composite_shape(self):
        product_titles = [
            "S2A_MSIL2A_20240302T131301_N0510_R081_T26WPT_20240302T155848",
            "S2B_MSIL2A_20240330T131709_N0510_R124_T26WPT_20240330T154559",
        ]

        metadata = build_monthly_composite_metadata(
            "26WPT",
            2024,
            3,
            product_titles,
            "europe-satellite-timeseries",
        )

        self.assertEqual(
            metadata,
            {
                "title": "S2S_MSIL2A_20240302_NXXX_RXXX_T26WPT_20240330_64081333",
                "products": [{"title": product_title} for product_title in product_titles],
                "first_date": datetime(2024, 3, 2),
                "last_date": datetime(2024, 3, 30),
                "S3Bucket": "europe-satellite-timeseries",
                "S3BandsPrefix": (
                    "26WPT/2024/March/composites/"
                    "S2S_MSIL2A_20240302_NXXX_RXXX_T26WPT_20240330_64081333/raw/"
                ),
                "tile": "26WPT",
            },
        )

    def test_upsert_monthly_composite_metadata_sets_index_key(self):
        composite_col = FakeCompositeCollection()
        product_titles = [
            "S2A_MSIL2A_20240302T131301_N0510_R081_T26WPT_20240302T155848",
            "S2B_MSIL2A_20240330T131709_N0510_R124_T26WPT_20240330T154559",
        ]
        index_key = (
            "26WPT/2024/March/composites/"
            "S2S_MSIL2A_20240302_NXXX_RXXX_T26WPT_20240330_64081333/indexes/ndvi.tif"
        )

        upsert_monthly_composite_metadata(
            composite_col,
            "26WPT",
            2024,
            3,
            product_titles,
            "europe-satellite-timeseries",
            "NDVI",
            index_key,
        )

        self.assertEqual(len(composite_col.calls), 1)
        query, update, upsert = composite_col.calls[0]
        self.assertEqual(query, {"title": "S2S_MSIL2A_20240302_NXXX_RXXX_T26WPT_20240330_64081333"})
        self.assertTrue(upsert)
        self.assertEqual(
            update["$set"]["indexes.ndvi"],
            {
                "name": "ndvi",
                "rasterS3Bucket": "europe-satellite-timeseries",
                "rasterS3Key": index_key,
            },
        )

    def test_geojson_monthly_mean_keeps_float_ndwi_and_ndvi_ground_truth(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            transform = from_origin(0, 2, 1, 1)
            metadata = {
                "driver": "GTiff",
                "height": 2,
                "width": 2,
                "count": 1,
                "dtype": rasterio.int16,
                "crs": "EPSG:32630",
                "transform": transform,
                "nodata": -32768,
            }

            product_titles = [
                "S2A_MSIL2A_20240101T105301_N0510_R051_T30SUF_20240101T125001",
                "S2B_MSIL2A_20240111T105259_N0510_R051_T30SUF_20240111T125417",
            ]
            source_values = {
                "p1_ndwi": np.array([[-2000, -1000], [-32768, -500]], dtype=np.int16),
                "p2_ndwi": np.array([[-1000, -3000], [-2000, -32768]], dtype=np.int16),
                "p1_ndvi": np.array([[3000, 4000], [-32768, 2000]], dtype=np.int16),
                "p2_ndvi": np.array([[1000, 2000], [5000, -32768]], dtype=np.int16),
            }
            object_paths = {}
            for object_name, data in source_values.items():
                path = tmp_path / f"{object_name}.tif"
                with rasterio.open(path, "w", **metadata) as dst:
                    dst.write(data, 1)
                object_paths[f"indexes/{object_name}.tif"] = path

            products_by_index = {
                "ndwi": [
                    {"title": product_titles[0], "indexes": {"ndwi": {"rasterS3Key": "indexes/p1_ndwi.tif"}}},
                    {"title": product_titles[1], "indexes": {"ndwi": {"rasterS3Key": "indexes/p2_ndwi.tif"}}},
                ],
                "ndvi": [
                    {"title": product_titles[0], "indexes": {"ndvi": {"rasterS3Key": "indexes/p1_ndvi.tif"}}},
                    {"title": product_titles[1], "indexes": {"ndvi": {"rasterS3Key": "indexes/p2_ndvi.tif"}}},
                ],
            }
            minio_client = FakeMinioClient(object_paths)
            composite_col = FakeCompositeCollection()

            with unittest.mock.patch(
                "main_script_geojson.get_geojson_monthly_index_products",
                side_effect=lambda geojson_path, year, month, index_name, mongo_col: products_by_index[index_name.lower()],
            ):
                ndwi_key = create_monthly_geojson_index_mean(
                    "test.geojson",
                    2024,
                    1,
                    "NDWI",
                    object(),
                    composite_col,
                    minio_client,
                    tmp_path / "monthly",
                )
                ndvi_key = create_monthly_geojson_index_mean(
                    "test.geojson",
                    2024,
                    1,
                    "NDVI",
                    object(),
                    composite_col,
                    minio_client,
                    tmp_path / "monthly",
                )

            self.assertTrue(ndwi_key.endswith("/indexes/ndwi.tif"))
            self.assertTrue(ndvi_key.endswith("/indexes/ndvi.tif"))
            self.assertEqual(len(minio_client.uploads), 2)

            uploaded = {Path(upload[2]).name: upload[2] for upload in minio_client.uploads}
            with rasterio.open(uploaded["ndwi.tif"]) as ndwi_src, rasterio.open(uploaded["ndvi.tif"]) as ndvi_src:
                ndwi = ndwi_src.read(1)
                ndvi = ndvi_src.read(1)

                self.assertEqual(ndwi_src.dtypes[0], "float32")
                self.assertEqual(ndvi_src.dtypes[0], "float32")
                np.testing.assert_allclose(
                    ndwi,
                    np.array([[-0.15, -0.2], [-0.2, -0.05]], dtype=np.float32),
                    equal_nan=True,
                )
                np.testing.assert_allclose(
                    ndvi,
                    np.array([[0.2, 0.3], [0.5, 0.2]], dtype=np.float32),
                    equal_nan=True,
                )


if __name__ == "__main__":
    unittest.main()

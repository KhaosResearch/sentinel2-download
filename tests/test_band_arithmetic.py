import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import rasterio
from rasterio.transform import from_origin

from ds_download import band_arithmetic


class ReadCacheTests(unittest.TestCase):
    def test_reuses_raster_reads_without_sharing_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            raster_path = Path(temp_dir, "band.tif")
            profile = {
                "driver": "GTiff",
                "height": 1,
                "width": 1,
                "count": 1,
                "dtype": "float32",
                "transform": from_origin(0, 1, 1, 1),
            }
            with rasterio.open(raster_path, "w", **profile) as raster:
                raster.write(np.array([[[1]]], dtype=np.float32))

            actual_open = rasterio.open
            open_count = 0

            def counting_open(*args, **kwargs):
                nonlocal open_count
                open_count += 1
                return actual_open(*args, **kwargs)

            band_arithmetic.clear_read_cache()
            with patch("ds_download.band_arithmetic.rasterio.open", counting_open):
                _, first_kwargs = band_arithmetic.read(raster_path)
                first_kwargs["driver"] = "changed"
                _, second_kwargs = band_arithmetic.read(raster_path)

            self.assertEqual(open_count, 1)
            self.assertEqual(second_kwargs["driver"], "GTiff")


if __name__ == "__main__":
    unittest.main()

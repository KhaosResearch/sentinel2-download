from pathlib import Path
from typing import Tuple

import numpy as np
import rasterio


def encode_geotiff(path: Path, raster_kind: str, quantize: bool) -> Tuple[Path, dict]:
    if not quantize:
        return path, {}

    path = Path(path)
    encoded_path = path.with_name(path.stem + "_quantized.tif")
    with rasterio.open(path) as src:
        data = src.read()
        meta = src.meta.copy()

    meta.update(driver="GTiff", compress="deflate", predictor=2, tiled=True, BIGTIFF="IF_SAFER")
    encoding = {"compressed": True, "quantized": True}

    if raster_kind == "index":
        nodata = -32768
        scale = 0.0001
        data = np.where(np.isnan(data), nodata, np.rint(data / scale))
        data = np.clip(data, -32767, 32767).astype(np.int16)
        meta.update(dtype="int16", nodata=nodata)
        encoding.update(dtype="int16", scale=scale, offset=0, nodata=nodata)
    elif raster_kind == "band":
        nodata = 0
        data = np.where(np.isnan(data), nodata, np.rint(data))
        data = np.clip(data, 0, 65535).astype(np.uint16)
        meta.update(dtype="uint16", nodata=nodata)
        encoding.update(dtype="uint16", scale=1, offset=0, nodata=nodata)
    else:
        encoding.update(dtype=str(data.dtype), scale=1, offset=0, nodata=meta.get("nodata"))

    with rasterio.open(encoded_path, "w", **meta) as dst:
        dst.write(data)

    return encoded_path, encoding

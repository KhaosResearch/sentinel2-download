from typing import Tuple

import numpy as np
import rasterio
from rasterio.warp import Resampling, reproject

def rescale_band(band: np.ndarray, kwargs: dict) -> Tuple[np.ndarray, dict]:
    """
    Rescale band image data to 10 meters per pixel resolution.

    :param band: Band image array data.
    :param kwargs: Band image metadata.
    :return: Rescaled band image array data and metadata.
    """
    img_resolution = kwargs["transform"][0]
    scale_factor = img_resolution / 10

    # Scale the image to a resolution of 10m per pixel
    if img_resolution != 10:
        new_kwargs = kwargs.copy()
        new_kwargs["height"] = int(kwargs["height"] * scale_factor)
        new_kwargs["width"] = int(kwargs["width"] * scale_factor)
        new_kwargs["transform"] = rasterio.Affine(
            10, kwargs["transform"][1], kwargs["transform"][2], kwargs["transform"][3], -10, kwargs["transform"][5]
        )

        rescaled_raster = np.ndarray(
            shape=(kwargs["count"], new_kwargs["height"], new_kwargs["width"]), dtype=np.float32
        )

        reproject(
            source=band,
            destination=rescaled_raster,
            src_transform=kwargs["transform"],
            src_crs=kwargs["crs"],
            dst_resolution=(new_kwargs["width"], new_kwargs["height"]),
            dst_transform=new_kwargs["transform"],
            dst_crs=new_kwargs["crs"],
            resampling=Resampling.nearest,
        )
        band = rescaled_raster
        kwargs = new_kwargs

    return band, kwargs
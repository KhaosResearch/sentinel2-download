import logging
import os
import re
import shutil
from collections import defaultdict
from datetime import datetime, timedelta
from hashlib import sha256
from itertools import compress
from pathlib import Path
from typing import Iterable, List, Tuple

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.windows import bounds, from_bounds


from os.path import join

from ds_download.minio_connection import MinioConnection, get_minio_bucket_name
from ds_download.mongo_connection import MongoConnection
from ds_download.observability import configure_logging
from ds_download.download_using_sentinel_api import _load_season_date_ranges

from ds_download.raw_index_calculation import calculate_raw_index
from ds_download.band_arithmetic import _rescale_band
from ds_download.raster_encoding import encode_geotiff

logger = logging.getLogger(__name__)

_PRODUCT_INDEXES = [
    "Moisture", "NDVI", "NDWI", "NDSI", "EVI", "OSAVI",
    "EVI2", "NDRE", "NDYI", "MNDWI", "BRI", "TCI",
    "RI", "BSI", "CRI1",
]

def get_products_by_tile_and_date(tile, start_date, end_date, min_useful_data_percentage):

    mongo_collection = MongoConnection().get_collection_object()

    pipeline = [
        {
            "$match": {
                "title": {"$regex": tile},
                "datetakeSensingTime": {"$gte": start_date, "$lt": end_date}
            }
        },
        {
            "$addFields": {
                "usefulPixelsPercentage": {
                    "$subtract": [
                        100,
                        {
                            "$add": [
                                {"$multiply": ["$intermediateProducts.cloudmask.rasterMeanValue", 100]},
                                "$noDataPercentage"
                            ]
                        }
                    ]
                }
            }
        },
        {
            "$match": {
                "$expr": {"$gt": ["$usefulPixelsPercentage", min_useful_data_percentage]}
            }
        },
        {
            "$sort": {"usefulPixelsPercentage": -1}
        }
    ]
    
    product_metadata_cursor = mongo_collection.aggregate(pipeline)

    return product_metadata_cursor

def _get_kwargs_raster(raster_path):
    """
    Get a raster's metadata from a raster's path
    """
    with rasterio.open(raster_path) as raster_file:
        kwargs = raster_file.meta
        return kwargs
    
def _get_product_rasters_paths(
    product_title: str, minio_client: MinioConnection, is_composite: bool
) -> Tuple[Iterable[str], Iterable[bool]]:
    """
    Get the paths to all rasters of a product in minio.
    Another list of boolean is returned, it points out if each raster is a sentinel band or not (e.g. index).
    """
    splits = product_title.split("_T")
    tile_id = str(splits[1][0:5])
    splits = product_title.split("_")
    year = splits[2][0:4]
    month = datetime.strptime(splits[2][4:6], "%m")

    rasters_folder = "composited" if is_composite else "products"
    minio_bucket = minio_client.bucket_name
    minio_dir = join(tile_id, year, month.strftime("%B"), rasters_folder, product_title, "")

    bands_dir = join(minio_dir, "raw", "")
    indexes_dir = join(minio_dir, "indexes", "")
    intermediate_products_dir = join(minio_dir, "intermediateProducts", "")

    bands_paths = minio_client.list_objects(minio_bucket, prefix=bands_dir)
    indexes_path = minio_client.list_objects(minio_bucket, prefix=indexes_dir)
    intermediate_products_path = minio_client.list_objects(
        minio_bucket, prefix=intermediate_products_dir
    )

    rasters = []
    is_band = []
    for index_path in indexes_path:
        rasters.append(index_path.object_name)
        is_band.append(False)
    for band_path in bands_paths:
        rasters.append(band_path.object_name)
        is_band.append(True)
    for intermediate_product_path in intermediate_products_path:    
        rasters.append(intermediate_product_path.object_name)
        is_band.append(False)

    return (rasters, is_band)

def _get_raster_filename_from_path(raster_path):
    """
    Get a filename from a raster's path
    """
    return raster_path.split("/")[-1]

def _get_raster_name_from_path(raster_path):
    """
    Get a raster's name from a raster's path
    """
    raster_filename = _get_raster_filename_from_path(raster_path)
    return raster_filename.split(".")[0].split("_")[0]

def _get_spatial_resolution_raster(raster_path):
    """
    Get a raster's spatial resolution from a raster's path
    """
    kwargs = _get_kwargs_raster(raster_path)
    return kwargs["transform"][0]

def _sentinel_date_to_datetime(date: str):
    """
    Parse a string date (YYYYMMDDTHHMMSS) to a sentinel datetime
    """
    date_datetime = datetime.strptime(date, '%Y%m%dT%H%M%S')
    return date_datetime

def _read_raster(
    band_path: str,
    rescale: bool = False,
    path_to_disk: str = None,
    to_tif: bool = True
):
    """
    Reads a raster as a numpy array.
    """

    with rasterio.open(band_path) as band_file:

        kwargs = band_file.meta
        band = band_file.read()

    # Just in case...
    if len(band.shape) == 2:
        band = band.reshape((kwargs["count"], *band.shape))

    # to_float may be better
    if to_tif:
        if kwargs["driver"] == "JP2OpenJPEG":
            band = band.astype(np.float32)
            kwargs["dtype"] = "float32"
            band = np.where(band == 0, np.nan, band)
            kwargs["nodata"] = np.nan
            kwargs["driver"] = "GTiff"

            if path_to_disk is not None:
                path_to_disk = path_to_disk[:-3] + "tif"

    if rescale:
        band, kwargs = _rescale_band(band, kwargs, 10)

    if path_to_disk is not None:
        with rasterio.open(path_to_disk, "w", **kwargs) as dst_file:
            dst_file.write(band)

    return band


def _composite(
    band_paths: List[str], method: str = "median", cloud_masks: List[np.ndarray] = None
) -> Tuple[np.ndarray, dict]:
    """
    Calculate the composite between a series of bands.

    Parameters:
        band_paths (List[str]) : List of paths to calculate the composite from.
        method (str) : To calculate the composite. Values: "median", "mean".
        cloud_masks (List[np.ndarray]) : Cloud masks of each band, cloudy pixels would not be taken into account for making the composite.

    Returns:
        (composite_out, composite_kwargs) (Tuple[np.ndarray, dict]) : Tuple containing the numpy array of the composed band, along with its kwargs.
    """
    if cloud_masks is None:
        cloud_masks = []

    composite_bands = []
    composite_kwargs = None
    for i in range(len(band_paths)):
        band_path = band_paths[i]
        if composite_kwargs is None:
            composite_kwargs = _get_kwargs_raster(band_path)

        if composite_kwargs["driver"] == "JP2OpenJPEG":
            composite_kwargs["dtype"] = "float32"
            composite_kwargs["nodata"] = np.nan
            composite_kwargs["driver"] = "GTiff"
        logger.debug("reading raster for composite", extra={"raster.path": band_path})
        band = _read_raster(band_path)

        # Remove nodata pixels
        band = np.where(band == 0, np.nan, band)
        if i < len(cloud_masks):
            cloud_mask = cloud_masks[i]
            # Remove cloud-related pixels
            band = np.where(cloud_mask == 1, np.nan, band)

        composite_bands.append(band)
        Path.unlink(Path(band_path))

    shapes = [np.shape(band) for band in composite_bands]

    # Check if all arrays are of the same shape
    if not np.all(np.array(list(map(lambda x: x == shapes[0], shapes)))):
        raise ValueError(f"Not all bands have the same shape\n{shapes}")
    elif method == "median":
        composite_out = np.nanmedian(composite_bands, axis=0)
    elif method == "mean":
        composite_out = np.nanmean(composite_bands, axis=0)
    else:
        raise ValueError(f"Method '{method}' is not recognized.")

    return (composite_out, composite_kwargs)


def _read_mask_window(mask_file, target_file, window):
    out_shape = (int(window.height), int(window.width))
    mask_window = window
    if (
        mask_file.width != target_file.width
        or mask_file.height != target_file.height
        or mask_file.transform != target_file.transform
    ):
        mask_window = from_bounds(
            *bounds(window, target_file.transform), transform=mask_file.transform
        )

    return mask_file.read(
        window=mask_window,
        out_shape=(1, *out_shape),
        resampling=Resampling.nearest,
    )[0].astype(bool)


def _write_composite_raster(
    band_paths: List[str],
    output_path: Path,
    method: str = "median",
    cloud_mask_paths: List[str] = None,
) -> float:
    if cloud_mask_paths is None:
        cloud_mask_paths = []

    band_files = []
    mask_files = []
    try:
        band_files = [rasterio.open(path) for path in band_paths]
        mask_files = [rasterio.open(path) for path in cloud_mask_paths]
        first_file = band_files[0]
        shapes = [
            (band_file.count, band_file.height, band_file.width)
            for band_file in band_files
        ]
        if any(shape != shapes[0] for shape in shapes):
            raise ValueError(f"Not all bands have the same shape\n{shapes}")

        kwargs_composite = first_file.meta.copy()
        kwargs_composite.update(driver="GTiff", dtype="float32", nodata=np.nan)
        output_path.parent.mkdir(exist_ok=True, parents=True)

        valid_sum = 0.0
        valid_count = 0
        with rasterio.open(output_path, "w", **kwargs_composite) as file_composite:
            for _, window in first_file.block_windows(1):
                composite_bands = []
                for i, band_file in enumerate(band_files):
                    band = band_file.read(window=window).astype(np.float32)
                    band = np.where(band == 0, np.nan, band)
                    if i < len(mask_files):
                        cloud_mask = _read_mask_window(mask_files[i], band_file, window)
                        band = np.where(cloud_mask.reshape((1, *cloud_mask.shape)), np.nan, band)
                    composite_bands.append(band)

                if method == "median":
                    composite_out = np.nanmedian(composite_bands, axis=0)
                elif method == "mean":
                    composite_out = np.nanmean(composite_bands, axis=0)
                else:
                    raise ValueError(f"Method '{method}' is not recognized.")

                file_composite.write(composite_out.astype(np.float32), window=window)
                valid_sum += float(np.nansum(composite_out))
                valid_count += int(np.count_nonzero(np.isfinite(composite_out)))

        return valid_sum / valid_count if valid_count else np.nan
    finally:
        for raster_file in band_files + mask_files:
            raster_file.close()
        for band_path in band_paths:
            Path.unlink(Path(band_path))


def _write_cloud_mask_raster(scl_path: str, output_path: str, scl_cloud_values: List[int]) -> None:
    with rasterio.open(scl_path) as scl_file:
        kwargs = scl_file.meta.copy()
        kwargs.update(driver="GTiff", dtype="uint8", nodata=0)
        with rasterio.open(output_path, "w", **kwargs) as mask_file:
            for _, window in scl_file.block_windows(1):
                scl_band = scl_file.read(window=window)
                mask_file.write(
                    np.isin(scl_band, scl_cloud_values).astype(np.uint8), window=window
                )


def _get_hash_composite(products_titles: List[str]) -> str:
    """
    Calculate the hash of a composite using its products' titles and the execution mode.
    """
    products_titles.sort()
    concat_titles = "".join(products_titles)
    hashed_titles = sha256(concat_titles.encode("utf-8")).hexdigest()
    return hashed_titles


def _get_title_composite(product_titles: List[str]) -> str:
    """
    Get the title of a composite.
    If the execution mode is training, the title will contain the "S2E" prefix, else it will be "S2S".
    """
    tiles = [product_title.split("_")[5] for product_title in product_titles]
    if not all(tile == tiles[0] for tile in tiles):
        raise ValueError("All products must have the same tile")
    tile = tiles[0]
    composite_hash = _get_hash_composite(product_titles)
    products_dates = [product_title.split("_")[2] for product_title in product_titles]
    first_product_date, last_product_date = min(products_dates), max(products_dates)
    first_product_date = first_product_date.split("T")[0]
    last_product_date = last_product_date.split("T")[0]
    prefix = "S2S"
    composite_title = f"{prefix}_MSIL2A_{first_product_date}_NXXX_RXXX_{tile}_{last_product_date}_{composite_hash[:8]}"
    return composite_title


# Intentar leer de mongo si existe algun composite con esos products
def _get_composite(
    products_metadata
) -> dict:
    """
    Search a composite metadata in mongo.
    """
    mongo_collection = MongoConnection().get_composite_collection_object()
    products_titles = [products_metadata["title"] for products_metadata in products_metadata]
    title_composite = _get_title_composite(products_titles)
    composite_metadata = mongo_collection.find_one({"title": title_composite})
    return composite_metadata


def seasonal_composite_exists(tile: str, year: int, season_name: str) -> bool:
    prefix = join(tile, str(year), season_name, "composites", "")
    mongo_collection = MongoConnection().get_composite_collection_object()
    return mongo_collection.find_one({"S3BandsPrefix": {"$regex": f"^{re.escape(prefix)}"}}) is not None


def _get_product_prefix(product_title: str) -> str:
    splits = product_title.split("_T")
    tile_id = str(splits[1][0:5])
    splits = product_title.split("_")
    year = splits[2][0:4]
    month = datetime.strptime(splits[2][4:6], "%m")
    return join(tile_id, year, month.strftime("%B"), "products", product_title, "")


def _cleanup_products_from_minio(products_metadata: Iterable[dict], minio_client: MinioConnection) -> None:
    bucket_name = minio_client.bucket_name
    mongo_collection = MongoConnection().get_collection_object()
    for product_metadata in products_metadata:
        product_title = product_metadata["title"]
        prefix = _get_product_prefix(product_title)
        objects = list(minio_client.list_objects(bucket_name, prefix=prefix, recursive=True))
        for obj in objects:
            minio_client.remove_object(bucket_name=bucket_name, object_name=obj.object_name)
        mongo_collection.update_many(
            {"title": product_title},
            {"$unset": {"indexes": "", "intermediateProducts": ""}},
        )
        logger.info(
            "deleted product objects after composite",
            extra={
                "s2.product": product_title,
                "minio.bucket": bucket_name,
                "minio.prefix": prefix,
                "object.count": len(objects),
            },
        )


def _cleanup_month_folders_from_minio(
    tile: str,
    start_date: datetime,
    end_date: datetime,
    minio_client: MinioConnection,
) -> None:
    bucket_name = minio_client.bucket_name
    month = datetime(start_date.year, start_date.month, 1)
    while month < end_date:
        prefix = join(tile, str(month.year), month.strftime("%B"), "")
        objects = list(minio_client.list_objects(bucket_name, prefix=prefix, recursive=True))
        for obj in objects:
            minio_client.remove_object(bucket_name=bucket_name, object_name=obj.object_name)
        logger.info(
            "deleted seasonal source month folder",
            extra={
                "s2.tile": tile,
                "minio.bucket": bucket_name,
                "minio.prefix": prefix,
                "object.count": len(objects),
            },
        )
        month = (month + timedelta(days=31)).replace(day=1)


def _cleanup_composite_temp_paths(
    tmp_dir: str,
    products_titles: Iterable[str],
    composite_title: str,
    temp_paths: Iterable[str | Path],
) -> None:
    for temp_path in temp_paths:
        path = Path(temp_path)
        if path.exists():
            path.unlink()

    temp_dirs = [
        *(Path(tmp_dir, title) for title in products_titles),
        Path(tmp_dir, composite_title),
    ]
    for temp_dir in temp_dirs:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _calculate_product_indexes(products_metadata: Iterable[dict], quantize_rasters: bool) -> None:
    for product_metadata in products_metadata:
        calculate_raw_index(
            product_title=product_metadata["title"],
            index=_PRODUCT_INDEXES,
            quantize_rasters=quantize_rasters,
        )


def _create_composite(
    products_metadata: Iterable[dict],
    method: str = "median",
    include_indexes: bool = False,
    period: str = "monthly",
    period_label: str = None,
    storage_year: int = None,
    storage_period: str = None,
    cleanup_products: bool = False,
    quantize_rasters: bool = False
) -> None:
    """
    Compose multiple Sentinel-2 products into a new product, this product is called "composite".
    Each band of the composite is computed using the pixel-wise median. Cloudy pixels of each product are not used in the median.
    Cloud masks are expanded if the execution mode is training, creating a composite with "S2E" prefix.
    If execution mode is predict, Sentinel's default cloud masks are used, creating a composite with "S2S" prefix.
    Once computed, the composite is stored in Minio, and its metadata in Mongo. 
    """

    product_minio_client = MinioConnection()
    product_bucket_name = product_minio_client.bucket_name
    composite_minio_client = MinioConnection(bucket_name=get_minio_bucket_name(is_composite=True))
    composite_bucket_name = composite_minio_client.bucket_name
    mongo_composites_collection = MongoConnection().get_composite_collection_object()

    products_metadata = list(products_metadata)
    products_titles = [product["title"] for product in products_metadata]
    products_dates = [product_title.split("_")[2] for product_title in products_titles]
    logger.info(
        "composite creation started",
        extra={
            "product.count": len(products_titles),
            "s2.products": products_titles,
            "pipeline.period": period,
            "pipeline.season": period_label,
        },
    )

    tmp_dir = os.environ.get("TMP_DIR")

    bands_paths_products = []
    indexes_paths_products = []
    cloud_masks_temp_paths = []
    cloud_mask_paths = {"10": [], "20": [], "60": []}
    scl_cloud_values = [3, 8, 9, 10]

    logger.info(
        "composite cloud mask download started",
        extra={
            "product.count": len(products_titles),
            "pipeline.period": period,
            "pipeline.season": period_label,
        },
    )
    for product_metadata in products_metadata:
        product_title = product_metadata["title"]

        (rasters_paths, is_band) = _get_product_rasters_paths(
            product_title, product_minio_client, False
        )
        bands_paths_product = list(compress(rasters_paths, is_band))
        indexes_paths_product = [
            raster_path
            for raster_path, raster_is_band in zip(rasters_paths, is_band)
            if not raster_is_band and "/indexes/" in raster_path
        ]
        bands_paths_products.append(bands_paths_product)
        indexes_paths_products.append(indexes_paths_product)

        # Download cloud masks in all different spatial resolutions
        for band_path in bands_paths_product:
            band_name = _get_raster_name_from_path(band_path)
            band_filename = _get_raster_filename_from_path(band_path)
            if "SCL" in band_name:
                temp_dir_product = f"{tmp_dir}/{product_title}"
                Path(temp_dir_product).mkdir(exist_ok=True, parents=True)
                temp_path_product_band = f"{temp_dir_product}/{band_filename}"
                product_minio_client.fget_object(product_bucket_name, band_path, str(temp_path_product_band))
                cloud_masks_temp_paths.append(temp_path_product_band)
                spatial_resolution = str(
                    int(_get_spatial_resolution_raster(temp_path_product_band))
                )
                cloud_mask_path = str(Path(temp_path_product_band).with_suffix(".tif"))
                _write_cloud_mask_raster(temp_path_product_band, cloud_mask_path, scl_cloud_values)
                cloud_masks_temp_paths.append(cloud_mask_path)
                cloud_mask_paths[spatial_resolution].append(cloud_mask_path)

    composite_title = _get_title_composite(products_titles)
    temp_path_composite = Path(tmp_dir, composite_title)

    uploaded_composite_band_paths = []
    uploaded_composite_index_paths = []
    uploaded_composite_indexes = {}
    temp_paths_composite_bands = []
    result = None
    try:
        if include_indexes and any(not paths for paths in indexes_paths_products):
            missing_titles = [
                product_metadata["title"]
                for product_metadata, paths in zip(products_metadata, indexes_paths_products)
                if not paths
            ]
            raise ValueError(f"Missing product index rasters for seasonal composite: {missing_titles}")

        rasters_to_compose = [("raw", bands_paths_products, True)]
        if include_indexes:
            rasters_to_compose.append(("indexes", indexes_paths_products, True))

        for minio_folder_name, raster_paths_products, apply_cloud_masks in rasters_to_compose:
            composite_bands_dict = defaultdict(list)
            for paths_product in raster_paths_products:
                for raster_path in paths_product:
                    raster_name = _get_raster_name_from_path(raster_path)
                    raster_filename = _get_raster_filename_from_path(raster_path)
                    if minio_folder_name == "raw" and "SCL" in raster_name:
                        continue
                    composite_bands_dict[raster_filename].append(raster_path)

            for band_filename, band_paths in composite_bands_dict.items():
                logger.info(
                    "composite raster started",
                    extra={
                        "s2.composite": composite_title,
                        "raster.name": band_filename,
                        "minio.folder": minio_folder_name,
                        "input.raster_count": len(band_paths),
                    },
                )

                if len(band_paths) != len(products_titles):
                    logger.warning(
                        "raster missing in some products; skipping composite raster",
                        extra={
                            "raster.name": band_filename,
                            "expected.product_count": len(products_titles),
                            "actual.product_count": len(band_paths),
                        },
                    )
                    continue

                band_name = _get_raster_name_from_path(band_paths[0])
                if minio_folder_name == "raw" and "SCL" in band_name:
                    continue
                temp_path_composite_band = Path(temp_path_composite, minio_folder_name, band_filename)

                temp_path_list = []

                for band_path in band_paths:
                    product_title = band_path.split("/")[4]

                    temp_dir_product = f"{tmp_dir}/{product_title}/{minio_folder_name}"
                    Path(temp_dir_product).mkdir(exist_ok=True, parents=True)
                    temp_path_product_band = f"{temp_dir_product}/{band_filename}"
                    product_minio_client.fget_object(product_bucket_name, band_path, str(temp_path_product_band))

                    temp_path_list.append(temp_path_product_band)

                temp_path_composite_band = str(temp_path_composite_band)
                if temp_path_composite_band.endswith(".jp2"):
                    temp_path_composite_band = temp_path_composite_band[:-3] + "tif"

                temp_path_composite_band = Path(temp_path_composite_band)

                temp_paths_composite_bands.append(temp_path_composite_band)
                spatial_resolution = str(
                    int(_get_spatial_resolution_raster(temp_path_list[0]))
                )
                raster_cloud_mask_paths = []
                if apply_cloud_masks:
                    raster_cloud_mask_paths = cloud_mask_paths[spatial_resolution]
                    if not raster_cloud_mask_paths and spatial_resolution == "10":
                        raster_cloud_mask_paths = cloud_mask_paths["20"]

                raster_mean_value = _write_composite_raster(
                    temp_path_list,
                    temp_path_composite_band,
                    method=method,
                    cloud_mask_paths=raster_cloud_mask_paths,
                )
                logger.info(
                    "composite raster written locally",
                    extra={
                        "s2.composite": composite_title,
                        "raster.name": band_filename,
                        "local.path": str(temp_path_composite_band),
                    },
                )

                # Upload raster to minio
                band_filename = band_filename[:-3] + "tif" if band_filename.endswith(".jp2") else band_filename
                raster_kind = "band" if minio_folder_name == "raw" or Path(band_filename).stem == "tci" else "index"
                upload_path, encoding = encode_geotiff(
                    temp_path_composite_band,
                    raster_kind,
                    quantize_rasters,
                )
                if upload_path != temp_path_composite_band:
                    temp_paths_composite_bands.append(upload_path)
                splits = composite_title.split("_T")
                tile_id = str(splits[1][0:5])
                splits = composite_title.split("_")
                year = storage_year or int(splits[2][0:4])
                month = datetime.strptime(splits[2][4:6], "%m")
                period_folder = storage_period or month.strftime("%B")
                minio_band_path = join(
                    tile_id,
                    str(year),
                    period_folder,
                    "composites",
                    composite_title,
                    minio_folder_name,
                    band_filename,
                )
                composite_minio_client.fput_object(
                    bucket_name=composite_bucket_name,
                    object_name=minio_band_path,
                    file_path=upload_path,
                    content_type="image/tif",
                )
                if minio_folder_name == "indexes":
                    uploaded_composite_index_paths.append(minio_band_path)
                    index_name = Path(band_filename).stem
                    uploaded_composite_indexes[index_name] = {
                        "name": index_name,
                        "rasterS3Bucket": composite_bucket_name,
                        "rasterS3Key": minio_band_path,
                        "rasterMeanValue": float(raster_mean_value),
                    }
                    if encoding:
                        uploaded_composite_indexes[index_name]["encoding"] = encoding
                else:
                    uploaded_composite_band_paths.append(minio_band_path)
                logger.info(
                    "composite raster uploaded",
                    extra={
                        "s2.composite": composite_title,
                        "raster.name": band_filename,
                        "local.path": str(temp_path_composite_band),
                        "minio.bucket": composite_bucket_name,
                        "minio.key": minio_band_path,
                    },
                )

        composite_metadata = dict()
        composite_metadata["title"] = composite_title
        composite_metadata["products"] = [{"title": products_title} for products_title in products_titles]
        composite_metadata["first_date"] = _sentinel_date_to_datetime(
            min(products_dates)
        )
        composite_metadata["last_date"] = _sentinel_date_to_datetime(
            max(products_dates)
        )
        composite_metadata["S3Bucket"] = composite_bucket_name
        composite_metadata["S3BandsPrefix"] = join(tile_id, str(year), period_folder, "composites", composite_title, "raw", "")
        if quantize_rasters:
            composite_metadata["rawEncoding"] = {
                "compressed": True,
                "quantized": True,
                "dtype": "uint16",
                "scale": 1,
                "offset": 0,
                "nodata": 0,
            }
        if include_indexes:
            composite_metadata["S3IndexesPrefix"] = join(tile_id, str(year), period_folder, "composites", composite_title, "indexes", "")
            composite_metadata["indexes"] = uploaded_composite_indexes
        composite_metadata["tile"] = tile_id
        composite_metadata["period"] = period
        composite_metadata["periodLabel"] = period_label
        composite_metadata["compositeMethod"] = method

        # Upload metadata to mongo
        logger.info(
            "composite metadata insert started",
            extra={"s2.composite": composite_title, "index.count": len(uploaded_composite_indexes)},
        )
        result = mongo_composites_collection.insert_one(composite_metadata)
        logger.info(
            "composite metadata inserted in mongo",
            extra={"s2.composite": composite_title, "mongo.id": str(result.inserted_id)},
        )

        if cleanup_products:
            _cleanup_products_from_minio(products_metadata, product_minio_client)

    except (Exception, KeyboardInterrupt) as e:
        logger.exception(
            "composite creation failed; removing uncompleted composite from minio",
            extra={"s2.composite": _get_title_composite(products_titles) if products_titles else None},
        )
        for composite_band in uploaded_composite_band_paths + uploaded_composite_index_paths:
            composite_minio_client.remove_object(
                bucket_name=composite_bucket_name, object_name=composite_band
            )
        products_titles = [
            products_metadata["title"] for products_metadata in products_metadata
        ]
        mongo_composites_collection.delete_one({"title": _get_title_composite(products_titles)})
        raise e

    finally:
        _cleanup_composite_temp_paths(
            tmp_dir,
            products_titles,
            composite_title,
            temp_paths_composite_bands + cloud_masks_temp_paths,
        )

    return composite_metadata

def create_composite_by_tile_and_date(
    calculate_raw_indexes: bool,
    calculate_intermediate_products: bool,
    tile: str, 
    start_date: datetime, 
    end_date: datetime, 
    min_useful_data_percentage: float,
    method: str = "median",
    include_product_indexes: bool = False,
    period: str = "monthly",
    period_label: str = None,
    storage_year: int = None,
    storage_period: str = None,
    cleanup_products: bool = False,
    quantize_rasters: bool = False
) -> None:
    """
    Create a composite by tile and date range.
    """
    configure_logging()
    logger.info(
        "composite lookup started",
        extra={
            "s2.tile": tile,
            "pipeline.start_date": start_date.isoformat(),
            "pipeline.end_date": end_date.isoformat(),
            "pipeline.period": period,
            "pipeline.season": period_label,
        },
    )
    products_metadata_cursor = get_products_by_tile_and_date(
        tile, start_date, end_date, min_useful_data_percentage
    )

    max_products = int(os.environ.get("MAX_PRODUCTS_COMPOSITE"))
    products_metadata = list(products_metadata_cursor)
    products_metadata = products_metadata[:max_products]
    logger.info(
        "composite products selected",
        extra={
            "s2.tile": tile,
            "product.count": len(products_metadata),
            "max.product_count": max_products,
            "include.product_indexes": include_product_indexes,
            "pipeline.period": period,
            "pipeline.season": period_label,
        },
    )
    if not products_metadata:
        if include_product_indexes:
            raise ValueError(f"No products with index rasters found for tile {tile} between {start_date} and {end_date}")
        raise ValueError(f"No products found for tile {tile} between {start_date} and {end_date}")
    
    composite_metadata = _get_composite(
                products_metadata
            )
    if composite_metadata is None:
        try:
            if include_product_indexes:
                _calculate_product_indexes(products_metadata, quantize_rasters)
            composite_metadata = _create_composite(
                products_metadata,
                method=method,
                include_indexes=include_product_indexes,
                period=period,
                period_label=period_label,
                storage_year=storage_year,
                storage_period=storage_period,
                cleanup_products=cleanup_products,
                quantize_rasters=quantize_rasters,
            )
        except Exception:
            logger.exception(
                "composite creation failed",
                extra={
                    "s2.tile": tile,
                    "pipeline.period": period,
                    "pipeline.season": period_label,
                    "product.count": len(products_metadata),
                },
            )
            raise
        
        # Check if there was another composite for the same month and tile
        if period == "monthly":
            mongo_composite_col = MongoConnection().get_composite_collection_object()
            cursor = mongo_composite_col.find({"$and":[{"tile":tile}, {"title":{"$regex":f"{start_date.year}{start_date.month:02}"}}]})
            composites_for_month_and_tile = list(cursor)
            if len(composites_for_month_and_tile) > 1:
                logger.warning(
                    "more than one composite found for same month and tile",
                    extra={"s2.tile": tile, "composite.count": len(composites_for_month_and_tile)},
                )
                for composite in composites_for_month_and_tile:
                    if composite["title"] != composite_metadata["title"]:
                        logger.warning(
                            "deleting duplicate composite metadata",
                            extra={"s2.composite": composite["title"]},
                        )
                        mongo_composite_col.delete_one({"_id":composite["_id"]})
    else:
        logger.info(
            "composite already exists in mongo",
            extra={"s2.composite": composite_metadata["title"], "s2.tile": tile},
        )
        if cleanup_products:
            _cleanup_products_from_minio(products_metadata, MinioConnection())

    composite_title = composite_metadata["title"]

    if calculate_intermediate_products:
        logger.info(
            "composite intermediate product calculation started",
            extra={"s2.composite": composite_title, "s2.tile": tile},
        )
        calculate_raw_index(
            product_title=composite_title,
            index=[
                "CloudMask",
            ],
            minio_folder_name="intermediateProducts",
            is_composite=True,
            quantize_rasters=quantize_rasters
        )

    if calculate_raw_indexes:
        logger.info(
            "composite raw index calculation started",
            extra={"s2.composite": composite_title, "s2.tile": tile},
        )
        calculate_raw_index(
            product_title=composite_title,
            index=[
                "Moisture",
                "NDVI",
                "NDWI",
                "NDSI",
                "EVI",
                "OSAVI",
                "EVI2",
                "NDRE",
                "NDYI",
                "MNDWI",
                "BRI",
                "TCI",
                "RI",
                "BSI",
                "CRI1"
            ],
            is_composite=True,
            quantize_rasters=quantize_rasters
        )

    if cleanup_products and period == "seasonal":
        _cleanup_month_folders_from_minio(tile, start_date, end_date, MinioConnection())


def get_season_date_ranges(year: int) -> List[Tuple[str, datetime, datetime]]:
    return _load_season_date_ranges("app_data/seasons.json")


def create_seasonal_composites_by_tile_and_year(
    tile: str,
    year: int,
    min_useful_data_percentage: float,
    cleanup_products: bool = True,
    quantize_rasters: bool = False,
) -> None:
    for season_name, start_date, end_date in get_season_date_ranges(year):
        create_composite_by_tile_and_date(
            calculate_raw_indexes=False,
            calculate_intermediate_products=False,
            tile=tile,
            start_date=start_date,
            end_date=end_date,
            min_useful_data_percentage=min_useful_data_percentage,
            method="mean",
            include_product_indexes=True,
            period="seasonal",
            period_label=season_name,
            storage_year=year,
            storage_period=season_name,
            cleanup_products=cleanup_products,
            quantize_rasters=quantize_rasters,
        )

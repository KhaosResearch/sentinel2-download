import os
from os.path import join
import shutil
from enum import Enum
from pathlib import Path
from datetime import datetime

import numpy as np
import rasterio
from dotenv import load_dotenv
from ds_download.band_arithmetic import (
    bri,
    bsi,
    cloud_mask,
    cri1,
    evi,
    evi2,
    moisture,
    mndwi,
    ndre,
    ndsi,
    ndvi,
    ndwi,
    ndyi,
    osavi,
    ri,
    true_color
)
from ds_download.minio_connection import MinioConnection
from ds_download.mongo_connection import MongoConnection

import structlog
logger = structlog.get_logger()

load_dotenv(".env")

INT16_NODATA = -32768

indexes_bands = dict(
    moisture={"b8a": "B8A_20m", "b11": "B11_20m"},
    ndvi={"b4": "B04_10m", "b8": "B08_10m"},
    ndwi={"b3": "B03_10m", "b8": "B08_10m"},
    ndsi={"b3": "B03_20m", "b11": "B11_20m"},
    evi={"b2": "B02_10m", "b4": "B04_10m", "b8": "B08_10m"},
    cloudmask={"scl": "SCL_20m"},
    osavi={"b4": "B04_10m", "b8": "B08_10m"},
    evi2={"b4": "B04_10m", "b8": "B08_10m"},
    ndre={"b5": "B05_60m", "b9": "B09_60m"},
    ndyi={"b2": "B02_10m", "b3": "B03_10m"},
    mndwi={"b3": "B03_20m", "b11": "B11_20m"},
    bri={"b3": "B03_10m", "b5": "B05_20m", "b8": "B08_10m"},
    bsi={"b2": "B02_10m", "b4": "B04_10m", "b8": "B08_10m", "b11": "B11_20m"},
    # tci={"b2": "B02_10m", "b3": "B03_10m", "b4": "B04_10m"},
    ri={"b3": "B03_10m", "b4": "B04_10m"},
    cri1={"b2": "B02_10m", "b3": "B03_10m"},
)


def compress_and_quantize_tiff(tif_path: str | Path) -> Path:
    """
    Rewrite a TIFF as compressed Int16 before uploading it to MinIO.

    Float rasters with values in [-1, 1] are scaled by 10000. Other numeric
    rasters are rounded directly into Int16. NaN values are stored as nodata.
    """
    tif_path = Path(tif_path)
    with rasterio.open(tif_path) as src:
        data = src.read()
        kwargs = src.meta.copy()
        source_nodata = src.nodata

    finite_mask = np.isfinite(data)
    if source_nodata is not None and np.isfinite(source_nodata):
        finite_mask &= data != source_nodata

    scale = 1
    if finite_mask.any():
        finite_values = data[finite_mask]
        has_fractional_values = np.any(~np.isclose(finite_values, np.rint(finite_values)))
        should_scale_normalized_float = (
            np.issubdtype(data.dtype, np.floating)
            and finite_values.min() >= -1
            and finite_values.max() <= 1
            and has_fractional_values
        )
        if should_scale_normalized_float:
            scale = 10000

    scaled = np.rint(data * scale)

    quantized = np.full(data.shape, INT16_NODATA, dtype=np.int16)
    quantized[finite_mask] = np.clip(
        scaled[finite_mask],
        INT16_NODATA + 1,
        np.iinfo(np.int16).max,
    ).astype(np.int16)

    kwargs.update(
        driver="GTiff",
        dtype=rasterio.int16,
        nodata=INT16_NODATA,
        compress="DEFLATE",
        predictor=2,
        zlevel=9,
    )
    with rasterio.open(tif_path, "w", **kwargs) as dst:
        dst.write(quantized)

    return tif_path


def find_product_image(band_name: str, product_title: str) -> Path:
    """
    Finds image matching a pattern in the product folder with glob.
    Prefers .tif (GeoJSON masked) over .jp2 (full tile).
    
    :param band_name: Band name to match (e.g., "B08_10m").
    :param product_title: Product title for the folder.
    :return: A Path object pointing to the first found image.
    """
    product_folder = join(os.environ.get("TMP_DIR"), product_title)
    
    # Try to find .tif first (GeoJSON mode)
    tif_matches = [f for f in Path(product_folder).glob("*" + band_name + "*.tif")]
    if tif_matches:
        return tif_matches[0]
    
    # Fall back to .jp2 (tile mode)
    jp2_matches = [f for f in Path(product_folder).glob("*" + band_name + "*.jp2")]
    if jp2_matches:
        return jp2_matches[0]
    
    raise FileNotFoundError(f"No band file found for {band_name} in {product_folder}")

def get_index(index_name, bands_dict, product_title, minio_folder_name, is_composite):

    band_extension = ".tif" if is_composite else ".jp2"

    indexes_folder = join(os.environ.get("TMP_DIR"), product_title, minio_folder_name)
    output = Path(indexes_folder + "/" + index_name + ".tif")
    try:
        if index_name == "moisture":
            index_value = moisture(
                b8a=find_product_image(bands_dict["b8a"], product_title),
                b11=find_product_image(bands_dict["b11"], product_title),
                output=output,
            )
        elif index_name == "ndvi":
            index_value = ndvi(
                b4=find_product_image(bands_dict["b4"], product_title),
                b8=find_product_image(bands_dict["b8"], product_title),
                output=output,
            )
        elif index_name == "ndwi":
            index_value = ndwi(
                b3=find_product_image(bands_dict["b3"], product_title),
                b8=find_product_image(bands_dict["b8"], product_title),
                output=output,
            )
        elif index_name == "ndsi":
            index_value = ndsi(
                b3=find_product_image(bands_dict["b3"], product_title),
                b11=find_product_image(bands_dict["b11"], product_title),
                output=output,
            )
        elif index_name == "evi":
            index_value = evi(
                b2=find_product_image(bands_dict["b2"], product_title),
                b4=find_product_image(bands_dict["b4"], product_title),
                b8=find_product_image(bands_dict["b8"], product_title),
                output=output,
            )
        elif index_name == "cloudmask":
            index_value = cloud_mask(
                scl=find_product_image(bands_dict["scl"], product_title),
                output=output,
            )
        elif index_name == "osavi":
            index_value = osavi(
                b4=find_product_image(bands_dict["b4"], product_title),
                b8=find_product_image(bands_dict["b8"], product_title),
                Y=0.16,
                output=output,
            )
        elif index_name == "evi2":
            index_value = evi2(
                b4=find_product_image(bands_dict["b4"], product_title),
                b8=find_product_image(bands_dict["b8"], product_title),
                output=output,
            )
        elif index_name == "ndre":
            index_value = ndre(
                b5=find_product_image(bands_dict["b5"], product_title),
                b9=find_product_image(bands_dict["b9"], product_title),
                output=output,
            )
        elif index_name == "ndyi":
            index_value = ndyi(
                b2=find_product_image(bands_dict["b2"], product_title),
                b3=find_product_image(bands_dict["b3"], product_title),
                output=output,
            )
        elif index_name == "mndwi":
            index_value = mndwi(
                b3=find_product_image(bands_dict["b3"], product_title),
                b11=find_product_image(bands_dict["b11"], product_title),
                output=output,
            )
        elif index_name == "bri":
            index_value = bri(
                b3=find_product_image(bands_dict["b3"], product_title),
                b5=find_product_image(bands_dict["b5"], product_title),
                b8=find_product_image(bands_dict["b8"], product_title),
                output=output,
            )
        elif index_name == "bsi":
            index_value = bsi(
                b2=find_product_image(bands_dict["b2"], product_title),
                b4=find_product_image(bands_dict["b4"], product_title),
                b8=find_product_image(bands_dict["b8"], product_title),
                b11=find_product_image(bands_dict["b11"], product_title),
                output=output,
            )
        elif index_name == "ri":
            index_value = ri(
                b3=find_product_image(bands_dict["b3"], product_title),
                b4=find_product_image(bands_dict["b4"], product_title),
                output=output,
            )
        elif index_name == "cri1":
            index_value = cri1(
                b2=find_product_image(bands_dict["b2"], product_title),
                b3=find_product_image(bands_dict["b3"], product_title),
                output=output,
            )
        elif index_name == "tci":
            index_value = true_color(
                b=find_product_image(bands_dict["b2"], product_title),
                g=find_product_image(bands_dict["b3"], product_title),
                r=find_product_image(bands_dict["b4"], product_title),
                output=output,
            )

        index_value = (
            np.nanmean(index_value)
            if index_value is not None and not isinstance(index_value, float)
            else index_value
        )
    except Exception as e:
        raise e

    minio_client = MinioConnection()
    minio_bucket_name = minio_client.bucket_name

    if is_composite:
        date = datetime.strptime(product_title.split('_')[2], "%Y%m%d")
    else:
        date = datetime.strptime(product_title.split('_')[2], "%Y%m%dT%H%M%S")
    year = date.strftime("%Y")
    month = date.strftime("%B")
    tile_id = product_title.split("_T")[1][0:5]
    minio_dir = join(tile_id, year, month, "")
    minio_dir = join(minio_dir, "products", "") if not is_composite else join(minio_dir, "composites", "")

    tif_minio_path = join(minio_dir, product_title, minio_folder_name ,index_name + ".tif")


    upload_path = compress_and_quantize_tiff(indexes_folder + "/" + index_name + ".tif")

    minio_client.fput_object(
        minio_bucket_name,
        tif_minio_path,
        upload_path,
        content_type="image/tif",
    )

    band = dict()
    for k, v in bands_dict.items():
        band[k] = {}
        band[k]["rasterS3Bucket"] = minio_bucket_name
        band[k]["rasterS3Key"] = join(minio_dir, product_title, "raw" , v + band_extension)

    index_dict = {
        "name": index_name,
        "rasterS3Bucket": minio_bucket_name,
        "rasterS3Key": tif_minio_path,
        "bands": band,
        "rasterMeanValue": float(index_value) if index_value is not None else index_value
    }

    return index_dict


def calculate_raw_index(
    product_title: str,
    index: list,
    minio_folder_name: str = "indexes",
    is_composite: bool = False
):
    """
    Example: python raw_index_calculation.py --uid dad7f379-de8c-49ec-b4cf-44348d0f418c --index ndvi --index ndsi --temp-dir ./data
    """
    
    # Connect with mongo
    if is_composite:
        mongo_col = MongoConnection().get_composite_collection_object()
        band_extension = ".tif"
        date = datetime.strptime(product_title.split('_')[2], "%Y%m%d")        
    else:
        mongo_col = MongoConnection().get_collection_object()
        band_extension = ".jp2"
        date = datetime.strptime(product_title.split('_')[2], "%Y%m%dT%H%M%S")

    # Search product metadata in Mongo
    product_data = mongo_col.find_one({"title": product_title})

    temp_dir = os.environ.get("TMP_DIR")
    product_local_folder = join(temp_dir, product_title)

    # Create folder to store tif files
    indexes_folder = join(product_local_folder, minio_folder_name)
    Path(indexes_folder).mkdir(exist_ok=True, parents=True)

    # Determine the Minio folder
    year = date.strftime("%Y")
    month = date.strftime("%B")
    tile_id = product_data["title"].split("_T")[1][0:5]
    minio_dir = join(tile_id, year, month, "")
    minio_dir = join(minio_dir, "products", "") if not is_composite else join(minio_dir, "composites", "")
    bands_dir = join(minio_dir, product_title, "raw", "")

    # Create dictionary of indexes
    index_dicts = {}

    if minio_folder_name not in product_data:
        product_data[minio_folder_name] = []

    minio_client = MinioConnection()
    minio_bucket_name = minio_client.bucket_name

    for idx in index:
        index_name = idx.lower()

        if index_name in product_data[minio_folder_name]:
            logger.warning("The index " + index_name + " is already calculated")
            continue


        logger.info("Calculating index " + index_name)

        for v in indexes_bands[index_name].values():

            # Try .tif first (GeoJSON masked), then .jp2 (full tile)
            local_band_path = join(product_local_folder, v + ".tif")
            minio_band_file_tif = v + ".tif"
            minio_band_file_jp2 = v + band_extension
            
            logger.debug(f"MINIO OBJECT (.tif): {join(bands_dir, minio_band_file_tif)}")
            logger.debug(f"MINIO OBJECT (.jp2): {join(bands_dir, minio_band_file_jp2)}")
            logger.debug(f"LOCAL OBJECT: {local_band_path}")
            
            if not os.path.exists(local_band_path):
                # Try .tif first
                try:
                    minio_client.fget_object(
                        minio_bucket_name,
                        join(bands_dir, minio_band_file_tif),
                        local_band_path
                    )
                    logger.debug(f"Downloaded .tif band: {minio_band_file_tif}")
                except Exception:
                    # Fall back to .jp2
                    local_band_path_jp2 = join(product_local_folder, v + band_extension)
                    try:
                        minio_client.fget_object(
                            minio_bucket_name,
                            join(bands_dir, minio_band_file_jp2),
                            local_band_path_jp2
                        )
                        local_band_path = local_band_path_jp2
                        logger.debug(f"Downloaded .jp2 band: {minio_band_file_jp2}")
                    except Exception as e:
                        logger.error(f"Could not download band {v}: {e}")
                        raise

        dict_key = index_name.replace("-", "")
        index_dicts[index_name] = get_index(
            index_name=index_name,
            bands_dict=indexes_bands[dict_key],
            product_title=product_title,
            minio_folder_name=minio_folder_name,
            is_composite=is_composite
        )


    # merge existing indexes in the product_data with the new ones
    index_dicts.update(product_data[minio_folder_name])

    # Push the index_dicts to MongoDB
    mongo_col.update_one(
        {"title": product_title},
        {"$set": {minio_folder_name: index_dicts}}
    )

    # Remove product from local folder
    try:
        shutil.rmtree(product_local_folder)
    except OSError as e:
        logger.exception("Error: %s - %s." % (e.filename, e.strerror))

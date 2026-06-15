import os
import shutil
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

import numpy as np
import rasterio

import geopandas as gpd

from rasterio.io import MemoryFile
from rasterio.mask import mask
from google.cloud import storage
from os.path import join
import geojson
import json

from ds_download.mongo_connection import MongoConnection
from ds_download.minio_connection import MinioConnection
from ds_download.raw_index_calculation import calculate_raw_index
import structlog
logger = structlog.get_logger()

# Load environment variables
load_dotenv(".env")


def calculate_no_data(folder: str, is_geojson: bool = False) -> float:
    """
    Calculate the percentage of no-data pixels in the folder of a downloaded Sentinel-2 product.

    Args:
        folder (str): The path to the folder containing JP2 images.

    Returns:
        float: The percentage of no-data pixels in the image.
    """
    # logger.debug(f"FOLDER: {folder}")
    # band_dir = list(Path(folder).glob("GRANULE/*/IMG_DATA/R10m/*.jp2"))
    # logger.debug(f"BAND DIR: {band_dir}")
    if is_geojson: 
        tif_options = list(Path(folder).glob(f"*B0*.tif")) or list(Path(folder).glob(f"*.tif"))
        band_dir = tif_options[0]
    else:
        band_dir = list(Path(folder).glob("GRANULE/*/IMG_DATA/R10m/*.jp2"))[0]
    with rasterio.open(band_dir) as band:
        n_pixels = np.prod(band.shape)
        no_data_count = n_pixels - np.count_nonzero(band.read(1))
        percentage = (no_data_count * 100) / n_pixels

    return percentage


def _matches_required_band(blob_name: str, required_bands: list[str] = None) -> bool:
    if required_bands is None:
        return True
    return any(f"_{band}." in blob_name for band in required_bands)


def load_geojson_shapes(geojson_path: str) -> list[dict]:
    """
    Load geometry shapes from a GeoJSON file.

    Args:
        geojson_path (str): Path to the GeoJSON file.

    Returns:
        list[dict]: List of geometry dictionaries from the GeoJSON features.
    """
    with open(geojson_path) as f:
        geojson_obj = geojson.load(f)

    if isinstance(geojson_obj, dict) and geojson_obj.get("features"):
        shapes = [feature["geometry"] for feature in geojson_obj["features"] if feature.get("geometry")]
    elif isinstance(geojson_obj, dict) and geojson_obj.get("type"):
        shapes = [geojson_obj]
    else:
        raise ValueError(f"GeoJSON file does not contain a valid geometry: {geojson_path}")

    if not shapes:
        raise ValueError(f"GeoJSON file contains no valid geometry shapes: {geojson_path}")

    return shapes


def mask_jp2_to_geotiff(
    jp2_bytes: bytes,
    geojson_path: str,
    output_path: Path,
) -> bool:
    """
    Mask a JP2 raster to a GeoJSON geometry and write as GeoTIFF.
    Dynamically handles CRS projection transformation between WGS84 and UTM.

    Args:
        jp2_bytes (bytes): Binary content of JP2 file from Google Cloud.
        geojson_path (str): Path to GeoJSON file with target geometry.
        output_path (Path): Local path to write masked GeoTIFF.

    Returns:
        bool: True if mask was successful, False if AOI doesn't intersect raster.
        """
    try:
        # Load the target vectors using GeoPandas to make re-projection seamless
        gdf = gpd.read_file(geojson_path)
        
        with MemoryFile(jp2_bytes) as jp2_memfile:
            with jp2_memfile.open() as jp2_src:
                
                # 1. Dynamically match the vector geometry projection to the raster band's projection
                gdf_reprojected = gdf.to_crs(jp2_src.crs)
                
                # 2. Extract ALL geometry objects from GeoDataFrame using __geo_interface__
                # This works natively for Polygon, MultiPolygon, and multiple rows.
                shapes = [geom.__geo_interface__ for geom in gdf_reprojected.geometry if geom is not None]
                
                if not shapes:
                    logger.warning(f"No valid geometries in {geojson_path}")
                    return False
                
                try:
                    # Execute crop operations on matching coordinate grids
                    out_image, out_transform = mask(jp2_src, shapes, crop=True)
                except ValueError as e:
                    # AOI physically does not overlap this specific tile's imagery boundaries
                    logger.debug(f"AOI does not intersect JP2 tile: {e}")
                    return False

                if out_image.size == 0:
                    logger.warning(f"Masked output is empty for {geojson_path}")
                    return False

                # Ensure disk target folder exists safely
                output_path.parent.mkdir(parents=True, exist_ok=True)
                
                # 3. Duplicate profile metadata and configure output compression properties
                out_meta = jp2_src.meta.copy()
                out_meta.update(
                    driver="GTiff",
                    height=out_image.shape[1],
                    width=out_image.shape[2],
                    transform=out_transform,
                    compress="deflate",
                )
                
                with rasterio.open(output_path, "w", **out_meta) as dst:
                    dst.write(out_image)
                    
                return True
                
    except Exception as e:
        logger.warning(f"Failed to mask JP2 to GeoTIFF due to pipeline execution error: {e}")
        return False

def _matches_required_band(blob_name: str, required_bands: list[str] = None) -> bool:
    if required_bands is None:
        return True
    return any(f"_{band}." in blob_name for band in required_bands)


def download_one_google_cloud(
    calculate_raw_indexes: bool,
    calculate_intermediate_products: bool,
    product_title: str,
    sentinel_metadata: dict = {},
    required_bands: list[str] = None,
    is_geojson: bool = False,
    geojson_path: str = None,
) -> None:
    """
    Download a Sentinel-2 product from Google Cloud, process it, and upload to MinIO and Mongo.

    For tile mode: downloads full JP2 bands to MinIO.
    For GeoJSON mode: masks JP2 to AOI, uploads as GeoTIFF to MinIO.

    Args:
        calculate_raw_indexes (bool): Whether to calculate raw indexes for the product.
        calculate_intermediate_products (bool): Whether to calculate intermediate products for the product.
        product_title (str): The title of the Sentinel-2 product to download.
        sentinel_metadata (dict, optional): Optional metadata from the Sentinel API.
        required_bands (list[str], optional): Sentinel band filenames to download, for example ["B03_10m", "B08_10m"].
        is_geojson (bool): Whether this is a GeoJSON AOI download (vs. full tile).
        geojson_path (str): Path to GeoJSON file if is_geojson=True.

    Returns:
        None
    """

    tmp_dir = os.environ.get("TMP_DIR")

    # Extract google cloud blob name
    splits = product_title.split("_T")
    tile_id = str(splits[1][0:5])
    tile_number = str(splits[1][0:2])
    tile_type = str(splits[1][2])
    tile_subtype = str(splits[1][3:5])
    list_of_names = ["L2/tiles", tile_number, tile_type, tile_subtype, product_title + ".SAFE"]
    source_blob_name = join(*list_of_names)

    # Find in google cloud
    storage_client = storage.Client()
    bucket_name = os.environ.get("GOOGLE_CLOUD_BUCKET_NAME")

    bucket = storage_client.bucket(bucket_name)
    blobs = list(bucket.list_blobs(prefix=source_blob_name))

    if len(blobs) == 0:
        logger.warning(f"Product {product_title} not found in Google Cloud.")
        logger.warning(f"Path in cloud storage should be {source_blob_name}, but it was not found.")
        logger.warning("Trying to find alternative name for the same product (different product discriminator)")

        product_title_without_discriminator = "_".join(product_title.split("_")[:3])
        list_of_names[-1] = product_title_without_discriminator
        source_blob_alternative_name = join(*list_of_names)
        blobs = list(bucket.list_blobs(prefix=source_blob_alternative_name))

        if len(blobs) == 0:
            logger.debug("Alternative name not found in Google Cloud. Skipping product.")
            return
        else:
            logger.debug("Alternative name found in Google Cloud")
            product_title = blobs[0].name.split("/")[-2].replace(".SAFE", "")
            list_of_names = ["L2/tiles", tile_number, tile_type, tile_subtype, product_title + ".SAFE"]
            source_blob_name = join(*list_of_names)
            logger.debug(f"Alternative product title is: {product_title}")


    # Connect with MongoDB
    mongo_col = MongoConnection().get_collection_object()

    # Connect with MinIO
    minio_client = MinioConnection()

    # Check if the product is already in Mongo and MinIO
    product_mongo_data = mongo_col.find_one({"title": product_title})
    
    splits_check = product_title.split("_")
    year = splits_check[2][0:4]
    month = datetime.strptime(splits_check[2][4:6], "%m")
    minio_dir = join(tile_id, year, month.strftime("%B"), "products", "")

    minio_found = False
    try:
        minio_found = False
        objects = minio_client.list_objects(minio_client.bucket_name, prefix=join(minio_dir, product_title), recursive=False)
        for _ in objects:
            minio_found = True
            break
    except Exception:
        logger.exception(f"Product not found in MinIO")

    if product_mongo_data and minio_found:
        logger.debug("The product is already in MongoDB and MinIO. Nothing to do.")
    else:
        unzip_folder = join(tmp_dir, product_title, "")

        # Download from Google Cloud
        bucket = storage_client.bucket(bucket_name)
        blobs = list(bucket.list_blobs(prefix=source_blob_name))

        product_folder = Path(source_blob_name).name
        local_images = []
        
        for blob in blobs:
            if blob.name.endswith("/") or "IMG_DATA" not in blob.name:  # Ignore folders
                continue
            if not _matches_required_band(blob.name, required_bands):
                continue

            image_name = os.path.basename(blob.name).split("_")[-2:]
            image_name = "_".join(image_name)

            logger.debug(f'BLOB NAME: {blob.name}')
            logger.info(f"Processing {image_name} for {product_title}")

            if is_geojson and geojson_path:
                # GeoJSON mode: mask JP2 to AOI and save as GeoTIFF
                jp2_bytes = blob.download_as_bytes()
                local_tif_path = Path(tmp_dir) / product_title / image_name.replace(".jp2", ".tif")
                success = mask_jp2_to_geotiff(jp2_bytes, geojson_path, local_tif_path)
                if not success:
                    logger.warning(f"AOI does not intersect band {image_name} for {product_title}. Skipping.")
                    continue

                # Track for verification and upload
                local_images.append((local_tif_path, image_name.replace(".jp2", ".tif")))
            else:
                # Tile mode: download JP2 as-is to local storage
                output_folder = Path(tmp_dir, product_folder)
                local_blob_name = Path(blob.name[len(source_blob_name + "/"):] if blob.name.startswith(source_blob_name + "/") else blob.name)
                local_blob_path = output_folder / local_blob_name
                local_blob_path = Path(str(local_blob_path).replace(".SAFE", ""))

                local_blob_path.parent.mkdir(parents=True, exist_ok=True)
                if not Path.is_file(local_blob_path):
                    blob.download_to_filename(str(local_blob_path))
                else:
                    logger.debug("File exists, skipping.")
                if Path.is_file(local_blob_path):
                    local_images.append((local_blob_path, image_name))

        # Check if we got any bands
        if not local_images:
            logger.warning(f"No bands processed for {product_title}. Skipping.")
            return
        else:
            # Verify required bands
            downloaded_band_names = {"_".join(os.path.basename(os.path.splitext(image[-1])[0]).split("_")[-2:]).replace(".jp2", "") for image in local_images}


        logger.debug(f"REQUIRED BANDS: {required_bands}")
        logger.debug(f"DOWNLOADED BANDS: {downloaded_band_names}")
        if required_bands and not set(required_bands).issubset(downloaded_band_names):
            missing_bands = sorted(set(required_bands) - downloaded_band_names)
            logger.warning(f"Missing required bands for {product_title}: {missing_bands}. Skipping product.")
            return

        # Upload bands to MinIO
        for local_path, image_name in local_images:
            logger.info(f"Uploading {image_name} to MinIO")
            content_type = "image/tif" if image_name.endswith(".tif") else "image/jp2"
            minio_client.fput_object(
                minio_client.bucket_name,
                minio_dir + product_title + "/raw/" + image_name,
                str(local_path),
                content_type=content_type,
            )

        try:
            objects = minio_client.list_objects(minio_client.bucket_name, prefix=join(minio_dir, product_title), recursive=False)
            for _ in objects:
                break
        except Exception:
            logger.exception(f"Product not uploaded to MinIO")
            return

        # Insert metadata into MongoDB
        metadata = {}
        if sentinel_metadata:
            metadata["s2APIMetadata"] = sentinel_metadata

        metadata["dataSource"] = {
                                "googleCloudStorage": {
                                    "bucketName": "gcp-public-data-sentinel-2",
                                    "prefix": source_blob_name
                                    }
                                }
        
        metadata["title"] = product_title
        metadata["S3Bucket"] = minio_client.bucket_name
        metadata["S3BandsPrefix"] = join(minio_dir, product_title, "raw", "")
        metadata["noDataPercentage"] = calculate_no_data(unzip_folder, is_geojson)
        metadata["datetakeSensingTime"] = datetime.strptime(product_title.split("_")[2], "%Y%m%dT%H%M%S")
        metadata["tile"] = tile_id
        mongo_col.insert_one(metadata)

        # Clean up
        try:
            if Path.is_dir(Path(unzip_folder)):
                shutil.rmtree(unzip_folder)
        except OSError as e:
            logger.exception(f"Error: {e.filename} - {e.strerror}.")

    if calculate_intermediate_products:
        calculate_raw_index(
            product_title=product_title,
            index=["CloudMask"],
            minio_folder_name="intermediateProducts",
        )

    if calculate_raw_indexes:
        calculate_raw_index(
            product_title=product_title,
            index=[
                "NDVI", "NDWI", 
                # "Moisture", "NDSI", "EVI", "OSAVI",
                # "EVI2", "NDRE", "NDYI", "MNDWI", "BRI",
                # "TCI", 
                # "RI", "BSI", "CRI1"
            ],
        )

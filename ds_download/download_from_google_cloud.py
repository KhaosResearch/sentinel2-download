import os
import shutil
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
import numpy as np
import rasterio
from google.cloud import storage
from os.path import join

from ds_download.mongo_connection import MongoConnection
from ds_download.minio_connection import MinioConnection
from ds_download.raw_index_calculation import calculate_raw_index

# Load environment variables
load_dotenv(".env")


def calculate_no_data(folder: str) -> float:
    """
    Calculate the percentage of no-data pixels in the folder of a downloaded Sentinel-2 product.

    Args:
        folder (str): The path to the folder containing JP2 images.

    Returns:
        float: The percentage of no-data pixels in the image.
    """
    band_dir = list(Path(folder).glob("GRANULE/*/IMG_DATA/R10m/*.jp2"))[0]
    with rasterio.open(band_dir) as band:
        n_pixels = np.prod(band.shape)
        no_data_count = n_pixels - np.count_nonzero(band.read(1))
        percentage = (no_data_count * 100) / n_pixels

    return percentage


def download_one_google_cloud(
    calculate_raw_indexes: bool,
    calculate_intermediate_products: bool,
    product_title: str,
    sentinel_metadata: dict = {}
) -> None:
    """
    Download a Sentinel-2 product from Google Cloud, process it, and upload to MinIO and Mongo.

    Args:
        calculate_raw_indexes (bool): Whether to calculate raw indexes for the product.
        calculate_intermediate_products (bool): Whether to calculate intermediate products for the product.
        product_title (str): The title of the Sentinel-2 product to download.
        sentinel_metadata (dict, optional): Optional metadata from the Sentinel API.

    Returns:
        None
    """
    # Connect with MongoDB
    mongo_col = MongoConnection().get_collection_object()

    # Connect with MinIO
    minio_client = MinioConnection()

    # Connect with Google Cloud
    storage_client = storage.Client()
    bucket_name = os.environ.get("GOOGLE_CLOUD_BUCKET_NAME")

    tmp_dir = os.environ.get("TMP_DIR")

    splits = product_title.split("_T")
    tile_id = str(splits[1][0:5])
    tile_number = str(splits[1][0:2])
    tile_type = str(splits[1][2])
    tile_subtype = str(splits[1][3:5])
    list_of_names = ["L2/tiles", tile_number, tile_type, tile_subtype, product_title + ".SAFE"]
    source_blob_name = join(*list_of_names)

    # Check if the product is already in Mongo and MinIO
    product_mongo_data = mongo_col.find_one({"title": product_title})
    
    splits_check = product_title.split("_")
    year = splits_check[2][0:4]
    month = datetime.strptime(splits_check[2][4:6], "%m")
    minio_dir = join(tile_id, year, month.strftime("%B"), "products", "")

    try:
        objects = minio_client.list_objects(minio_client.bucket_name, prefix=join(minio_dir, product_title), recursive=False)
        for _ in objects:
            minio_found = True
            break
    except Exception:
        print(f"Product not found in MinIO")
        minio_found = False

    if product_mongo_data and minio_found:
        print("The product is already in MongoDB and MinIO. Nothing to do.")
    else:
        unzip_folder = join(tmp_dir, product_title, "")

        # Download from Google Cloud
        bucket = storage_client.bucket(bucket_name)
        blobs = list(bucket.list_blobs(prefix=source_blob_name))

        product_folder = Path(source_blob_name).name

        if len(blobs) == 0:
            print("Product not found in Google Cloud")
            print(f"Failing blob name: {source_blob_name} title: {product_title}")
            return
        
        for blob in blobs:
            if blob.name.endswith("/") or "IMG_DATA" not in blob.name:  # Ignore folders and GCloud files
                continue
            print(blob.name)

            # Prepare path to local file
            output_folder = join(tmp_dir, product_folder)
            local_blob_name = Path(blob.name[len(source_blob_name + "/"):] if blob.name.startswith(source_blob_name + "/") else blob.name)
            local_blob_path = output_folder / local_blob_name
            local_blob_path = Path(str(local_blob_path).replace(".SAFE", ""))

            # Ensure output folder exists
            local_blob_path.parent.mkdir(parents=True, exist_ok=True)

            # Download if file doesn't exist
            if not Path.is_file(local_blob_path):
                blob.download_to_filename(local_blob_path)
            else:
                print("File exists, skipping.")

        # Upload bands to MinIO
        images = Path(unzip_folder).rglob("*.jp2")
        for image in images:
            image_name = os.path.basename(image).split("_", 2)[2]
            print(f"Uploading {image_name} to MinIO")
            minio_client.fput_object(
                minio_client.bucket_name,
                minio_dir + product_title + "/raw/" + image_name,
                image,
                content_type="image/jp2",
            )

        try:
            objects = minio_client.list_objects(minio_client.bucket_name, prefix=join(minio_dir, product_title), recursive=False)
            for _ in objects:
                break
        except Exception:
            print(f"Product not uploaded to MinIO")
            return

        # Insert metadata into MongoDB
        metadata = sentinel_metadata if sentinel_metadata else {}
        metadata["title"] = product_title
        metadata["minioBucket"] = minio_client.bucket_name
        metadata["minioBandsPath"] = join(minio_dir, product_title, "raw", "")
        metadata["noDataPercentage"] = calculate_no_data(unzip_folder)
        metadata["datetakeSensingTime"] = datetime.strptime(product_title.split("_")[2], "%Y%m%dT%H%M%S")
        mongo_col.insert_one(metadata)

        # Clean up
        try:
            if Path.is_dir(Path(unzip_folder)):
                shutil.rmtree(unzip_folder)
        except OSError as e:
            print(f"Error: {e.filename} - {e.strerror}.")

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
                "Moisture", "NDVI", "NDWI", "NDSI", "EVI", "OSAVI",
                "EVI2", "NDRE", "NDYI", "MNDWI", "BRI", "TCI", 
                "RI", "BSI", "CRI1"
            ],
        )

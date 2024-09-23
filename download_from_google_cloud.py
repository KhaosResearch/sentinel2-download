import os
import shutil
import datetime
import numpy as np
import rasterio
from datetime import datetime
from pathlib import Path
from operator import mul
from dotenv import load_dotenv
from google.cloud import storage
from raw_index_calculation import calculate_raw_index
from os.path import join

from mongo_connection import MongoConnection
from minio_connection import MinioConnection    

load_dotenv()


def get_gcloud_path(title: str) -> str:
    """
    Gets the Google cloud bucket prefix for a given Sentinel-2 product.
    Products are group by tile id or MGRS coordinates.

    :param title: Sentinel-2 title.
    :return: Google Cloud path for title.
    """
    tile_id = title.split("_T")[1]  # This is always a 2 digit and 3 letter ID  E.g: 30SUF
    tile_number = str(tile_id[0:2])
    tile_type = str(tile_id[2])
    tile_subtype = str(tile_id[3:5])
    return "/".join(["L2/tiles", tile_number, tile_type, tile_subtype, f"{title}.SAFE"])

def calculate_no_data(folder: str) -> float:
    
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
):

    # Connect with mongo
    mongo_col = MongoConnection().get_collection_object()

    # Connect with minio
    minio_client = MinioConnection()

    # Connect with google cloud
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

    # Check if the product is already downloaded (metadata in mongo and folder existing in minio)
    product_mongo_data = mongo_col.find_one({"title": product_title})
    
    splits_check = product_title.split("_")
    year = splits_check[2][0:4]
    month = datetime.strptime(splits_check[2][4:6], "%m")
    minio_dir = join(tile_id, year, month.strftime("%B"), "products", "")

    try:
        objects = minio_client.list_objects(minio_client.products_bucket, prefix=join(minio_dir, product_title), recursive=False)
        for _ in objects:
            minio_found = True
            break
    except Exception:
        print(f"Product not found in MinIO")
        minio_found = False
        pass

    if product_mongo_data and minio_found:
        print("The product is already in mongo and minio. Nothing to do")
    else:

        unzip_folder = join(tmp_dir, product_title, "")

        # The name for the new bucket
        bucket = storage_client.bucket(bucket_name)
        blobs = list(bucket.list_blobs(prefix=source_blob_name))

        product_folder = Path(source_blob_name).name

        if len(blobs) == 0:
            print("Product not found in Google Cloud")
            print("Failing blob name: " + source_blob_name + " title: " + product_title)
            return
        for blob in blobs:
            if blob.name.endswith("/") or blob.name.endswith("$folder$") or "IMG_DATA" not in blob.name:  # Ignore folders and GCloud files
                continue
            print(blob.name)
            # Prepare path to local file
            output_folder = join(tmp_dir, product_folder)
            local_blob_name = Path(blob.name[len(source_blob_name + "/"):] if blob.name.startswith(source_blob_name + "/") else blob.name)
            local_blob_path = output_folder / local_blob_name
            local_blob_path = Path(str(local_blob_path).replace(".SAFE", ""))

            # Make sure output folder exists
            local_blob_path.parent.mkdir(parents=True, exist_ok=True)

            # Download if doesn't exist
            if not Path.is_file(local_blob_path):
                blob.download_to_filename(local_blob_path)
            else:
                print("File exists, skipping")
        
        # # Upload bands
        images = Path(unzip_folder).rglob("*.jp2")
        for image in images:
            image_name = os.path.basename(image).split("_", 2)[2]
            print("Uploading ", image_name, "to MinIO")
            minio_client.fput_object(
                minio_client.products_bucket,
                minio_dir + product_title + "/raw/" + image_name,
                image,
                content_type="image/jp2",
            )

        try:
            objects = minio_client.list_objects(minio_client.products_bucket, prefix=join(minio_dir, product_title), recursive=False)
            for _ in objects:
                break
        except Exception:
            print(f"Product not uploaded to MinIO")
            return

        metadata = {}
        if sentinel_metadata != {}:
            metadata["sentinelAPI"] = sentinel_metadata
        metadata["title"] = product_title
        metadata["minioBucket"] = minio_client.products_bucket
        metadata["minioBandsPath"] = join(minio_dir, product_title, "raw", "")
        metadata["noDataPercentage"] = calculate_no_data(unzip_folder)
        mongo_col.insert_one(metadata)

        try:
            if Path.is_dir(Path(unzip_folder)):
                shutil.rmtree(unzip_folder)
        except OSError as e:
            print("Error: %s - %s." % (e.filename, e.strerror))

    if calculate_raw_indexes:
        calculate_raw_index(
            product_title=product_title,
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
        )
    
    if calculate_intermediate_products:
        calculate_raw_index(
            product_title=product_title,
            index=[
                "CloudMask",
            ],
            minio_folder_name="intermediateProducts",
        )

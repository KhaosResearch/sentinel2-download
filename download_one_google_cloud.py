import os
import shutil
from typing import Dict, Optional
import zipfile
import datetime
import typer
import numpy as np
import rasterio
from datetime import datetime
from pathlib import Path
from operator import mul
from dotenv import load_dotenv
from minio import Minio
from pymongo import MongoClient
from google.cloud import storage
from raw_index_calculation import calculate_raw_index

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
        # Get the number of pixel in the band
        if len(band.shape) == 2:
            n_pixeles = mul(*np.shape(band))
        if len(band.shape) == 3:
            n_pixeles = np.shape(band)[1] * np.shape(band)[2]
            
        # Calculates percentage of no data
        percentage = (n_pixeles - np.count_nonzero(band)) * 100 / n_pixeles
        
    return percentage

def download_one_google_cloud(
    calculate_raw_indexes: bool,
    calculate_intermediate_products: bool,
    product_title: str,
    temp_dir: str = "data",
    mongo_host: str = os.environ.get("MONGO_HOST"),
    mongo_port: str = os.environ.get("MONGO_PORT"),
    mongo_username: str = os.environ.get("MONGO_USERNAME"),
    mongo_password: str = os.environ.get("MONGO_PASSWORD"),
    mongo_database_name: str = os.environ.get("MONGO_DATABASE_NAME"),
    mongo_collection_name: str = os.environ.get("MONGO_COLLECTION_NAME"),
    minio_host: str = os.environ.get("MINIO_HOST"),
    minio_port: str = os.environ.get("MINIO_PORT"),
    minio_access_key: str = os.environ.get("MINIO_ACCESS_KEY"),
    minio_secret_key: str = os.environ.get("MINIO_SECRET_KEY"),
    minio_bucket_name: str = os.environ.get("MINIO_BUCKET_NAME"),
    dhus_username: str = os.environ.get("DHUS_USERNAME"),
    dhus_password: str = os.environ.get("DHUS_PASSWORD"),
    dhus_host: str = os.environ.get("DHUS_HOST"),
    google_cloud_bucket_name: str = os.environ.get("GOOGLE_CLOUD_BUCKET_NAME"),
    metadata: Optional[Dict] = typer.Option({}, help="Dict with metadata"),
):
    """
    Example: python download_one_from_google_cloud.py --product_title S2A_MSIL1C_20150704T101006_N0204_R022_T33UUP_20150704T101337 --temp-dir ./data --calculate-raw-indexes
    """

    # Connect with mongo
    mongo_client = MongoClient(
        "mongodb://" + mongo_host + ":" + mongo_port + "/",
        username=mongo_username,
        password=mongo_password,
    )
    mongo_db = mongo_client[mongo_database_name]
    mongo_col = mongo_db[mongo_collection_name]

    # Connect with minio
    client = Minio(
        minio_host + ":" + minio_port,
        access_key=minio_access_key,
        secret_key=minio_secret_key,
        secure=False,
    )

    product_sentinel_data = metadata

    # Connect with google cloud
    storage_client = storage.Client()
    bucket_name = google_cloud_bucket_name
    splits = product_title.split("_T")
    tile_id = str(splits[1][0:5])
    tile_number = str(splits[1][0:2])
    tile_type = str(splits[1][2])
    tile_subtype = str(splits[1][3:5])
    list_of_names = ["L2/tiles", tile_number, tile_type, tile_subtype, product_title + ".SAFE"]
    delimiter = "/"
    source_blob_name = delimiter.join(list_of_names)

    # Check if the product is already downloaded
    product_mongo_data = mongo_col.find_one({"title": product_title})
    splits_check = product_title.split("_")
    year = splits_check[2][0:4]
    month = datetime.strptime(splits_check[2][4:6], "%m")
    minio_dir = tile_id + "/" + year + "/" + month.strftime("%B") + "/products/"

    minio_found = False

    try:
        client.stat_object(minio_bucket_name, minio_dir + product_title + ".zip")
        minio_found = True
    except Exception as e:
        print("Zip file is not in MinIO")
        pass

    if product_mongo_data and minio_found:
        print("The product is already in mongo and minio. Nothing to do")

    else:
        temp_dir = str(temp_dir)
        unzip_folder = temp_dir + "/" + product_title + ".SAFE"
        product_dir = temp_dir + "/" + product_title + ".zip"

        # Download product
        if minio_found:
            print(
                "The product is already in minio. The download is made from the database"
            )
            client.fget_object(minio_bucket_name, minio_dir + product_title + ".zip", product_dir)
            
            print("Unzipping " + product_title)
            with zipfile.ZipFile(Path(product_dir), "r") as zip_file_object:
                zip_file_object.extractall(unzip_folder)
                
        else:
            # The name for the new bucket
            bucket = storage_client.bucket(bucket_name)
            blobs = list(bucket.list_blobs(prefix=source_blob_name))

            product_folder = Path(source_blob_name).name

            if len(blobs) == 0:
                print("Product not found in Google Cloud")
                print("Failing blob name: " + source_blob_name + " title: " + product_title)
                return
            for blob in blobs:
                if blob.name.endswith("/") or blob.name.endswith("$folder$"):  # Ignore folders and GCloud files
                    continue

                # Prepare path to local file
                output_folder = Path(temp_dir).resolve() / product_folder
                local_blob_name = Path(blob.name.removeprefix(source_blob_name + "/"))
                local_blob_path = output_folder / local_blob_name

                # Make sure output folder exists
                local_blob_path.parent.mkdir(parents=True, exist_ok=True)

                # Download if doesn't exist
                if not Path.is_file(local_blob_path):
                    blob.download_to_filename(local_blob_path)
                else:
                    print("File exists, skipping")
            # Upload product zip file to minio
            shutil.make_archive(
                temp_dir + "/" + product_title,
                "zip",
                output_folder,
            )
            client.fput_object(
                minio_bucket_name,
                minio_dir + product_title + ".zip",
                product_dir,
                content_type="application/zip",
            )

        # # Prepare dictionary to save in mongo
        product_as_dict = product_sentinel_data
        product_as_dict.setdefault("indexes", [])
        product_as_dict.setdefault("intermediate_products", [])

        # # Append product metadata
        product_as_dict["date"] = product_as_dict["OriginDate"]
        product_as_dict["objectName"] = str(product_dir)
        product_as_dict["processingLevel"] = int(product_title.split("_")[3][2:])
        product_as_dict["noDataPercentage"] = calculate_no_data(unzip_folder)
        
        # # Upload bands
        images = Path(unzip_folder).glob("GRANULE/*/IMG_DATA/**/*.jp2")

        for image in images:
            image_name = os.path.basename(image).split("_", 2)[2]
            client.fput_object(
                minio_bucket_name,
                minio_dir + product_title + "/raw/" + image_name,
                image,
                content_type="image/jp2",
            )

        upload_minio = False
        try:
            client.stat_object(minio_bucket_name, minio_dir + product_title + ".zip")
            upload_minio = True
        except Exception as e:
            print("Zip file is not in MinIO. The upload to Mongo cannot be performed")
            pass

        # # Insert dictionary to mongo collection
        if upload_minio:
            if (
                mongo_col.find_one(
                    {"title": {"$regex": product_as_dict["title"], "$options": "i"}}
                )
                == None
            ):
                mongo_col.insert_one(product_as_dict)

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
                    "CRI1",
                    # Classifier is calculated manually for selected products as is very time consuming
                ],
                temp_dir=temp_dir,
                mongo_host=mongo_host,
                mongo_port=mongo_port,
                mongo_username=mongo_username,
                mongo_password=mongo_password,
                mongo_database_name=mongo_database_name,
                mongo_collection_name=mongo_collection_name,
                minio_host=minio_host,
                minio_port=minio_port,
                minio_access_key=minio_access_key,
                minio_secret_key=minio_secret_key,
                minio_bucket_name=minio_bucket_name,
            )
        
        if calculate_intermediate_products:
            calculate_raw_index(
                product_title=product_title,
                index=[
                    "Cloud-Mask",
                ],
                temp_dir=temp_dir,
                mongo_host=mongo_host,
                mongo_port=mongo_port,
                mongo_username=mongo_username,
                mongo_password=mongo_password,
                mongo_database_name=mongo_database_name,
                mongo_collection_name=mongo_collection_name,
                minio_host=minio_host,
                minio_port=minio_port,
                minio_access_key=minio_access_key,
                minio_secret_key=minio_secret_key,
                minio_bucket_name=minio_bucket_name,
                minio_folder_name="intermediate_products",
            )

        # Remove product from local folder
        try:
            shutil.rmtree(unzip_folder)
        except OSError as e:
            print("Error: %s - %s." % (e.filename, e.strerror))

        if os.path.exists(product_dir):
            os.remove(product_dir)
        else:
            print("File not found in the directory")

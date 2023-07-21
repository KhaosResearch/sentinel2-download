import os
import shutil
import zipfile
from pathlib import Path

import typer
from dotenv import load_dotenv
from greensenti.band_arithmetic import *
from minio import Minio
from pymongo import MongoClient
from sentinelsat.sentinel import SentinelAPI

from raw_index_calculation import calculate_raw_index

load_dotenv()


def download_one(
    calculate_raw_indexes: bool,
    product_title: str,
    uid: str,
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
):
    """
    Example: python download_one.py --uid dad7f379-de8c-49ec-b4cf-44348d0f418c --temp-dir ./data --calculate-raw-indexes
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

    # Initialize Sentinel client
    sentinel_api = SentinelAPI(
        dhus_username,
        dhus_password,
        dhus_host,
        show_progressbars=False,
    )

    # Check if the product is already downloaded
    product_mongo_data = mongo_col.find_one({"title": product_title})

    product_sentinel_data = sentinel_api.get_product_odata(uid, full=True)
    year = product_sentinel_data["date"].strftime("%Y")
    month = product_sentinel_data["date"].strftime("%B")
    minio_dir = year + "/" + month + "/"
    title = product_sentinel_data["title"]
    zip_file = title + ".zip"

    minio_found = False
    try:
        client.stat_object(minio_bucket_name, minio_dir + zip_file)
        minio_found = True
    except Exception as e:
        pass

    if product_mongo_data and minio_found:
        print("The product is already in mongo and minio. Nothing to do.")

    else:
        temp_dir = str(temp_dir)
        unzip_folder = temp_dir + "/" + title + ".SAFE"
        product_dir = temp_dir + "/" + zip_file

        # Download product
        if minio_found:
            print(
                "The product is already in minio. The download is made from the database"
            )
            client.fget_object(minio_bucket_name, minio_dir + zip_file, product_dir)
        else:
            sentinel_api.download(uid, temp_dir)

            # Make 'minio_bucket_name' bucket if not exist.
            found = client.bucket_exists(minio_bucket_name)
            if not found:
                client.make_bucket(minio_bucket_name)
            else:
                print(f"Bucket {minio_bucket_name} already exists")

            # Upload product zip file to minio
            client.fput_object(
                minio_bucket_name,
                minio_dir + zip_file,
                product_dir,
                content_type="application/zip",
            )

        if not os.path.exists(unzip_folder):
            print("Unzipping " + title)
            with zipfile.ZipFile(Path(product_dir), "r") as zip_file_object:
                zip_file_object.extractall(temp_dir)

        # Prepare dictionary to save in mongo
        product_as_dict = dict()
        product_as_dict.setdefault("indexes", [])

        # Append product metadata
        product_as_dict["id"] = product_sentinel_data["id"]
        product_as_dict["title"] = product_sentinel_data["title"]
        product_as_dict["size"] = product_sentinel_data["size"]
        product_as_dict["date"] = product_sentinel_data["date"]
        product_as_dict["creationDate"] = product_sentinel_data["Creation Date"]
        product_as_dict["ingestionDate"] = product_sentinel_data["Ingestion Date"]
        product_as_dict["objectName"] = str(product_dir)
        processing_level = int(
            product_sentinel_data["title"].split("_N")[1].split("_")[0]
        )
        product_as_dict["processingLevel"] = processing_level

        # Upload bands
        images = Path(unzip_folder).glob("GRANULE/*/IMG_DATA/**/*.jp2")
        for image in images:
            image_name = os.path.basename(image).split("_", 2)[2]
            client.fput_object(
                minio_bucket_name,
                minio_dir + title + "/raw/" + image_name,
                image,
                content_type="image/jp2",
            )

        # Insert dictionary to mongo collection
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
                    "Cover-Percentage",
                    "Cloud-Mask",
                    "OSAVI",
                    "EVI2",
                    "NDRE",
                    "NDYI",
                    "MNDWI",
                    "BRI",
                    "TCI",
                    "RI",
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

        # Remove product from local folder
        try:
            shutil.rmtree(unzip_folder)
        except OSError as e:
            print("Error: %s - %s." % (e.filename, e.strerror))

        if os.path.exists(product_dir):
            os.remove(product_dir)
        else:
            print("File not found in the directory")


if __name__ == "__main__":
    typer.run(download_one)

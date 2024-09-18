import os
import shutil
from enum import Enum
from pathlib import Path
from datetime import datetime

import numpy as np
from dotenv import load_dotenv
from band_arithmetic import (
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
from minio_connection import MinioConnection
from mongo_connection import MongoConnection

load_dotenv()


class Index(str, Enum):
    moisture = "Moisture"
    ndvi = "NDVI"
    ndwi = "NDWI"
    ndsi = "NDSI"
    evi = "EVI"
    cloud_mask = "Cloud-Mask"
    osavi = "OSAVI"
    evi2 = "EVI2"
    ndre = "NDRE"
    ndyi = "NDYI"
    mndwi = "MNDWI"
    bri = "BRI"
    bsi = "BSI"
    tci = "TCI"
    ri = "RI"
    cri1 = "CRI1"


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
    tci={"b2": "B02_10m", "b3": "B03_10m", "b4": "B04_10m"},
    ri={"b3": "B03_10m", "b4": "B04_10m"},
    cri1={"b2": "B02_10m", "b3": "B03_10m"},
)


def find_product_image(pattern: str) -> Path:
    """
    Finds image matching a pattern in the product folder with glob.
    :param pattern: A pattern to match.
    :return: A Path object pointing to the first found image.
    """
    return (
        [
            f
            for f in Path(unzip_folder).glob(
                "GRANULE/*/IMG_DATA/**/*" + pattern + ".jp2"
            )
        ]
        + [f for f in Path(unzip_folder).glob("*" + pattern + ".jp2")]
    )[0]

def get_index(index_name, bands_dict, download_first):
    if download_first:
        for v in bands_dict.values():
            band_file = v + ".jp2"
            client.fget_object(
                minio_bucket_name,
                bands_dir + band_file,
                unzip_folder + "/" + band_file,
            )

    output = Path(indexes_folder + "/" + index_name + ".tif")
    try:
        if index_name == "moisture":
            index_value = moisture(
                b8a=find_product_image(bands_dict["b8a"]),
                b11=find_product_image(bands_dict["b11"]),
                output=output,
            )
        elif index_name == "ndvi":
            index_value = ndvi(
                b4=find_product_image(bands_dict["b4"]),
                b8=find_product_image(bands_dict["b8"]),
                output=output,
            )
        elif index_name == "ndwi":
            index_value = ndwi(
                b3=find_product_image(bands_dict["b3"]),
                b8=find_product_image(bands_dict["b8"]),
                output=output,
            )
        elif index_name == "ndsi":
            index_value = ndsi(
                b3=find_product_image(bands_dict["b3"]),
                b11=find_product_image(bands_dict["b11"]),
                output=output,
            )
        elif index_name == "evi":
            index_value = evi(
                b2=find_product_image(bands_dict["b2"]),
                b4=find_product_image(bands_dict["b4"]),
                b8=find_product_image(bands_dict["b8"]),
                output=output,
            )
        elif index_name == "cloud-mask":
            index_value = cloud_mask(
                scl=find_product_image(bands_dict["scl"]),
                output=output,
            )
        elif index_name == "osavi":
            index_value = osavi(
                b4=find_product_image(bands_dict["b4"]),
                b8=find_product_image(bands_dict["b8"]),
                Y=0.16,
                output=output,
            )
        elif index_name == "evi2":
            index_value = evi2(
                b4=find_product_image(bands_dict["b4"]),
                b8=find_product_image(bands_dict["b8"]),
                output=output,
            )
        elif index_name == "ndre":
            index_value = ndre(
                b5=find_product_image(bands_dict["b5"]),
                b9=find_product_image(bands_dict["b9"]),
                output=output,
            )
        elif index_name == "ndyi":
            index_value = ndyi(
                b2=find_product_image(bands_dict["b2"]),
                b3=find_product_image(bands_dict["b3"]),
                output=output,
            )
        elif index_name == "mndwi":
            index_value = mndwi(
                b3=find_product_image(bands_dict["b3"]),
                b11=find_product_image(bands_dict["b11"]),
                output=output,
            )
        elif index_name == "bri":
            index_value = bri(
                b3=find_product_image(bands_dict["b3"]),
                b5=find_product_image(bands_dict["b5"]),
                b8=find_product_image(bands_dict["b8"]),
                output=output,
            )
        elif index_name == "bsi":
            index_value = bsi(
                b2=find_product_image(bands_dict["b2"]),
                b4=find_product_image(bands_dict["b4"]),
                b8=find_product_image(bands_dict["b8"]),
                b11=find_product_image(bands_dict["b11"]),
                output=output,
            )
        elif index_name == "ri":
            index_value = ri(
                b3=find_product_image(bands_dict["b3"]),
                b4=find_product_image(bands_dict["b4"]),
                output=output,
            )
        elif index_name == "cri1":
            index_value = cri1(
                b2=find_product_image(bands_dict["b2"]),
                b3=find_product_image(bands_dict["b3"]),
                output=output,
            )
        elif index_name == "tci":
            index_value = true_color(
                b=find_product_image(bands_dict["b2"]),
                g=find_product_image(bands_dict["b3"]),
                r=find_product_image(bands_dict["b4"]),
                output=output,
            )

        index_value = (
            np.nanmean(index_value)
            if index_value is not None and not isinstance(index_value, float)
            else index_value
        )
    except Exception as e:
        raise e

    metadata_path = "minio://" + minio_bucket_name + "/"
    tif_minio_path = (
        minio_dir + title + "/" + minio_folder_name + "/" + index_name + ".tif"
    )

    tif_meta_minio_path = metadata_path + tif_minio_path
    band_meta_minio_path = "minio://" + minio_bucket_name + "/" + bands_dir

    client.fput_object(
        minio_bucket_name,
        tif_minio_path,
        indexes_folder + "/" + index_name + ".tif",
        content_type="image/tif",
    )

    band = dict()
    for k, v in bands_dict.items():
        band[k] = band_meta_minio_path + v + ".jp2"

    index_dict = dict(
        name=index_name,
        objectName=None,
        rawObjectName=tif_meta_minio_path,
        band=band,
        value=float(index_value) if index_value is not None else index_value,
    )

    return index_dict


def calculate_raw_index(
    product_title: str,
    index: list,
    minio_folder_name: str = "indexes",
):
    """
    Example: python raw_index_calculation.py --uid dad7f379-de8c-49ec-b4cf-44348d0f418c --index ndvi --index ndsi --temp-dir ./data
    """

    # Connect with mongo
    mongo_col = MongoConnection().get_collection_object()

    # Search product metadata in Mongo
    product_data = mongo_col.find_one({"title": product_title})

    # Find if the file is already unzipped in the temporary folder
    temp_dir = str(temp_dir)
    title = product_data["title"]
    unzip_folder = temp_dir + "/" + title
    exists_unzip = Path(unzip_folder + "/GRANULE").is_dir()

    # Declare function for image search


    # Connect with minio
    client = MinioConnection()
    minio_bucket_name = client.products_bucket

    # Create folder to store tif files
    indexes_folder = unzip_folder + "/" + minio_folder_name
    Path(indexes_folder).mkdir(exist_ok=True, parents=True)

    # Determine the Minio folder
    date = datetime.strptime(product_data["title"].split('_')[2], "%Y%m%dT%H%M%S")
    year = date.strftime("%Y")
    month = date.strftime("%B")
    tile_id = product_data["title"].split("_T")[1][0:5]
    minio_dir = tile_id + "/" + year + "/" + month + "/products/"
    bands_dir = minio_dir + title + "/raw/"

    # Create dictionary of indexes
    index_dicts = {}
    mfn_splitted = minio_folder_name.split('_')
    indexes_mongo_key = mfn_splitted[0] + ''.join(ele.title() for ele in mfn_splitted[1:])

    if minio_folder_name not in product_data:
        product_data[minio_folder_name] = []

    if indexes_mongo_key not in product_data:
        product_data[indexes_mongo_key] = []

    for idx in index:
        index_name = idx.lower()

        # Check if the index is already calculated
        raster_index_dict = next(
            (
                item
                for item in product_data[indexes_mongo_key]
                if item["name"] == index_name
            ),
            None,
        )
        if not raster_index_dict:
            dict_key = index_name.replace("-", "")
            index_dicts[index_name] = get_index(
                index_name=index_name,
                bands_dict=indexes_bands[dict_key],
                download_first=not exists_unzip,
            )
        else:
            print("The index " + index_name + " is already calculated")

    # Push the index_dicts to MongoDB
    mongo_col.update_one(
        {"title": product_title},
        {"$set": {indexes_mongo_key: index_dicts}}  # Change to $set to overwrite the key with the new dict
    )

    if not exists_unzip:
        # Remove product from local folder
        try:
            shutil.rmtree(unzip_folder)
        except OSError as e:
            print("Error: %s - %s." % (e.filename, e.strerror))

import json
import os
import shutil
from enum import Enum
from pathlib import Path
from typing import List, Optional

import geojson as GeoJson
import numpy as np
from dotenv import load_dotenv
from greensenti.band_arithmetic import *
from greensenti.raster import crop_by_shape, project_shape
from minio import Minio
from pymongo import MongoClient
import typer

load_dotenv()


class NotValidScheme(Exception):
    """
    Raised when scheme is not present.
    """

    pass


def apply_mask(
    filename: Path = typer.Argument(
        ..., exists=True, file_okay=True, help="Path to input file"
    ),
    geojson: GeoJson = typer.Argument(..., help="Geojson object"),
    output: Path = typer.Option(..., help="Path to output file"),
) -> Path:
    """
    Crop image data (jp2 imagery file) by shape.
    :return: Path to output file.
    """
    # makes sure the output dir exists
    if not output.parent.is_dir():
        output.parent.mkdir(parents=True)

    shape = project_shape(geojson["features"][0]["geometry"])

    # mask product based on location
    crop_by_shape(filename=str(filename), outfile=str(output), geom=shape)

    return output


class Index(str, Enum):
    moisture = "Moisture"
    ndvi = "NDVI"
    ndwi = "NDWI"
    ndsi = "NDSI"
    evi = "EVI"
    cover_percentage = "Cover-percentage"
    cloud_mask = "Cloud-mask"
    osavi = "OSAVI"
    evi2 = "EVI2"
    ndre = "NDRE"
    ndyi = "NDYI"
    mndwi = "MNDWI"
    bri = "BRI"
    tci = "TCI"
    ri = "RI"
    cri1 = "CRI1"
    bsi = "BSI"


def index_calculation(
    ids_file: str = typer.Option(
        ...,
        exists=True,
        file_okay=True,
        dir_okay=False,
        writable=False,
        readable=True,
        resolve_path=True,
        help="Path to the file with the list of the identifiers corresponding to the products for which the index is intended to be calculated",
    ),
    geojson_file: Path = typer.Option(
        None,
        exists=True,
        file_okay=True,
        dir_okay=False,
        writable=False,
        readable=True,
        resolve_path=True,
        help="Path of the GeoJson file that delimits the area over which the indexes are calculated",
    ),
    index: Optional[List[Index]] = typer.Option(
        ..., case_sensitive=False, help="Index to be calculated"
    ),
    temp_dir: Path = typer.Option("./data", exists=False, help="Temporary folder path"),
    output_file: Path = typer.Option(
        ...,
        exists=False,
        help="Path of the output file where the identifiers of the successful products will be stored",
    ),
    output_metadata_file: Path = typer.Option(
        ...,
        exists=False,
        help="Path of the output metadata file where the identifiers of the successful and pending products will be stored together with the identifier of the geojson used",
    ),
    mongo_host: str = typer.Argument(..., envvar="MONGO_HOST", show_envvar=False),
    mongo_port: str = typer.Argument(..., envvar="MONGO_PORT", show_envvar=False),
    mongo_username: str = typer.Argument(
        ..., envvar="MONGO_USERNAME", show_envvar=False
    ),
    mongo_password: str = typer.Argument(
        ..., envvar="MONGO_PASSWORD", show_envvar=False
    ),
    mongo_database_name: str = typer.Argument(
        ..., envvar="MONGO_DATABASE_NAME", show_envvar=False
    ),
    mongo_collection_name: str = typer.Argument(
        ..., envvar="MONGO_COLLECTION_NAME", show_envvar=False
    ),
    minio_host: str = typer.Argument(..., envvar="MINIO_HOST", show_envvar=False),
    minio_port: str = typer.Argument(..., envvar="MINIO_PORT", show_envvar=False),
    minio_access_key: str = typer.Argument(
        ..., envvar="MINIO_ACCESS_KEY", show_envvar=False
    ),
    minio_secret_key: str = typer.Argument(
        ..., envvar="MINIO_SECRET_KEY", show_envvar=False
    ),
    minio_bucket_name: str = typer.Argument(
        ..., envvar="MINIO_BUCKET_NAME", show_envvar=False
    ),
):
    """
    Description: \n
    This component is in charge of calculating the index specified for the products whose identifiers are in the input list for the area delimited by the geojson file.

    Example: \n
    >>> python crop_products.py --ids-file /path/to/ids_file.json --geojson-file /path/to/geojson_file.json --index NDVI --index OSAVI --index MNDWI --temp-dir /path/to/temp_dir/ --output-file /path/to/output_file.json --output-metadata-file /path/to/output_metadata_file.json --mongo-host mongo_host --mongo-port mongo_port --mongo-username mongo_username --mongo-password mongo_password --mongo-database-name mongo_database_name --mongo-collection-name mongo_collection_name --minio-host minio_host --minio-port minio_port --minio-access-key minio_access_key --minio-secret-key minio_secret_key --minio-bucket-name minio_bucket_name
    """

    # Load list of ids
    with open(ids_file) as id_d:
        json_data = json.load(id_d)
        ids = json_data["ids"]

    # Load geojson object from the input file
    if geojson_file:
        with open(geojson_file) as geo_d:
            geojson = json.load(geo_d)
        # Obtain the geojson identifier
        geojson_id = str(geojson_file).split("/")[-1].split(".")[0]

    else:
        geojson = None
        geojson_id = None

    # Connect to Mongo
    mongo_client = MongoClient(
        host=mongo_host,
        port=int(mongo_port),
        username=mongo_username,
        password=mongo_password,
    )
    mongo_db = mongo_client[mongo_database_name]
    mongo_col = mongo_db[mongo_collection_name]

    # Connect to Minio
    client = Minio(
        minio_host + ":" + minio_port,
        access_key=minio_access_key,
        secret_key=minio_secret_key,
        secure=False,
    )

    success_products = []
    pending_products = []

    for uid in ids:
        # Check in the database if the product is already downloaded
        product_data = mongo_col.find_one({"id": uid})

        if product_data:
            if not geojson:
                for idx in index:
                    # Obtain the raw index dictionary
                    raster_index_dict = next(
                        (
                            item
                            for item in product_data["indexes"]
                            if item["name"] == str.lower(idx) and item["mask"] == None
                        ),
                        None,
                    )

                    if not raster_index_dict:
                        raise Exception(
                            "The product with id: "
                            + uid
                            + "is incomplete. The "
                            + idx
                            + " index has not been calculated for raw data."
                        )

                success_products.append(uid)

            else:
                title = product_data["title"]
                product_dir = str(temp_dir) + "/" + title
                ld_indexes = product_data["indexes"]

                for idx in index:
                    # Check if the index is already calculated for the input geojson
                    index_dict = None

                    for d_idx in ld_indexes:
                        name = d_idx["name"]
                        if not name == idx.lower():
                            continue

                        mask = d_idx["mask"]
                        if not mask:
                            continue

                        geojson_data_id = mask["geojson"]
                        if geojson_data_id == geojson_id:
                            # found!
                            index_dict = d_idx
                            break

                    if index_dict:
                        print("Value was already calculated")

                    else:
                        # Obtain the raw index dictionary
                        raster_index_dict = next(
                            (
                                item
                                for item in product_data["indexes"]
                                if item["name"] == str.lower(idx)
                                and item["mask"] == None
                            ),
                            None,
                        )

                        if not raster_index_dict:
                            raise Exception(
                                "The product with id: "
                                + uid
                                + "is incomplete. The "
                                + idx
                                + " index has not been calculated for raw data."
                            )

                        # Remove scheme and deconstruct path
                        bucket_name, object_name = raster_index_dict["rawObjectName"][
                            len("minio://") :
                        ].split("/", 1)
                        tif_filename = os.path.basename(object_name)
                        tif_local_dir = product_dir + "/" + tif_filename

                        # Download raw tif file
                        client.fget_object(bucket_name, object_name, tif_local_dir)

                        # Apply mask
                        masked_tif_filename = (
                            os.path.splitext(tif_filename)[0] + "_masked.tif"
                        )
                        masked_tif_local_dir = product_dir + "/" + masked_tif_filename
                        apply_mask(
                            filename=Path(tif_local_dir),
                            geojson=geojson,
                            output=Path(masked_tif_local_dir),
                        )

                        # Calculate method value
                        with rasterio.open(masked_tif_local_dir) as tif_file:
                            tif_object = tif_file.read(1).astype(np.float32)

                        if idx == "Cover-percentage":
                            bucket_name, object_name = raster_index_dict["band"]["b3"][
                                len("minio://") :
                            ].split("/", 1)
                            band_filename = os.path.basename(object_name)
                            band_local_dir = product_dir + "/" + band_filename
                            client.fget_object(bucket_name, object_name, band_local_dir)
                            with rasterio.open(band_local_dir) as green:
                                GREEN = green.read(1).astype(np.float32)
                            value = (
                                np.count_nonzero(tif_object)
                                * 100
                                / np.count_nonzero(GREEN)
                            )

                        else:
                            value = np.nanmean(tif_object)

                        # Upload tif and geojson files
                        year = product_data["ingestionDate"].strftime("%Y")
                        month = product_data["ingestionDate"].strftime("%B")

                        minio_dir = year + "/" + month + "/" + title + "/"
                        masked_tif_minio_dir = (
                            minio_dir + "indexes/" + geojson_id + "/" + tif_filename
                        )
                        geojson_minio_dir = minio_dir + "mask/" + geojson_id + ".json"

                        client.fput_object(
                            minio_bucket_name,
                            masked_tif_minio_dir,
                            masked_tif_local_dir,
                            content_type="image/tif",
                        )

                        client.fput_object(
                            minio_bucket_name,
                            geojson_minio_dir,
                            geojson_file,
                            content_type="application/json",
                        )

                        # Actualizar Mongo
                        lower_index = idx.lower()
                        bands = raster_index_dict["band"]
                        new_index = dict(
                            name=lower_index,
                            objectName=None,
                            rawObjectName="minio://"
                            + minio_bucket_name
                            + "/"
                            + masked_tif_minio_dir,
                            band=bands,
                            mask={
                                "geojson": geojson_id,
                                "objectName": "minio://"
                                + minio_bucket_name
                                + "/"
                                + geojson_minio_dir,
                            },
                            value=float(value),
                        )

                        mongo_col.update_one(
                            {"id": uid}, {"$push": {"indexes": new_index}}
                        )

                        # Remove temporary files
                        try:
                            shutil.rmtree(product_dir)
                        except OSError as e:
                            print("Error: %s - %s." % (e.filename, e.strerror))

                success_products.append(uid)

        else:
            pending_products.append(uid)

    # Write output files
    output_metadata_dict = dict(
        success_products=success_products,
        pending_products=pending_products,
        geojson_id=geojson_id,
    )

    output_dict = dict(success_products=success_products)

    with open(output_metadata_file, "w") as json_file:
        json.dump(output_metadata_dict, json_file, indent=2)

    with open(output_file, "w") as json_file:
        json.dump(output_dict, json_file, indent=2)


if __name__ == "__main__":
    typer.run(index_calculation)

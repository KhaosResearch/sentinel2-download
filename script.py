import time
import os

from datetime import datetime
from enum import Enum
import pandas as pd
from pathlib import Path
from typing import Tuple
import typer
from dotenv import load_dotenv
from greensenti.band_arithmetic import *
from pymongo import MongoClient
from sentinelsat.sentinel import SentinelAPI, geojson_to_wkt, read_geojson

from download_one import download_one
from download_one_google_cloud import download_one_google_cloud

load_dotenv()


class Country(str, Enum):
    albania = "Albania"
    algeria = "Algeria"
    bulgaria = "Bulgaria"
    canarias = "Canarias"
    croatia = "Croatia"
    egypt = "Egypt"
    france = "France"
    gibraltar = "Gibraltar"
    greece = "Greece"
    italy = "Italy"
    lybia = "Lybia"
    malta = "Malta"
    montenegro = "MonteNegro"
    morocco = "Morocco"
    slovenia = "Slovenia"
    spain = "Spain"
    syria = "Syria"
    teatinos = "Teatinos"
    tunisia = "Tunisia"
    turkey = "Turkey"
    andalucia = "Andalucia"
    portugal = "Portugal"


def script(
    country: Country,
    start_date: datetime,
    end_date: datetime,
    cloud_limit_soft: int = 100,
    full_coverage: bool = False,
    cloud_limit_max: int = 15,
    cloud_limit_composite: int = 20,
    calculate_raw_indexes: bool = False,
    temp_dir: Path = "data",
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
    search_selected: str = "google",
):
    """
    Example simplified: python script.py --country Spain --start-date 2020-02-01 --end-date 2020-02-07 --temp-dir ./data --cloud_limit_soft 10 --calculate-raw-indexes

    Example for full coverage: python script.py --country Spain --start-date 2020-02-01 --end-date 2020-02-07 --temp-dir ./data --cloud_limit_soft 10 --full_coverage --cloud_limit_max 25 --cloud_limit_composite 40 --calculate-raw-indexes

    """
    geojson = read_geojson(Path(f"geojson/{str(country.value).lower()}.geojson"))
    footprint = geojson_to_wkt(geojson)

    # Initialize Sentinel client
    sentinel_api = SentinelAPI(
        dhus_username,
        dhus_password,
        dhus_host,
        show_progressbars=False,
    )

    typer.echo("Searching for products in scene")

    if full_coverage:
        products_df, ids = compute_full_coverage(
            sentinel_api,
            footprint,
            cloud_limit_soft,
            cloud_limit_max,
            cloud_limit_composite,
            start_date,
            end_date,
        )

    else:
        products_df, ids = search(
            sentinel_api, footprint, cloud_limit_soft, start_date, end_date
        )

    metadata_dict = {}
    titles_list = []

    for elem in list(products_df["title"]):
        titles_list.append(elem)

    for id in ids:
        product_sentinel_data = sentinel_api.get_product_odata(id, full=True)
        metadata_dict[products_df["title"][id]] = product_sentinel_data

    typer.echo(f"Found {len(titles_list)} scenes between {start_date} and {end_date}")

    if search_selected == "google":
        for title in titles_list:
            start_time = time.time()
            print("TITLE: ", title)
            download_one_google_cloud(
                calculate_raw_indexes=calculate_raw_indexes,
                calculate_intermediate_products=calculate_raw_indexes,
                product_title=title,
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
                dhus_host=dhus_host,
                dhus_username=dhus_username,
                dhus_password=dhus_password,
                google_cloud_bucket_name=google_cloud_bucket_name,
                metadata=metadata_dict[title],
            )
            print("Time for tile: " + str(time.time() - start_time))

    if search_selected == "sentinel":
        # Connect to Mongo
        mongo_client = MongoClient(
            "mongodb://" + mongo_host + ":" + mongo_port + "/",
            username=mongo_username,
            password=mongo_password,
        )
        mongo_db = mongo_client[mongo_database_name]
        mongo_col = mongo_db[mongo_collection_name]

        # List ids that are not in Mongo
        pending_ids = []
        for uid in ids:
            product_data = mongo_col.find_one({"id": uid})
            if not product_data:
                pending_ids.append(uid)

        print(str(len(pending_ids)) + " products pending to download")

        # Handling errored products
        error_products = []
        offline_products = []

        for uid in pending_ids:
            backoff = 5
            while True:
                try:
                    is_online = sentinel_api.is_online(uid)
                    break
                except:
                    time.sleep(backoff)
                    backoff += 5

            if not is_online:
                offline_products.append(uid)
                print(offline_products)
            else:
                try:
                    download_one(
                        calculate_raw_indexes=calculate_raw_indexes,
                        product_title=products_df["title"][uid],
                        uid=uid,
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
                        dhus_host=dhus_host,
                        dhus_username=dhus_username,
                        dhus_password=dhus_password,
                    )
                except Exception as e:
                    print(e)
                    error_products.append(uid)
                    continue

        print("Error products: " + str(error_products))
        print("Offline products: " + str(offline_products))

        for uid in offline_products:
            while True:
                try:
                    sentinel_api.trigger_offline_retrieval(uid)
                    print(
                        "Waiting 15 minutes to ask permission for a offline product ..."
                    )
                    time.sleep(450)
                    break
                except Exception as e:
                    print(e)
                    pass

        while len(error_products) > 0 or len(offline_products) > 0:
            for uid in error_products:
                try:
                    download_one(
                        calculate_raw_indexes=calculate_raw_indexes,
                        product_title=products_df["title"][uid],
                        uid=uid,
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
                        dhus_host=dhus_host,
                        dhus_username=dhus_username,
                        dhus_password=dhus_password,
                    )
                    error_products.remove(uid)
                except Exception as e:
                    print(e)
                    continue

            for uid in offline_products:
                print("Offline products")
                backoff = 5
                while True:
                    try:
                        is_online = sentinel_api.is_online(uid)
                        break
                    except:
                        time.sleep(backoff)
                        backoff += 5
                if is_online:
                    offline_products.remove(uid)
                    try:
                        download_one(
                            calculate_raw_indexes=calculate_raw_indexes,
                            product_title=products_df["title"][uid],
                            uid=uid,
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
                            dhus_host=dhus_host,
                            dhus_username=dhus_username,
                            dhus_password=dhus_password,
                        )
                    except Exception as e:
                        print(e)
                        error_products.append(uid)
                        continue

            print("Waiting 1 hour ...")
            print("Error products: " + str(error_products))
            print("Offline products: " + str(offline_products))
            time.sleep(3600)


def search(
    sentinel_api: SentinelAPI,
    footprint: str,
    cloud_limit_soft: int,
    start_date: datetime,
    end_date: datetime,
    limit_download=None,
    **kwargs,
) -> Tuple[pd.DataFrame, pd.Series]:
    # Search is limited to those scenes that intersect with the AOI (area of interest) polygon
    str_filename = f"S2*_{kwargs.get('filename', '*')}_*"

    products = sentinel_api.query(
        area=footprint,
        filename=str_filename,
        producttype="S2MSI2A",
        platformname="Sentinel-2",
        cloudcoverpercentage=(0, cloud_limit_soft),
        date=(start_date, end_date),
        order_by="cloudcoverpercentage, +beginposition",
        limit=limit_download,
    )

    # Get the list of products
    products_df = sentinel_api.to_dataframe(products)
    ids = products_df.index

    return (products_df, ids)


def compute_full_coverage(
    sentinel_api: SentinelAPI,
    footprint: str,
    cloud_limit_soft: int,
    cloud_limit_max: int,
    cloud_limit_composite: int,
    start_date: datetime,
    end_date: datetime,
) -> Tuple[pd.DataFrame, pd.Index]:
    # Gets at least one product for tile in footprint area
    from_date = datetime.strptime("2021-01-01", "%Y-%m-%d")
    to_date = datetime.strptime("2021-01-20", "%Y-%m-%d")

    full_coverage_products, _ = search(
        sentinel_api, footprint, 100, from_date, to_date
    )

    # Extracts tile ID in products_list
    tiles = pd.Series(full_coverage_products["title"]).str[38:44]
    tiles = pd.unique(tiles)

    # Search products for tiles
    result_products = pd.DataFrame()
    result_ids = result_products.index

    for tile in tiles:
        new_products = pd.DataFrame()
        cover = cloud_limit_soft
        while new_products.empty:
            if cover <= cloud_limit_max:
                new_products, new_ids = search(
                    sentinel_api,
                    footprint,
                    cover,
                    start_date,
                    end_date,
                    limit_download=3,
                    filename=f"{tile}",
                )  # Download only one product per tile

            else:
                new_products, new_ids = search(
                    sentinel_api,
                    footprint,
                    cover,
                    start_date,
                    end_date,
                    filename=f"{tile}",
                )

            cover += 5
            time.sleep(1)

        result_products = pd.concat(
            [result_products, new_products], axis=0, join="outer"
        )
        result_ids = result_ids.append(new_ids)

        if cover - 5 > cloud_limit_composite:
            print("Warning: maximum clouds limit exceeded for tile: ", tile)
        time.sleep(1)

    return (result_products, result_ids)


if __name__ == "__main__":
    typer.run(script)

import json
import logging
import os
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import geojson
import geomet.wkt
import requests
from dateutil import parser as dparser

from ds_download.download_from_google_cloud import download_one_google_cloud
from ds_download.observability import configure_logging


logger = logging.getLogger(__name__)


def _load_season_date_ranges(seasons_path: str) -> list:
    with open(seasons_path) as f:
        seasons = json.load(f)

    return [
        (
            season_name,
            datetime.strptime(season["start"], "%Y-%m-%d"),
            datetime.strptime(season["end"], "%Y-%m-%d") + timedelta(days=1),
        )
        for season_name, season in seasons.items()
    ]


def _cleanup_tile_non_composites_from_minio(tile_id: str) -> None:
    from ds_download.minio_connection import MinioConnection

    minio_client = MinioConnection()
    bucket_name = minio_client.bucket_name
    objects = list(minio_client.list_objects(bucket_name, prefix=f"{tile_id}/", recursive=True))
    deleted = 0
    for obj in objects:
        if "/composites/" in obj.object_name:
            continue
        minio_client.remove_object(bucket_name=bucket_name, object_name=obj.object_name)
        deleted += 1
    logger.info(
        "deleted non-composite tile objects",
        extra={"s2.tile": tile_id, "minio.bucket": bucket_name, "object.count": deleted},
    )


def _run_seasonal_pipeline_for_tile(
    tile_id: str,
    seasons_path: str,
    min_useful_data_percentage: float,
    cleanup_products: bool,
    quantize_rasters: bool,
) -> None:
    from ds_download.compute_composite import create_composite_by_tile_and_date

    for season_name, start_date, end_date in _load_season_date_ranges(seasons_path):
        logger.info(
            "seasonal pipeline started",
            extra={
                "s2.tile": tile_id,
                "pipeline.season": season_name,
                "pipeline.start_date": start_date.isoformat(),
                "pipeline.end_date": end_date.isoformat(),
            },
        )
        download_product_using_sentinel_api(
            calculate_raw_indexes=True,
            calculate_intermediate_products=True,
            from_date=start_date,
            to_date=end_date,
            tile_id=tile_id,
            quantize_rasters=quantize_rasters,
        )
        create_composite_by_tile_and_date(
            calculate_raw_indexes=False,
            calculate_intermediate_products=False,
            tile=tile_id,
            start_date=start_date,
            end_date=end_date,
            min_useful_data_percentage=min_useful_data_percentage,
            method="median",
            include_product_indexes=True,
            period="seasonal",
            period_label=season_name,
            storage_year=start_date.year,
            storage_period=season_name,
            cleanup_products=cleanup_products,
            quantize_rasters=quantize_rasters,
        )
        if cleanup_products:
            _cleanup_tile_non_composites_from_minio(tile_id)
        logger.info(
            "seasonal pipeline finished",
            extra={"s2.tile": tile_id, "pipeline.season": season_name},
        )


def to_wkt(geojson_file: str, decimals: int = 4) -> str:
    """
    Convert a GeoJSON file to its Well-Known Text (WKT) representation.

    This function reads a GeoJSON file, extracts the geometry of the first 
    feature, and converts it to WKT format with a specified number of decimal places. 
    It also removes unnecessary spaces from the WKT string for a more compact output.

    Args:
        geojson_file (str): Path to the GeoJSON file containing geospatial data.
        decimals (int, optional): Number of decimal places to include in the WKT 
                                  coordinates. Defaults to 4.

    Returns:
        str: A Well-Known Text (WKT) string representing the geometry.
    """
    with open(geojson_file) as f:
        geojson_ = geojson.load(f)

    geometry = geojson_["features"][0]["geometry"]
    wkt = geomet.wkt.dumps(geometry, decimals=decimals)

    # Strip unnecessary spaces
    wkt = re.sub(r"(?<!\d) ", "", wkt)
    return wkt


def dict_to_camel_case_and_str_to_date(d: dict) -> dict:
    """
    Convert dictionary keys to camelCase and parse date strings to datetime objects.

    This function processes a nested dictionary, converting all its keys to camelCase 
    (i.e., making the first letter lowercase) and parsing any value that contains "date" 
    in its key to a `datetime` object. It applies these transformations recursively for 
    any nested dictionaries.

    Args:
        d (dict): The dictionary whose keys will be converted to camelCase and where 
                  date strings will be parsed to `datetime` objects.

    Returns:
        dict: A new dictionary with camelCase keys and date strings converted to `datetime` objects.

    Raises:
        ValueError: If a date string cannot be parsed into a valid date.
    """
    new_dict = {}
    for k, v in d.items():
        new_key = k[0].lower() + k[1:] if k else k
        if isinstance(v, dict):
            new_dict[new_key] = dict_to_camel_case_and_str_to_date(v)
        else:
            new_dict[new_key] = v if "date" not in k.lower() else dparser.parse(v)
    return new_dict


def find_products_sentinel_api_by_tile_id(tile_id: str, from_date: str, to_date: str) -> list:
    """
    Find Sentinel-2 products using the Copernicus Open Access Hub API by tile ID.

    Args:
        tile_id (str): Sentinel-2 tile ID.
        from_date (str): Start date for the search, in the format 'YYYY-MM-DD'.
        to_date (str): End date for the search, in the format 'YYYY-MM-DD'.

    Returns:
        list: A list of Sentinel-2 products matching the search criteria.
    """
    response = requests.get(
        f"https://catalogue.dataspace.copernicus.eu/odata/v1/Products?$filter=Collection/Name eq 'SENTINEL-2' and contains(Name,'MSIL2A') and contains(Name,'{tile_id}') and ContentDate/Start ge {from_date}T00:00:00.000Z and ContentDate/Start lt {to_date}T00:00:00.000Z&$top=1000"
    ).json()["value"]
    
    return response


def find_products_sentinel_api_by_geojson_file(geojson_path: str, from_date: str, to_date: str) -> list:
    """
    Find Sentinel-2 products using the Copernicus Open Access Hub API by a GeoJSON file.

    Args:
        geojson_path (str): Path to the GeoJSON file for the search.
        from_date (str): Start date for the search, in the format 'YYYY-MM-DD'.
        to_date (str): End date for the search, in the format 'YYYY-MM-DD'.

    Returns:
        list: A list of Sentinel-2 products matching the search criteria.
    """
    footprint = to_wkt(geojson_path)
    response = requests.get(
        f"https://catalogue.dataspace.copernicus.eu/odata/v1/Products?$filter=Collection/Name eq 'SENTINEL-2' and contains(Name,'MSIL2A') and OData.CSC.Intersects(area=geography'SRID=4326;{footprint}') and ContentDate/Start ge {from_date}T00:00:00.000Z and ContentDate/Start lt {to_date}T00:00:00.000Z&$top=1000"
    ).json()["value"]
    
    return response


def download_product_using_sentinel_api(
    calculate_raw_indexes: bool, 
    calculate_intermediate_products: bool,
    from_date: datetime = None,
    to_date: datetime = None,
    geojson_path: str = None, 
    tile_id: str = None,
    quantize_rasters: bool = False,
    run_seasonal_pipeline: bool = False,
    seasons_path: str = "app_data/seasons.json",
    min_useful_data_percentage: float = 30,
    cleanup_products: bool = True,
) -> None:
    """
    Download Sentinel-2 products using the Copernicus Open Access Hub API.

    Args:
        calculate_raw_indexes (bool): Whether to calculate raw indexes for the products.
        calculate_intermediate_products (bool): Whether to calculate intermediate products for the products.
        from_date (datetime): Start date for the search.
        to_date (datetime): End date for the search.
        geojson_path (str, optional): Path to the GeoJSON file for spatial search.
        tile_id (str, optional): Sentinel-2 tile ID for the search.
        run_seasonal_pipeline (bool): Run the one-tile seasonal pipeline from seasons_path.
        seasons_path (str): JSON file with season start/end dates.
        cleanup_products (bool): Delete non-composite MinIO objects after each seasonal composite.

    Returns:
        None
    """
    configure_logging()
    if run_seasonal_pipeline:
        if not tile_id:
            raise ValueError("tile_id is required when run_seasonal_pipeline is True.")
        _run_seasonal_pipeline_for_tile(
            tile_id,
            seasons_path,
            min_useful_data_percentage,
            cleanup_products,
            quantize_rasters,
        )
        return

    if not isinstance(from_date, datetime):
        raise ValueError("from_date must be a datetime object.")
    if not isinstance(to_date, datetime):
        raise ValueError("to_date must be a datetime object.")

    from_date = from_date.strftime("%Y-%m-%d")
    to_date = to_date.strftime("%Y-%m-%d")
    tiles = set()
    tmp_dir = Path(os.environ["TMP_DIR"])
    tmp_dir.mkdir(parents=True, exist_ok=True)

    if tile_id:
        logger.info(
            "sentinel product search started",
            extra={
                "s2.tile": tile_id,
                "pipeline.from_date": from_date,
                "pipeline.to_date": to_date,
            },
        )
        response = find_products_sentinel_api_by_tile_id(tile_id, from_date, to_date)
    elif geojson_path:
        logger.info(
            "sentinel product search started",
            extra={
                "geojson.path": geojson_path,
                "pipeline.from_date": from_date,
                "pipeline.to_date": to_date,
            },
        )
        response = find_products_sentinel_api_by_geojson_file(geojson_path, from_date, to_date)
    else:
        raise ValueError("You must provide either a GeoJSON file or a tile ID.")

    logger.info(
        "sentinel product search finished",
        extra={
            "s2.tile": tile_id,
            "geojson.path": geojson_path,
            "product.count": len(response),
        },
    )

    for product_metadata in response:
        for key in list(product_metadata):
            if "." in key:
                new_key = key.replace(".", "_")
                product_metadata[new_key] = product_metadata.pop(key)
        product_metadata = dict_to_camel_case_and_str_to_date(product_metadata)
        product_title = product_metadata["name"].replace(".SAFE", "")
        tiles.add(product_title.split("_T")[1][0:5])
        try:
            logger.info(
                "product download started",
                extra={"s2.product": product_title, "s2.tile": product_title.split("_T")[1][0:5]},
            )
            download_one_google_cloud(
                calculate_raw_indexes,
                calculate_intermediate_products,
                product_title,
                product_metadata,
                quantize_rasters=quantize_rasters,
            )
            logger.info(
                "product download finished",
                extra={"s2.product": product_title, "s2.tile": product_title.split("_T")[1][0:5]},
            )
        except Exception:
            logger.exception(
                "product download failed",
                extra={"s2.product": product_title, "s2.tile": product_title.split("_T")[1][0:5]},
            )
            raise
        finally:
            shutil.rmtree(tmp_dir / product_title, ignore_errors=True)

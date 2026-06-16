import json
import os
import re
import structlog

import geojson
import geomet.wkt
import requests

import geopandas as gpd

from collections import defaultdict
from datetime import datetime
from dateutil import parser as dparser
from requests.adapters import HTTPAdapter
from shapely.geometry import shape
from urllib3.util.retry import Retry

from ds_download.download_from_google_cloud import download_one_google_cloud

logger = structlog.get_logger(__file__)


CATALOGUE_TIMEOUT = (10, 120)
CATALOGUE_RETRIES = 3


def catalogue_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=CATALOGUE_RETRIES,
        connect=CATALOGUE_RETRIES,
        read=CATALOGUE_RETRIES,
        status=CATALOGUE_RETRIES,
        backoff_factor=2,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def get_catalogue_products(url: str) -> list:
    response = catalogue_session().get(url, timeout=CATALOGUE_TIMEOUT)
    response.raise_for_status()
    return response.json()["value"]


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
    if len(str(geometry)) <= 500:
        wkt = geomet.wkt.dumps(geometry, decimals=decimals) 
    else:
        gdf = gpd.read_file(geojson_file)

        minx, miny, maxx, maxy = gdf.total_bounds

        wkt = (
            f"POLYGON(("
            f"{minx} {miny},"
            f"{maxx} {miny},"
            f"{maxx} {maxy},"
            f"{minx} {maxy},"
            f"{minx} {miny}"
            f"))"
        )
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
    response = get_catalogue_products(
        f"https://catalogue.dataspace.copernicus.eu/odata/v1/Products?$filter=Collection/Name eq 'SENTINEL-2' and contains(Name,'MSIL2A') and contains(Name,'{tile_id}') and ContentDate/Start gt {from_date}T00:00:00.000Z and ContentDate/Start lt {to_date}T00:00:00.000Z&$top=1000"
    )
    
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
    response = get_catalogue_products(
        f"https://catalogue.dataspace.copernicus.eu/odata/v1/Products?$filter=Collection/Name eq 'SENTINEL-2' and contains(Name,'MSIL2A') and OData.CSC.Intersects(area=geography'SRID=4326;{footprint}') and ContentDate/Start gt {from_date}T00:00:00.000Z and ContentDate/Start lt {to_date}T00:00:00.000Z&$top=1000"
    )
    
    return response


def filter_response_by_anchor_tile(response_list: list, geojson_path: str) -> list:
    """
    Filters out redundant overlapping tiles for the same day.
    If one tile consistently covers the parcel best across all samples for a date,
    only that tile's products are kept. If no anchor tile meets the criteria, 
    the products for the best and second best coverage tiles are retained.
    """
    if not response_list:
        return response_list

    # Load the parcel geometry as a Shapely object
    with open(geojson_path, "r") as f:
        geojson_data = json.load(f)
    
    parcel_geom = shape(geojson_data["features"][0]["geometry"])
    parcel_area = parcel_geom.area
    if parcel_area == 0:
        logger.warning("Parcel geometry has zero area; skipping anchor tile filtering.")
        return response_list

    # Single-sweep initialization combining date grouping, metrics calculation, and tracking
    # Group structure per date: {"tile_id": [ratios...]}, {"tile_id": [products...]}
    date_coverages = defaultdict(lambda: defaultdict(list))
    date_products = defaultdict(lambda: defaultdict(list))
    date_raw_products = defaultdict(list)
    date_valid_flag = defaultdict(bool)
    
    # Sweep 1: Single loop to group, calculate intersections, and track metadata properties
    for item in response_list:
        name = item.get("Name", "")
        date_value = item.get("ContentDate", {}).get("Start")
        if not name or not date_value:
            continue
        date_str = str(date_value)[:10]
        
        # Save a reference to the global daily collection
        date_raw_products[date_str].append(item)
        
        footprint_data = item.get("GeoFootprint") or item.get("Footprint")
        tile_id = name.split("_T")[-1][:5]
        
        if not footprint_data:
            logger.debug(f"{date_str} | {tile_id} | no footprint available, keeping product for now.")
            continue

        try:
            tile_geom = shape(footprint_data)
            overlap = tile_geom.intersection(parcel_geom).area
            coverage_ratio = overlap / parcel_area if parcel_area else 0
            date_coverages[date_str][tile_id].append(coverage_ratio)
            date_products[date_str][tile_id].append((item, coverage_ratio))
            date_valid_flag[date_str] = True
            logger.info(f"{date_str} | {tile_id} | sample coverage={coverage_ratio:.4f}")
        except Exception as exc:
            logger.warning(f"{date_str} | {tile_id} | failed to compute footprint coverage: {exc}")
        
    filtered_response = []

    # Final pixel extraction step without nested data parsing loops
    for date_str, raw_products in date_raw_products.items():
        if len(raw_products) == 1:
            filtered_response.append(raw_products[0])
            continue

        if not date_valid_flag[date_str]:
            logger.warning(f"{date_str} | no valid tile footprint coverage computed, keeping all products.")
            filtered_response.extend(raw_products)
            continue

        # Compute mean coverages from collected maps data structures
        tile_mean_coverage = {
            tile_id: sum(ratios) / len(ratios)
            for tile_id, ratios in date_coverages[date_str].items()
        }

        # Sort tiles by mean coverage descending to instantly extract 1st and 2nd best paths
        sorted_tiles = sorted(tile_mean_coverage.items(), key=lambda x: x[1], reverse=True)
        
        best_tile_id, best_mean = sorted_tiles[0]
        logger.info(
            f"{date_str} | tile mean coverages={ {k: round(v, 4) for k, v in tile_mean_coverage.items()} } | best={best_tile_id}:{best_mean:.4f}"
        )

        if best_mean > 0.9:
            anchor_products = [prod for prod, _ in date_products[date_str][best_tile_id]]
            filtered_response.extend(anchor_products)
            logger.info(
                f"Anchor tile filter active: selecting tile {best_tile_id} for date {date_str} with mean coverage {best_mean:.4f}."
            )
        else:
            # Fallback logic: Fetch products for best and second best coverage tiles
            fallback_products = [prod for prod, _ in date_products[date_str][best_tile_id]]
            
            second_best_log_str = "None"
            if len(sorted_tiles) > 1:
                second_best_tile_id, second_best_mean = sorted_tiles[1]
                second_best_log_str = f"{second_best_tile_id}:{second_best_mean:.4f}"
                second_best_products = [prod for prod, _ in date_products[date_str][second_best_tile_id]]
                fallback_products.extend(second_best_products)
                
            logger.debug(f"Parcel is not fully contained by a single tile on {date_str} (best mean coverage {best_mean:.4f}). ")
            logger.debug(f"Retaining best and second best coverage tiles ({best_tile_id}:{best_mean:.4f}, {second_best_log_str}).")
            filtered_response.extend(fallback_products)

    return filtered_response

def download_product_using_sentinel_api(
    calculate_raw_indexes: bool, 
    calculate_intermediate_products: bool,
    from_date: datetime, 
    to_date: datetime, 
    geojson_path: str = None, 
    tile_id: str = None,
    required_bands: list[str] = None,
    quantize: bool = True,
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
        required_bands (list[str], optional): Sentinel band filenames to download, for example ["B03_10m", "B08_10m"].
        quantize (bool, optional): If 'True', it quantizes the resulting `.tif` indexes from `float32` to `int16` to reduce file size. Defaults to True.

    Returns:
        None
    """
    if not isinstance(from_date, datetime):
        raise ValueError("from_date must be a datetime object.")
    if not isinstance(to_date, datetime):
        raise ValueError("to_date must be a datetime object.")

    from_date = from_date.strftime("%Y-%m-%d")
    to_date = to_date.strftime("%Y-%m-%d")
    tiles = set()
    tmp_dir = os.environ.get("TMP_DIR")
    if not os.path.exists(tmp_dir):
        os.makedirs(tmp_dir, exist_ok=True)

    if tile_id:
        response = find_products_sentinel_api_by_tile_id(tile_id, from_date, to_date)
    elif geojson_path:
        response = find_products_sentinel_api_by_geojson_file(geojson_path, from_date, to_date)
    else:
        raise ValueError("You must provide either a GeoJSON file or a tile ID.")
    
    # Remove overlapping tiles from repsonse
    logger.info(f"Initial search returned {len(response)} items across boundaries.\n")
    filetered_response = filter_response_by_anchor_tile(response, geojson_path)
    print()
    logger.info(f"Filtered search context down to {len(filetered_response)} clean execution targets.\n")
    # Download product data
    for product_metadata in filetered_response:
        for key in product_metadata:
            if "." in list(product_metadata.keys()):
                new_key = key.replace(".", "_")
                product_metadata[new_key] = product_metadata.pop(key)
        product_metadata = dict_to_camel_case_and_str_to_date(product_metadata)
        product_title = product_metadata["name"].replace(".SAFE", "")
        tiles.add(product_title.split("_T")[1][0:5])
        download_one_google_cloud(
            calculate_raw_indexes,
            calculate_intermediate_products,
            product_title,
            product_metadata,
            required_bands=required_bands,
            is_geojson=geojson_path is not None,
            geojson_path=geojson_path,
            quantize=quantize,
        )

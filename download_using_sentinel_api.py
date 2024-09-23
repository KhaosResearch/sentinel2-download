import os
import re
import shutil

import geomet.wkt
import requests
from dateutil import parser as dparser

import geojson
from download_from_google_cloud import download_one_google_cloud

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

    # Extract geometry from GeoJSON
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

    Notes:
        - If a key contains the word "date" (case-insensitive), the function attempts to parse the 
          corresponding value as a date.
        - Nested dictionaries are processed recursively.
        - Non-date values remain unchanged.

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

def find_products_sentinel_api_by_tile_id(tile_id, from_date, to_date):
    """
    Find Sentinel-2 products using the Copernicus Open Access Hub API by tile ID.
    """
    response = requests.get(
        f"https://catalogue.dataspace.copernicus.eu/odata/v1/Products?$filter=Collection/Name eq 'SENTINEL-2' and contains(Name,'MSIL2A') and contains(Name,'{tile_id}') and ContentDate/Start gt {from_date}T00:00:00.000Z and ContentDate/Start lt {to_date}T00:00:00.000Z&$top=1000"
    ).json()["value"]
        
    return response

def find_products_sentinel_api_by_geojson_file(geojson_path, from_date, to_date):
    """
    Find Sentinel-2 products using the Copernicus Open Access Hub API by a GeoJSON file.
    """
    footprint = to_wkt(geojson_path)
    response = requests.get(
        f"https://catalogue.dataspace.copernicus.eu/odata/v1/Products?$filter=Collection/Name eq 'SENTINEL-2' and contains(Name,'MSIL2A') and OData.CSC.Intersects(area=geography'SRID=4326;{footprint}') and ContentDate/Start gt {from_date}T00:00:00.000Z and ContentDate/Start lt {to_date}T00:00:00.000Z&$top=1000"
    ).json()["value"]
    return response

def download_product_using_sentinel_api(from_date, to_date, geojson_path=None, tile_id=None):
    tiles = set()
    tmp_dir = os.environ.get("TMP_DIR")
    if not os.path.exists(tmp_dir):
        os.mkdir(tmp_dir)
    if tile_id is not None:
        response = find_products_sentinel_api_by_tile_id(tile_id, from_date, to_date)
    elif geojson_path is not None:
        response = find_products_sentinel_api_by_geojson_file(geojson_path, from_date, to_date)
    else:
        raise ValueError("You must provide a GeoJSON file or a tile ID.")
    for product_metadata in response:
        product_metadata = dict_to_camel_case_and_str_to_date(product_metadata)
        product_metadata["title"] = product_metadata["name"].replace(".SAFE", "")
        tiles.add(product_metadata["title"].split("_T")[1][0:5])
        download_one_google_cloud(False, True, product_metadata["title"], metadata=product_metadata)
    shutil.rmtree(tmp_dir)

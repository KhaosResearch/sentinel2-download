import argparse
import os
import re
import shutil
from pathlib import Path

import geomet.wkt
import requests
from dateutil import parser as dparser
from landcoverpy.execution_mode import ExecutionMode
from minio import Minio
from pymongo import MongoClient

import geojson
from download_one_google_cloud import download_one_google_cloud
from landcoverpy_cover import create_composite, get_products_by_tile_and_date

def to_wkt(geojson_file: Path, decimals: int = 4) -> str:
    with open(geojson_file) as f:
        geojson_ = geojson.load(f)

    # Extract geometry from GeoJSON
    geometry = geojson_["features"][0]["geometry"]

    wkt = geomet.wkt.dumps(geometry, decimals=decimals)
    # Strip unnecessary spaces
    wkt = re.sub(r"(?<!\d) ", "", wkt)
    return wkt

def dict_to_camel_case_and_str_to_date(d: dict) -> dict:
    new_dict = {}
    for k, v in d.items():
        new_key = k[0].lower() + k[1:] if k else k
        new_dict[new_key] = dict_to_camel_case_and_str_to_date(v) if isinstance(v, dict) else (v if "date" not in k.lower() else dparser.parse(v))
    return new_dict

def main():
    # Configuración de argparse para recibir argumentos desde la línea de comandos
    parser = argparse.ArgumentParser(description="Descarga productos Sentinel-2 desde Copernicus utilizando parámetros específicos.")
    parser.add_argument("--geojson", type=str, required=False, default=None, help="Ruta al archivo GeoJSON.")
    parser.add_argument("--tile", type=str, required=False, default=None, help="ID del tile como alternativa al geojson.")
    parser.add_argument("--from-date", type=str, required=True, help="Fecha de inicio en formato aaaa-mm-dd.")
    parser.add_argument("--to-date", type=str, required=True, help="Fecha de fin en formato aaaa-mm-dd.")
    
    args = parser.parse_args()

    # Uso de los argumentos proporcionados
    geojson_path = args.geojson
    tile_id = args.tile
    from_date = args.from_date
    to_date = args.to_date
    
    if geojson_path is None and tile_id is None:
        raise ValueError("Debe proporcionar un archivo GeoJSON o un ID de tile.")
    
    if geojson_path is not None and tile_id is not None:
        raise ValueError("Debe proporcionar un archivo GeoJSON o un ID de tile, no ambos.")
    
    if geojson_path is not None:
        footprint = to_wkt(Path(geojson_path))
        # Petición a la API de Copernicus
        response = requests.get(
            f"https://catalogue.dataspace.copernicus.eu/odata/v1/Products?$filter=Collection/Name eq 'SENTINEL-2' and contains(Name,'MSIL2A') and OData.CSC.Intersects(area=geography'SRID=4326;{footprint}') and ContentDate/Start gt {from_date}T00:00:00.000Z and ContentDate/Start lt {to_date}T00:00:00.000Z&$top=1000"
        ).json()["value"]
    else:
        response = requests.get(
            f"https://catalogue.dataspace.copernicus.eu/odata/v1/Products?$filter=Collection/Name eq 'SENTINEL-2' and contains(Name,'MSIL2A') and contains(Name,'{tile_id}') and ContentDate/Start gt {from_date}T00:00:00.000Z and ContentDate/Start lt {to_date}T00:00:00.000Z&$top=1000"
        ).json()["value"]
        
        
    tiles = set()
    tmp_dir = os.environ.get("TMP_DIR")
    os.mkdir(tmp_dir)
    for product in response:
        camel_case_product = dict_to_camel_case_and_str_to_date(product)
        camel_case_product["title"] = camel_case_product["name"].replace(".SAFE", "")
        tiles.add(camel_case_product["title"].split("_T")[1][0:5])

        download_one_google_cloud(False, True, camel_case_product["title"], temp_dir=tmp_dir, metadata=camel_case_product)
    shutil.rmtree(tmp_dir)
        
        
    # 1. Conectar a las bases de datos
    ## Connect with mongo
    mongo_host = os.environ.get("MONGO_HOST")
    mongo_port = os.environ.get("MONGO_PORT")
    mongo_username = os.environ.get("MONGO_USERNAME")
    mongo_password = os.environ.get("MONGO_PASSWORD")
    mongo_database_name = os.environ.get("MONGO_DATABASE_NAME")
    mongo_collection_name = os.environ.get("MONGO_COLLECTION_NAME")
    
    mongo_client = MongoClient(
        "mongodb://" + mongo_host + ":" + mongo_port + "/",
        username=mongo_username,
        password=mongo_password,
    )
    mongo_db = mongo_client[mongo_database_name]
    mongo_col = mongo_db[mongo_collection_name]
    
    ## Connect with minio
    minio_host = os.environ.get("MINIO_HOST")
    minio_port = os.environ.get("MINIO_PORT")
    minio_access_key = os.environ.get("MINIO_ACCESS_KEY")
    minio_secret_key = os.environ.get("MINIO_SECRET_KEY")
    minio_client = Minio(
        minio_host + ":" + minio_port,
        access_key=minio_access_key,
        secret_key=minio_secret_key,
        secure=False,
    )
    
    for tile in tiles:
        # 2. Obtener la lista de products del rango de fechas de entrada con un porcentaje de nubes inferior a 30%
        ## Obtener la lista de products
        composite_products = get_products_by_tile_and_date(
            tile=tile,
            mongo_collection=mongo_col,
            start_date=dparser.parse(from_date),
            end_date=dparser.parse(to_date),
            cloud_percentage=30
        )
        
        # 3. Hacer composite
        ## Create composite
        minio_bucket_name = os.environ.get("MINIO_BUCKET_NAME")
        minio_composite_bucket_name = os.environ.get("MINIO_COMPOSITE_BUCKET_NAME")
        mongo_composite_collection_name = os.environ.get("MONGO_COMPOSITE_COLLECTION_NAME")
        execution_mode = ExecutionMode.LAND_COVER_PREDICTION
        products_metadata = list(composite_products)
        create_composite(
            products_metadata=products_metadata,
            minio_client=minio_client,
            bucket_products=minio_bucket_name,
            bucket_composites=minio_composite_bucket_name,
            mongo_composites_collection=mongo_db[mongo_composite_collection_name],
            execution_mode=execution_mode
        )

if __name__ == "__main__":
    main()

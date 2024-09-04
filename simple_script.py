import argparse
from download_one_google_cloud import download_one_google_cloud
import requests
from pathlib import Path
import geojson
import geomet.wkt
import re
import os
import shutil

def to_wkt(geojson_file: Path, decimals: int = 4) -> str:
    with open(geojson_file) as f:
        geojson_ = geojson.load(f)

    # Extract geometry from GeoJSON
    geometry = geojson_["features"][0]["geometry"]

    wkt = geomet.wkt.dumps(geometry, decimals=decimals)
    # Strip unnecessary spaces
    wkt = re.sub(r"(?<!\d) ", "", wkt)
    return wkt

def main():
    # Configuración de argparse para recibir argumentos desde la línea de comandos
    parser = argparse.ArgumentParser(description="Descarga productos Sentinel-2 desde Copernicus utilizando parámetros específicos.")
    parser.add_argument("--geojson", type=str, required=True, help="Ruta al archivo GeoJSON.")
    parser.add_argument("--from-date", type=str, required=True, help="Fecha de inicio en formato aaaa-mm-dd.")
    parser.add_argument("--to-date", type=str, required=True, help="Fecha de fin en formato aaaa-mm-dd.")
    
    args = parser.parse_args()

    # Uso de los argumentos proporcionados
    geojson_path = args.geojson
    from_date = args.from_date
    to_date = args.to_date

    footprint = to_wkt(Path(geojson_path))

    # Petición a la API de Copernicus utilizando los parámetros proporcionados
    response = requests.get(
        f"https://catalogue.dataspace.copernicus.eu/odata/v1/Products?$filter=Collection/Name eq 'SENTINEL-2' and contains(Name,'MSIL2A') and OData.CSC.Intersects(area=geography'SRID=4326;{footprint}') and ContentDate/Start gt {from_date}T00:00:00.000Z and ContentDate/Start lt {to_date}T00:00:00.000Z&$top=1000"
    ).json()["value"]

    for product in response:
        product["title"] = product["Name"]

        download_one_google_cloud(True, product["title"], temp_dir="/home/khaosadmin/tmp_products/", metadata=product)
        shutil.rmtree("/home/khaosadmin/tmp_products/")
        os.mkdir("/home/khaosadmin/tmp_products/")

if __name__ == "__main__":
    main()

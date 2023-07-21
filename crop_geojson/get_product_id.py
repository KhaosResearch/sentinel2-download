import json
from datetime import datetime
from pathlib import Path

import typer
from dotenv import load_dotenv
from sentinelsat.sentinel import SentinelAPI, geojson_to_wkt

load_dotenv()


def product_search(
    geojson_file: Path = typer.Option(
        ...,
        exists=True,
        file_okay=True,
        dir_okay=False,
        writable=False,
        readable=True,
        resolve_path=True,
        help="Path to the GeoJson file which delimits the surface on which the filtering is to be carried out",
    ),
    start_date: datetime = typer.Option(..., help="Start date of the time interval"),
    end_date: datetime = typer.Option(..., help="End date of the time interval"),
    max_cover_percentage: int = typer.Option(
        100, min=1, max=100, help="Maximum percentage of clouds"
    ),
    output_file: Path = typer.Option(
        ...,
        exists=False,
        help="Path of the output file where the identifiers of the products found will be stored",
    ),
    dhus_username: str = typer.Argument(..., envvar="DHUS_USERNAME"),
    dhus_password: str = typer.Argument(..., envvar="DHUS_PASSWORD"),
    dhus_host: str = typer.Argument(..., envvar="DHUS_HOST"),
):
    """
    This component is responsible for searching for product identifiers in the sentinel database. To do this, the start and end dates are specified as input parameters along with a geojson that delimits the area.

    >>> python get_product_id.py --geojson-file /path/to/geojson_file.json --start-date 2018-05-01 --end-date 2018-05-30 --output-file /path/to/ids_file.json --dhus-username username --dhus-password password --dhus-host host
    """

    # Load geojson object from the input file
    with open(geojson_file) as d:
        geojson = json.load(d)
        footprint = geojson_to_wkt(geojson)

    # Initialize Sentinel client
    sentinel_api = SentinelAPI(
        dhus_username,
        dhus_password,
        dhus_host,
        show_progressbars=False,
    )

    print("Searching for products in scene")

    # Search is limited to those scenes that intersect with the AOI (area of interest) polygon
    products = sentinel_api.query(
        area=footprint,
        filename="S2*",
        producttype="S2MSI2A",
        platformname="Sentinel-2",
        cloudcoverpercentage=(0, max_cover_percentage),
        date=(start_date, end_date),
    )

    # Get the list of products
    products_df = sentinel_api.to_dataframe(products)
    ids = list(products_df.index)

    print(f"Found {len(ids)} scenes between {start_date} and {end_date}")

    # Write output file
    output_dict = dict(ids=ids)

    with open(output_file, "w") as json_file:
        json.dump(output_dict, json_file, indent=2)


if __name__ == "__main__":
    typer.run(product_search)

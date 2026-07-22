import argparse
import logging
from datetime import datetime, timedelta
from dask.distributed import Client
from dotenv import load_dotenv
from ds_download.download_using_sentinel_api import download_product_using_sentinel_api
from ds_download.compute_composite import create_composite_by_tile_and_date, get_season_date_ranges, seasonal_composite_exists
from ds_download.observability import configure_logging

# Load environment variables
load_dotenv(".env")

logger = logging.getLogger(__name__)

def process_month(year: int, month: int, tile: str, quantize_rasters: bool = False) -> str:
    """
    Process a month's worth of Sentinel-2 data for a specific tile by downloading products 
    and creating a composite.

    Args:
        year (int): The year to process.
        month (int): The month to process.
        tile (str): The Sentinel-2 tile ID to process.

    Returns:
        str: A message indicating the success or failure of the process.
    """
    configure_logging()
    try:
        init_date = datetime(year, month, 1)
        end_date = (init_date + timedelta(days=31)).replace(day=1)
        logger.info(
            "dask monthly task started",
            extra={"pipeline.year": year, "pipeline.month": month, "s2.tile": tile},
        )
        
        # Download Sentinel-2 products
        download_product_using_sentinel_api(False, True, init_date, end_date, tile_id=tile, quantize_rasters=quantize_rasters)
        
        # Create a composite from the downloaded data
        create_composite_by_tile_and_date(True, False, tile, init_date, end_date, 30, quantize_rasters=quantize_rasters)
        
        message = f"Processed {tile}, {year}-{month}"
        logger.info(
            "dask monthly task finished",
            extra={"pipeline.year": year, "pipeline.month": month, "s2.tile": tile},
        )
        return message

    except Exception as e:
        logger.exception(
            "dask monthly task failed",
            extra={"pipeline.year": year, "pipeline.month": month, "s2.tile": tile},
        )
        return f"Error for {tile}, {year}-{month}: {str(e)}"


def process_season(year: int, season_name: str, start_date: datetime, end_date: datetime, tile: str, cleanup_products: bool, quantize_rasters: bool = False) -> str:
    """
    Process a season's worth of Sentinel-2 data for a specific tile by downloading products
    with per-product indexes and creating seasonal mean rasters.
    """
    configure_logging()
    try:
        logger.info(
            "dask seasonal task started",
            extra={"pipeline.year": year, "pipeline.season": season_name, "s2.tile": tile},
        )
        if seasonal_composite_exists(tile, year, season_name):
            logger.info(
                "seasonal composite already exists; skipping pipeline",
                extra={"pipeline.year": year, "pipeline.season": season_name, "s2.tile": tile},
            )
            return f"Skipped existing {tile}, {year}-{season_name}"

        download_product_using_sentinel_api(
            False,
            True,
            start_date,
            end_date,
            tile_id=tile,
            quantize_rasters=quantize_rasters,
            season_name=season_name,
        )

        create_composite_by_tile_and_date(
            calculate_raw_indexes=False,
            calculate_intermediate_products=False,
            tile=tile,
            start_date=start_date,
            end_date=end_date,
            min_useful_data_percentage=30,
            method="mean",
            include_product_indexes=True,
            period="seasonal",
            period_label=season_name,
            storage_year=year,
            storage_period=season_name,
            cleanup_products=cleanup_products,
            quantize_rasters=quantize_rasters,
        )

        message = f"Processed {tile}, {year}-{season_name}"
        logger.info(
            "dask seasonal task finished",
            extra={"pipeline.year": year, "pipeline.season": season_name, "s2.tile": tile},
        )
        return message

    except Exception as e:
        logger.exception(
            "dask seasonal task failed",
            extra={"pipeline.year": year, "pipeline.season": season_name, "s2.tile": tile},
        )
        return f"Error for {tile}, {year}-{season_name}: {str(e)}"


# Define the tiles to process
tiles = [
    '29SLC', '29SLD', '29SMC', '29SMD', '29SNA', '29SNB', '29SNC',
    '29SND', '29SPA', '29SPB', '29SPC', '29SPD', '29SQA', '29SQB',
    '29SQC', '29SQD', '29SQV', '29THN', '29TME', '29TMH', '29TNE',
    '29TNF', '29TNG', '29TNH', '29TNJ', '29TPE', '29TPF', '29TPG',
    '29TPH', '29TPJ', '29TQE', '29TQF', '29TQG', '29TQH', '29TQJ',
    '30STE', '30STF', '30STG', '30STH', '30STJ', '30SUE', '30SUF',
    '30SUG', '30SUH', '30SUJ', '30SVF', '30SVG', '30SVH', '30SVJ',
    '30SWF', '30SWG', '30SWH', '30SWJ', '30SXF', '30SXG', '30SXH',
    '30SXJ', '30SYH', '30SYJ', '30TTK', '30TTL', '30TTM', '30TUK',
    '30TUL', '30TUM', '30TUN', '30TUP', '30TVK', '30TVL', '30TVM',
    '30TVN', '30TVP', '30TWK', '30TWL', '30TWM', '30TWN', '30TWP',
    '30TXK', '30TXL', '30TXM', '30TXN', '30TYK', '30TYL', '30TYM',
    '30TYN', '31SBC', '31SBD', '31TBE', '31TBF', '31TBG', '31TCF',
    '31TCG', '31TCH', '31TDF', '31TDG', '30TXN', '30TYN', '31TCH',
    '31TDH', '30TXP', '30TYP', '31TCJ', '31TDJ', '31TEJ', '30TXQ',
    '30TYQ', '31TCK', '31TDK', '31TEK', '31TFK', '31TGK', '30TXR',
    '30TYR', '31TCL', '31TDL', '31TEL', '31TLF', '31TGL', '30TXS',
    '30TYS', '31TCM', '31TDM', '31TEM', '31TFM', '31TGM'
]

# Years and months to process
years = [2021]
# TODO: Add May
months = [4, 7, 11, 10, 3, 6]

def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--scheduler", default="<dask-scheduler-host>:<dask-scheduler-port>")
    parser.add_argument("--composite-period", choices=["monthly", "seasonal"], default="monthly")
    parser.add_argument("--keep-products", action="store_true", help="Do not delete product rasters after seasonal composites are uploaded.")
    parser.add_argument("--quantize-rasters", action="store_true", help="Write generated bands and indexes as quantized compressed GeoTIFFs.")
    args = parser.parse_args()

    client = Client(args.scheduler)
    logger.info(
        "dask pipeline started",
        extra={"dask.scheduler": args.scheduler, "pipeline.period": args.composite_period},
    )

    if args.composite_period == "monthly":
        for month in months:
            futures = [
                client.submit(process_month, year, month, tile, args.quantize_rasters)
                for year in years
                for tile in tiles
            ]

            results = client.gather(futures)

            for result in results:
                logger.info("dask task result", extra={"task.result": result})
        return

    for year in years:
        for season_name, start_date, end_date in get_season_date_ranges(year):
            futures = [
                client.submit(
                    process_season,
                    year,
                    season_name,
                    start_date,
                    end_date,
                    tile,
                    not args.keep_products,
                    args.quantize_rasters,
                )
                for tile in tiles
            ]

            results = client.gather(futures)

            for result in results:
                logger.info("dask task result", extra={"task.result": result})


if __name__ == "__main__":
    main()

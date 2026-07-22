import argparse
import logging
import time
from datetime import datetime, timedelta
from ds_download.download_using_sentinel_api import download_product_using_sentinel_api
from ds_download.compute_composite import (
    create_composite_by_tile_and_date,
    get_season_date_ranges,
    seasonal_composite_exists,
)
from ds_download.observability import configure_logging


logger = logging.getLogger(__name__)

# Define the path for the log file
log_file_path = "execution_time_log.txt"

def log_time(file_path: str, year: int, period: str, tile: str, function_name: str, execution_time: float) -> None:
    """
    Log the execution time of a function to a log file.

    Args:
        file_path (str): The path of the log file.
        year (int): The year the process is running for.
        period (str): The month or season the process is running for.
        tile (str): The Sentinel-2 tile being processed.
        function_name (str): The name of the function whose execution time is being logged.
        execution_time (float): The execution time of the function in seconds.

    Returns:
        None
    """
    with open(file_path, 'a') as log_file:
        log_file.write(f"Year: {year}, Period: {period}, Tile: {tile}, Function: {function_name}, Time: {execution_time:.2f} seconds\n")
    logger.info(
        "pipeline step finished",
        extra={
            "pipeline.year": year,
            "pipeline.period": period,
            "s2.tile": tile,
            "function.name": function_name,
            "duration.seconds": round(execution_time, 2),
        },
    )

def main_workflow(
    tiles: list,
    year: int,
    from_month: int,
    to_month: int,
    composite_period: str = "monthly",
    cleanup_products: bool = True,
    quantize_rasters: bool = False,
) -> None:
    """
    Main workflow to download Sentinel-2 products and create composites, logging execution times for each step.

    Args:
        tiles (list): A list of Sentinel-2 tiles to process.
        year (int): The year to process.
        from_month (int): The starting month to process.
        to_month (int): The ending month to process.

    Returns:
        None
    """
    if composite_period == "monthly":
        for month in range(from_month, to_month + 1):
            for tile in tiles:
                try:
                    init_date = datetime(year, month, 1)
                    end_date = (init_date + timedelta(days=31)).replace(day=1)  # End date is the first day of the next month

                    start_time = time.time()
                    download_product_using_sentinel_api(False, True, init_date, end_date, tile_id=tile, quantize_rasters=quantize_rasters)
                    download_time = time.time() - start_time
                    log_time(log_file_path, year, str(month), tile, 'download_product_using_sentinel_api', download_time)

                    start_time = time.time()
                    create_composite_by_tile_and_date(True, False, tile, init_date, end_date, 30, quantize_rasters=quantize_rasters)
                    composite_time = time.time() - start_time
                    log_time(log_file_path, year, str(month), tile, 'create_composite_by_tile_and_date', composite_time)

                except Exception as e:
                    error_message = f"Year: {year}, Month: {month}, Tile: {tile}, Error: {str(e)}"
                    with open(log_file_path, 'a') as log_file:
                        log_file.write(error_message + "\n")
                    logger.exception(
                        "monthly pipeline failed",
                        extra={"pipeline.year": year, "pipeline.month": month, "s2.tile": tile},
                    )
                    continue
        return

    for season_name, init_date, end_date in get_season_date_ranges(year):
        for tile in tiles:
            try:
                if seasonal_composite_exists(tile, year, season_name):
                    logger.info(
                        "seasonal composite already exists; skipping pipeline",
                        extra={"pipeline.year": year, "pipeline.season": season_name, "s2.tile": tile},
                    )
                    continue

                start_time = time.time()
                download_product_using_sentinel_api(
                    False,
                    True,
                    init_date,
                    end_date,
                    tile_id=tile,
                    quantize_rasters=quantize_rasters,
                    season_name=season_name,
                )
                download_time = time.time() - start_time
                log_time(log_file_path, year, season_name, tile, 'download_product_using_sentinel_api', download_time)

                start_time = time.time()
                create_composite_by_tile_and_date(
                    calculate_raw_indexes=False,
                    calculate_intermediate_products=False,
                    tile=tile,
                    start_date=init_date,
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
                composite_time = time.time() - start_time
                log_time(log_file_path, year, season_name, tile, 'create_composite_by_tile_and_date', composite_time)

            except Exception as e:
                error_message = f"Year: {year}, Season: {season_name}, Tile: {tile}, Error: {str(e)}"
                with open(log_file_path, 'a') as log_file:
                    log_file.write(error_message + "\n")
                logger.exception(
                    "seasonal pipeline failed",
                    extra={"pipeline.year": year, "pipeline.season": season_name, "s2.tile": tile},
                )
                continue

if __name__ == "__main__":
    configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--composite-period", choices=["monthly", "seasonal"], default="monthly")
    parser.add_argument("--keep-products", action="store_true", help="Do not delete product rasters after seasonal composites are uploaded.")
    parser.add_argument("--quantize-rasters", action="store_true", help="Write generated bands and indexes as quantized compressed GeoTIFFs.")
    args = parser.parse_args()

    # List of tiles to process
    tiles = ["31STF"]
    
    # Clear the log file at the beginning of the execution
    open(log_file_path, 'w').close()  # This clears the content of the log file

    # Loop through the years from 2018 to 2023 and process each year
    years = range(2018, 2024)
    for year in years:
        main_workflow(
            tiles,
            year,
            1,
            12,
            composite_period=args.composite_period,
            cleanup_products=not args.keep_products,
            quantize_rasters=args.quantize_rasters,
        )

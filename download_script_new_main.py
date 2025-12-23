from .main_script import *
import structlog

logger = structlog.get_logger()

def main_workflow(tiles: list, year: int, from_month: int, to_month: int, delete_products: bool = True) -> None:
    """
    Main workflow to download Sentinel-2 products and create composites, logging execution times for each step.

    Args:
        tiles (list): A list of Sentinel-2 tiles to process.
        year (int): The year to process.
        from_month (int): The starting month to process.
        to_month (int): The ending month to process.
        delete_products (bool): Whether to delete products after processing the composites to save space. Default is `True`.

    Returns:
        None
    """
    for month in range(from_month, to_month + 1):
        for tile in tiles:
            try:
                init_date = datetime(year, month, 1)
                end_date = (init_date + timedelta(days=31)).replace(day=1)  # End date is the first day of the next month
                
                # Measure time for download_product_using_sentinel_api
                start_time = time.time()
                download_product_using_sentinel_api(False, True, init_date, end_date, tile_id=tile)
                download_time = time.time() - start_time
                log_time(log_file_path, year, month, tile, 'download_product_using_sentinel_api', download_time)
                
                # Measure time for create_composite_by_tile_and_date
                start_time = time.time()
                create_composite_by_tile_and_date(True, False, tile, init_date, end_date, 30, delete_products)
                composite_time = time.time() - start_time
                log_time(log_file_path, year, month, tile, 'create_composite_by_tile_and_date', composite_time)

            except Exception as e:
                error_message = f"Year: {year}, Month: {month}, Tile: {tile}, Error: {str(e)}"
                logger.error(str(e))
                with open(log_file_path, 'a') as log_file:
                    log_file.write(error_message + "\n")
                print(error_message)
                continue

if __name__ == "__main__":
    
    # Andalusia Tiles
    tiles = ["29SPC", "29SQC", "30STH", "30SUH", "30SVH", "30SWH", "30SXH", "30SYH", "30SXG", 
            "30SWG", "30SVG", "30SUG", "30STG", "29SQB" ,"29SPB", "30STF", "30SUF", 
            "30SVF", "30SWF"
            ]
    start_year = 2025
    start_month = 1
    end_year = 2025
    end_month = 2

    # Clear the log file at the beginning of the execution
    open(log_file_path, 'w').close()  # This clears the content of the log file

    # Loop through the years and process each year
    years = range(start_year, end_year + 1)
    for year in years:
        main_workflow(tiles, year, start_month, end_month)

import time
from datetime import datetime, timedelta
from download_using_sentinel_api import download_product_using_sentinel_api
from compute_composite import create_composite_by_tile_and_date

# Define the path for a single log file
log_file_path = "execution_time_log.txt"

def log_time(file_path, year, month, tile, function_name, execution_time):
    with open(file_path, 'a') as log_file:
        log_file.write(f"Year: {year}, Month: {month}, Tile: {tile}, Function: {function_name}, Time: {execution_time:.2f} seconds\n")

def main_workflow(tiles, year, from_month, to_month):
    for month in range(from_month, to_month+1):
        for tile in tiles:
            try:
                init_date = datetime(year, month, 1)
                end_date = (init_date + timedelta(days=31)).replace(day=1)  # End date is the first day of next month
                
                # Measure time for download_product_using_sentinel_api
                start_time = time.time()
                download_product_using_sentinel_api(False, True, init_date, end_date, tile_id=tile)
                download_time = time.time() - start_time
                log_time(log_file_path, year, month, tile, 'download_product_using_sentinel_api', download_time)
                
                # Measure time for create_composite_by_tile_and_date
                start_time = time.time()
                create_composite_by_tile_and_date(True, False, tile, init_date, end_date, 30)
                composite_time = time.time() - start_time
                log_time(log_file_path, year, month, tile, 'create_composite_by_tile_and_date', composite_time)
            except Exception as e:
                with open(log_file_path, 'a') as log_file:
                    log_file.write(f"Year: {year}, Month: {month}, Tile: {tile}, Error: {str(e)}\n")
                    print(f"Error: {str(e)}")
                    continue

if __name__ == "__main__":
    tiles = ["30STG", "30SUG", "30SUF", "30STF"]
    
    # Clear the log file at the beginning of the execution
    open(log_file_path, 'w').close()  # This clears the content of the log file
    years = [2018]
    for year in years:
        main_workflow(tiles, year, 1, 12)

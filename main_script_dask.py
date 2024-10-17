from datetime import datetime, timedelta
from dask.distributed import Client
from dotenv import load_dotenv
from ds_download.download_using_sentinel_api import download_product_using_sentinel_api
from ds_download.compute_composite import create_composite_by_tile_and_date

# Load environment variables
load_dotenv(".env")

# Set up the Dask distributed client with the scheduler address
client = Client("<dask-scheduler-host>:<dask-scheduler-port>")

def process_month(year: int, month: int, tile: str) -> str:
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
    try:
        init_date = datetime(year, month, 1)
        end_date = (init_date + timedelta(days=31)).replace(day=1)
        
        # Download Sentinel-2 products
        download_product_using_sentinel_api(False, True, init_date, end_date, tile_id=tile)
        
        # Create a composite from the downloaded data
        create_composite_by_tile_and_date(True, False, tile, init_date, end_date, 30)
        
        return f"Processed {tile}, {year}-{month}"

    except Exception as e:
        return f"Error for {tile}, {year}-{month}: {str(e)}"

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
months = [4, 7, 11, 10, 3, 6]

# Submit Dask tasks for processing the tiles and months
for month in months:
    futures = [
        client.submit(process_month, year, month, tile)
        for year in years
        for tile in tiles
    ]
    
    # Gather the results from all futures
    results = client.gather(futures)
    
    # Print the results of the processing
    for result in results:
        print(result)

from ds_download.download_using_sentinel_api import download_product_using_sentinel_api
from datetime import datetime

# Download products for a specific tile and date range
start_date = datetime(2017, 1, 1)
end_date = datetime(2025, 12, 31)
end_date = datetime.now() if end_date > datetime.now() else end_date

tiles = ["29SPC", "29SQC", "30STH", "30SUH", "30SVH", "30SWH", "30SXH", "30SYH", "30SXG", 
        "30SWG", "30SVG", "30SUG", "30STG", "29SQB" ,"29SPB", "30STF", "30SUF", 
        "30SVF", "30SWF"
        ]
years = range(start_date.year, end_date.year)
for year in years:
    for month in range(1, 13):
        for tile_id in tiles:
            download_product_using_sentinel_api(
                calculate_raw_indexes=False,
                calculate_intermediate_products=True,
                from_date=start_date,
                to_date=end_date,
                tile_id=tile_id
            )
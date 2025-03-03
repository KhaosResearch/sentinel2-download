from datetime import datetime, timedelta
from ds_download.download_using_sentinel_api import download_product_using_sentinel_api
from ds_download.compute_composite import create_composite_by_tile_and_date


if __name__ == "__main__":

    tiles = ["29SPC", "29SQC", "30STH", "30SUH", "30SVH", "30SWH", "30SXH", "30SYH", "30SXG", 
            "30SWG", "30SVG", "30SUG", "30STG", "29SQB" ,"29SPB", "30SQA", "30STF", "30SUF", 
            "30SVF", "30SWF"
            ]

    now = datetime.now() - timedelta(days=2)
    look_from = now - timedelta(days=3)

    for tile in tiles:
        print(f"Looking for new data for {tile} from {look_from} to {now}")
        download_product_using_sentinel_api(
            calculate_raw_indexes=False,
            calculate_intermediate_products=True,
            from_date=look_from,
            to_date=now,
            tile_id=tile
        )

    if now.day == 1:
        init_date = (now - timedelta(days=1)).replace(day=1)
        end_date = datetime(now.year, now.month, 1)

        for tile in tiles:
            print(f"Creating composite for {tile} from {init_date} to {end_date}")
            create_composite_by_tile_and_date(
                calculate_raw_indexes=True,
                calculate_intermediate_products=False,
                tile=tile,
                start_date=init_date,
                end_date=end_date,
                min_useful_data_percentage=30
            )


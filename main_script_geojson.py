
import os
import shutil
import structlog

from argparse import ArgumentParser
from datetime import datetime
from hashlib import sha256
import re
from os.path import join
from pathlib import Path

from dotenv import load_dotenv
from ds_download.download_using_sentinel_api import (
    download_product_using_sentinel_api,
    find_products_sentinel_api_by_geojson_file,
)
from ds_download.minio_connection import MinioConnection
from ds_download.mongo_connection import MongoConnection
from ds_download.raw_index_calculation import compress_and_quantize_tiff
from main_script_europe import month_range, product_title_date, required_bands_for_indexes, write_windowed_mean

logger = structlog.get_logger(__file__)

def _normalize_geojson_name(geojson_path: str) -> str:
    geojson_name = Path(geojson_path).stem
    return re.sub(r"[^A-Za-z0-9_-]+", "_", geojson_name)


def str_to_bool(value: str) -> bool:
    """Convert a string command line argument to a boolean."""
    if isinstance(value, bool):
        return value
    value_lower = value.strip().lower()
    if value_lower in {"true", "t", "yes", "y", "1"}:
        return True
    if value_lower in {"false", "f", "no", "n", "0"}:
        return False
    raise ValueError(f"Boolean value expected for quantize, got '{value}'")


def geojson_composite_title(geojson_name: str, product_titles: list[str]) -> str | None:
    if not product_titles:
        return None

    product_dates = [title.split("_")[2].split("T")[0] for title in product_titles]
    title_hash = sha256("".join(sorted(product_titles)).encode("utf-8")).hexdigest()[:8]
    normalized_name = re.sub(r"[^A-Za-z0-9_-]+", "_", geojson_name)
    return f"S2S_MSIL2A_{min(product_dates)}_NXXX_RXXX_{normalized_name}_{max(product_dates)}_{title_hash}"


def geojson_monthly_index_key(
    geojson_name: str,
    year: int,
    month: int,
    product_titles: list[str],
    index_name: str,
) -> str | None:
    title = geojson_composite_title(geojson_name, product_titles)
    if title is None:
        return None

    month_name = datetime(year, month, 1).strftime("%B")
    return join(
        "geojson",
        geojson_name,
        str(year),
        month_name,
        "composites",
        title,
        "indexes",
        f"{index_name.lower()}.tif",
    )


def geojson_monthly_composite_raw_prefix(
    geojson_name: str,
    year: int,
    month: int,
    title: str,
) -> str:
    month_name = datetime(year, month, 1).strftime("%B")
    return join("geojson", geojson_name, str(year), month_name, "composites", title, "raw", "")


def build_geojson_monthly_composite_metadata(
    geojson_name: str,
    year: int,
    month: int,
    product_titles: list[str],
    bucket_name: str,
) -> dict | None:
    title = geojson_composite_title(geojson_name, product_titles)
    if title is None:
        return None

    products_dates = [product_title_date(product_title) for product_title in product_titles]
    return {
        "title": title,
        "products": [{"title": product_title} for product_title in product_titles],
        "first_date": min(products_dates),
        "last_date": max(products_dates),
        "S3Bucket": bucket_name,
        "S3BandsPrefix": geojson_monthly_composite_raw_prefix(
            geojson_name,
            year,
            month,
            title,
        ),
        "tile": f"geojson/{geojson_name}",
    }


def product_filter_for_tiles(tile_ids: list[str], start_date: datetime, end_date: datetime) -> dict:
    return {
        "tile": {"$in": tile_ids},
        "datetakeSensingTime": {"$gte": start_date, "$lt": end_date},
    }


def get_geojson_tiles_for_month(
    geojson_path: str,
    year: int,
    month: int,
) -> list[str]:
    start_date, end_date = month_range(year, month)
    response = find_products_sentinel_api_by_geojson_file(
        geojson_path,
        start_date.strftime("%Y-%m-%d"),
        end_date.strftime("%Y-%m-%d"),
    )
    tiles = set()
    for product in response:
        product_title = product["Name"].replace(".SAFE", "")
        tile_id = product_title.split("_T")[1][0:5]
        tiles.add(tile_id)
    return sorted(tiles)


def get_geojson_monthly_index_products(
    geojson_path: str,
    year: int,
    month: int,
    index_name: str,
    mongo_col,
) -> list[dict]:
    start_date, end_date = month_range(year, month)
    tiles = get_geojson_tiles_for_month(geojson_path, year, month)
    if not tiles:
        return []

    raster_key = f"indexes.{index_name.lower()}.rasterS3Key"
    return list(mongo_col.find(
        {
            **product_filter_for_tiles(tiles, start_date, end_date),
            raster_key: {"$exists": True},
        },
        {"title": 1, raster_key: 1},
    ))


def create_monthly_geojson_index_mean(
    geojson_path: str,
    year: int,
    month: int,
    index_name: str,
    mongo_col,
    composite_col,
    minio_client: MinioConnection,
    tmp_dir: Path,
) -> str | None:
    geojson_name = _normalize_geojson_name(geojson_path)
    products = get_geojson_monthly_index_products(
        geojson_path,
        year,
        month,
        index_name,
        mongo_col,
    )
    if not products:
        logger.warning(
            f"No products found for '{index_name.upper()}' index for GeoJSON '{geojson_name}' "
            f"for the following params: Year: {year} | Month {month}"
        )
        return None

    local_index_paths = []
    normalized_index_name = index_name.lower()
    for product in products:
        index_key = product["indexes"][normalized_index_name]["rasterS3Key"]
        local_path = tmp_dir / geojson_name / str(year) / f"{month:02}" / f"{product['title']}_{normalized_index_name}.tif"
        local_path.parent.mkdir(parents=True, exist_ok=True)
        minio_client.fget_object(minio_client.bucket_name, index_key, str(local_path))
        local_index_paths.append(local_path)

    product_titles = [product["title"] for product in products]
    object_name = geojson_monthly_index_key(
        geojson_name,
        year,
        month,
        product_titles,
        index_name,
    )
    if object_name is None:
        return None

    month_name = datetime(year, month, 1).strftime("%B")
    local_output = tmp_dir / geojson_name / str(year) / month_name / f"{normalized_index_name}.tif"
    logger.debug(f"LOCAL INDEX PATHS: {local_index_paths}")
    logger.debug(f"LOCAL OUTPUT: {local_output}")
    write_windowed_mean(local_index_paths, local_output)

    # For GeoJSON monthly composites keep float32 output (do not quantize),
    # so visualization and downstream consumers receive correctly scaled values
    # in the [-1, 1] range. This avoids confusion from int16 10000x scaling.
    # compress_and_quantize_tiff(local_output)
    minio_client.fput_object(
        minio_client.bucket_name,
        object_name,
        local_output,
        content_type="image/tif",
    )
    metadata = build_geojson_monthly_composite_metadata(
        geojson_name,
        year,
        month,
        product_titles,
        minio_client.bucket_name,
    )
    if metadata:
        normalized_index_name = index_name.lower()
        composite_col.update_one(
            {"title": metadata["title"]},
            {
                "$set": {
                    **metadata,
                    f"indexes.{normalized_index_name}": {
                        "name": normalized_index_name,
                        "rasterS3Bucket": minio_client.bucket_name,
                        "rasterS3Key": object_name,
                    },
                },
            },
            upsert=True,
        )
    return object_name


def cleanup_geojson_product_data(
    geojson_path: str,
    year: int,
    month: int,
    mongo_col,
    minio_client: MinioConnection,
) -> None:
    start_date, end_date = month_range(year, month)
    tiles = get_geojson_tiles_for_month(geojson_path, year, month)
    if not tiles:
        return

    query = product_filter_for_tiles(tiles, start_date, end_date)
    products = list(mongo_col.find(query, {"title": 1}))

    for product in products:
        title = product["title"]
        try:
            tile_id = title.split("_T")[1][0:5]
            year_str = title.split("_")[2][0:4]
            month_name = datetime.strptime(title.split("_")[2][4:6], "%m").strftime("%B")
            prefix = join(tile_id, year_str, month_name, "products", title, "")
        except Exception as exc:
            logger.warning(f"Could not parse product title for cleanup: {title} ({exc})")
            continue

        for obj in minio_client.list_objects(minio_client.bucket_name, prefix=prefix, recursive=True):
            minio_client.remove_object(minio_client.bucket_name, obj.object_name)

    mongo_col.delete_many(query)


def parse_args():
    parser = ArgumentParser(
        description="Download Sentinel-2 products for a GeoJSON area and calculate monthly indexes."
    )
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument(
        "--geojson-path",
        type=str,
        default="geometry.geojson",
        help="Path to the GeoJSON area file. Defaults to geometry.geojson.",
    )
    parser.add_argument("--from-month", type=int, default=1)
    parser.add_argument("--to-month", type=int, default=12)
    parser.add_argument(
        "--indexes",
        nargs="+",
        default=["NDVI", "NDWI"],
        help="Index names to calculate for each GeoJSON monthly composite. Defaults to NDVI and NDWI.",
    )
    parser.add_argument(
        "--quantize",
        type=str_to_bool,
        default=False,
        help="If True, quantizes the resulting `.tif` indexes from float32 to int16 to reduce file size. Defaults to True.",
    )

    return parser.parse_args()


# Example CLI usage:
# python main_script_geojson.py --year 2024
# python main_script_geojson.py --year 2024 --geojson-path geometry.geojson --from-month 1 --to-month 12
# python main_script_geojson.py --year 2024 --geojson-path geometry.geojson --indexes NDVI NDWI
# Required bands are derived from the selected indexes using required_bands_for_indexes(args.indexes).

def main():
    init_time = datetime.now()
    load_dotenv(".env")
    args = parse_args()
    geojson_name = _normalize_geojson_name(args.geojson_path)
    base_tmp_dir = Path(os.environ.get("TMP_DIR", "tmp"))
    run_tmp_dir = base_tmp_dir / f"geojson_{args.year}_{geojson_name}_{os.getpid()}"
    run_tmp_dir.mkdir(parents=True, exist_ok=True)
    os.environ["TMP_DIR"] = str(run_tmp_dir)

    quantize = args.quantize

    mongo_connection = MongoConnection()
    mongo_col = mongo_connection.get_collection_object()
    composite_col = mongo_connection.get_composite_collection_object()
    minio_client = MinioConnection()
    tmp_dir = run_tmp_dir / geojson_name / "monthly_indexes"

    try:
        for month in range(args.from_month, args.to_month + 1):
            month_start, month_end = month_range(args.year, month)
            month_name = month_start.strftime("%B")
            logger.info(
                f"Downloading products for GeoJSON {args.geojson_path} in {month_name} {args.year}"
            )

            download_product_using_sentinel_api(
                calculate_raw_indexes=True,
                calculate_intermediate_products=False,
                from_date=month_start,
                to_date=month_end,
                geojson_path=args.geojson_path,
                required_bands=required_bands_for_indexes(args.indexes),
                quantize=quantize,
            )

            monthly_outputs = []
            for index_name in args.indexes:
                output_key = create_monthly_geojson_index_mean(
                    args.geojson_path,
                    args.year,
                    month,
                    index_name,
                    mongo_col,
                    composite_col,
                    minio_client,
                    tmp_dir,
                )
                if output_key:
                    monthly_outputs.append(output_key)
                    logger.info(f"Uploaded monthly {index_name} mean: {output_key}")

            if len(monthly_outputs) == len(args.indexes):
                cleanup_geojson_product_data(
                    args.geojson_path,
                    args.year,
                    month,
                    mongo_col,
                    minio_client,
                )
                shutil.rmtree(tmp_dir / str(args.year) / month_name, ignore_errors=True)
            else:
                logger.warning(
                    f"Skipping cleanup for {month_name} {args.year}: not all requested indexes were generated."
                )
    except Exception as e:
        logger.exception("An exception occurred:")
        logger.error(str(e))
        # cleanup_geojson_product_data(
        #             args.geojson_path,
        #             args.year,
        #             month,
        #             mongo_col,
        #             minio_client,
        #         )
    finally:
        shutil.rmtree(run_tmp_dir, ignore_errors=True)
        print()
        logger.info(f"FINAL TIME: {datetime.now() - init_time}")

if __name__ == "__main__":
    main()

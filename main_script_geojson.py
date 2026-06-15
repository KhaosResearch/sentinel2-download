
import os
import shutil
import structlog

from argparse import ArgumentParser
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from ds_download.download_geojson_utils import build_geojson_monthly_composite_metadata, cleanup_geojson_product_data, geojson_monthly_index_key, get_geojson_monthly_index_products, normalize_geojson_name
from ds_download.download_using_sentinel_api import download_product_using_sentinel_api
from ds_download.minio_connection import MinioConnection
from ds_download.mongo_connection import MongoConnection
from ds_download.raw_index_calculation import compress_and_quantize_tiff

from main_script_europe import month_range, required_bands_for_indexes, write_windowed_mean

logger = structlog.get_logger(__file__)

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
    geojson_name = normalize_geojson_name(geojson_path)
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
        type=_str_to_bool,
        default=False,
        help="If True, quantizes the resulting `.tif` indexes from float32 to int16 to reduce file size. Defaults to True.",
    )

    return parser.parse_args()


def main():
    init_time = datetime.now()
    load_dotenv(".env")
    
    # Prepare input pipeline
    args = parse_args()
    geojson_name = normalize_geojson_name(args.geojson_path)
    base_tmp_dir = Path(os.environ.get("TMP_DIR", "tmp"))
    run_tmp_dir = base_tmp_dir / f"geojson_{args.year}_{geojson_name}_{os.getpid()}"
    run_tmp_dir.mkdir(parents=True, exist_ok=True)
    os.environ["TMP_DIR"] = str(run_tmp_dir)

    quantize = args.quantize

    # Prepare connections and file structure
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
        cleanup_geojson_product_data(
                    args.geojson_path,
                    args.year,
                    month,
                    mongo_col,
                    minio_client,
                )
    finally:
        shutil.rmtree(run_tmp_dir, ignore_errors=True)
        print()
        logger.info(f"FINAL TIME: {datetime.now() - init_time}")

if __name__ == "__main__":
    main()
    # Example CLI usage:
    # python main_script_geojson.py --year 2024
    # python main_script_geojson.py --year 2024 --geojson-path geometry.geojson --from-month 1 --to-month 12
    # python main_script_geojson.py --year 2024 --geojson-path geometry.geojson --indexes NDVI NDWI --quantize False
    # Required bands are derived from the selected indexes using required_bands_for_indexes(args.indexes).

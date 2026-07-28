import argparse
import logging
from datetime import datetime, timedelta
from dask.distributed import Client, as_completed
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


def process_season(year: int, season_name: str, start_date: datetime, end_date: datetime, tile: str, cleanup_products: bool, quantize_rasters: bool = False) -> dict:
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
            return {"status": "skipped", "tile": tile, "year": year, "season": season_name}

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

        logger.info(
            "dask seasonal task finished",
            extra={"pipeline.year": year, "pipeline.season": season_name, "s2.tile": tile},
        )
        return {"status": "finished", "tile": tile, "year": year, "season": season_name}

    except Exception as e:
        logger.exception(
            "dask seasonal task failed",
            extra={"pipeline.year": year, "pipeline.season": season_name, "s2.tile": tile},
        )
        return {
            "status": "failed",
            "tile": tile,
            "year": year,
            "season": season_name,
            "error": str(e),
        }


# Define the tiles to process
tiles = [ '28RFQ','28RFR','28RFS','28RGR','28RGS','29RKL','29RKM','29RLM','29RLN','29RMM','29RMN','29RMP','29RMQ','29RNN','29RNP','29RNQ','29RPP','29RPQ','29RQP','29RQQ','29SMA','29SMB','29SMC','29SMD','29SMR','29SMS','29SNA','29SNB','29SNC','29SND','29SNR','29SNS','29SNT','29SPA','29SPB','29SPC','29SPD','29SPR','29SPS','29SPT','29SQA','29SQB','29SQC','29SQD','29SQR','29SQS','29SQT','29SQU','29SQV','29TME','29TNE','29TNF','29TNG','29TNH','29TPE','29TPF','29TPG','29TPH','29TQE','29TQF','29TQG','29TQH','30RTU','30RTV','30RUV','30STA','30STB','30STC','30STD','30STE','30STF','30STG','30STH','30STJ','30SUA','30SUB','30SUC','30SUD','30SUE','30SUF','30SUG','30SUH','30SUJ','30SVA','30SVB','30SVC','30SVD','30SVE','30SVF','30SVG','30SVH','30SVJ','30SWA','30SWB','30SWC','30SWD','30SWE','30SWF','30SWG','30SWH','30SWJ','30SXA','30SXB','30SXC','30SXD','30SXE','30SXG','30SXH','30SXJ','30SYA','30SYB','30SYC','30SYD','30SYE','30SYF','30SYG','30SYH','30SYJ','30TTK','30TTL','30TTM','30TUK','30TUL','30TUM','30TUN','30TVK','30TVL','30TVM','30TVN','30TWK','30TWL','30TWM','30TWN','30TXK','30TXL','30TXM','30TXN','30TYK','30TYL','30TYM','30TYN','31SBA','31SBC','31SBD','31SBR','31SBS','31SBT','31SBU','31SBV','31SCA','31SCC','31SCD','31SCS','31SCT','31SCU','31SCV','31SDA','31SDD','31SDS','31SDT','31SDU','31SDV','31SEA','31SED','31SES','31SET','31SEU','31SEV','31SFA','31SFT','31SFU','31SFV','31SGA','31SGB','31SGU','31SGV','31TBE','31TBF','31TBG','31TCE','31TCF','31TCG','31TCH','31TDE','31TDF','31TDG','31TDH','31TDJ','31TDK','31TEE','31TEG','31TEH','31TEJ','31TEK','31TFE','31TFH','31TFJ','31TFK','31TFL','31TGH','31TGJ','31TGK','32SKD','32SKE','32SKF','32SLD','32SLE','32SLF','32SLG','32SMC','32SMD','32SME','32SMF','32SMG','32SMJ','32SNB','32SNC','32SND','32SNE','32SNF','32SNG','32SNJ','32SPA','32SPB','32SPC','32SPD','32SPE','32SPF','32SPG','32SQA','32SQB','32SQC','32SQD','32SQF','32SQG','32SQH','32TLN','32TLP','32TLQ','32TMK','32TML','32TMM','32TMN','32TMP','32TMQ','32TMR','32TNK','32TNL','32TNM','32TNN','32TNP','32TNQ','32TPM','32TPN','32TPP','32TPQ','32TQL','32TQM','32TQN','32TQP','32TQQ','33RVQ','33RWQ','33RXQ','33STA','33STB','33STC','33STR','33STS','33STV','33SUB','33SUC','33SUR','33SUS','33SVA','33SVB','33SVC','33SVR','33SVS','33SVV','33SWA','33SWB','33SWC','33SWD','33SWR','33SXC','33SXD','33TTF','33TTG','33TUF','33TUG','33TUH','33TUJ','33TUK','33TUL','33TUM','33TVE','33TVF','33TVG','33TVH','33TVJ','33TVK','33TVL','33TVM','33TWE','33TWF','33TWG','33TWH','33TWJ','33TWK','33TXE','33TXF','33TXH','33TXJ','33TYE','33TYF','33TYG','33TYH','33TYJ','34RCV','34RDV','34SCA','34SCJ','34SDA','34SDB','34SDG','34SDH','34SDJ','34SEA','34SEB','34SEF','34SEG','34SEH','34SEJ','34SFA','34SFB','34SFE','34SFF','34SFG','34SFH','34SFJ','34SGD','34SGE','34SGF','34SGG','34SGH','34SGJ','34TBK','34TBL','34TBM','34TCK','34TCL','34TCM','34TCN','34TDK','34TDL','34TDM','34TDN','34TEK','34TEL','34TEM','34TFK','34TFL','34TFM','34TGK','34TGL','35RPQ','35RQQ','35SKA','35SKB','35SKC','35SKD','35SKU','35SKV','35SLA','35SLB','35SLC','35SLD','35SLU','35SLV','35SMA','35SMB','35SMC','35SMD','35SMU','35SMV','35SNA','35SNB','35SNC','35SND','35SNV','35SPA','35SPB','35SPC','35SPD','35SQA','35SQB','35SQC','35SQD','35TKE','35TKF','35TLE','35TLF','35TME','35TMF','35TNE','35TNF','35TPE','35TPF','35TQE','35TQF','36RTV','36RUV','36RXV','36RYT','36RYU','36RYV','36STA','36STF','36STG','36STH','36STJ','36SUA','36SUF','36SUG','36SUH','36SUJ','36SVD','36SVE','36SVF','36SVG','36SVJ','36SWD','36SWE','36SWF','36SWG','36SXA','36SXB','36SXC','36SXE','36SXF','36SXG','36SXH','36SYA','36SYB','36SYC','36SYD','36SYE','36SYF','36SYG','36SYH','36TTK','36TTL','36TUK','37RBQ','37SBA','37SBB','37SBC','37SBR','37SBS','37SBT','37SBU','37SBV','37SCA','37SCB','37SCC','37SCR','37SCS','37SCT','37SCU','37SCV','37SDA','37SDB','37SDC','37SDV','37SEA','37SEB','37SEC','37SFA','37SFB','37SFC','37SGA','37SGB','37SGC','38SKF','38SKG','38SKH']

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
            futures = {}
            for tile in tiles:
                logger.info(
                    "dask seasonal tile started",
                    extra={"pipeline.year": year, "pipeline.season": season_name, "s2.tile": tile},
                )
                future = client.submit(
                    process_season,
                    year,
                    season_name,
                    start_date,
                    end_date,
                    tile,
                    not args.keep_products,
                    args.quantize_rasters,
                )
                futures[future] = {"pipeline.year": year, "pipeline.season": season_name, "s2.tile": tile}

            for future in as_completed(futures):
                context = futures[future]
                try:
                    result = future.result()
                except Exception as e:
                    logger.exception(
                        "dask seasonal tile failed",
                        extra={**context, "error": str(e)},
                    )
                    continue

                if result["status"] == "failed":
                    logger.error(
                        "dask seasonal tile failed",
                        extra={**context, "error": result["error"]},
                    )
                else:
                    logger.info(
                        "dask seasonal tile finished",
                        extra={**context, "task.status": result["status"]},
                    )


if __name__ == "__main__":
    main()

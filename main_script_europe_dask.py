from argparse import ArgumentParser
from pathlib import Path
import traceback

from dask.distributed import Client, as_completed
from dotenv import load_dotenv

from main_script_europe import INDEXES, MONTHS, get_europe_land_tiles


def run_process_tile_month(tile: str, year: int, month: int, indexes: list[str]) -> str:
    from main_script_europe import process_tile_month

    try:
        return process_tile_month(tile, year, month, indexes)
    except Exception as exc:
        formatted_traceback = traceback.format_exc()
        raise RuntimeError(f"Failed {tile} {year}-{month:02}: {type(exc).__name__}: {exc}\n{formatted_traceback}") from None


def parse_args():
    parser = ArgumentParser(description="Submit Europe Sentinel-2 monthly index processing to a Dask cluster.")
    parser.add_argument("--scheduler", required=True, help="Dask scheduler address, for example tcp://127.0.0.1:8786.")
    parser.add_argument("--start-year", type=int, default=2017)
    parser.add_argument("--end-year", type=int, default=2026)
    parser.add_argument("--refresh-tiles", action="store_true")
    parser.add_argument("--tiles", nargs="+", help="Optional tile list. Defaults to all cached/detected Europe land tiles.")
    parser.add_argument("--indexes", nargs="+", default=INDEXES, choices=INDEXES)
    return parser.parse_args()


def main():
    load_dotenv(".env")
    args = parse_args()
    tiles = args.tiles or get_europe_land_tiles(refresh=args.refresh_tiles)
    years = range(args.start_year, args.end_year + 1)

    client = Client(args.scheduler)
    client.upload_file(str(Path(__file__).with_name("main_script_europe.py")))
    futures = [
        client.submit(run_process_tile_month, tile, year, month, args.indexes, pure=False, retries=2)
        for year in years
        for tile in tiles
        for month in MONTHS
    ]

    print(f"Submitted {len(futures)} tile/month tasks")
    for future in as_completed(futures):
        try:
            print(future.result())
        except Exception as exc:
            print(f"Task failed after retries: {exc}")


if __name__ == "__main__":
    main()

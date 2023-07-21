import typer
from raw_index_calculation import calculate_raw_index
from pymongo import MongoClient
from tqdm import tqdm
import os


def fix_missing_indices(
    index: str = typer.Option(..., help="Index to be completed"),
    tile: str = typer.Option(..., help="Tile for which to complete the index"),
):
    mongo_collection = MongoClient(
        f"mongodb://"
        + os.environ.get("MONGO_HOST")
        + ":"
        + os.environ.get("MONGO_PORT")
        + "/",
        username=os.environ.get("MONGO_USERNAME"),
        password=os.environ.get("MONGO_PASSWORD"),
    )[os.environ.get("MONGO_DATABASE_NAME")][os.environ.get("MONGO_COLLECTION_NAME")]

    broken_products_cursor = mongo_collection.aggregate(
        [
            {
                "$project": {
                    "indexes": {
                        "$filter": {
                            "input": "$indexes",
                            "as": "index",
                            "cond": {"$eq": ["$$index.name", index]},
                        }
                    },
                    "title": 1,
                    "id": 1,
                }
            },
            {
                "$match": {
                    "indexes.0": {"$exists": False},
                    "title": {"$regex": f"{tile}"},
                }
            },
        ]
    )

    broken_products_list = list(broken_products_cursor)

    for product_metadata in tqdm(broken_products_list):
        title = product_metadata["title"]
        calculate_raw_index(product_title=title, index=[index])


if __name__ == "__main__":
    typer.run(fix_missing_indices)

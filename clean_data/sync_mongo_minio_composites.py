import json
from bson.json_util import dumps
from ds_download.minio_connection import MinioConnection
from ds_download.mongo_connection import MongoConnection
from ds_download.raw_index_calculation import calculate_raw_index
from os.path import join
from collections import defaultdict

def rename_keys(nested_dict, key_map):
    """Recursively rename keys in a nested dictionary."""
    if isinstance(nested_dict, dict):
        new_dict = {}
        for key, value in nested_dict.items():
            new_key = key_map.get(key, key)  # Rename if key exists in key_map
            new_dict[new_key] = rename_keys(value, key_map)
        return new_dict
    elif isinstance(nested_dict, list):
        return [rename_keys(item, key_map) for item in nested_dict]
    else:
        return nested_dict  # Return value unchanged if not a dict or list


mongo_col = MongoConnection().get_composite_collection_object()
minio_client = MinioConnection()

cursor = mongo_col.find({})
composite_dict = defaultdict(list)

# get {indexes:{$exists:false}} and calculate indexes

cursor = mongo_col.find({"indexes": {"$exists": False}})
for document in cursor:
    print(f"Calculating indexes for {document['title']}")
    calculate_raw_index(
        product_title=document["title"],
        index=[
            "Moisture",
            "NDVI",
            "NDWI",
            "NDSI",
            "EVI",
            "OSAVI",
            "EVI2",
            "NDRE",
            "NDYI",
            "MNDWI",
            "BRI",
            # "TCI",
            "RI",
            "BSI",
            "CRI1"
        ],
        is_composite=True
    )


for document in cursor:
    break
    tile = document["title"].split("_T")[1][0:5]
    document["tile"] = tile
    mongo_col.update_one({"_id": document["_id"]}, {"$set": document})


# Delete "id" in "products" array
cursor = mongo_col.find({})

for document in cursor:
    break
    products = document["products"]
    if "id" in document:
        document.pop("id")
    for product in products:
        if "id" in product:
            product.pop("id")
    document["products"] = products
    mongo_col.replace_one({"_id": document["_id"]}, document)
    print(f"Document {document['_id']} updated.")



# Step 4. Delete documents that are not found in MinIO

cursor = mongo_col.find({})

for document in cursor:
    break
    minio_bucket = document["S3Bucket"]
    minio_prefix = document["S3BandsPrefix"]
    minio_objects = minio_client.list_objects(minio_bucket, minio_prefix, recursive=True)
    if not any(minio_objects):
        mongo_col.delete_one({"_id": document["_id"]})
        print(f"Document {document['_id']} deleted.")

# Step 6. rename desired keys

key_replacements = {
    "minioBandsPath": "S3BandsPrefix",
    "minioBucket": "S3Bucket",
    "rasterMinioBucket": "rasterS3Bucket",
    "rasterMinioPath": "rasterS3Key",
}

cursor = mongo_col.find({})


for document in cursor:
    break
    new_document = rename_keys(document, key_replacements)

    new_document["_id"] = document["_id"]

    # Update the document instead of delete + insert
    mongo_col.replace_one({"_id": document["_id"]}, new_document)
    print(f"Document {document['_id']} updated.")

    # Step 7. Check that only one composite exists for each each month and tile

cursor = mongo_col.find({})
composite_dict = defaultdict(list)
for document in cursor:
    date = document["first_date"]
    year = date.year
    month = date.month
    tile = document["title"].split("_T")[1][0:5]
    key = f"{year}-{month}-{tile}"
    print(tile, key)
    composite_dict[key].append(document["title"])

for key in composite_dict:
    if len(composite_dict[key]) > 1:
        print(f"Multiple composites found for {key}")
        # keep the one with greater "_id" (added last) (both have same name)
        ids = mongo_col.find({"title": {"$in": composite_dict[key]}})
        max_id = max([doc["_id"] for doc in ids])
        mongo_col.delete_many({"title": {"$in": composite_dict[key]}, "_id": {"$ne": max_id}})
        print(f"Deleted composites for {key}")
        


    

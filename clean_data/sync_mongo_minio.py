import json
from bson.json_util import dumps
from ds_download.minio_connection import MinioConnection
from ds_download.mongo_connection import MongoConnection
from ds_download.raw_index_calculation import calculate_raw_index
from os.path import join

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


mongo_col = MongoConnection().get_collection_object()
minio_client = MinioConnection()

cursor = mongo_col.find({})

for document in cursor:
    product_title = document["title"]
    splits = product_title.split("_T")
    tile_id = str(splits[1][0:5])
    document["tile"] = tile_id
    mongo_col.replace_one({"_id": document["_id"]}, document)

raise Exception("Stop here")
 
sentinel_api_keys = ['@odataMediaContentType', 'id', 'name', 'contentType', 'contentLength', 'originDate', 'publicationDate', 'modificationDate', 'online', 'evictionDate', 's3Path', 'checksum', 'contentDate', 'footprint', 'geoFootprint']

# Step 1. If it doesnt have sentinelAPI key, group all its keys in a dictionary and add it to the document
for document in cursor:
    if "sentinelAPI" in document or "s2APIMetadata" in document:
        continue
    else:
        sentinel_api_dict = {"sentinelAPI": {}}
        for key in sentinel_api_keys:
            
            sentinel_api_dict["sentinelAPI"][key] = document[key]
            document.pop(key)
        document.update(sentinel_api_dict)
        mongo_col.replace_one({"_id": document["_id"]}, document)
        print(f"Document {document['_id']} updated.") 

# Step 2. Change sentinelAPI key to s2APIMetadata
cursor = mongo_col.find({})
for document in cursor:
    if "sentinelAPI" in document:
        s2APIMetadata = document.pop("sentinelAPI")
        document["s2APIMetadata"] = s2APIMetadata
        mongo_col.replace_one({"_id": document["_id"]}, document)
        print(f"Document {document['_id']} updated.")

# Steap 3. Delete duplicated with the same title or s2APIMetadata.name

## Group "titles" that have same "s2APIMetadata.name"

cursor = mongo_col.aggregate([
    {"$group": {"_id": "$s2APIMetadata.name", "count": {"$sum": 1}, "titles": {"$push": "$title"}}},
    {"$match": {"count": {"$gt": 1}}}
])

## keep the first one and delete the rest
for document in cursor:
    print(document)
    for title in document["titles"][1:]:
        mongo_col.delete_one({"title": title})
        print(f"Document {title} deleted.")

## Group "s2APIMetadata.name" that have same "title"
cursor = mongo_col.aggregate([
    {"$group": {"_id": "$title", "count": {"$sum": 1}, "names": {"$push": "$s2APIMetadata.name"}}},
    {"$match": {"count": {"$gt": 1}}}
])

## keep the first one and delete the rest
for document in cursor:
    for name in document["names"][1:]:
        mongo_col.delete_one({"s2APIMetadata.name": name})
        print(f"Document {name} deleted.")

# Step 4. Delete documents that are not found in MinIO

cursor = mongo_col.find({})

for document in cursor:
    break
    minio_bucket = document["minioBucket"]
    minio_prefix = document["minioBandsPath"]
    minio_objects = minio_client.list_objects(minio_bucket, minio_prefix, recursive=True)
    if not any(minio_objects):
        #mongo_col.delete_one({"_id": document["_id"]})
        print(f"Document {document['_id']} deleted.")

# Step 5. compute intermediate_products for documents missing
cursor = mongo_col.find({})
for document in cursor:
    break
    if "intermediateProducts" in document:
        continue
    else:
        try:
            calculate_raw_index(
                product_title=document["title"],
                index=["CloudMask"],
                minio_folder_name="intermediateProducts",
            )
        except Exception as e:
            print(f"Error: {e}")
            mongo_col.delete_one({"_id": document["_id"]})
            continue
        print(f"Document {document['_id']} updated.")

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

# Step 7. create data source dictionary

cursor = mongo_col.find({})

for document in cursor:

    product_title = document["title"]
    splits = product_title.split("_T")
    tile_id = str(splits[1][0:5])
    tile_number = str(splits[1][0:2])
    tile_type = str(splits[1][2])
    tile_subtype = str(splits[1][3:5])
    list_of_names = ["L2/tiles", tile_number, tile_type, tile_subtype, product_title + ".SAFE"]
    source_blob_name = join(*list_of_names)

    data_source_dict = {
                        "dataSource": {
                            "googleCloudStorage": {
                                "bucketName": "gcp-public-data-sentinel-2",
                                "prefix": source_blob_name
                                }
                            }
                        }

    new_document = document
    new_document.update(data_source_dict)
    mongo_col.replace_one({"_id": document["_id"]}, new_document)
    print(f"Document {document['_id']} updated.")


    

#!/usr/bin/env bash
echo "Starting the script, downloading the desired products"
. ./.env
python3 script.py Teatinos 2023-01-01 2099-12-30 --temp-dir data --cloud-limit-soft 100 --calculate-raw-indexes 
status=$?

echo "Products have been downloaded and indices have been computed, now cropping for Teatinos and El Ejido"
status=$?
[ $status -eq 0 ] && python3 crop_geojson/get_product_id.py --geojson-file geojson/teatinos.geojson --start-date 2022-01-01 --end-date 2099-12-30 --output-file data/ids_file.json || python3 send.py --script fix_missing_indices.py 
status=$?
[ $status -eq 0 ] && python3 crop_geojson/crop_products.py --ids-file data/ids_file.json --geojson-file geojson/teatinos.geojson --index NDVI --index OSAVI --index MNDWI --index TCI --index MOISTURE --index EVI --index EVI2 --index NDRE --index NDYI --index BRI --index NDSI --index NDWI --index BSI --temp-dir data --output-file data/output_file.json --output-metadata-file data/output_metadata_file.json || python3 send.py --script get_product_id.py

status=$?
[ $status -eq 0 ] && python3 crop_geojson/get_product_id.py --geojson-file geojson/ejido.geojson --start-date 2022-01-01 --end-date 2099-12-30 --output-file data/ids_file.json || python3 send.py --script fix_missing_indices.py 
status=$?
[ $status -eq 0 ] && python3 crop_geojson/crop_products.py --ids-file data/ids_file.json --geojson-file geojson/ejido.geojson --index NDVI --index OSAVI --index MNDWI --index TCI --index MOISTURE --index EVI --index EVI2 --index NDRE --index NDYI --index BRI --index NDSI --index NDWI --index BSI --temp-dir data --output-file data/output_file.json --output-metadata-file data/output_metadata_file.json || python3 send.py --script get_product_id.py

status=$?
[ $status -eq 0 ] && echo "Proccess completed" || python3 send.py --script crop_products.py

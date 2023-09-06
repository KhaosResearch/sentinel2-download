# Green-Senti's Sentinel-2 product download script
This repository includes the necessary code for downloading Sentinel-2 products for the geojson geometries at [geojson](geojson). It downloads, computes the specified list of satellite indices, and crops the product to the geojson area. The products are stored in MinIO and the metadata are stored in MongoDB. 

## Configuration files
This script falls back to a Google Cloud endpoint for downloading products, as Copernicus' DHUS provides a worse performance. A file named `etc-uma-88a65f4add8a.json`, and the `.env` file including all the environment variables have been saved in the group's private NAS at this path `/backup/greensenti-download-script`.

## Dependencies
The required dependencies can be found at [requirements.txt](requirements.txt). However, there is an additional one that refers to a specific commit in the package's git repository (https://github.com/KhaosResearch/greensenti/commit/107e0616feaafb48a7a0f825ccfcc0b04bc8a1ac). Therefore, all dependencies can be installed as follows:

```sh
$ python3 -m pip install "greensenti @ git+https://github.com/KhaosResearch/greensenti.git@107e0616feaafb48a7a0f825ccfcc0b04bc8a1ac"
$ python3 -m pip install -r requirements.txt
```

## Execution
The script [execute.sh](execute.sh) defines the execution pipeline for downloading, computing indices and cropping products. It has been developed specifically for Green-Senti's use case of the university campuses of Teatinos and El Ejido. However, it should prove useful to see how the code can be executed for performing isolated tasks. Since the aim of this repository if for preserving a working cronjob for downloading products for Green-Senti, this script has not been made generic.

It is worth mentioning that the recommended way of running the script is inside a Docker container, the following commands can be used for building and running the script inside a container:

```sh
$ docker build . -t sentinel2-downloads:1.0.0
$ docker run -d sentinel2-downloads:1.0.0
```

In order to set this task as a cronjob that executes once per month, simply edit the crontab file using `crontab -e`. Then append the following line: `0 0 1 1-12 * docker run -d sentinel2-downloads:1.0.0`
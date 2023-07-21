# Script for downloading products from Spain and Italy

This script downloads the products from Spain and Italy for the date indicated as input parameter.

The products are stored in minio along with their respective bands and the most significant indexes. The metadata of this process is stored in mongo database.

For example, to download the products of Spain of the month March we execute the following:

```
$ pm2 start script.py -- --country Spain --start-date 2020-02-01 --end-date 2020-02-07 --temp-dir ./data --cloud_limit_soft 10 --full_coverage --cloud_limit_max 25 --cloud_limit_composite 40 --calculate-raw-indexes
```
from .main_script import *

# Andalusia Tiles
tiles = ["29SPC", "29SQC", "30STH", "30SUH", "30SVH", "30SWH", "30SXH", "30SYH", "30SXG", 
        "30SWG", "30SVG", "30SUG", "30STG", "29SQB" ,"29SPB", "30STF", "30SUF", 
        "30SVF", "30SWF"
        ]
start_year = 2025
start_month = 1
end_year = 2025
end_month = 2

# Clear the log file at the beginning of the execution
open(log_file_path, 'w').close()  # This clears the content of the log file

# Loop through the years and process each year
years = range(start_year, end_year)
for year in years:
    main_workflow(tiles, year, start_month, end_month)

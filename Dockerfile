FROM python:3.9.5-slim-buster

WORKDIR sentinel2-downloads
COPY . .

RUN apt-get update && apt-get install -y git

RUN python3 -m pip install "greensenti @ git+https://github.com/KhaosResearch/greensenti.git@v0.5.1"
RUN python3 -m pip install "landcoverpy @ git+https://github.com/KhaosResearch/landcoverpy.git@v1.0.0"

RUN python3 -m pip install -r requirements.txt

CMD ["sh", "./execute.sh"]
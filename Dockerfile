FROM python:3.10-slim-buster

WORKDIR sentinel2-downloads

RUN apt-get update && apt-get install -y git

RUN python3 -m pip install "greensenti @ git+https://github.com/KhaosResearch/greensenti.git@107e0616feaafb48a7a0f825ccfcc0b04bc8a1ac"

COPY requirements.txt ./requirements.txt

RUN python3 -m pip install -r requirements.txt

COPY . .

CMD ["sh", "./execute.sh"]
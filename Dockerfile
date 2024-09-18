FROM python:3.10-slim-buster

WORKDIR sentinel2-downloads

RUN apt-get update && apt-get install -y git

COPY requirements.txt ./requirements.txt

RUN python3 -m pip install -r requirements.txt

COPY . .

CMD ["sh", "./execute.sh"]
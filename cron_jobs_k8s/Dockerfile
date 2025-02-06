FROM python:3.10-slim

RUN apt-get update && apt-get install -y libexpat1

WORKDIR /app

COPY requirements.txt .
COPY update_andalusia.py .  

# Modify the following line to include the credentials for the private repository
ENV EXTRA_PIP_PACKAGES="--index-url=http://<user>:<pass>@192.168.219.5:8099/simple --trusted-host 192.168.219.5 ds-download --upgrade"

RUN pip install --no-cache-dir -r requirements.txt && \
    pip install $EXTRA_PIP_PACKAGES

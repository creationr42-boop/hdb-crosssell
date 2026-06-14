FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    wget curl unzip \
    && rm -rf /var/lib/apt/lists/*

ENV CHROMEDRIVER_PATH=/usr/bin/chromedriver
ENV GOOGLE_CHROME_BIN=/usr/bin/chromium
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000
CMD gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 600

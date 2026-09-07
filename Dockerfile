# Suno Music Downloader Telegram Bot
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# ffmpeg is required to convert non-MP3 audio containers to MP3 V0.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Volatile runtime data.
RUN mkdir -p /app/downloads
VOLUME ["/app/downloads"]

CMD ["python", "main.py"]

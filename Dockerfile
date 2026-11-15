# Creative Factory auf dem VPS: FastAPI + Chromium (Motion-Rendering) + ffmpeg
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends chromium ffmpeg fonts-dejavu-core fonts-liberation ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
ENV CF_DATA=/data CHROME=/usr/bin/chromium PYTHONUNBUFFERED=1
EXPOSE 8040
# Ein Prozess: die Hintergrund-Jobs laufen im Prozess (jobs.py)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8040", "--proxy-headers", "--forwarded-allow-ips", "*"]

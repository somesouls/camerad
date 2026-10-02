FROM python:3.12-slim

# ffmpeg untuk potong/encode, lib runtime OpenCV untuk deteksi wajah
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libgl1 libglib2.0-0 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py .

ENV CLIP_WORK_DIR=/data/clips PYTHONUNBUFFERED=1
RUN mkdir -p /data/clips

EXPOSE 8080
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]

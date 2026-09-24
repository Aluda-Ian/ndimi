# Ndimi model service. Build:  docker build -t ndimi-models .
# Run:   docker run -p 8000:8000 -e NDIMI_MODELS_TOKEN=change-me -v $PWD/models.json:/app/models.json \
#               -v /path/to/models:/models ndimi-models
# On a GPU machine add --gpus all (and use an NVIDIA CUDA base image for best speed).
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py ndimi_models.py models.example.json ./

ENV NDIMI_MODELS_CONFIG=/app/models.json \
    HF_HOME=/models-cache \
    PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]

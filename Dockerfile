# One image for both Railway services. SERVICE_ROLE picks what runs (see start.sh):
#   web    -> the Django website (gunicorn)
#   worker -> python manage.py run_dub_worker
FROM python:3.11-slim

# ffmpeg does all audio/video work (the website uses it too, to read media info).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Demucs (best voice/music separation) adds ~1 GB (PyTorch, CPU build). Set the Railway
# variable WITH_DEMUCS=1 on the worker service to include it.
ARG WITH_DEMUCS=0
RUN if [ "$WITH_DEMUCS" = "1" ]; then \
      pip install --no-cache-dir torch torchaudio --index-url https://download.pytorch.org/whl/cpu \
      && pip install --no-cache-dir demucs; \
    fi

COPY . .
RUN chmod +x start.sh

ENV PYTHONUNBUFFERED=1 \
    DUB_WORK_ROOT=/data/jobs

CMD ["./start.sh"]

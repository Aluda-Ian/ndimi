#!/bin/sh
# Railway start command for both services. Set SERVICE_ROLE=web or SERVICE_ROLE=worker.
set -e

if [ "$SERVICE_ROLE" = "worker" ]; then
    # Only the worker applies database changes, so two services never migrate at once.
    python manage.py migrate --noinput
    exec python manage.py run_dub_worker --name "${WORKER_NAME:-railway-worker}"
fi

python manage.py collectstatic --noinput
exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:${PORT:-8000}" \
    --workers "${WEB_CONCURRENCY:-2}" \
    --timeout 120 \
    --access-logfile -

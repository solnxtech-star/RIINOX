#!/bin/bash
set -o errexit
set -o pipefail

echo "🚀 Starting Django container..."

echo "📦 Collecting static files..."
python manage.py collectstatic --noinput || true

echo "🧭 Generating schema..."
python manage.py spectacular --color --file schema.yml || true

echo "🗄️ Applying migrations..."
python manage.py migrate --noinput

echo "🔥 Starting Gunicorn (production server)..."
exec gunicorn config.asgi:application --bind 0.0.0.0:$PORT -k uvicorn.workers.UvicornWorker

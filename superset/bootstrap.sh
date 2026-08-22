#!/bin/bash
set -e

superset db upgrade
superset fab create-admin \
    --username "${SUPERSET_ADMIN_USER:-admin}" \
    --firstname Admin --lastname Admin \
    --email admin@example.com \
    --password "${SUPERSET_ADMIN_PASSWORD:-admin}" || true
superset init

exec gunicorn -w 2 -k gthread --threads 4 -b 0.0.0.0:8088 --timeout 120 "superset.app:create_app()"

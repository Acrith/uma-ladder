# syntax=docker/dockerfile:1
# Stage 1 — Tailwind CLI build (Node only at build time, not at runtime)
FROM node:20-alpine AS tailwind
WORKDIR /build
RUN npm install -g tailwindcss@3.4.13
COPY tailwind.config.js ./
COPY uma_ladder/static/css/input.css ./uma_ladder/static/css/input.css
COPY uma_ladder ./uma_ladder
RUN tailwindcss \
    -c tailwind.config.js \
    -i ./uma_ladder/static/css/input.css \
    -o /build/output.css \
    --minify

# Stage 2 — Python runtime
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLASK_APP=uma_ladder \
    FLASK_CONFIG=production \
    TAILWIND_BUILT=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir "."

COPY uma_ladder ./uma_ladder
COPY migrations ./migrations
COPY data ./data
COPY --from=tailwind /build/output.css ./uma_ladder/static/css/output.css

# Drop privileges.
RUN useradd --system --uid 1000 --create-home uma && \
    mkdir -p /app/instance/uploads && \
    chown -R uma:uma /app
USER uma

EXPOSE 8080

# Run migrations then start gunicorn.
CMD ["sh", "-c", "flask db upgrade && gunicorn --bind 0.0.0.0:8080 --workers 2 'uma_ladder:create_app()'"]

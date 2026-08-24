FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLYWHEEL_HOST=0.0.0.0 \
    FLYWHEEL_PORT=8743 \
    FLYWHEEL_DATABASE_PATH=/app/runtime/flywheel.db \
    FLYWHEEL_DATA_DIR=/app/runtime \
    FLYWHEEL_SEED_DATA_DIR=/app/data/seeds \
    FLYWHEEL_CORS_ORIGINS=http://localhost:8080

WORKDIR /app
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*
COPY backend /app/backend
COPY data/seeds /app/data/seeds
RUN python -m pip install --no-cache-dir /app/backend

RUN useradd --create-home --uid 10001 flywheel \
    && mkdir -p /app/runtime \
    && chown -R flywheel:flywheel /app/runtime
USER flywheel

EXPOSE 8743
CMD ["python", "-m", "uvicorn", "foundry.app:app", "--host", "0.0.0.0", "--port", "8743"]

FROM python:3.12.6-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.11.9 /uv /usr/local/bin/uv

ARG APP_UID=1000
ARG APP_GID=1000

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app/src \
    HOME=/home/app

RUN groupadd --gid ${APP_GID} app \
    && useradd --uid ${APP_UID} --gid ${APP_GID} --create-home --home-dir /home/app app \
    && chmod 1777 /home/app

WORKDIR /app

# Locked dependencies first (cache-friendly); DuckDB comes from uv.lock.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --extra dev --no-install-project \
    && python -m ipykernel install --sys-prefix \
        --name wearable-health-data-pipeline \
        --display-name "Python (wearable-health-data-pipeline)" \
    && chmod -R a+rX /opt/venv

# Code and tests only; .dockerignore keeps data/ and output/ out of the build context.
COPY . .
RUN chmod -R a+rX /app

USER app

CMD ["python", "-m", "wearable_pipeline", "run", "--data-dir", "/data", "--output-dir", "/output"]

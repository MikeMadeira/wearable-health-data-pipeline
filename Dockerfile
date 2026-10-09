FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY tests ./tests
COPY scripts ./scripts
RUN python -m pip install '.[dev]'
RUN python -m ipykernel install --sys-prefix \
    --name wearable-health-data-pipeline \
    --display-name "Python (wearable-health-data-pipeline)"

COPY . .

CMD ["python", "-m", "pytest", "-q"]
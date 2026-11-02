FROM ghcr.io/astral-sh/uv:0.10.8 AS uv
FROM python:3.12-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app
COPY --from=uv /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project && rm -rf /root/.cache/uv
RUN groupadd --gid 10001 workbench && useradd --no-create-home --uid 10001 --gid 10001 workbench
ENV PATH="/opt/venv/bin:${PATH}"
USER 10001:10001

FROM base AS application
COPY workbench ./workbench
EXPOSE 8010
CMD ["uvicorn", "workbench.api:app", "--host", "0.0.0.0", "--port", "8010", "--no-access-log", "--limit-concurrency", "40"]

FROM base AS parser
USER root
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng tesseract-ocr-osd && rm -rf /var/lib/apt/lists/*
COPY workbench ./workbench
USER 10001:10001
EXPOSE 8011
CMD ["uvicorn", "workbench.parser_api:app", "--host", "0.0.0.0", "--port", "8011", "--no-access-log", "--limit-concurrency", "4"]

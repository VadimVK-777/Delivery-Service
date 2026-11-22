FROM python:3.12.10-slim-bookworm AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 POETRY_VIRTUALENVS_CREATE=false
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
RUN pip install poetry==2.5.1
COPY pyproject.toml poetry.lock README.md ./
RUN poetry install --only main --no-root --no-interaction
COPY src ./src
COPY knowledge ./knowledge
COPY migrations ./migrations
COPY alembic.ini ./
RUN poetry install --only-root --no-interaction && useradd --create-home app
ENV HF_HOME=/home/app/.cache/huggingface
RUN mkdir -p /home/app/.cache && chown -R app:app /home/app
USER app
EXPOSE 8000
CMD ["uvicorn", "delivery_service.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

FROM base AS test
USER root
RUN poetry install --with dev --no-interaction
COPY tests ./tests
COPY scripts ./scripts
RUN chown -R app:app /app
USER app
CMD ["pytest"]

FROM base AS production

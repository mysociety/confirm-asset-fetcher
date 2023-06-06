FROM python:3.11

ENV PYTHONFAULTHANDLER=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=random \
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_DEFAULT_TIMEOUT=100 \
    POETRY_VERSION=1.5.1

RUN pip install "poetry==$POETRY_VERSION" \
    && apt-get update && apt-get install -y libsqlite3-mod-spatialite libgdal-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY poetry.lock pyproject.toml /app/

RUN poetry config virtualenvs.create false \
    && poetry install --only main --no-interaction --no-ansi

COPY fetch_assets.py /app

CMD poetry -q run python fetch_assets.py

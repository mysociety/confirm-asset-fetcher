FROM python:alpine
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONFAULTHANDLER=1 \
  PYTHONUNBUFFERED=1 \
  PYTHONHASHSEED=random

RUN apk add --update --no-cache gcc g++ musl-dev libffi-dev libspatialite-dev gdal-dev

WORKDIR /app

COPY fetch_assets.py version.txt /app/

RUN uv sync --script fetch_assets.py

CMD ["uv", "run", "--script", "fetch_assets.py"]

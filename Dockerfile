FROM kennethreitz/pipenv

RUN apt-get update && apt-get install -y libsqlite3-mod-spatialite \
    && rm -rf /var/lib/apt/lists/*

COPY fetch_assets.py /app

CMD python3 fetch_assets.py

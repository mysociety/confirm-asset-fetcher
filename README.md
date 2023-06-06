# confirm-asset-fetcher

Script to download assets from Confirm and store them in a reusable format e.g.
shapefile/GeoJSON/geopackage.

## Quickstart

The quickest way to get going is with the pre-built Docker image and the Makefile
in this repository.

 1. Clone this repo
 1. Make sure Docker is installed and running ([Docker Desktop](https://www.docker.com/products/docker-desktop/) is probably best).
 1. Copy `general.yml-example` to `general.yml` and configure as appropriate.
 1. Run the script:
    ```bash
    $ make fetch
    ```

## Running locally

The script uses [Poetry](https://python-poetry.org) for virtualenv management, so install that if you haven't already. Then, run the script:

```
$ poetry install
$ poetry run python fetch_assets.py
```

## Caveats

 - Confirm returns a maximum of 100 features for each `AssetSearch` call, so if a
   call returns 100 features it's assumed there may be more that weren't returned.
   In this case the bbox for the current call is broken into 4 smaller quadrants
   and `AssetSearch` is called for each of them and so on. This may take some time
   for larger areas.
 - Feature property names will be truncated if outputting to a shapefile, due to
   limitations of that format.

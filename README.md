# confirm-asset-fetcher

Script to download assets from Confirm and store them in a reusable format e.g.
shapefile/GeoJSON/geopackage.

## Quickstart

The quickest way to get going is with the pre-built Docker image and the Makefile
in this repository.

First, copy `general.yml-example` to `general.yml` and configure as appropriate.
Then run the script:

```bash
$ make fetch
```

## Running locally

The script uses `pipenv` for virtualenv management, so install that if you haven't already. Then, run the script:

```
$ pipenv install
$ pipenv run python fetch_assets.py
```

## Caveats

 - Confirm returns a maximum of 100 features for each `AssetSearch` call, so if a
   call returns 100 features it's assumed there may be more that weren't returned.
   In this case the bbox for the current call is broken into 4 smaller quadrants
   and `AssetSearch` is called for each of them and so on. This may take some time
   for larger areas.
 - Feature property names will be truncated if outputting to a shapefile, due to
   limitations of that format.

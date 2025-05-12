#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "fiona",
#     "geomet",
#     "geopandas",
#     "lxml",
#     "numpy",
#     "pyproj",
#     "pyyaml",
#     "requests",
#     "shapely",
# ]
# ///
import sys
import os
import os.path
from datetime import datetime
from math import ceil, floor
from time import sleep
from pprint import pprint
import io
from base64 import b64encode

import yaml
import fiona
import requests
import lxml.etree as etree
from geomet import wkt
import geopandas as gpd
import numpy as np
from shapely.geometry import box
import requests
import pyproj
from shapely.ops import transform


DRIVERS = {
    "gpkg": "GPKG",
    "shp": "ESRI Shapefile",
    "geojson": "GeoJSON",
}

OUTPUT_PREFIX = os.environ.get("OUTPUT_PREFIX", "")


def log(*msgs):
    """Because sometimes logging.getLogger is too much"""
    print(datetime.now(), *msgs, file=sys.stderr)


def make_operation_request(config, *operations):
    operations_xml = "\n".join(
        f"<Operation>{operation}</Operation>" for operation in operations
    )
    request_body = f"""<?xml version='1.0' encoding='utf-8'?>
<soap-env:Envelope
    xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/"
    xmlns:web="http://www.confirm.co.uk/schema/am/connector/webservice">
    <soap-env:Body>
        <web:ProcessOperationsRequest>
            <Request>
                <Authentication>
                    <Username>{config['user']}</Username>
                    <Password>{config['password']}</Password>
                    <DatabaseId>{config['tenant']}</DatabaseId>
                </Authentication>
                {operations_xml}
            </Request>
        </web:ProcessOperationsRequest>
    </soap-env:Body>
</soap-env:Envelope>
""".encode("utf-8")
    headers = {
        "User-Agent": "FixMyStreet/1.0",
        "Content-Type": "text/xml; charset=utf-8",
        "Soapaction": "http://www.confirm.co.uk/schema/am/connector/webservice/ProcessOperations",
    }
    for _ in range(3):
        response = requests.post(
            config["url"], request_body, headers=headers, stream=True
        )
        if response.ok:
            return response
        sleep(5)


def AssetSearchFeaturesForBBOX(source, bbox, feature_types=[]):
    x1, y1, x2, y2 = bbox
    feature_types = "\n".join(
        [f"<FeatureGroupCode>{f}</FeatureGroupCode>" for f in feature_types]
    )
    try:
        response = operation_request_as_dict(
            source,
            f"""<AssetSearch>
            <SearchBoundX1>{x1}</SearchBoundX1>
            <SearchBoundY1>{y1}</SearchBoundY1>
            <SearchBoundX2>{x2}</SearchBoundX2>
            <SearchBoundY2>{y2}</SearchBoundY2>
            {feature_types}
        </AssetSearch>""",
        )
    except Exception as err:
        log(f"Error calling AssetSearch for {bbox} {feature_types}")
        log(str(err))
        return []

    features = []
    for asset in response.get("AssetSearchResponse", []):
        if not asset or isinstance(asset, str):
            continue
        asset = asset.get("Asset", [])
        feature = {"type": "Feature", "id": "-1", "properties": {}}

        for key, value in asset.items():
            if key == "WKT":
                feature["geometry"] = wkt.loads(f"SRID=27700;{value}")
            else:
                feature["properties"][key] = value

        features.append(feature)
    return features


def get_graphql_features(source, bbox, layer):
    x1, y1, x2, y2 = (int(i) for i in bbox)
    feature_types = layer["feature_types"]
    url = f"{source['url'].rstrip("/")}/{source['tenant']}/graphql"
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": "Basic "
        + b64encode(f"{source['user']}:{source['token']}".encode()).decode(),
    }

    geom_srs = layer.get("geometry_srs", 4326)
    output_srs = layer.get("output_srs", 27700)

    types = ",".join(feature_types)
    query = (
        """{features(filter: {geometry: {intersectsBbox: {X1:%s X2:%s Y1:%s Y2:%s}} featureTypeCode: {inList: [ %s ]}})@_size_1000{centralAssetId centroidEasting centroidNorthing featureKey featureId featureTypeCode geometry key location notes siteCode featureType@_size_1000{featureGroupCode name}}}"""
        % (x1, x2, y1, y2, types)
    )
    log(f"Querying GraphQL for bbox {(x1, x2, y1, y2)}")
    response = requests.post(url, json={"query": query}, headers=headers)
    response.raise_for_status()

    for props in response.json()["data"]["features"]:
        ftype = props.pop("featureType", {})
        ftype["featureTypeName"] = ftype.pop("name")
        props.update(ftype)

        geometry = props.pop("geometry")

        if not geometry and layer.get("ignore_empty_geometries"):
            continue

        feature = {"type": "Feature", "id": "-1", "properties": props}

        if geometry:
            # might need to reproject this feature
            if geom_srs != output_srs:
                gdf = gpd.GeoDataFrame(
                    geometry=gpd.GeoSeries.from_wkt([geometry]),
                    crs=geom_srs,
                ).to_crs(output_srs)
                feature["geometry"] = gdf.geometry.iloc[0].__geo_interface__
                log(f"{geometry} became {feature['geometry']}")
            else:
                feature["geometry"] = wkt.loads(f"SRID={geom_srs};{geometry}")

        else:
            feature["geometry"] = wkt.loads(
                f"SRID=27700;POINT({props['centroidEasting']} {props['centroidNorthing']})"
            )

        if (
            feature["geometry"]["type"] == "LineString"
            and layer.get("geometry_type") == "MultiLineString"
        ):
            feature["geometry"]["type"] = "MultiLineString"
            feature["geometry"]["coordinates"] = [feature["geometry"]["coordinates"]]

        if (
            feature["geometry"]["type"] == "Point"
            and layer.get("geometry_type") == "MultiPoint"
        ):
            feature["geometry"]["type"] = "MultiPoint"
            feature["geometry"]["coordinates"] = [feature["geometry"]["coordinates"]]

        yield feature


def AssetSearchToFeatures(source, bbox, feature_types=[], box_size=None, indent=0):
    x1, y1, x2, y2 = bbox

    # Pad by a metre in case there are assets right on the edge
    overlap = 1
    if box_size is not None:
        xs = list(range(int(floor(x1)), int(ceil(x2)), box_size)) + [x2]
        ys = list(range(int(floor(y1)), int(ceil(y2)), box_size)) + [y2]
        bounding_boxes = []
        for w, e in zip(xs, xs[1:]):
            for s, n in zip(ys, ys[1:]):
                bounding_boxes.append(
                    (w - overlap, s - overlap, e + overlap, n + overlap)
                )
    else:
        bounding_boxes = [(x1, y1, x2, y2)]

    pad = "\t" * indent

    while bounding_boxes:
        log(f"{pad}Queue size: {len(bounding_boxes)}")
        current_bbox = bounding_boxes.pop(0)
        box_features = AssetSearchFeaturesForBBOX(
            source, current_bbox, feature_types=feature_types
        )
        if len(box_features) == 100 and box_size > 10:
            # Confirm only returns the first 100 assets for a given search box
            # so split the current box into quarters and add them to the list
            new_box_size = ceil(box_size / 2)
            log(f"{pad}Recursing to box size {new_box_size}")
            yield from AssetSearchToFeatures(
                source,
                current_bbox,
                feature_types=feature_types,
                box_size=new_box_size,
                indent=indent + 1,
            )
        else:
            yield from box_features


def operation_request_as_dict(config, operation):
    response = make_operation_request(config, operation)
    doc = etree.parse(response.raw)
    parsed = etree_to_dict(doc.getroot())
    try:
        return parsed["{http://schemas.xmlsoap.org/soap/envelope/}Envelope"][
            "{http://schemas.xmlsoap.org/soap/envelope/}Body"
        ][
            "{http://www.confirm.co.uk/schema/am/connector/webservice}ProcessOperationsResult"
        ]["Response"]["OperationResponse"]
    except KeyError:
        log(pprint(parsed))
        raise


def etree_to_dict(t):
    d = {t.tag: list(map(etree_to_dict, t.iterchildren()))}
    d.update(("@" + k, v) for k, v in t.attrib.iteritems())
    if len(d[t.tag]) == 0:
        d[t.tag] = t.text
        return d
    d[t.tag].sort(key=lambda x: list(x.keys())[0])
    if len(d[t.tag]) == 1:
        d[t.tag] = d[t.tag][0]
    if t.text:
        d["text"] = t.text
    if len(d[t.tag]) > 1 and {type(e) for e in d[t.tag]} == {dict}:
        flat_dict = dict(list(i.items())[0] for i in d[t.tag])
        if len(flat_dict) == len(d[t.tag]):
            d[t.tag] = flat_dict
    return d


def get_utm_projection(geometry):
    """Get appropriate UTM projection for the geometry's centroid"""
    # Get centroid coordinates
    centroid = geometry.centroid
    lon, lat = centroid.x, centroid.y

    # Calculate UTM zone
    utm_zone = int(np.floor((lon + 180) / 6) + 1)
    hemisphere = "north" if lat >= 0 else "south"

    # Create projection string
    proj_string = f"+proj=utm +zone={utm_zone} +{hemisphere} +ellps=WGS84 +datum=WGS84 +units=m +no_defs"
    return proj_string


def get_bbox_in_bng(geometry):
    """Convert geometry to EPSG:27700 and return bounding box"""
    # Create transformation function from WGS84 to BNG
    project_to_bng = pyproj.Transformer.from_crs(
        "EPSG:4326", "EPSG:27700", always_xy=True
    ).transform

    # Transform geometry to BNG
    bng_geometry = transform(project_to_bng, geometry)

    # Get bounds
    return bng_geometry.bounds


def subdivide_polygon(polygon, max_size_meters=1000, reproject=True):
    """Subdivide a polygon into smaller polygons with max dimension of max_size_meters"""
    # Get appropriate UTM projection for accurate measurements
    utm_proj = get_utm_projection(polygon)

    # Create transformation functions
    project_to_utm = pyproj.Transformer.from_crs(
        "EPSG:4326", utm_proj, always_xy=True
    ).transform
    project_to_wgs84 = pyproj.Transformer.from_crs(
        utm_proj, "EPSG:4326", always_xy=True
    ).transform

    # Transform polygon to UTM for accurate measurements
    utm_polygon = transform(project_to_utm, polygon)

    # Get bounds and calculate grid
    minx, miny, maxx, maxy = utm_polygon.bounds

    x = minx
    while x < maxx:
        y = miny
        while y < maxy:
            # Calculate cell width and height (handling edge cases)
            width = min(max_size_meters, maxx - x)
            height = min(max_size_meters, maxy - y)

            # Create cell in UTM coordinates
            cell = box(x, y, x + width, y + height)

            # Convert back to WGS84
            wgs84_cell = transform(project_to_wgs84, cell)

            # Only include cells that intersect with the original polygon
            if wgs84_cell.intersects(polygon):
                intersection = wgs84_cell.intersection(polygon)
                if not intersection.is_empty:
                    if reproject:
                        # Get bounding box in BNG (EPSG:27700)
                        (w, s, e, n) = get_bbox_in_bng(intersection)
                    else:
                        (w, s, e, n) = intersection.bounds
                    yield (w, s, e, n)

            y += max_size_meters
        x += max_size_meters


def get_mapit_bboxes(area_id, api_key, max_size=1000, reproject=True):
    """Main function to download and process GeoJSON"""
    # Download GeoJSON
    geojson_url = f"https://mapit.mysociety.org/area/{area_id}.geojson"
    log(f"Downloading GeoJSON from {geojson_url}...")
    headers = {"X-Api-Key": api_key}
    response = requests.get(geojson_url, headers=headers)
    response.raise_for_status()  # Raise exception for HTTP errors
    gdf = gpd.read_file(io.StringIO(response.text))

    # Check CRS and convert to WGS84 if needed
    if gdf.crs is None:
        log("Warning: GeoJSON has no CRS specified, assuming WGS84")
        gdf.crs = "EPSG:4326"
    elif gdf.crs != "EPSG:4326":
        log(f"Converting from {gdf.crs} to WGS84")
        gdf = gdf.to_crs("EPSG:4326")

    log(f"Found {len(gdf)} features in the GeoJSON")

    # Process each geometry in the original file
    for idx, row in gdf.iterrows():
        log(f"Processing feature {idx+1}/{len(gdf)}...")
        geom = row.geometry

        # Handle different geometry types
        if geom.geom_type == "Polygon":
            yield from subdivide_polygon(geom, max_size, reproject)
        elif geom.geom_type == "MultiPolygon":
            for poly in geom.geoms:
                yield from subdivide_polygon(poly, max_size, reproject)
        else:
            log(
                f"Skipping {geom.geom_type} geometry (only Polygon and MultiPolygon supported)"
            )


def skip_invalid_features(features, geometry_type):
    for f in features:
        if f["geometry"]["type"] != geometry_type:
            cid = f["properties"].get("CentralAssetId") or f["properties"].get(
                "centralAssetId"
            )
            log(
                f"Skipping feature CentralAssetId {cid} with invalid geometry type ({f['geometry']['type']})"
            )
            continue
        yield f


def process_layer(layer, config):
    source = config["sources"][layer["source"]]
    graphql = True if "token" in source else False

    if "mapit_id" in layer:
        api_key = (config.get("mapit") or {}).get("api_key")
        bboxes = get_mapit_bboxes(
            layer["mapit_id"], api_key
        )  # , reproject=not graphql)
    else:
        bboxes = [[int(x) for x in layer["bbox"].split(",")]]

    log(f"Saving layer {layer['output']}")

    geometry_type = layer.get("geometry_type", "Point")

    default_props = {
        "FeatureX": "float",
        "FeatureY": "float",
        "CentralAssetId": "str",
        "FeatureId": "str",
        "FeatureLocation": "str",
        "FeatureTypeName": "str",
        "AddressReference": "str",
    }

    graphql_props = {
        "centralAssetId": "str",
        "centroidEasting": "float",
        "centroidNorthing": "float",
        "featureKey": "float",
        "featureId": "str",
        "featureTypeCode": "str",
        "key": "str",
        "location": "str",
        "notes": "str",
        "siteCode": "str",
        "featureGroupCode": "str",
        "featureTypeName": "str",
    }

    meta = {
        "crs": {"init": f"epsg:{layer.get('output_srs', '27700')}"},
        "driver": DRIVERS.get(layer["output"].rsplit(".", 1)[-1]),
        "schema": {
            "geometry": geometry_type,
            "properties": graphql_props if graphql else default_props,
        },
    }

    features = []
    for bbox in bboxes:
        features.extend(
            get_graphql_features(source, bbox, layer)
            if graphql
            else AssetSearchToFeatures(
                source, bbox, layer["feature_types"], layer["box_size"]
            )
        )
        log(f"Assets so far: {len(features)}")
    features = skip_invalid_features(features, geometry_type)

    outpath = os.path.join(OUTPUT_PREFIX, layer["output"])
    os.makedirs(os.path.dirname(outpath), exist_ok=True)

    log(f"Writing {outpath}...")
    with fiona.open(outpath, "w", **meta) as output:
        output.writerecords(features)
    log("done.")


def main():
    with open("general.yml") as f:
        config = yaml.safe_load(f)

    for layer in config["layers"]:
        process_layer(layer, config)


if __name__ == "__main__":
    main()

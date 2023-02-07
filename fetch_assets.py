#!/usr/bin/env python3
import sys
import os
from datetime import datetime
from math import ceil
from time import sleep

import yaml
import fiona
import requests
import lxml.etree as etree
from geomet import wkt


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
""".encode(
        "utf-8"
    )
    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "Soapaction": "http://www.confirm.co.uk/schema/am/connector/webservice/ProcessOperations",
    }
    for attempt in range(3):
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


def AssetSearchToFeatures(source, bbox, feature_types=[], box_size=None, indent=0):
    x1, y1, x2, y2 = bbox
    features = []

    # Pad by a metre in case there are assets right on the edge
    overlap = 1
    if box_size is not None:
        xs = list(range(x1, x2, box_size)) + [x2]
        ys = list(range(y1, y2, box_size)) + [y2]
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
    return parsed["{http://schemas.xmlsoap.org/soap/envelope/}Envelope"][
        "{http://schemas.xmlsoap.org/soap/envelope/}Body"
    ][
        "{http://www.confirm.co.uk/schema/am/connector/webservice}ProcessOperationsResult"
    ][
        "Response"
    ][
        "OperationResponse"
    ]


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


def get_mapit_bbox(area_id, api_key):
    headers = {"X-Api-Key": api_key}
    area = requests.get(
        f"https://mapit.mysociety.org/area/{area_id}/geometry", headers=headers
    ).json()
    w, s, e, n = (
        int(area["min_e"]),
        int(area["min_n"]),
        int(area["max_e"]),
        int(area["max_n"]),
    )
    return (w, s, e, n)


def process_layer(layer, config):
    if "mapit_id" in layer:
        api_key = (config.get("mapit") or {}).get("api_key")
        bbox = get_mapit_bbox(layer["mapit_id"], api_key)
    else:
        bbox = [int(x) for x in layer["bbox"].split(",")]

    log(f"Saving layer {layer['output']}")

    meta = {
        "crs": {"init": "epsg:27700"},
        "driver": DRIVERS.get(layer["output"].rsplit(".", 1)[-1]),
        "schema": {
            "geometry": layer.get("geometry_type", "Point"),
            "properties": {
                "FeatureX": "float",
                "FeatureY": "float",
                "CentralAssetId": "str",
                "FeatureId": "str",
                "FeatureLocation": "str",
                "FeatureTypeName": "str",
                "AddressReference": "str",
            },
        },
    }

    source = config["sources"][layer["source"]]
    features = AssetSearchToFeatures(
        source, bbox, layer["feature_types"], layer["box_size"]
    )

    with fiona.open(
        os.path.join(OUTPUT_PREFIX, layer["output"]), "w", **meta
    ) as output:
        output.writerecords(features)
    log("done.")


def main():
    with open("general.yml") as f:
        config = yaml.safe_load(f)

    for layer in config["layers"]:
        process_layer(layer, config)


if __name__ == "__main__":
    main()

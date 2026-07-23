#!/usr/bin/env python3
"""Create an HTML map of ASTER/GDEM tiles stored in MinIO buckets."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ds_download.minio_connection import MinioConnection  # noqa: E402

TILE_RE = re.compile(r"(?<![A-Z0-9])([NS]\d{2}[EW]\d{3})(?![A-Z0-9])", re.I)


def parse_tile(tile: str) -> tuple[float, float, float, float]:
    lat = int(tile[1:3]) * (1 if tile[0].upper() == "N" else -1)
    lon = int(tile[4:7]) * (1 if tile[3].upper() == "E" else -1)
    return lon, lat, lon + 1, lat + 1


def tiles_from_bucket(bucket_name: str) -> set[str]:
    client = MinioConnection(bucket_name=bucket_name)
    tiles: set[str] = set()
    for obj in client.list_objects(bucket_name, recursive=True):
        match = TILE_RE.search(obj.object_name)
        if match:
            tiles.add(match.group(1).upper())
    return tiles


def feature(tile: str, sources: list[str]) -> dict:
    west, south, east, north = parse_tile(tile)
    return {
        "type": "Feature",
        "properties": {"tile": tile, "sources": sources},
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [west, south],
                [east, south],
                [east, north],
                [west, north],
                [west, south],
            ]],
        },
    }


def build_geojson(aster_tiles: set[str], dem_tiles: set[str]) -> dict:
    features = []
    for tile in sorted(aster_tiles | dem_tiles):
        sources = []
        if tile in aster_tiles:
            sources.append("ASTER")
        if tile in dem_tiles:
            sources.append("DEM")
        features.append(feature(tile, sources))
    return {"type": "FeatureCollection", "features": features}


def render_html(geojson: dict) -> str:
    data = json.dumps(geojson, separators=(",", ":"))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ASTER GDEM MinIO Tiles</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
    html, body, #map {{ height: 100%; margin: 0; }}
    .legend {{
      background: white;
      border: 1px solid #bbb;
      border-radius: 4px;
      font: 14px/1.4 sans-serif;
      padding: 8px 10px;
    }}
    .swatch {{
      display: inline-block;
      height: 10px;
      margin-right: 6px;
      width: 18px;
    }}
  </style>
</head>
<body>
<div id="map"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const tiles = {data};
const map = L.map("map");
L.tileLayer("https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{
  maxZoom: 12,
  attribution: "&copy; OpenStreetMap contributors"
}}).addTo(map);

function color(sources) {{
  if (sources.length === 2) return "#15803d";
  return sources[0] === "ASTER" ? "#2563eb" : "#d97706";
}}

const layer = L.geoJSON(tiles, {{
  style: feature => {{
    const c = color(feature.properties.sources);
    return {{ color: c, fillColor: c, fillOpacity: 0.28, weight: 1 }};
  }},
  onEachFeature: (feature, polygon) => {{
    polygon.bindPopup(
      `<b>${{feature.properties.tile}}</b><br>${{feature.properties.sources.join(", ")}}`
    );
  }}
}}).addTo(map);

if (tiles.features.length) {{
  map.fitBounds(layer.getBounds(), {{ padding: [20, 20] }});
}} else {{
  map.setView([0, 0], 2);
}}

const legend = L.control({{ position: "bottomright" }});
legend.onAdd = () => {{
  const div = L.DomUtil.create("div", "legend");
  div.innerHTML = `
    <div><span class="swatch" style="background:#2563eb"></span>ASTER only</div>
    <div><span class="swatch" style="background:#d97706"></span>DEM only</div>
    <div><span class="swatch" style="background:#15803d"></span>Both buckets</div>
  `;
  return div;
}};
legend.addTo(map);
</script>
</body>
</html>
"""


def create_map(output: Path) -> None:
    load_dotenv(ROOT / ".env")
    aster_bucket = os.environ.get("MINIO_BUCKET_NAME_ASTER")
    dem_bucket = os.environ.get("MINIO_BUCKET_NAME_DEM")
    missing = [
        name
        for name, value in {
            "MINIO_BUCKET_NAME_ASTER": aster_bucket,
            "MINIO_BUCKET_NAME_DEM": dem_bucket,
        }.items()
        if not value
    ]
    if missing:
        raise SystemExit(f"Missing env variable(s): {', '.join(missing)}")

    geojson = build_geojson(
        tiles_from_bucket(aster_bucket),
        tiles_from_bucket(dem_bucket),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_html(geojson), encoding="utf-8")
    print(f"Wrote {output} with {len(geojson['features'])} tile(s).")


def self_test() -> None:
    assert parse_tile("N36W006") == (-6, 36, -5, 37)
    assert parse_tile("S01E010") == (10, -1, 11, 0)
    geojson = build_geojson({"N36W006"}, {"N36W006", "S01E010"})
    assert len(geojson["features"]) == 2
    assert geojson["features"][0]["properties"]["sources"] == ["ASTER", "DEM"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("aster_gdem_tiles_map.html"),
        help="HTML file to write",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return
    create_map(args.output)


if __name__ == "__main__":
    main()

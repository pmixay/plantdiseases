#!/usr/bin/env python3
"""Pack Natural Earth 1:110m Admin-0 GeoJSON for WorldMapView.

The Android widget only needs country identity/region aliases and polygon
coordinates. Keeping those fields in a tiny binary container avoids shipping
the roughly 840 KB source GeoJSON or adding a GeoJSON parser dependency.

Usage:
    python3 tools/build_world_map_asset.py \
        ne_110m_admin_0_countries.geojson \
        app/src/main/assets/natural_earth_countries.bin
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path
from typing import BinaryIO, Iterable


MAGIC = b"NEWM"
VERSION = 1
COORDINATE_SCALE = 100_000

IDENTITY_FIELDS = (
    "ADM0_A3",
    "ISO_A2",
    "ISO_A3",
    "SOV_A3",
    "NAME",
    "NAME_LONG",
    "ADMIN",
    "SOVEREIGNT",
    "ABBREV",
    "FORMAL_EN",
    "NAME_CIAWF",
    "NAME_SORT",
    "NAME_ALT",
    "NAME_EN",
    "NAME_RU",
)
REGION_FIELDS = ("CONTINENT", "SUBREGION", "REGION_WB", "REGION_UN")


def text_values(properties: dict, fields: Iterable[str]) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for field in fields:
        value = str(properties.get(field) or "").strip()
        if not value or value == "-99":
            continue
        folded = value.casefold()
        if folded not in seen:
            seen.add(folded)
            values.append(value)
    return values


def write_text(output: BinaryIO, value: str) -> None:
    encoded = value.encode("utf-8")
    if len(encoded) > 65_535:
        raise ValueError(f"String too long for map asset: {value[:80]}")
    output.write(struct.pack(">H", len(encoded)))
    output.write(encoded)


def write_text_list(output: BinaryIO, values: list[str]) -> None:
    if len(values) > 255:
        raise ValueError("Too many aliases")
    output.write(struct.pack(">B", len(values)))
    for value in values:
        write_text(output, value)


def polygons_for(feature: dict) -> list:
    geometry = feature.get("geometry") or {}
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    if geometry_type == "Polygon":
        return [coordinates]
    if geometry_type == "MultiPolygon":
        return coordinates
    raise ValueError(f"Unsupported Natural Earth geometry: {geometry_type}")


def build(source: Path, destination: Path) -> None:
    data = json.loads(source.read_text(encoding="utf-8"))
    features = data.get("features") or []
    destination.parent.mkdir(parents=True, exist_ok=True)

    with destination.open("wb") as output:
        output.write(MAGIC)
        output.write(struct.pack(">BH", VERSION, len(features)))
        for feature in features:
            properties = feature.get("properties") or {}
            code = str(properties.get("ADM0_A3") or "").strip()
            name_en = str(
                properties.get("NAME_EN")
                or properties.get("NAME")
                or properties.get("ADMIN")
                or code
            ).strip()
            name_ru = str(properties.get("NAME_RU") or name_en).strip()
            write_text(output, code)
            write_text(output, name_en)
            write_text(output, name_ru)
            write_text_list(output, text_values(properties, IDENTITY_FIELDS))
            write_text_list(output, text_values(properties, REGION_FIELDS))

            polygons = polygons_for(feature)
            output.write(struct.pack(">H", len(polygons)))
            for polygon in polygons:
                output.write(struct.pack(">H", len(polygon)))
                for ring in polygon:
                    if len(ring) > 65_535:
                        raise ValueError(f"Ring too large in {name_en}")
                    output.write(struct.pack(">H", len(ring)))
                    for longitude, latitude in ring:
                        output.write(
                            struct.pack(
                                ">ii",
                                round(float(longitude) * COORDINATE_SCALE),
                                round(float(latitude) * COORDINATE_SCALE),
                            )
                        )

    size = destination.stat().st_size
    if size > 150 * 1024:
        raise ValueError(f"Packed asset exceeds 150 KB budget: {size} bytes")
    print(f"Wrote {len(features)} countries, {size} bytes -> {destination}")


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("Expected input GeoJSON and output asset paths")
    build(Path(sys.argv[1]), Path(sys.argv[2]))


if __name__ == "__main__":
    main()

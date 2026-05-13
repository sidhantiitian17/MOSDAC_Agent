"""Static MOSDAC product catalogue + Indian-state bounding-box lookup.

Both are pure data, loaded once at import time. To extend without touching
code, drop a JSON file and point `MOSDAC_CATALOG_JSON_PATH` /
`MOSDAC_REGIONS_JSON_PATH` at it. Schemas:

    catalog.json  -> list[{dataset_id, name, satellite, sensor, level, bands?}]
    regions.json  -> dict[str, str]  e.g. {"tamil nadu": "76.2,8.0,80.4,13.6"}
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Iterable, List, Optional

from mosdac_agent.config import mosdac_settings

_BUILTIN_CATALOGUE: List[dict] = [
    {
        "dataset_id": "3SIMG_L1B_STD",
        "name": "INSAT-3D Imager L1B (Standard)",
        "satellite": "INSAT-3D",
        "sensor": "Imager",
        "level": "L1B",
        "bands": ["VIS", "SWIR", "MIR", "WV", "TIR-1", "TIR-2"],
    },
    {
        "dataset_id": "3SIMG_L1C_STD",
        "name": "INSAT-3D Imager L1C",
        "satellite": "INSAT-3D",
        "sensor": "Imager",
        "level": "L1C",
    },
    {
        "dataset_id": "3DIMG_L2B_CMK",
        "name": "INSAT-3D Imager L2B Cloud Map",
        "satellite": "INSAT-3D",
        "sensor": "Imager",
        "level": "L2B",
    },
    {
        "dataset_id": "3RIMG_L1B_STD",
        "name": "INSAT-3DR Imager L1B (Standard)",
        "satellite": "INSAT-3DR",
        "sensor": "Imager",
        "level": "L1B",
        "bands": ["VIS", "SWIR", "MIR", "WV", "TIR-1", "TIR-2"],
    },
    {
        "dataset_id": "3DSND_L1B_STD",
        "name": "INSAT-3D Sounder L1B",
        "satellite": "INSAT-3D",
        "sensor": "Sounder",
        "level": "L1B",
    },
    {
        "dataset_id": "SCAT_L2B_OWS",
        "name": "SCATSAT-1 L2B Ocean Wind Speed",
        "satellite": "SCATSAT-1",
        "sensor": "OSCAT",
        "level": "L2B",
    },
]

_BUILTIN_REGIONS = {
    "tamil nadu": "76.2,8.0,80.4,13.6",
    "kerala": "74.5,8.2,77.5,12.9",
    "karnataka": "74.0,11.5,78.6,18.5",
    "maharashtra": "72.6,15.6,80.9,22.0",
    "andhra pradesh": "76.7,12.6,84.8,19.9",
    "gujarat": "68.1,20.1,74.5,24.7",
    "rajasthan": "69.5,23.0,78.3,30.2",
    "uttar pradesh": "77.0,23.9,84.6,30.4",
    "west bengal": "85.8,21.5,89.9,27.2",
    "odisha": "81.4,17.8,87.5,22.6",
    "madhya pradesh": "74.0,21.1,82.8,26.9",
    "punjab": "73.9,29.5,76.9,32.5",
    "haryana": "74.5,27.4,77.6,30.9",
    "bihar": "83.3,24.3,88.1,27.5",
    "assam": "89.7,24.1,96.0,28.0",
    "delhi": "76.8,28.4,77.4,28.9",
    "india": "68.1,6.5,97.4,35.7",
}


def _load_json(path: str) -> Optional[object]:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def get_catalogue() -> List[dict]:
    override = _load_json(mosdac_settings.catalog_json_path)
    if isinstance(override, list) and override:
        return override
    return list(_BUILTIN_CATALOGUE)


@lru_cache(maxsize=1)
def get_regions() -> dict:
    override = _load_json(mosdac_settings.regions_json_path)
    if isinstance(override, dict) and override:
        return {k.lower(): v for k, v in override.items()}
    return dict(_BUILTIN_REGIONS)


def search_catalogue(
    query: str = "",
    satellite: Optional[str] = None,
    sensor: Optional[str] = None,
) -> List[dict]:
    q = (query or "").lower().strip()
    out: List[dict] = []
    for row in get_catalogue():
        if satellite and satellite.lower() not in row.get("satellite", "").lower():
            continue
        if sensor and sensor.lower() not in row.get("sensor", "").lower():
            continue
        haystack = " ".join(
            [
                row.get("name", ""),
                row.get("dataset_id", ""),
                row.get("satellite", ""),
                row.get("sensor", ""),
                row.get("level", ""),
                " ".join(row.get("bands") or []),
            ]
        ).lower()
        if q and q not in haystack:
            continue
        out.append(row)
    return out


def resolve_region(name: str) -> Optional[str]:
    if not name:
        return None
    return get_regions().get(name.strip().lower())


def invalidate_caches() -> None:
    get_catalogue.cache_clear()
    get_regions.cache_clear()


def dataset_ids() -> Iterable[str]:
    return (row["dataset_id"] for row in get_catalogue())

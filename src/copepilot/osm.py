"""OpenStreetMap data fetching and road geometry extraction."""

import math
import requests
from dataclasses import dataclass
from typing import List, Tuple


@dataclass
class RoadSegment:
    """A segment of road with its geometry."""

    way_id: int
    name: str
    points: List[Tuple[float, float]]  # (lat, lon) pairs
    speed_limit: int  # km/h, 0 if unknown


class OSMFetcher:
    """Fetches road data from OpenStreetMap Overpass API."""

    OVERPASS_URL = "https://overpass-api.de/api/interpreter"

    def __init__(self, cache_dir: str = "~/.copepilot/cache"):
        self.cache_dir = cache_dir

    def fetch_roads_around(
        self, lat: float, lon: float, radius_m: float = 2000
    ) -> List[RoadSegment]:
        """Fetch all drivable roads within radius of a point."""
        query = f"""
        [out:json][timeout:25];
        (
          way["highway"~"^(motorway|trunk|primary|secondary|tertiary|unclassified|residential)$"]
            (around:{radius_m},{lat},{lon});
        );
        out body;
        >;
        out skel qt;
        """

        response = requests.post(self.OVERPASS_URL, data={"data": query}, timeout=30)
        response.raise_for_status()
        data = response.json()

        return self._parse_response(data)

    def _parse_response(self, data: dict) -> List[RoadSegment]:
        """Parse Overpass API response into RoadSegments."""
        nodes = {}
        ways = []

        for element in data.get("elements", []):
            if element["type"] == "node":
                nodes[element["id"]] = (element["lat"], element["lon"])
            elif element["type"] == "way":
                ways.append(element)

        segments = []
        for way in ways:
            tags = way.get("tags", {})
            points = [nodes[nid] for nid in way["nodes"] if nid in nodes]

            if len(points) < 2:
                continue

            speed_limit = self._parse_speed_limit(tags.get("maxspeed", ""))

            segments.append(
                RoadSegment(
                    way_id=way["id"],
                    name=tags.get("name", ""),
                    points=points,
                    speed_limit=speed_limit,
                )
            )

        return segments

    def _parse_speed_limit(self, value: str) -> int:
        """Parse OSM maxspeed tag to km/h."""
        if not value:
            return 0
        try:
            if "mph" in value:
                return int(float(value.replace("mph", "").strip()) * 1.60934)
            return int(value)
        except ValueError:
            return 0

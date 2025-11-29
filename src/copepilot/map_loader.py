"""Load road network from OSM PBF file."""

import math
import os
import pickle
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple
from pathlib import Path

try:
    import osmium
    OSMIUM_AVAILABLE = True
except ImportError:
    OSMIUM_AVAILABLE = False

from .geometry import haversine_distance, bearing


@dataclass
class Node:
    """OSM node with coordinates."""
    id: int
    lat: float
    lon: float


@dataclass
class Way:
    """OSM way representing a road segment."""
    id: int
    nodes: List[int]  # Node IDs in order
    name: str = ""
    highway_type: str = ""
    oneway: bool = False
    speed_limit: int = 0  # km/h, 0 if unknown
    bridge: bool = False


@dataclass
class Junction:
    """A junction where roads meet."""
    node_id: int
    lat: float
    lon: float
    connected_ways: List[int]  # Way IDs that meet here
    is_t_junction: bool = False


@dataclass
class RoadNetwork:
    """Cached road network for a geographic area."""
    nodes: Dict[int, Node] = field(default_factory=dict)
    ways: Dict[int, Way] = field(default_factory=dict)
    junctions: Dict[int, Junction] = field(default_factory=dict)
    # Node ID -> list of Way IDs that contain this node
    node_to_ways: Dict[int, List[int]] = field(default_factory=dict)

    def get_way_geometry(self, way_id: int) -> List[Tuple[float, float]]:
        """Get list of (lat, lon) points for a way."""
        way = self.ways.get(way_id)
        if not way:
            return []
        return [
            (self.nodes[nid].lat, self.nodes[nid].lon)
            for nid in way.nodes
            if nid in self.nodes
        ]


class PBFRoadHandler(osmium.SimpleHandler if OSMIUM_AVAILABLE else object):
    """Osmium handler to extract road network from PBF."""

    HIGHWAY_TYPES = {
        "motorway", "motorway_link",
        "trunk", "trunk_link",
        "primary", "primary_link",
        "secondary", "secondary_link",
        "tertiary", "tertiary_link",
        "unclassified", "residential",
        "living_street", "service",
    }

    def __init__(self, bounds: Tuple[float, float, float, float]):
        """
        Initialize handler with geographic bounds.

        Args:
            bounds: (min_lat, min_lon, max_lat, max_lon)
        """
        if OSMIUM_AVAILABLE:
            super().__init__()
        self.bounds = bounds
        self.nodes: Dict[int, Node] = {}
        self.ways: Dict[int, Way] = {}
        self.needed_nodes: Set[int] = set()

    def _in_bounds(self, lat: float, lon: float) -> bool:
        """Check if point is within our bounds."""
        min_lat, min_lon, max_lat, max_lon = self.bounds
        return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon

    def way(self, w):
        """Process a way element."""
        tags = {tag.k: tag.v for tag in w.tags}
        highway = tags.get("highway", "")

        if highway not in self.HIGHWAY_TYPES:
            return

        # Get node refs
        node_refs = [n.ref for n in w.nodes]

        # Parse attributes
        name = tags.get("name", "")
        oneway = tags.get("oneway", "no") in ("yes", "true", "1")
        speed_limit = self._parse_speed_limit(tags.get("maxspeed", ""))
        bridge = tags.get("bridge", "no") not in ("no", "")

        self.ways[w.id] = Way(
            id=w.id,
            nodes=node_refs,
            name=name,
            highway_type=highway,
            oneway=oneway,
            speed_limit=speed_limit,
            bridge=bridge,
        )
        self.needed_nodes.update(node_refs)

    def node(self, n):
        """Process a node element."""
        if n.id in self.needed_nodes or self._in_bounds(n.location.lat, n.location.lon):
            self.nodes[n.id] = Node(
                id=n.id,
                lat=n.location.lat,
                lon=n.location.lon,
            )

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


class MapLoader:
    """Load and query road network from OSM PBF file."""

    def __init__(self, pbf_path: Path):
        if not OSMIUM_AVAILABLE:
            raise ImportError(
                "osmium not available. Install with: pip install osmium"
            )
        self.pbf_path = Path(pbf_path)
        self._cache_file = self.pbf_path.with_suffix(".roads.pkl")
        self._full_network: Optional[RoadNetwork] = None
        self._query_cache: Optional[RoadNetwork] = None
        self._query_cache_center: Optional[Tuple[float, float]] = None
        self._query_cache_radius: float = 0

    def _get_full_network(self) -> RoadNetwork:
        """Get the full road network, loading from cache or PBF."""
        if self._full_network:
            return self._full_network

        # Try loading from cache file
        if self._cache_file.exists():
            pbf_mtime = os.path.getmtime(self.pbf_path)
            cache_mtime = os.path.getmtime(self._cache_file)
            if cache_mtime > pbf_mtime:
                try:
                    print(f"  Loading cached roads from {self._cache_file.name}...")
                    with open(self._cache_file, "rb") as f:
                        self._full_network = pickle.load(f)
                    print(f"  Loaded {len(self._full_network.ways)} roads from cache")
                    return self._full_network
                except Exception as e:
                    print(f"  Cache load failed: {e}, rebuilding...")

        # Extract all roads from PBF
        print(f"  Extracting roads from PBF (first time only)...")
        self._full_network = self._extract_all_roads()

        # Save to cache
        try:
            print(f"  Saving cache to {self._cache_file.name}...")
            with open(self._cache_file, "wb") as f:
                pickle.dump(self._full_network, f)
            size_mb = os.path.getsize(self._cache_file) / 1024 / 1024
            print(f"  Cache saved ({size_mb:.1f} MB)")
        except Exception as e:
            print(f"  Warning: Could not save cache: {e}")

        return self._full_network

    def _extract_all_roads(self) -> RoadNetwork:
        """Extract all roads from the PBF file."""
        # Use very large bounds to get everything
        bounds = (-90, -180, 90, 180)
        handler = PBFRoadHandler(bounds)
        handler.apply_file(str(self.pbf_path), locations=True)
        print(f"  Found {len(handler.ways)} roads, {len(handler.nodes)} nodes")

        # Build network
        network = RoadNetwork()

        for nid, node in handler.nodes.items():
            if nid in handler.needed_nodes:
                network.nodes[nid] = node

        for wid, way in handler.ways.items():
            if all(nid in network.nodes for nid in way.nodes):
                network.ways[wid] = way
                for nid in way.nodes:
                    if nid not in network.node_to_ways:
                        network.node_to_ways[nid] = []
                    network.node_to_ways[nid].append(wid)

        # Build junctions
        for nid, way_ids in network.node_to_ways.items():
            if len(way_ids) >= 2:
                node = network.nodes[nid]
                network.junctions[nid] = Junction(
                    node_id=nid,
                    lat=node.lat,
                    lon=node.lon,
                    connected_ways=way_ids,
                    is_t_junction=self._is_t_junction(nid, way_ids, network),
                )

        return network

    def load_around(
        self,
        lat: float,
        lon: float,
        radius_m: float = 2000
    ) -> RoadNetwork:
        """
        Load road network around a point.

        Uses caching - if we already have data covering this area, returns cache.
        """
        # Check if query cache covers this request
        if self._query_cache and self._query_cache_center:
            dist = haversine_distance(
                lat, lon,
                self._query_cache_center[0], self._query_cache_center[1]
            )
            if dist < self._query_cache_radius / 2:
                return self._query_cache

        # Get full network (from cache or PBF)
        full_network = self._get_full_network()

        # Calculate bounds
        lat_delta = radius_m / 111000
        lon_delta = radius_m / (111000 * math.cos(math.radians(lat)))
        min_lat, max_lat = lat - lat_delta, lat + lat_delta
        min_lon, max_lon = lon - lon_delta, lon + lon_delta

        # Filter to region
        network = RoadNetwork()

        # Find nodes in bounds
        for nid, node in full_network.nodes.items():
            if min_lat <= node.lat <= max_lat and min_lon <= node.lon <= max_lon:
                network.nodes[nid] = node

        # Find ways with at least one node in bounds, include all their nodes
        for wid, way in full_network.ways.items():
            if any(nid in network.nodes for nid in way.nodes):
                network.ways[wid] = way
                # Add all nodes of this way
                for nid in way.nodes:
                    if nid not in network.nodes and nid in full_network.nodes:
                        network.nodes[nid] = full_network.nodes[nid]

        # Build node-to-way index for filtered ways
        for wid, way in network.ways.items():
            for nid in way.nodes:
                if nid not in network.node_to_ways:
                    network.node_to_ways[nid] = []
                network.node_to_ways[nid].append(wid)

        # Copy junctions
        for nid, way_ids in network.node_to_ways.items():
            if len(way_ids) >= 2 and nid in full_network.junctions:
                network.junctions[nid] = full_network.junctions[nid]

        # Cache query result
        self._query_cache = network
        self._query_cache_center = (lat, lon)
        self._query_cache_radius = radius_m

        return network

    def _is_t_junction(
        self,
        node_id: int,
        way_ids: List[int],
        network: RoadNetwork
    ) -> bool:
        """
        Check if this junction is a T-junction.

        A T-junction has exactly 3 road segments meeting, with two being
        roughly opposite (continuing road) and one perpendicular (side road).
        """
        if len(way_ids) < 2:
            return False

        # Get bearings of all road segments leaving this junction
        node = network.nodes[node_id]
        bearings = []

        for wid in way_ids:
            way = network.ways[wid]
            try:
                idx = way.nodes.index(node_id)
            except ValueError:
                continue

            # Get bearing in each direction along this way
            if idx > 0:
                prev_node = network.nodes[way.nodes[idx - 1]]
                b = bearing(node.lat, node.lon, prev_node.lat, prev_node.lon)
                bearings.append(b)
            if idx < len(way.nodes) - 1:
                next_node = network.nodes[way.nodes[idx + 1]]
                b = bearing(node.lat, node.lon, next_node.lat, next_node.lon)
                bearings.append(b)

        if len(bearings) < 3:
            return False

        # Check if we have 2 opposite bearings (within 30°) and 1 perpendicular
        # This is a simplified check - true T-junction detection is complex
        for i, b1 in enumerate(bearings):
            for j, b2 in enumerate(bearings):
                if i >= j:
                    continue
                # Check if roughly opposite (180° apart)
                diff = abs((b1 - b2 + 180) % 360 - 180)
                if 150 < diff < 210 or diff < 30:
                    # These two are roughly aligned - check for perpendicular third
                    for k, b3 in enumerate(bearings):
                        if k == i or k == j:
                            continue
                        diff1 = abs((b3 - b1 + 180) % 360 - 180)
                        if 60 < diff1 < 120:
                            return True

        return False

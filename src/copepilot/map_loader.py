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
    tunnel: bool = False
    surface: str = ""  # asphalt, gravel, concrete, etc.
    ford: bool = False
    traffic_calming: str = ""  # bump, hump, table, etc.
    width: float = 0.0  # Road width in meters, 0 if unknown
    narrow: bool = False  # Explicit narrow tag


@dataclass
class Junction:
    """A junction where roads meet."""
    node_id: int
    lat: float
    lon: float
    connected_ways: List[int]  # Way IDs that meet here
    is_t_junction: bool = False


@dataclass
class RailwayCrossing:
    """A railway level crossing."""
    node_id: int
    lat: float
    lon: float


@dataclass
class Barrier:
    """A barrier on the road (cattle grid, gate, etc.)."""
    node_id: int
    lat: float
    lon: float
    barrier_type: str  # cattle_grid, gate, etc.


@dataclass
class RoadNetwork:
    """Cached road network for a geographic area."""
    nodes: Dict[int, Node] = field(default_factory=dict)
    ways: Dict[int, Way] = field(default_factory=dict)
    junctions: Dict[int, Junction] = field(default_factory=dict)
    # Node ID -> list of Way IDs that contain this node
    node_to_ways: Dict[int, List[int]] = field(default_factory=dict)
    # Railway level crossings
    railway_crossings: Dict[int, RailwayCrossing] = field(default_factory=dict)
    # Barriers (cattle grids, gates, etc.)
    barriers: Dict[int, Barrier] = field(default_factory=dict)

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
        self.railway_crossings: Dict[int, RailwayCrossing] = {}
        self.barriers: Dict[int, Barrier] = {}

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
        tunnel = tags.get("tunnel", "no") not in ("no", "")
        surface = tags.get("surface", "")
        ford = tags.get("ford", "no") not in ("no", "")
        traffic_calming = tags.get("traffic_calming", "")
        width = self._parse_width(tags.get("width", ""))
        narrow = tags.get("narrow", "no") not in ("no", "")

        self.ways[w.id] = Way(
            id=w.id,
            nodes=node_refs,
            name=name,
            highway_type=highway,
            oneway=oneway,
            speed_limit=speed_limit,
            bridge=bridge,
            tunnel=tunnel,
            surface=surface,
            ford=ford,
            traffic_calming=traffic_calming,
            width=width,
            narrow=narrow,
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
            # Check for special node types
            tags = {tag.k: tag.v for tag in n.tags}

            # Railway level crossing
            if tags.get("railway") == "level_crossing":
                self.railway_crossings[n.id] = RailwayCrossing(
                    node_id=n.id,
                    lat=n.location.lat,
                    lon=n.location.lon,
                )

            # Barriers (cattle grids, gates)
            barrier_type = tags.get("barrier", "")
            if barrier_type in ("cattle_grid", "gate"):
                self.barriers[n.id] = Barrier(
                    node_id=n.id,
                    lat=n.location.lat,
                    lon=n.location.lon,
                    barrier_type=barrier_type,
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

    def _parse_width(self, value: str) -> float:
        """Parse OSM width tag to meters."""
        if not value:
            return 0.0
        try:
            # Handle common formats: "3", "3.5", "3 m", "3.5m"
            value = value.lower().replace("m", "").strip()
            return float(value)
        except ValueError:
            return 0.0


class MapLoader:
    """Load and query road network from pickle cache or OSM PBF file.

    Accepts either a directory or a specific file path:
    - Directory: looks for .roads.pkl first, falls back to .osm.pbf
    - File path: uses that specific file (pkl or pbf)

    To update maps: either replace the .pkl directly, or delete .pkl and add .pbf
    """

    def __init__(self, map_path: Path):
        self.map_path = Path(map_path)
        self._pkl_file: Optional[Path] = None
        self._pbf_file: Optional[Path] = None
        self._full_network: Optional[RoadNetwork] = None
        self._query_cache: Optional[RoadNetwork] = None
        self._query_cache_center: Optional[Tuple[float, float]] = None
        self._query_cache_radius: float = 0

        # Find map files
        self._find_map_files()

    def _find_map_files(self) -> None:
        """Find pickle cache and/or PBF file."""
        if self.map_path.is_dir():
            # Look for files in directory
            pkls = list(self.map_path.glob("*.roads.pkl"))
            pbfs = list(self.map_path.glob("*.osm.pbf"))

            if pkls:
                # Use most recent pickle
                self._pkl_file = max(pkls, key=lambda p: p.stat().st_mtime)
            if pbfs:
                # Use most recent PBF
                self._pbf_file = max(pbfs, key=lambda p: p.stat().st_mtime)
        else:
            # Specific file provided
            if self.map_path.suffix == ".pkl" or str(self.map_path).endswith(".roads.pkl"):
                self._pkl_file = self.map_path
            elif self.map_path.suffix == ".pbf":
                self._pbf_file = self.map_path
                # Check for matching pickle
                pkl_path = self.map_path.with_suffix(".roads.pkl")
                if pkl_path.exists():
                    self._pkl_file = pkl_path

    def _get_full_network(self) -> RoadNetwork:
        """Get the full road network, loading from cache or PBF."""
        if self._full_network:
            return self._full_network

        # Try loading from pickle cache first
        if self._pkl_file and self._pkl_file.exists():
            try:
                print(f"  Loading cached roads from {self._pkl_file.name}...")
                with open(self._pkl_file, "rb") as f:
                    self._full_network = pickle.load(f)
                print(f"  Loaded {len(self._full_network.ways)} roads from cache")
                return self._full_network
            except Exception as e:
                print(f"  Cache load failed: {e}")

        # Fall back to extracting from PBF
        if not self._pbf_file or not self._pbf_file.exists():
            raise FileNotFoundError(
                f"No map data found. Provide a .roads.pkl or .osm.pbf file."
            )

        if not OSMIUM_AVAILABLE:
            raise ImportError(
                "osmium not available. Install with: pip install osmium"
            )

        print(f"  Extracting roads from {self._pbf_file.name}...")
        self._full_network = self._extract_all_roads()

        # Save to cache (alongside PBF)
        cache_file = self._pbf_file.with_suffix(".roads.pkl")
        try:
            print(f"  Saving cache to {cache_file.name}...")
            with open(cache_file, "wb") as f:
                pickle.dump(self._full_network, f)
            size_mb = os.path.getsize(cache_file) / 1024 / 1024
            print(f"  Cache saved ({size_mb:.1f} MB)")
            self._pkl_file = cache_file
        except Exception as e:
            print(f"  Warning: Could not save cache: {e}")

        return self._full_network

    def _extract_all_roads(self) -> RoadNetwork:
        """Extract all roads from the PBF file."""
        # Use very large bounds to get everything
        bounds = (-90, -180, 90, 180)
        handler = PBFRoadHandler(bounds)
        handler.apply_file(str(self._pbf_file), locations=True)
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

        # Copy railway crossings that are on roads we have
        for nid, crossing in handler.railway_crossings.items():
            if nid in network.node_to_ways:
                network.railway_crossings[nid] = crossing

        # Copy barriers that are on roads we have
        for nid, barrier in handler.barriers.items():
            if nid in network.node_to_ways:
                network.barriers[nid] = barrier

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

        # Copy railway crossings that are on our roads
        for nid in network.node_to_ways:
            if nid in full_network.railway_crossings:
                network.railway_crossings[nid] = full_network.railway_crossings[nid]

        # Copy barriers that are on our roads
        for nid in network.node_to_ways:
            if nid in full_network.barriers:
                network.barriers[nid] = full_network.barriers[nid]

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

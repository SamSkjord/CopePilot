"""Load road network from OSM PBF file."""

import math
import os
import pickle
import sqlite3
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

    @staticmethod
    def _parse_speed_limit(value: str) -> int:
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
        self._sqlite_cache_file = self.pbf_path.with_suffix(".roads.sqlite")
        self._full_network: Optional[RoadNetwork] = None
        self._query_cache: Optional[RoadNetwork] = None
        self._query_cache_center: Optional[Tuple[float, float]] = None
        self._query_cache_radius: float = 0
        # Avoid rebuilding heavy pickle caches for country-scale extracts
        self._max_pickle_nodes = 750_000

    def _get_full_network(self) -> RoadNetwork:
        """Get the full road network, loading from cache or PBF."""
        if self._full_network:
            return self._full_network

        # Prefer SQLite cache when available to avoid reparsing PBF
        if self._sqlite_cache_file.exists():
            pbf_mtime = os.path.getmtime(self.pbf_path)
            cache_mtime = os.path.getmtime(self._sqlite_cache_file)
            if cache_mtime > pbf_mtime:
                try:
                    print(f"  Loading cached roads from {self._sqlite_cache_file.name}...")
                    self._full_network = self._load_from_sqlite(self._sqlite_cache_file)
                    print(f"  Loaded {len(self._full_network.ways)} roads from SQLite cache")
                    return self._full_network
                except Exception as exc:  # pragma: no cover - defensive
                    print(f"  SQLite cache load failed: {exc}, falling back...")

        # Try loading from SQLite cache first
        # Try loading from pickle cache if SQLite missing or failed
        if self._cache_file.exists():
            pbf_mtime = os.path.getmtime(self.pbf_path)
            cache_mtime = os.path.getmtime(self._cache_file)
            if cache_mtime > pbf_mtime:
                try:
                    print(f"  Loading cached roads from {self._cache_file.name}...")
                    with open(self._cache_file, "rb") as f:
                        self._full_network = pickle.load(f)
                    print(f"  Loaded {len(self._full_network.ways)} roads from pickle cache")
                    return self._full_network
                except Exception as exc:
                    print(f"  Pickle cache load failed: {exc}, rebuilding...")

        # Extract all roads from PBF
        print(f"  Extracting roads from PBF (first time only)...")
        self._full_network = self._extract_all_roads()

        # Save to caches
        try:
            print(f"  Saving cache to {self._cache_file.name}...")
            with open(self._cache_file, "wb") as f:
                pickle.dump(self._full_network, f)
            size_mb = os.path.getsize(self._cache_file) / 1024 / 1024
            print(f"  Cache saved ({size_mb:.1f} MB)")
        except Exception as exc:  # pragma: no cover - defensive
            print(f"  Warning: Could not save pickle cache: {exc}")

        try:
            print(f"  Saving SQLite cache to {self._sqlite_cache_file.name}...")
            self._save_to_sqlite(self._sqlite_cache_file, self._full_network)
            size_mb = os.path.getsize(self._sqlite_cache_file) / 1024 / 1024
            print(f"  SQLite cache saved ({size_mb:.1f} MB)")
        except Exception as exc:  # pragma: no cover - defensive
            print(f"  Warning: Could not save SQLite cache: {exc}")

        return self._full_network

    def _save_to_sqlite(self, path: Path, network: RoadNetwork) -> None:
        """Persist the road network to a SQLite file with RTree support if available."""
        if path.exists():
            path.unlink()

        conn = sqlite3.connect(path)
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL;")
            cur.execute("PRAGMA synchronous=OFF;")

            cur.executescript(
                """
                CREATE TABLE nodes(
                    id INTEGER PRIMARY KEY,
                    lat REAL NOT NULL,
                    lon REAL NOT NULL
                );
                CREATE TABLE ways(
                    id INTEGER PRIMARY KEY,
                    name TEXT,
                    highway_type TEXT,
                    oneway INTEGER,
                    speed_limit INTEGER,
                    bridge INTEGER
                );
                CREATE TABLE way_nodes(
                    way_id INTEGER NOT NULL,
                    idx INTEGER NOT NULL,
                    node_id INTEGER NOT NULL,
                    PRIMARY KEY (way_id, idx)
                );
                CREATE INDEX IF NOT EXISTS idx_way_nodes_node ON way_nodes(node_id);
                CREATE INDEX IF NOT EXISTS idx_way_nodes_way ON way_nodes(way_id);
                """
            )

            cur.executemany(
                "INSERT INTO nodes(id, lat, lon) VALUES (?, ?, ?)",
                ((nid, node.lat, node.lon) for nid, node in network.nodes.items()),
            )

            cur.executemany(
                """
                INSERT INTO ways(id, name, highway_type, oneway, speed_limit, bridge)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        wid,
                        way.name,
                        way.highway_type,
                        int(way.oneway),
                        way.speed_limit,
                        int(way.bridge),
                    )
                    for wid, way in network.ways.items()
                ),
            )

            way_node_rows = []
            for wid, way in network.ways.items():
                way_node_rows.extend((wid, idx, nid) for idx, nid in enumerate(way.nodes))
            cur.executemany(
                "INSERT INTO way_nodes(way_id, idx, node_id) VALUES (?, ?, ?)",
                way_node_rows,
            )

            try:
                cur.execute(
                    """
                    CREATE VIRTUAL TABLE node_index USING rtree(
                        id,
                        min_lat, max_lat,
                        min_lon, max_lon
                    );
                    """
                )
                cur.executemany(
                    "INSERT INTO node_index(id, min_lat, max_lat, min_lon, max_lon) VALUES (?, ?, ?, ?, ?)",
                    (
                        (nid, node.lat, node.lat, node.lon, node.lon)
                        for nid, node in network.nodes.items()
                    ),
                )
            except sqlite3.OperationalError:
                # rtree module not available; continue without spatial index
                pass

            conn.commit()
        finally:
            conn.close()

    def _build_sqlite_cache_streaming(self) -> None:
        """Create a SQLite cache without materializing the whole network in RAM."""
        if self._sqlite_cache_file.exists():
            self._sqlite_cache_file.unlink()

        conn = sqlite3.connect(self._sqlite_cache_file)
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("PRAGMA synchronous=OFF;")
        cur.executescript(
            """
            CREATE TABLE nodes(
                id INTEGER PRIMARY KEY,
                lat REAL NOT NULL,
                lon REAL NOT NULL
            );
            CREATE TABLE ways(
                id INTEGER PRIMARY KEY,
                name TEXT,
                highway_type TEXT,
                oneway INTEGER,
                speed_limit INTEGER,
                bridge INTEGER
            );
            CREATE TABLE way_nodes(
                way_id INTEGER NOT NULL,
                idx INTEGER NOT NULL,
                node_id INTEGER NOT NULL,
                PRIMARY KEY (way_id, idx)
            );
            CREATE INDEX idx_way_nodes_node ON way_nodes(node_id);
            CREATE INDEX idx_way_nodes_way ON way_nodes(way_id);
            """
        )
        conn.commit()

        needed_nodes: Set[int] = set()
        way_rows: List[Tuple[int, str, str, int, int, int]] = []
        way_node_rows: List[Tuple[int, int, int]] = []
        batch = 5_000

        def flush_ways():
            if way_rows:
                cur.executemany(
                    "INSERT INTO ways(id, name, highway_type, oneway, speed_limit, bridge)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    way_rows,
                )
                way_rows.clear()
            if way_node_rows:
                cur.executemany(
                    "INSERT INTO way_nodes(way_id, idx, node_id) VALUES (?, ?, ?)",
                    way_node_rows,
                )
                way_node_rows.clear()
            conn.commit()

        class WayCollector(osmium.SimpleHandler):
            def way(self_inner, w):  # type: ignore[override]
                tags = {tag.k: tag.v for tag in w.tags}
                highway = tags.get("highway", "")
                if highway not in PBFRoadHandler.HIGHWAY_TYPES:
                    return

                name = tags.get("name", "")
                oneway = tags.get("oneway", "no") in ("yes", "true", "1")
                speed_limit = PBFRoadHandler._parse_speed_limit(tags.get("maxspeed", ""))
                bridge = tags.get("bridge", "no") not in ("no", "")

                way_rows.append(
                    (
                        int(w.id),
                        name,
                        highway,
                        int(oneway),
                        int(speed_limit),
                        int(bridge),
                    )
                )
                for idx, n in enumerate(w.nodes):
                    nid = int(n.ref)
                    way_node_rows.append((int(w.id), idx, nid))
                    needed_nodes.add(nid)

                if len(way_rows) >= batch or len(way_node_rows) >= batch * 4:
                    flush_ways()

        WayCollector().apply_file(str(self.pbf_path), locations=False)
        flush_ways()

        node_rows: List[Tuple[int, float, float]] = []

        def flush_nodes():
            if node_rows:
                cur.executemany(
                    "INSERT INTO nodes(id, lat, lon) VALUES (?, ?, ?)",
                    node_rows,
                )
                node_rows.clear()
                conn.commit()

        class NodeCollector(osmium.SimpleHandler):
            def node(self_inner, n):  # type: ignore[override]
                nid = int(n.id)
                if nid not in needed_nodes:
                    return
                node_rows.append((nid, float(n.location.lat), float(n.location.lon)))
                if len(node_rows) >= batch * 2:
                    flush_nodes()

        NodeCollector().apply_file(str(self.pbf_path), locations=True)
        flush_nodes()

        try:
            cur.execute(
                """
                CREATE VIRTUAL TABLE node_index USING rtree(
                    id,
                    min_lat, max_lat,
                    min_lon, max_lon
                );
                """
            )
            reader = conn.cursor()
            cur.executemany(
                "INSERT INTO node_index(id, min_lat, max_lat, min_lon, max_lon) VALUES (?, ?, ?, ?, ?)",
                ((nid, lat, lat, lon, lon) for nid, lat, lon in reader.execute("SELECT id, lat, lon FROM nodes")),
            )
        except sqlite3.OperationalError:
            pass

        conn.commit()
        conn.close()

        # Optionally emit a pickle for small extracts to preserve compatibility
        if len(needed_nodes) <= self._max_pickle_nodes:
            try:
                network = self._load_from_sqlite(self._sqlite_cache_file)
                with open(self._cache_file, "wb") as f:
                    pickle.dump(network, f)
            except Exception as exc:  # pragma: no cover - defensive
                print(f"  Warning: Skipping pickle cache generation: {exc}")
        else:
            print(
                "  Skipping pickle cache: extract too large for in-memory conversion"
            )

    def _load_from_sqlite(self, path: Path) -> RoadNetwork:
        """Load the road network from a SQLite cache file."""
        conn = sqlite3.connect(path)
        try:
            cur = conn.cursor()
            network = RoadNetwork()

            for nid, lat, lon in cur.execute("SELECT id, lat, lon FROM nodes"):
                network.nodes[int(nid)] = Node(id=int(nid), lat=float(lat), lon=float(lon))

            for row in cur.execute(
                "SELECT id, name, highway_type, oneway, speed_limit, bridge FROM ways"
            ):
                wid, name, highway_type, oneway, speed_limit, bridge = row
                network.ways[int(wid)] = Way(
                    id=int(wid),
                    nodes=[],
                    name=name or "",
                    highway_type=highway_type or "",
                    oneway=bool(oneway),
                    speed_limit=int(speed_limit or 0),
                    bridge=bool(bridge),
                )

            for wid, idx, nid in cur.execute(
                "SELECT way_id, idx, node_id FROM way_nodes ORDER BY way_id, idx"
            ):
                if int(wid) in network.ways:
                    network.ways[int(wid)].nodes.append(int(nid))
                    if int(nid) not in network.node_to_ways:
                        network.node_to_ways[int(nid)] = []
                    network.node_to_ways[int(nid)].append(int(wid))

            for nid, way_ids in network.node_to_ways.items():
                if len(way_ids) >= 2 and nid in network.nodes:
                    node = network.nodes[nid]
                    network.junctions[nid] = Junction(
                        node_id=nid,
                        lat=node.lat,
                        lon=node.lon,
                        connected_ways=way_ids,
                        is_t_junction=self._is_t_junction(nid, way_ids, network),
                    )

            return network
        finally:
            conn.close()

    def _load_region_from_sqlite(
        self, path: Path, lat: float, lon: float, radius_m: float
    ) -> RoadNetwork:
        """Load only the roads in a bounding circle from the SQLite cache."""
        conn = sqlite3.connect(path)
        try:
            cur = conn.cursor()
            lat_delta = radius_m / 111000
            lon_delta = radius_m / (111000 * math.cos(math.radians(lat)))
            min_lat, max_lat = lat - lat_delta, lat + lat_delta
            min_lon, max_lon = lon - lon_delta, lon + lon_delta

            way_ids: List[int] = [
                int(row[0])
                for row in cur.execute(
                    """
                    SELECT DISTINCT w.id
                    FROM ways w
                    JOIN way_nodes wn ON wn.way_id = w.id
                    JOIN nodes n ON n.id = wn.node_id
                    WHERE n.lat BETWEEN ? AND ? AND n.lon BETWEEN ? AND ?
                    """,
                    (min_lat, max_lat, min_lon, max_lon),
                )
            ]

            network = RoadNetwork()
            if not way_ids:
                return network

            def _chunk(seq, size=800):
                for i in range(0, len(seq), size):
                    yield seq[i : i + size]

            for chunk in _chunk(way_ids):
                placeholders = ",".join(["?"] * len(chunk))
                for row in cur.execute(
                    f"SELECT id, name, highway_type, oneway, speed_limit, bridge FROM ways WHERE id IN ({placeholders})",
                    tuple(chunk),
                ):
                    wid, name, highway_type, oneway, speed_limit, bridge = row
                    network.ways[int(wid)] = Way(
                        id=int(wid),
                        nodes=[],
                        name=name or "",
                        highway_type=highway_type or "",
                        oneway=bool(oneway),
                        speed_limit=int(speed_limit or 0),
                        bridge=bool(bridge),
                    )

            for chunk in _chunk(way_ids):
                placeholders = ",".join(["?"] * len(chunk))
                for row in cur.execute(
                    f"""
                    SELECT wn.way_id, wn.idx, wn.node_id, n.lat, n.lon
                    FROM way_nodes wn
                    JOIN nodes n ON n.id = wn.node_id
                    WHERE wn.way_id IN ({placeholders})
                    ORDER BY wn.way_id, wn.idx
                    """,
                    tuple(chunk),
                ):
                    way_id, idx, node_id, node_lat, node_lon = row
                    if int(way_id) not in network.ways:
                        continue
                    network.ways[int(way_id)].nodes.append(int(node_id))
                    if int(node_id) not in network.nodes:
                        network.nodes[int(node_id)] = Node(
                            id=int(node_id),
                            lat=float(node_lat),
                            lon=float(node_lon),
                        )
                    if int(node_id) not in network.node_to_ways:
                        network.node_to_ways[int(node_id)] = []
                    network.node_to_ways[int(node_id)].append(int(way_id))

            for nid, way_ids_for_node in network.node_to_ways.items():
                if len(way_ids_for_node) >= 2 and nid in network.nodes:
                    node = network.nodes[nid]
                    network.junctions[nid] = Junction(
                        node_id=nid,
                        lat=node.lat,
                        lon=node.lon,
                        connected_ways=way_ids_for_node,
                        is_t_junction=self._is_t_junction(nid, way_ids_for_node, network),
                    )

            return network
        finally:
            conn.close()

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

        network: Optional[RoadNetwork] = None

        try:
            pbf_mtime = os.path.getmtime(self.pbf_path)
            cache_mtime = os.path.getmtime(self._sqlite_cache_file) if self._sqlite_cache_file.exists() else 0
            if (not self._sqlite_cache_file.exists()) or cache_mtime <= pbf_mtime:
                print("  Building SQLite cache (streaming)...")
                self._build_sqlite_cache_streaming()

            network = self._load_region_from_sqlite(
                self._sqlite_cache_file, lat, lon, radius_m
            )
        except Exception as exc:  # pragma: no cover - defensive
            print(f"  SQLite regional load failed: {exc}, falling back to full cache")

        if network is None:
            network = RoadNetwork()

        if not network.ways:
            # Fallback to full-network filtering if SQLite unavailable
            full_network = self._get_full_network()

            # Calculate bounds
            lat_delta = radius_m / 111000
            lon_delta = radius_m / (111000 * math.cos(math.radians(lat)))
            min_lat, max_lat = lat - lat_delta, lat + lat_delta
            min_lon, max_lon = lon - lon_delta, lon + lon_delta

            # Filter to region
            for nid, node in full_network.nodes.items():
                if min_lat <= node.lat <= max_lat and min_lon <= node.lon <= max_lon:
                    network.nodes[nid] = node

            for wid, way in full_network.ways.items():
                if any(nid in network.nodes for nid in way.nodes):
                    network.ways[wid] = way
                    for nid in way.nodes:
                        if nid not in network.nodes and nid in full_network.nodes:
                            network.nodes[nid] = full_network.nodes[nid]

            for wid, way in network.ways.items():
                for nid in way.nodes:
                    if nid not in network.node_to_ways:
                        network.node_to_ways[nid] = []
                    network.node_to_ways[nid].append(wid)

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

"""SQLite-based road network cache for scalable map storage.

Provides efficient storage and spatial queries for large map regions without
loading everything into memory. Uses streaming PBF import and R-tree indices.

Usage:
    cache = SQLiteMapCache("region.roads.db")
    cache.import_from_pbf(Path("region.osm.pbf"))  # One-time import
    network = cache.load_region(51.46, -2.46, 5000)  # Query by bbox
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

try:
    import osmium
    OSMIUM_AVAILABLE = True
except ImportError:
    OSMIUM_AVAILABLE = False

from .geometry import bearing


# Schema version - increment when schema changes
SCHEMA_VERSION = 1


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
    nodes: List[int]
    name: str = ""
    highway_type: str = ""
    oneway: bool = False
    speed_limit: int = 0
    bridge: bool = False
    tunnel: bool = False
    surface: str = ""
    ford: bool = False
    traffic_calming: str = ""
    width: float = 0.0
    narrow: bool = False


@dataclass
class Junction:
    """A junction where roads meet."""
    node_id: int
    lat: float
    lon: float
    connected_ways: List[int]
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
    barrier_type: str


@dataclass
class RoadNetwork:
    """Road network loaded from SQLite cache."""
    nodes: Dict[int, Node]
    ways: Dict[int, Way]
    junctions: Dict[int, Junction]
    node_to_ways: Dict[int, List[int]]
    railway_crossings: Dict[int, RailwayCrossing]
    barriers: Dict[int, Barrier]

    def __init__(self):
        self.nodes = {}
        self.ways = {}
        self.junctions = {}
        self.node_to_ways = {}
        self.railway_crossings = {}
        self.barriers = {}

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


class SQLiteMapCache:
    """SQLite-based map cache with R-tree spatial indexing."""

    HIGHWAY_TYPES = {
        "motorway", "motorway_link",
        "trunk", "trunk_link",
        "primary", "primary_link",
        "secondary", "secondary_link",
        "tertiary", "tertiary_link",
        "unclassified", "residential",
        "living_street", "service",
    }

    def __init__(self, db_path: Path):
        """Open or create SQLite cache database."""
        self.db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None
        self._ensure_schema()

    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection (lazy init)."""
        if self._conn is None:
            # check_same_thread=False allows connection to be used across threads
            # This is safe with WAL mode for concurrent reads
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            # Enable WAL mode for better concurrent access and crash safety
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            # Performance optimizations for large read-heavy databases
            self._conn.execute("PRAGMA cache_size=-100000")  # 100MB page cache
            self._conn.execute("PRAGMA mmap_size=1073741824")  # 1GB memory-mapped I/O
            self._conn.execute("PRAGMA temp_store=MEMORY")  # Temp tables in RAM
        return self._conn

    def _ensure_schema(self) -> None:
        """Create database schema if needed."""
        conn = self._get_conn()

        # Check schema version
        try:
            version = conn.execute(
                "SELECT version FROM metadata WHERE key='schema_version'"
            ).fetchone()
            if version and version[0] == SCHEMA_VERSION:
                return  # Schema is current
        except sqlite3.OperationalError:
            pass  # Table doesn't exist yet

        # Create tables
        conn.executescript("""
            -- Metadata
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                version INTEGER
            );

            -- Nodes with coordinates
            CREATE TABLE IF NOT EXISTS nodes (
                id INTEGER PRIMARY KEY,
                lat REAL NOT NULL,
                lon REAL NOT NULL
            );

            -- Ways (roads)
            CREATE TABLE IF NOT EXISTS ways (
                id INTEGER PRIMARY KEY,
                name TEXT,
                highway_type TEXT,
                oneway INTEGER DEFAULT 0,
                speed_limit INTEGER DEFAULT 0,
                bridge INTEGER DEFAULT 0,
                tunnel INTEGER DEFAULT 0,
                surface TEXT DEFAULT '',
                ford INTEGER DEFAULT 0,
                traffic_calming TEXT DEFAULT '',
                width REAL DEFAULT 0.0,
                narrow INTEGER DEFAULT 0
            );

            -- Way-node relationships (ordered)
            CREATE TABLE IF NOT EXISTS way_nodes (
                way_id INTEGER,
                idx INTEGER,
                node_id INTEGER,
                PRIMARY KEY (way_id, idx)
            );

            -- Railway level crossings
            CREATE TABLE IF NOT EXISTS railway_crossings (
                node_id INTEGER PRIMARY KEY,
                lat REAL NOT NULL,
                lon REAL NOT NULL
            );

            -- Barriers (cattle grids, gates)
            CREATE TABLE IF NOT EXISTS barriers (
                node_id INTEGER PRIMARY KEY,
                lat REAL NOT NULL,
                lon REAL NOT NULL,
                barrier_type TEXT NOT NULL
            );

            -- Junctions (precomputed)
            CREATE TABLE IF NOT EXISTS junctions (
                node_id INTEGER PRIMARY KEY,
                lat REAL NOT NULL,
                lon REAL NOT NULL,
                is_t_junction INTEGER DEFAULT 0
            );

            -- Junction-way relationships
            CREATE TABLE IF NOT EXISTS junction_ways (
                junction_id INTEGER,
                way_id INTEGER,
                PRIMARY KEY (junction_id, way_id)
            );

            -- R-tree spatial index for nodes
            CREATE VIRTUAL TABLE IF NOT EXISTS node_rtree USING rtree(
                id,
                min_lat, max_lat,
                min_lon, max_lon
            );

            -- R-tree for railway crossings
            CREATE VIRTUAL TABLE IF NOT EXISTS railway_rtree USING rtree(
                id,
                min_lat, max_lat,
                min_lon, max_lon
            );

            -- R-tree for barriers
            CREATE VIRTUAL TABLE IF NOT EXISTS barrier_rtree USING rtree(
                id,
                min_lat, max_lat,
                min_lon, max_lon
            );

            -- Indices for faster lookups
            CREATE INDEX IF NOT EXISTS idx_way_nodes_node ON way_nodes(node_id);
            CREATE INDEX IF NOT EXISTS idx_way_nodes_way ON way_nodes(way_id);
        """)

        # Set schema version
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, version) VALUES ('schema_version', ?)",
            (SCHEMA_VERSION,)
        )
        conn.commit()

    def get_bounds(self) -> Optional[Tuple[float, float, float, float]]:
        """Get bounding box of all data (min_lat, min_lon, max_lat, max_lon)."""
        conn = self._get_conn()
        row = conn.execute("""
            SELECT MIN(lat), MIN(lon), MAX(lat), MAX(lon) FROM nodes
        """).fetchone()
        if row and row[0] is not None:
            return (row[0], row[1], row[2], row[3])
        return None

    def import_from_pbf(self, pbf_path: Path, progress_callback=None) -> None:
        """Import road network from OSM PBF file using streaming.

        Uses two-pass streaming to minimize memory usage:
        1. First pass: Extract ways, collect needed node IDs
        2. Second pass: Extract only needed nodes

        Args:
            pbf_path: Path to OSM PBF file
            progress_callback: Optional callback(stage, current, total)
        """
        if not OSMIUM_AVAILABLE:
            raise ImportError("osmium not available. Install with: pip install osmium")

        conn = self._get_conn()

        # Clear existing data
        conn.executescript("""
            DELETE FROM nodes;
            DELETE FROM ways;
            DELETE FROM way_nodes;
            DELETE FROM railway_crossings;
            DELETE FROM barriers;
            DELETE FROM junctions;
            DELETE FROM junction_ways;
            DELETE FROM node_rtree;
            DELETE FROM railway_rtree;
            DELETE FROM barrier_rtree;
        """)
        conn.commit()

        print(f"  Pass 1: Extracting ways...", flush=True)

        # Pass 1: Extract ways
        way_handler = _WayExtractor(self.HIGHWAY_TYPES)
        way_handler.apply_file(str(pbf_path))

        ways = way_handler.ways
        needed_nodes = way_handler.needed_nodes
        print(f"    Found {len(ways):,} roads, need {len(needed_nodes):,} nodes", flush=True)

        # Insert ways
        print(f"  Inserting ways...", flush=True)
        conn.executemany("""
            INSERT INTO ways (id, name, highway_type, oneway, speed_limit,
                             bridge, tunnel, surface, ford, traffic_calming,
                             width, narrow)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            (w.id, w.name, w.highway_type, w.oneway, w.speed_limit,
             w.bridge, w.tunnel, w.surface, w.ford, w.traffic_calming,
             w.width, w.narrow)
            for w in ways.values()
        ])

        # Insert way-node relationships
        print(f"  Inserting way-node relationships...", flush=True)
        way_node_rows = []
        for way in ways.values():
            for idx, node_id in enumerate(way.nodes):
                way_node_rows.append((way.id, idx, node_id))
        conn.executemany(
            "INSERT INTO way_nodes (way_id, idx, node_id) VALUES (?, ?, ?)",
            way_node_rows
        )
        conn.commit()
        del way_node_rows

        print(f"  Pass 2: Extracting nodes...", flush=True)

        # Pass 2: Extract needed nodes
        node_handler = _NodeExtractor(needed_nodes)
        node_handler.apply_file(str(pbf_path), locations=True)

        nodes = node_handler.nodes
        railway_crossings = node_handler.railway_crossings
        barriers = node_handler.barriers
        print(f"    Found {len(nodes):,} nodes, {len(railway_crossings):,} crossings, {len(barriers):,} barriers", flush=True)

        # Insert nodes and spatial index
        print(f"  Inserting nodes with spatial index...", flush=True)
        conn.executemany(
            "INSERT INTO nodes (id, lat, lon) VALUES (?, ?, ?)",
            [(n.id, n.lat, n.lon) for n in nodes.values()]
        )
        conn.executemany(
            "INSERT INTO node_rtree (id, min_lat, max_lat, min_lon, max_lon) VALUES (?, ?, ?, ?, ?)",
            [(n.id, n.lat, n.lat, n.lon, n.lon) for n in nodes.values()]
        )

        # Insert railway crossings
        if railway_crossings:
            conn.executemany(
                "INSERT INTO railway_crossings (node_id, lat, lon) VALUES (?, ?, ?)",
                [(r.node_id, r.lat, r.lon) for r in railway_crossings.values()]
            )
            conn.executemany(
                "INSERT INTO railway_rtree (id, min_lat, max_lat, min_lon, max_lon) VALUES (?, ?, ?, ?, ?)",
                [(r.node_id, r.lat, r.lat, r.lon, r.lon) for r in railway_crossings.values()]
            )

        # Insert barriers
        if barriers:
            conn.executemany(
                "INSERT INTO barriers (node_id, lat, lon, barrier_type) VALUES (?, ?, ?, ?)",
                [(b.node_id, b.lat, b.lon, b.barrier_type) for b in barriers.values()]
            )
            conn.executemany(
                "INSERT INTO barrier_rtree (id, min_lat, max_lat, min_lon, max_lon) VALUES (?, ?, ?, ?, ?)",
                [(b.node_id, b.lat, b.lat, b.lon, b.lon) for b in barriers.values()]
            )

        conn.commit()

        # Build junctions
        print(f"  Computing junctions...", flush=True)
        self._build_junctions(nodes, ways)

        # Store import metadata
        bounds = self.get_bounds()
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, version) VALUES ('source_file', ?)",
            (str(pbf_path),)
        )
        conn.commit()

        # Optimize query planner with statistics
        print(f"  Running ANALYZE for query optimization...", flush=True)
        conn.execute("ANALYZE")
        conn.commit()

        print(f"  Import complete!", flush=True)

    def _build_junctions(self, nodes: Dict[int, Node], ways: Dict[int, Way]) -> None:
        """Build junction table from way data."""
        conn = self._get_conn()

        # Count way references per node
        node_way_count: Dict[int, List[int]] = {}
        for way in ways.values():
            for node_id in way.nodes:
                if node_id not in node_way_count:
                    node_way_count[node_id] = []
                node_way_count[node_id].append(way.id)

        # Junctions are nodes with 2+ ways
        junctions = []
        junction_ways = []
        for node_id, way_ids in node_way_count.items():
            # De-duplicate way_ids (a node can appear multiple times in same way)
            unique_way_ids = list(set(way_ids))
            if len(unique_way_ids) >= 2 and node_id in nodes:
                node = nodes[node_id]
                is_t = self._is_t_junction(node_id, unique_way_ids, nodes, ways)
                junctions.append((node_id, node.lat, node.lon, is_t))
                for way_id in unique_way_ids:
                    junction_ways.append((node_id, way_id))

        conn.executemany(
            "INSERT INTO junctions (node_id, lat, lon, is_t_junction) VALUES (?, ?, ?, ?)",
            junctions
        )
        conn.executemany(
            "INSERT INTO junction_ways (junction_id, way_id) VALUES (?, ?)",
            junction_ways
        )
        conn.commit()
        print(f"    Found {len(junctions):,} junctions", flush=True)

    def _is_t_junction(
        self,
        node_id: int,
        way_ids: List[int],
        nodes: Dict[int, Node],
        ways: Dict[int, Way]
    ) -> bool:
        """Check if junction is a T-junction."""
        if len(way_ids) < 2:
            return False

        node = nodes[node_id]
        bearings = []

        for wid in way_ids:
            way = ways[wid]
            try:
                idx = way.nodes.index(node_id)
            except ValueError:
                continue

            if idx > 0 and way.nodes[idx - 1] in nodes:
                prev = nodes[way.nodes[idx - 1]]
                b = bearing(node.lat, node.lon, prev.lat, prev.lon)
                bearings.append(b)
            if idx < len(way.nodes) - 1 and way.nodes[idx + 1] in nodes:
                next_n = nodes[way.nodes[idx + 1]]
                b = bearing(node.lat, node.lon, next_n.lat, next_n.lon)
                bearings.append(b)

        if len(bearings) < 3:
            return False

        for i, b1 in enumerate(bearings):
            for j, b2 in enumerate(bearings):
                if i >= j:
                    continue
                diff = abs((b1 - b2 + 180) % 360 - 180)
                if 150 < diff < 210 or diff < 30:
                    for k, b3 in enumerate(bearings):
                        if k == i or k == j:
                            continue
                        diff1 = abs((b3 - b1 + 180) % 360 - 180)
                        if 60 < diff1 < 120:
                            return True
        return False

    def load_region(
        self,
        center_lat: float,
        center_lon: float,
        radius_m: float
    ) -> RoadNetwork:
        """Load road network within radius of center point.

        Uses R-tree spatial index for efficient bbox queries.

        Args:
            center_lat: Center latitude
            center_lon: Center longitude
            radius_m: Radius in meters

        Returns:
            RoadNetwork with nodes, ways, junctions in the region
        """
        import math

        conn = self._get_conn()

        # Calculate bounding box
        lat_delta = radius_m / 111000
        lon_delta = radius_m / (111000 * math.cos(math.radians(center_lat)))
        min_lat = center_lat - lat_delta
        max_lat = center_lat + lat_delta
        min_lon = center_lon - lon_delta
        max_lon = center_lon + lon_delta

        network = RoadNetwork()

        # Query nodes in bbox using R-tree
        node_rows = conn.execute("""
            SELECT n.id, n.lat, n.lon
            FROM nodes n
            INNER JOIN node_rtree r ON n.id = r.id
            WHERE r.min_lat >= ? AND r.max_lat <= ?
              AND r.min_lon >= ? AND r.max_lon <= ?
        """, (min_lat, max_lat, min_lon, max_lon)).fetchall()

        node_ids = set()
        for row in node_rows:
            network.nodes[row['id']] = Node(row['id'], row['lat'], row['lon'])
            node_ids.add(row['id'])

        if not node_ids:
            return network

        # Find ways that have nodes in our bbox (chunked to avoid SQLite variable limit)
        way_ids = set()
        node_list = list(node_ids)
        chunk_size = 500  # SQLite has ~999 variable limit
        for i in range(0, len(node_list), chunk_size):
            chunk = node_list[i:i + chunk_size]
            placeholders = ",".join("?" * len(chunk))
            rows = conn.execute(f"""
                SELECT DISTINCT way_id FROM way_nodes
                WHERE node_id IN ({placeholders})
            """, chunk).fetchall()
            way_ids.update(r['way_id'] for r in rows)

        way_ids = list(way_ids)
        if not way_ids:
            return network

        # Load ways (chunked)
        way_rows = []
        for i in range(0, len(way_ids), chunk_size):
            chunk = way_ids[i:i + chunk_size]
            placeholders = ",".join("?" * len(chunk))
            rows = conn.execute(f"""
                SELECT * FROM ways WHERE id IN ({placeholders})
            """, chunk).fetchall()
            way_rows.extend(rows)

        # Batch load all way_nodes at once (avoid N+1 queries)
        way_node_map: Dict[int, List[Tuple[int, int]]] = {}  # way_id -> [(idx, node_id)]
        for i in range(0, len(way_ids), chunk_size):
            chunk = way_ids[i:i + chunk_size]
            placeholders = ",".join("?" * len(chunk))
            wn_rows = conn.execute(f"""
                SELECT way_id, idx, node_id FROM way_nodes
                WHERE way_id IN ({placeholders})
            """, chunk).fetchall()
            for r in wn_rows:
                if r['way_id'] not in way_node_map:
                    way_node_map[r['way_id']] = []
                way_node_map[r['way_id']].append((r['idx'], r['node_id']))

        # Sort each way's nodes by idx and extract node_ids
        for way_id in way_node_map:
            way_node_map[way_id].sort(key=lambda x: x[0])

        for row in way_rows:
            node_list = [n[1] for n in way_node_map.get(row['id'], [])]

            network.ways[row['id']] = Way(
                id=row['id'],
                nodes=node_list,
                name=row['name'] or "",
                highway_type=row['highway_type'] or "",
                oneway=bool(row['oneway']),
                speed_limit=row['speed_limit'] or 0,
                bridge=bool(row['bridge']),
                tunnel=bool(row['tunnel']),
                surface=row['surface'] or "",
                ford=bool(row['ford']),
                traffic_calming=row['traffic_calming'] or "",
                width=row['width'] or 0.0,
                narrow=bool(row['narrow']),
            )

            # Load any nodes we don't have yet (way extends outside bbox)
            for nid in node_list:
                if nid not in network.nodes:
                    n_row = conn.execute(
                        "SELECT id, lat, lon FROM nodes WHERE id = ?", (nid,)
                    ).fetchone()
                    if n_row:
                        network.nodes[n_row['id']] = Node(n_row['id'], n_row['lat'], n_row['lon'])

        # Build node_to_ways index
        for way in network.ways.values():
            for nid in way.nodes:
                if nid not in network.node_to_ways:
                    network.node_to_ways[nid] = []
                network.node_to_ways[nid].append(way.id)

        # Load junctions in our node set (chunked)
        junction_node_ids = [nid for nid in network.node_to_ways if len(network.node_to_ways[nid]) >= 2]
        for i in range(0, len(junction_node_ids), chunk_size):
            chunk = junction_node_ids[i:i + chunk_size]
            placeholders = ",".join("?" * len(chunk))
            junc_rows = conn.execute(f"""
                SELECT node_id, lat, lon, is_t_junction FROM junctions
                WHERE node_id IN ({placeholders})
            """, chunk).fetchall()

            for row in junc_rows:
                # Get connected ways
                jw_rows = conn.execute("""
                    SELECT way_id FROM junction_ways WHERE junction_id = ?
                """, (row['node_id'],)).fetchall()
                way_list = [r['way_id'] for r in jw_rows]

                network.junctions[row['node_id']] = Junction(
                    node_id=row['node_id'],
                    lat=row['lat'],
                    lon=row['lon'],
                    connected_ways=way_list,
                    is_t_junction=bool(row['is_t_junction']),
                )

        # Load railway crossings using R-tree
        rail_rows = conn.execute("""
            SELECT rc.node_id, rc.lat, rc.lon
            FROM railway_crossings rc
            INNER JOIN railway_rtree r ON rc.node_id = r.id
            WHERE r.min_lat >= ? AND r.max_lat <= ?
              AND r.min_lon >= ? AND r.max_lon <= ?
        """, (min_lat, max_lat, min_lon, max_lon)).fetchall()

        for row in rail_rows:
            if row['node_id'] in network.node_to_ways:
                network.railway_crossings[row['node_id']] = RailwayCrossing(
                    node_id=row['node_id'],
                    lat=row['lat'],
                    lon=row['lon'],
                )

        # Load barriers using R-tree
        barrier_rows = conn.execute("""
            SELECT b.node_id, b.lat, b.lon, b.barrier_type
            FROM barriers b
            INNER JOIN barrier_rtree r ON b.node_id = r.id
            WHERE r.min_lat >= ? AND r.max_lat <= ?
              AND r.min_lon >= ? AND r.max_lon <= ?
        """, (min_lat, max_lat, min_lon, max_lon)).fetchall()

        for row in barrier_rows:
            if row['node_id'] in network.node_to_ways:
                network.barriers[row['node_id']] = Barrier(
                    node_id=row['node_id'],
                    lat=row['lat'],
                    lon=row['lon'],
                    barrier_type=row['barrier_type'],
                )

        return network

    def close(self) -> None:
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None


class _WayExtractor(osmium.SimpleHandler if OSMIUM_AVAILABLE else object):
    """First-pass handler: extract ways and collect needed node IDs."""

    def __init__(self, highway_types: Set[str]):
        if OSMIUM_AVAILABLE:
            super().__init__()
        self.highway_types = highway_types
        self.ways: Dict[int, Way] = {}
        self.needed_nodes: Set[int] = set()
        self._way_count = 0
        self._road_count = 0

    def way(self, w):
        self._way_count += 1
        if self._way_count % 500000 == 0:
            print(f"    Scanned {self._way_count:,} ways, found {self._road_count:,} roads...", flush=True)

        tags = {tag.k: tag.v for tag in w.tags}
        highway = tags.get("highway", "")

        if highway not in self.highway_types:
            return

        node_refs = [n.ref for n in w.nodes]

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
        self._road_count += 1

    def _parse_speed_limit(self, value: str) -> int:
        if not value:
            return 0
        try:
            if "mph" in value:
                return int(float(value.replace("mph", "").strip()) * 1.60934)
            return int(value)
        except ValueError:
            return 0

    def _parse_width(self, value: str) -> float:
        if not value:
            return 0.0
        try:
            value = value.lower().replace("m", "").strip()
            return float(value)
        except ValueError:
            return 0.0


class _NodeExtractor(osmium.SimpleHandler if OSMIUM_AVAILABLE else object):
    """Second-pass handler: extract only needed nodes."""

    def __init__(self, needed_nodes: Set[int]):
        if OSMIUM_AVAILABLE:
            super().__init__()
        self.needed_nodes = needed_nodes
        self.nodes: Dict[int, Node] = {}
        self.railway_crossings: Dict[int, RailwayCrossing] = {}
        self.barriers: Dict[int, Barrier] = {}
        self._node_count = 0
        self._found_count = 0

    def node(self, n):
        self._node_count += 1
        if self._node_count % 5000000 == 0:
            print(f"    Scanned {self._node_count:,} nodes, found {self._found_count:,} needed...", flush=True)

        if n.id not in self.needed_nodes:
            return

        self._found_count += 1

        self.nodes[n.id] = Node(
            id=n.id,
            lat=n.location.lat,
            lon=n.location.lon,
        )

        tags = {tag.k: tag.v for tag in n.tags}

        if tags.get("railway") == "level_crossing":
            self.railway_crossings[n.id] = RailwayCrossing(
                node_id=n.id,
                lat=n.location.lat,
                lon=n.location.lon,
            )

        barrier_type = tags.get("barrier", "")
        if barrier_type in ("cattle_grid", "gate"):
            self.barriers[n.id] = Barrier(
                node_id=n.id,
                lat=n.location.lat,
                lon=n.location.lon,
                barrier_type=barrier_type,
            )

#!/usr/bin/env python3
"""Generate road network SQLite cache from OSM PBF file.

Usage:
    python3 generate_cache.py                    # Process all .osm.pbf in current dir
    python3 generate_cache.py region.osm.pbf    # Process specific file
    python3 generate_cache.py /path/to/maps/    # Process all .osm.pbf in directory

The generated .roads.db files are SQLite databases with R-tree spatial
indices for efficient viewport queries. They can be transferred to target
machines without needing the original PBF file or osmium library.

Benefits over pickle (.roads.pkl):
- Streaming import: minimal RAM usage during generation
- Spatial queries: load only data for current viewport
- Smaller files: typically 50-70% of pickle size
- Concurrent access: WAL journaling for safe reads during writes
"""

import sys
import time
from pathlib import Path

# Add parent directory to path to import copepilot modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.copepilot.sqlite_cache import SQLiteMapCache


def generate_cache(pbf_path: Path) -> None:
    """Generate SQLite cache for a PBF file."""
    print(f"\n{'='*60}")
    print(f"Processing: {pbf_path.name}")
    print(f"{'='*60}")

    # Create SQLite database path
    db_path = Path(str(pbf_path).replace(".osm.pbf", ".roads.db"))

    start = time.time()
    cache = SQLiteMapCache(db_path)
    cache.import_from_pbf(pbf_path)
    elapsed = time.time() - start

    # Get stats
    bounds = cache.get_bounds()
    conn = cache._get_conn()
    way_count = conn.execute("SELECT COUNT(*) FROM ways").fetchone()[0]
    junction_count = conn.execute("SELECT COUNT(*) FROM junctions").fetchone()[0]
    rail_count = conn.execute("SELECT COUNT(*) FROM railway_crossings").fetchone()[0]
    barrier_count = conn.execute("SELECT COUNT(*) FROM barriers").fetchone()[0]

    print(f"\nCompleted in {elapsed:.1f}s")
    print(f"  Roads: {way_count:,}")
    print(f"  Junctions: {junction_count:,}")
    print(f"  Railway crossings: {rail_count:,}")
    print(f"  Barriers: {barrier_count:,}")

    if bounds:
        print(f"  Bounds: {bounds[0]:.2f},{bounds[1]:.2f} to {bounds[2]:.2f},{bounds[3]:.2f}")

    size_mb = db_path.stat().st_size / 1024 / 1024
    print(f"  Cache: {db_path.name} ({size_mb:.1f} MB)")

    cache.close()


def main():
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
    else:
        path = Path(".")

    if path.is_file() and path.suffix == ".pbf":
        # Single file
        generate_cache(path)
    elif path.is_dir():
        # Directory - find all PBF files
        pbf_files = list(path.glob("*.osm.pbf"))
        if not pbf_files:
            print(f"No .osm.pbf files found in {path}")
            sys.exit(1)

        print(f"Found {len(pbf_files)} PBF file(s)")
        for pbf in sorted(pbf_files):
            generate_cache(pbf)
    else:
        print(f"Error: {path} is not a PBF file or directory")
        sys.exit(1)

    print("\nDone! Transfer .roads.db files to target machine.")


if __name__ == "__main__":
    main()

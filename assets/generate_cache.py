#!/usr/bin/env python3
"""Generate road network pickle cache from OSM PBF file.

Usage:
    python3 generate_cache.py                    # Process all .osm.pbf in current dir
    python3 generate_cache.py region.osm.pbf    # Process specific file
    python3 generate_cache.py /path/to/maps/    # Process all .osm.pbf in directory

The generated .roads.pkl can be transferred to target machines without
needing the original PBF file or osmium library.
"""

import sys
import time
from pathlib import Path

# Add parent directory to path to import copepilot modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.copepilot.map_loader import MapLoader


def generate_cache(pbf_path: Path) -> None:
    """Generate pickle cache for a PBF file."""
    print(f"\n{'='*60}")
    print(f"Processing: {pbf_path.name}")
    print(f"{'='*60}")

    start = time.time()
    loader = MapLoader(pbf_path)
    network = loader._get_full_network()
    elapsed = time.time() - start

    print(f"\nCompleted in {elapsed:.1f}s")
    print(f"  Roads: {len(network.ways):,}")
    print(f"  Junctions: {len(network.junctions):,}")
    print(f"  Railway crossings: {len(network.railway_crossings):,}")
    print(f"  Barriers: {len(network.barriers):,}")

    if loader._pkl_file:
        size_mb = loader._pkl_file.stat().st_size / 1024 / 1024
        print(f"  Cache: {loader._pkl_file.name} ({size_mb:.1f} MB)")


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

    print("\nDone! Transfer .roads.pkl files to target machine.")


if __name__ == "__main__":
    main()

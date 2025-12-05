#!/usr/bin/env python3
"""Download and cache OSM county extracts from Geofabrik.

Usage:
    python3 split_tiles.py                          # List available UK regions
    python3 split_tiles.py gloucestershire          # Download and cache one region
    python3 split_tiles.py gloucestershire bristol  # Download multiple regions

Downloads from Geofabrik's pre-made county extracts (much faster than splitting).
See: https://download.geofabrik.de/europe/united-kingdom/england.html

The generated .roads.pkl files can be copied to target devices.
"""

import argparse
import subprocess
import sys
import urllib.request
from pathlib import Path

# Geofabrik UK regions (name -> path)
UK_REGIONS = {
    # England
    "bedfordshire": "europe/united-kingdom/england/bedfordshire",
    "berkshire": "europe/united-kingdom/england/berkshire",
    "bristol": "europe/united-kingdom/england/bristol",
    "buckinghamshire": "europe/united-kingdom/england/buckinghamshire",
    "cambridgeshire": "europe/united-kingdom/england/cambridgeshire",
    "cheshire": "europe/united-kingdom/england/cheshire",
    "cornwall": "europe/united-kingdom/england/cornwall",
    "cumbria": "europe/united-kingdom/england/cumbria",
    "derbyshire": "europe/united-kingdom/england/derbyshire",
    "devon": "europe/united-kingdom/england/devon",
    "dorset": "europe/united-kingdom/england/dorset",
    "durham": "europe/united-kingdom/england/durham",
    "east-sussex": "europe/united-kingdom/england/east-sussex",
    "east-yorkshire": "europe/united-kingdom/england/east-yorkshire-with-hull",
    "essex": "europe/united-kingdom/england/essex",
    "gloucestershire": "europe/united-kingdom/england/gloucestershire",
    "greater-london": "europe/united-kingdom/england/greater-london",
    "greater-manchester": "europe/united-kingdom/england/greater-manchester",
    "hampshire": "europe/united-kingdom/england/hampshire",
    "herefordshire": "europe/united-kingdom/england/herefordshire",
    "hertfordshire": "europe/united-kingdom/england/hertfordshire",
    "isle-of-wight": "europe/united-kingdom/england/isle-of-wight",
    "kent": "europe/united-kingdom/england/kent",
    "lancashire": "europe/united-kingdom/england/lancashire",
    "leicestershire": "europe/united-kingdom/england/leicestershire",
    "lincolnshire": "europe/united-kingdom/england/lincolnshire",
    "merseyside": "europe/united-kingdom/england/merseyside",
    "norfolk": "europe/united-kingdom/england/norfolk",
    "north-yorkshire": "europe/united-kingdom/england/north-yorkshire",
    "northamptonshire": "europe/united-kingdom/england/northamptonshire",
    "northumberland": "europe/united-kingdom/england/northumberland",
    "nottinghamshire": "europe/united-kingdom/england/nottinghamshire",
    "oxfordshire": "europe/united-kingdom/england/oxfordshire",
    "rutland": "europe/united-kingdom/england/rutland",
    "shropshire": "europe/united-kingdom/england/shropshire",
    "somerset": "europe/united-kingdom/england/somerset",
    "south-yorkshire": "europe/united-kingdom/england/south-yorkshire",
    "staffordshire": "europe/united-kingdom/england/staffordshire",
    "suffolk": "europe/united-kingdom/england/suffolk",
    "surrey": "europe/united-kingdom/england/surrey",
    "tyne-and-wear": "europe/united-kingdom/england/tyne-and-wear",
    "warwickshire": "europe/united-kingdom/england/warwickshire",
    "west-midlands": "europe/united-kingdom/england/west-midlands",
    "west-sussex": "europe/united-kingdom/england/west-sussex",
    "west-yorkshire": "europe/united-kingdom/england/west-yorkshire",
    "wiltshire": "europe/united-kingdom/england/wiltshire",
    "worcestershire": "europe/united-kingdom/england/worcestershire",
    # Scotland, Wales, NI
    "scotland": "europe/united-kingdom/scotland",
    "wales": "europe/united-kingdom/wales",
    "northern-ireland": "europe/united-kingdom/northern-ireland",
}

GEOFABRIK_BASE = "https://download.geofabrik.de"


def download_region(name: str, output_dir: Path) -> Path:
    """Download a region PBF from Geofabrik."""
    if name not in UK_REGIONS:
        print(f"Unknown region: {name}")
        print(f"Available: {', '.join(sorted(UK_REGIONS.keys()))}")
        sys.exit(1)

    path = UK_REGIONS[name]
    url = f"{GEOFABRIK_BASE}/{path}-latest.osm.pbf"
    output_path = output_dir / f"{name}-latest.osm.pbf"

    if output_path.exists():
        print(f"  {name}: already downloaded")
        return output_path

    print(f"  Downloading {name}...")
    try:
        urllib.request.urlretrieve(url, output_path)
        size_mb = output_path.stat().st_size / 1024 / 1024
        print(f"    Downloaded {size_mb:.1f} MB")
        return output_path
    except Exception as e:
        print(f"    Error: {e}")
        return None


def generate_pickle(pbf_path: Path) -> Path:
    """Generate pickle cache for a PBF file."""
    pkl_path = pbf_path.with_suffix(".roads.pkl")
    if pkl_path.exists():
        print(f"  {pbf_path.stem}: cache exists")
        return pkl_path

    print(f"  Generating cache for {pbf_path.name}...")

    # Import here to avoid circular deps
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from src.copepilot.map_loader import MapLoader

    try:
        loader = MapLoader(pbf_path)
        network = loader._get_full_network()
        size_mb = pkl_path.stat().st_size / 1024 / 1024
        print(f"    Created {pkl_path.name} ({size_mb:.1f} MB)")
        print(f"    Roads: {len(network.ways):,}, Junctions: {len(network.junctions):,}")
        return pkl_path
    except Exception as e:
        print(f"    Error: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(
        description="Download and cache OSM county extracts from Geofabrik"
    )
    parser.add_argument(
        "regions", nargs="*",
        help="Region names to download (e.g., gloucestershire bristol)"
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List available regions"
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("."),
        help="Output directory (default: current)"
    )
    parser.add_argument(
        "--download-only", action="store_true",
        help="Download PBFs but don't generate caches"
    )
    args = parser.parse_args()

    if args.list or not args.regions:
        print("Available UK regions:")
        print("=" * 40)
        for name in sorted(UK_REGIONS.keys()):
            print(f"  {name}")
        print()
        print("Usage: python3 split_tiles.py gloucestershire somerset")
        return

    args.output_dir.mkdir(exist_ok=True)

    print(f"Downloading {len(args.regions)} region(s) to {args.output_dir}/\n")

    for region in args.regions:
        pbf_path = download_region(region.lower(), args.output_dir)
        if pbf_path and not args.download_only:
            generate_pickle(pbf_path)
        print()

    print("Done! Copy .roads.pkl files to target device.")
    print("Multiple county caches in one directory will be loaded automatically.")


if __name__ == "__main__":
    main()

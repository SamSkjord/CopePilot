#!/usr/bin/env python3
"""Split large OSM PBF files into geographic grid tiles.

Usage:
    python3 split_tiles.py great-britain-latest.osm.pbf --tile-size 0.5

This creates tiles like:
    tiles/tile_51.0_-2.5.osm.pbf
    tiles/tile_51.0_-2.0.osm.pbf
    ...

Each tile can then be pickled separately with generate_cache.py.

Requires: osmium-tool (brew install osmium-tool / apt install osmium-tool)
"""

import argparse
import subprocess
import sys
from pathlib import Path


def get_pbf_bounds(pbf_path: Path) -> tuple:
    """Get bounding box of PBF file using osmium."""
    result = subprocess.run(
        ["osmium", "fileinfo", "-e", "-g", "header.boxes", str(pbf_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # Try without extended info
        result = subprocess.run(
            ["osmium", "fileinfo", "-g", "header.boxes", str(pbf_path)],
            capture_output=True,
            text=True,
        )

    if result.returncode != 0 or not result.stdout.strip():
        print("Could not determine bounds. Using UK defaults.")
        # UK approximate bounds
        return (49.5, -8.0, 61.0, 2.0)  # min_lat, min_lon, max_lat, max_lon

    # Parse "(min_lon,min_lat,max_lon,max_lat)"
    box = result.stdout.strip().strip("()")
    parts = [float(x) for x in box.split(",")]
    min_lon, min_lat, max_lon, max_lat = parts
    return (min_lat, min_lon, max_lat, max_lon)


def generate_tiles(min_lat: float, min_lon: float, max_lat: float, max_lon: float,
                   tile_size: float) -> list:
    """Generate list of tile bounds."""
    tiles = []
    lat = min_lat
    while lat < max_lat:
        lon = min_lon
        while lon < max_lon:
            tile_bounds = (
                lat,
                lon,
                min(lat + tile_size, max_lat),
                min(lon + tile_size, max_lon),
            )
            tiles.append(tile_bounds)
            lon += tile_size
        lat += tile_size
    return tiles


def tile_name(lat: float, lon: float, tile_size: float) -> str:
    """Generate tile filename from coordinates."""
    # Use bottom-left corner, rounded to tile grid
    tile_lat = (lat // tile_size) * tile_size
    tile_lon = (lon // tile_size) * tile_size
    return f"tile_{tile_lat:.1f}_{tile_lon:.1f}"


def extract_tile(pbf_path: Path, output_dir: Path, bounds: tuple, tile_size: float) -> Path:
    """Extract a single tile from PBF."""
    min_lat, min_lon, max_lat, max_lon = bounds
    name = tile_name(min_lat, min_lon, tile_size)
    output_path = output_dir / f"{name}.osm.pbf"

    if output_path.exists():
        print(f"  Skipping {name} (already exists)")
        return output_path

    # osmium extract uses: left,bottom,right,top (lon,lat,lon,lat)
    bbox = f"{min_lon},{min_lat},{max_lon},{max_lat}"

    print(f"  Extracting {name}...")
    result = subprocess.run(
        ["osmium", "extract", "-b", bbox, "-o", str(output_path), str(pbf_path)],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(f"    Error: {result.stderr}")
        return None

    # Check if tile has any data
    size = output_path.stat().st_size
    if size < 100:  # Empty tile
        output_path.unlink()
        print(f"    Empty tile, removed")
        return None

    size_mb = size / 1024 / 1024
    print(f"    Created {size_mb:.1f} MB")
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Split PBF into geographic tiles")
    parser.add_argument("pbf", type=Path, help="Input PBF file")
    parser.add_argument("--tile-size", type=float, default=0.5,
                        help="Tile size in degrees (default: 0.5 = ~35km)")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="Output directory (default: tiles/)")
    parser.add_argument("--pickle", action="store_true",
                        help="Also generate pickle caches for each tile")
    args = parser.parse_args()

    if not args.pbf.exists():
        print(f"Error: {args.pbf} not found")
        sys.exit(1)

    # Check osmium is available
    result = subprocess.run(["osmium", "--version"], capture_output=True)
    if result.returncode != 0:
        print("Error: osmium-tool not found")
        print("Install with: brew install osmium-tool (macOS)")
        print("           or: apt install osmium-tool (Linux)")
        sys.exit(1)

    output_dir = args.output_dir or Path("tiles")
    output_dir.mkdir(exist_ok=True)

    print(f"Input: {args.pbf}")
    print(f"Tile size: {args.tile_size}° (~{args.tile_size * 111:.0f}km)")
    print(f"Output: {output_dir}/")

    # Get bounds
    print("\nDetecting bounds...")
    bounds = get_pbf_bounds(args.pbf)
    min_lat, min_lon, max_lat, max_lon = bounds
    print(f"  Lat: {min_lat:.2f} to {max_lat:.2f}")
    print(f"  Lon: {min_lon:.2f} to {max_lon:.2f}")

    # Generate tile list
    tiles = generate_tiles(min_lat, min_lon, max_lat, max_lon, args.tile_size)
    print(f"\nGenerating {len(tiles)} tiles...")

    # Extract each tile
    created = 0
    for tile_bounds in tiles:
        result = extract_tile(args.pbf, output_dir, tile_bounds, args.tile_size)
        if result:
            created += 1

    print(f"\nCreated {created} tiles in {output_dir}/")

    # Optionally generate pickle caches
    if args.pickle:
        print("\nGenerating pickle caches...")
        # Import here to avoid circular deps
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from src.copepilot.map_loader import MapLoader

        for pbf in sorted(output_dir.glob("*.osm.pbf")):
            pkl = pbf.with_suffix(".roads.pkl")
            if pkl.exists():
                print(f"  Skipping {pbf.name} (cache exists)")
                continue
            print(f"  Processing {pbf.name}...")
            try:
                loader = MapLoader(pbf)
                loader._get_full_network()
            except Exception as e:
                print(f"    Error: {e}")

    print("\nDone! Use MapLoader with tiles/ directory.")


if __name__ == "__main__":
    main()

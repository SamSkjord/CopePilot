# Road caching strategy for Raspberry Pi deployments

Real-time road assessment on a Raspberry Pi needs a cache format that is fast to query, compact on disk, and resilient to power loss. Pickle caches rebuilt from PBFs do not scale to country-level data and are fragile. The options below highlight suitable replacements. The default implementation now streams PBFs directly into a SQLite cache (and writes a legacy pickle only for small extracts) to keep memory bounded.

## SQLite/SpatiaLite (single-file database)
- **Why it fits**: Remains a single file that can live next to the PBF; spatial indices (R*-tree) support bounding-box reads fast enough for per-second lookups while driving.
- **Pros**: Portable; safe atomic writes with WAL; works offline; small dependency footprint (SpatiaLite package) and runs comfortably on Raspberry Pi when using pragma tuning (e.g., `synchronous=NORMAL`, `journal_mode=WAL`).
- **Cons**: Initial import from PBF can take minutes on Pi-class CPUs; needs spatial extension installed.
- **How to use**: Import roads once, build an R-tree on geometry centroids or bounding boxes, and query by viewport centered on GPS. Suitable as a drop-in replacement for the current `.roads.pkl` cache.

## PMTiles / MBTiles (vector tiles)
- **Why it fits**: Tile-based random access keeps RAM usage predictable and enables streaming from SD card without loading the entire graph.
- **Pros**: Pre-generated tiles can be shipped per country; constant-time tile lookup; compatible with offline map tooling; read-only format reduces corruption risk on abrupt power loss.
- **Cons**: Adds a tiling pipeline; attributes may need preservation to keep routing fidelity; stitching roads across tile boundaries adds logic.
- **How to use**: Precompute tiles (z/x/y) for target zooms, store locally, and fetch tiles intersecting the upcoming route window.

## GeoParquet + external R-tree index
- **Why it fits**: Columnar compression minimizes disk space; memory mapping enables partial reads on low-RAM devices.
- **Pros**: Good size/performance balance; interoperable with Python geo stack; stays fully offline once written.
- **Cons**: Needs a separate spatial index (disk-backed R-tree) to avoid full scans; write path heavier than SQLite.
- **How to use**: Store roads as Parquet with geometry encoded as WKB; build an on-disk R-tree keyed by bounding boxes; query by intersecting bounding boxes then slicing Parquet row groups.

## PostGIS (local-only service)
- **Why it fits**: Full-featured spatial database for complex filtering (e.g., speed limits, surface types) if you are okay running a service on the Pi.
- **Pros**: Robust indexing and functions; handles country-scale data; supports concurrent consumers.
- **Cons**: Heavier operational footprint and startup cost on SBC hardware; less ideal for fully embedded/offline single-process use.
- **How to use**: Load via `osm2pgsql` or `imposm`; query via bounding boxes around current GPS position; tune shared buffers and autovacuum for low memory.

## Practical recommendation
- For **single-device, offline, minimal-ops** setups: start with **SQLite + SpatiaLite**, using WAL and R-tree indices, and cache query windows in RAM for the last GPS fix.
- If you need **read-only distribution** with easy updates: package **PMTiles/MBTiles** for the region and load tiles intersecting the drive corridor entirely from local storage.
- Keep the PBF as the ground truth and rebuild caches when map updates arrive; favor write-once/read-many formats to reduce SD card wear.

## Large extract observations (Great Britain)
- Parsing the full-country Geofabrik extract `great-britain-140101.osm.pbf` (~532MB) with the previous "load-everything" osmium path exhausted available RAM and was killed. The streaming SQLite writer keeps memory bounded and can finish on larger hosts; on a Pi, pre-building the `.roads.sqlite` elsewhere remains recommended.
- For Pi-class hardware, pre-cut the PBF to the driving area (e.g., `osmium extract --bbox`) or ingest into SQLite/PostGIS directly to avoid any long-running imports on-device.

## Implementation plan (SQLite/SpatiaLite in place, future extensions)

1. **Pick format and layout**
   - Choose SQLite with SpatiaLite extension packaged as an optional dependency for the Pi image; keep the file alongside the PBF in `assets/`.
   - Define tables for `roads` (id, geometry, tags of interest), `junctions`, and optional `road_properties` to avoid wide tables.

2. **Write import command**
   - Add a CLI entry point (e.g., `python -m src.copepilot.cache import --map path.osm.pbf --out path.osm.sqlite`) that streams PBF roads via `osmium` and bulk-inserts rows in batches.
   - Normalize tags during import (directionality, surface, speed) and precompute bounding boxes/centroids to avoid recomputation at query time.

3. **Build spatial indices**
   - Create R-tree indices on road bounding boxes and junction points; run `ANALYZE` once to optimize query plans.
   - Enable WAL + `synchronous=NORMAL`, `cache_size` tuned for Pi RAM (e.g., 64–128MB) to keep writes safe but fast during import.

4. **Add query adapter**
   - Implement a `SqliteRoadCache` class mirroring the existing pickle cache interface (`load_roads(viewport)` style API) so the caller does not change.
   - Use prepared statements for viewport queries (`MBRIntersects` on the R-tree) and fetch only the columns needed for corner detection.

5. **Fallback/compat mode**
   - Keep pickle cache loading for now, but prefer SQLite when a `.osm.sqlite` file exists; add a CLI flag `--cache sqlite|pickle` for explicit control.
   - Log a one-time warning when falling back to pickle on large PBFs to steer users toward the new cache.

6. **Validation and benchmarks**
   - Add a micro-benchmark script comparing cold/warm query latency between pickle and SQLite on a Pi image (can be run via QEMU in CI if hardware is unavailable).
   - Validate geometry integrity by comparing counts and random spot-checks (e.g., 100 sampled ways) between the PBF parsing output and the SQLite rows.

7. **Docs and distribution**
   - Update README and help text to describe the new cache file naming and import command; note that tiles/Parquet remain future options but SQLite is the default offline path.
   - Provide SD-card size guidance (expected MB/GB per region) and a quickstart snippet for generating the cache on-device.

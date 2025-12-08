# CLAUDE.md

This file provides guidance for Claude Code when working on CopePilot.

## Project Overview

CopePilot is a rally pacenote system that calls out upcoming corners, bridges, junctions, and road hazards while driving. It uses GPS position and OpenStreetMap data to generate audio callouts like a rally co-driver.

## Quick Start

```bash
# Run simulation with visualization
python3 -m src.copepilot.main --simulate 51.462979,-2.459749,0 --visualize --speed 20

# Run without audio (print only)
python3 -m src.copepilot.main --simulate 51.46,-2.46,0 --no-audio
```

## Architecture

```
GPS/Simulator → MapLoader → PathProjector → CornerDetector → PacenoteGenerator → AudioPlayer
                    ↓
              RoadNetwork (SQLite cache with R-tree spatial index)
```

### Key Components

| File | Purpose |
|------|---------|
| `main.py` | Main loop, coordinates all components |
| `simulator.py` | GPS simulation (GPSSimulator, VBOSimulator) |
| `map_loader.py` | OSM PBF parsing with SQLite/pickle caching |
| `sqlite_cache.py` | SQLite-based map cache with R-tree index |
| `path_projector.py` | Projects path ahead from current position |
| `corners.py` | ASC algorithm for corner detection |
| `pacenotes.py` | Generates callout text with distances |
| `audio.py` | Plays Nicky Grist samples or TTS fallback |
| `visualizer.py` | Matplotlib-based map visualization |

## Data Flow

1. `GPSSimulator.read_position()` returns current lat/lon/heading
2. `MapLoader.load_around()` queries roads from SQLite cache using R-tree spatial index
3. `PathProjector.project_path()` traces path ahead, returns points + junctions + road features
4. `CornerDetector.detect_corners()` finds corners in path geometry
5. `PacenoteGenerator.generate()` creates callouts with distances and multi-callout support
6. `AudioPlayer.say()` queues audio for playback

## Important Files

- `assets/*.osm.pbf` - OSM map data (source)
- `assets/*.roads.db` - SQLite cache (auto-generated, preferred)
- `assets/*.roads.pkl` - Pickle cache (legacy, still supported)
- `assets/codriver_Janne Laahanen/` - Audio samples (MIT licence, see Credits)
- `src/copepilot/config.py` - Configuration constants

## Map Caching

The system uses SQLite for scalable map caching:

```bash
# Generate SQLite cache from PBF (streaming, low RAM)
python3 assets/generate_cache.py region.osm.pbf

# Download UK county extracts from Geofabrik
python3 assets/download_regions.py gloucestershire somerset
python3 assets/download_regions.py --all  # All UK regions
```

Benefits of SQLite over pickle:
- **Streaming import**: Minimal RAM usage (~100MB vs 20GB for UK)
- **Spatial queries**: R-tree index for fast viewport loading
- **Smaller files**: ~50-70% of pickle size
- **Multi-region**: Load overlapping counties without duplication

### Performance (Britain & Ireland, 6.5GB cache)

| Query Radius | Time | Roads |
|--------------|------|-------|
| 2km | 0.09s | 1,300 |
| 5km | 0.44s | 8,700 |
| 10km | 6.9s | 38,700 |

Optimizations applied:
- 100MB page cache + 1GB memory-mapped I/O
- ANALYZE run after import for query planning
- Batched way_node queries (avoids N+1 problem)

## Testing

```bash
# Test simulator starting position
python3 -c "
from src.copepilot.simulator import GPSSimulator
from src.copepilot.map_loader import MapLoader
from src.copepilot import config

loader = MapLoader(config.MAP_FILE)
network = loader.load_around(51.46, -2.46, 5000)
sim = GPSSimulator(51.46, -2.46, 0, speed_mps=20)
sim.connect()
sim.set_network(network)
pos = sim.read_position()
print(f'Start: {pos.lat:.6f}, {pos.lon:.6f}')
"

# Test audio playback
python3 -c "
from pathlib import Path
from src.copepilot.audio import AudioPlayer
import time

player = AudioPlayer()
player.start()
player.say('left four tightens')
time.sleep(2)
player.stop()
"
```

## Common Issues

### Simulator position jumps at startup
- Fixed by resetting `_last_update` in `set_network()` and capping dt to 2.0s

### Speed parameter not working
- dt cap was too aggressive (0.2s) - increased to 2.0s

### First audio callout cut off
- Added sox warmup and 0.1s delay after audio thread starts

### Overlapping callouts missed
- Pacenote generator merges notes within 50m of each other with "into"
- e.g., bridge at 80m + hairpin at 95m → "over bridge into hairpin left"
- Audio queue also drains all pending items to catch late arrivals

### PBF loading slow
- Road network is cached to SQLite database with R-tree spatial index
- Streaming import uses minimal RAM (vs 20GB+ for pickle with large regions)
- Use `generate_cache.py` to pre-generate caches before deployment

## Rally Terminology

| Term | Meaning |
|------|---------|
| hairpin | Very tight U-turn (~180°, severity 1) |
| square | Right-angle turn (~90°, tight radius) |
| two/three/four/five/six | Corner severity (lower = tighter) |
| flat | Very slight bend (severity 7) |
| tightens | Corner gets tighter through |
| opens | Corner opens up through |
| long | Corner spans > 50m |
| over bridge | Road crosses a bridge |
| tunnel | Road enters a tunnel |
| over rails | Railway level crossing ahead |
| water | Ford (water crossing) ahead |
| bump/bumps | Speed bump or traffic calming |
| onto gravel/tarmac | Surface change ahead |
| junction | T-junction ahead (road ends) |
| chicane left/right | S-bend starting left or right |
| into | Links two features called in quick succession |
| cattle grid | Cattle grid on road |
| gate | Gate across road |
| narrows | Road narrows ahead |

## Multi-Callout System

Features are called at multiple distances to give the driver advance warning:

| Feature Type | Callout Distances | Notes |
|--------------|-------------------|-------|
| Corners | 1000m, 500m, 300m, 200m, 100m | Variable - uses earliest clear bracket |
| Hazards (rails, water, bumps, tunnels, surface, cattle grids, gates, narrows) | 500m, 300m, 100m | Called at each bracket |
| Bridges, Junctions | 100m only | Single callout |

### Corner Callout Logic

- Corners at 200m+ brackets only called if no closer corner exists (clear run)
- 100m bracket always called - it's the final warning before the corner
- On twisty roads: corners called at 100m as each becomes the closest
- On clear runs: corners called earlier at 200-300m for advance warning

### Speed-Scaled Timing

At speeds above 20 m/s (45 mph), callout distances automatically extend to ensure minimum 5 seconds warning time before hazards.

Adjacent features within 50m are merged with "into" (e.g., "over rails into left four").

## Dependencies

- `osmium` - OSM PBF parsing
- `sox` - Audio effects and sample extraction
- `matplotlib` - Visualization (optional)
- `pyserial` - Real GPS reading (optional)

## Credits

**Audio Samples**: Janne Laahanen and RaceRoom (MIT Licence)
- Co-driver voice samples from [CrewChiefV4](https://gitlab.com/mr_belern/CrewChiefV4)
- Original recordings by Janne Laahanen for RaceRoom Racing Experience

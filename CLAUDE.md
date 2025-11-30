# CLAUDE.md

This file provides guidance for Claude Code when working on CopePilot.

## Project Overview

CopePilot is a rally pacenote system that calls out upcoming corners, bridges, and junctions while driving. It uses GPS position and OpenStreetMap data to generate audio callouts like a rally co-driver.

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
              RoadNetwork (cached in .pkl file)
```

### Key Components

| File | Purpose |
|------|---------|
| `main.py` | Main loop, coordinates all components |
| `simulator.py` | GPS simulation (GPSSimulator, VBOSimulator) |
| `map_loader.py` | OSM PBF parsing with pickle caching |
| `path_projector.py` | Projects path ahead from current position |
| `corners.py` | ASC algorithm for corner detection |
| `pacenotes.py` | Generates callout text with distances |
| `audio.py` | Plays Nicky Grist samples or TTS fallback |
| `visualizer.py` | Matplotlib-based map visualization |

## Data Flow

1. `GPSSimulator.read_position()` returns current lat/lon/heading
2. `MapLoader.load_around()` loads roads from cached pickle (or parses PBF first time)
3. `PathProjector.project_path()` traces path ahead, returns points + junctions + bridges
4. `CornerDetector.detect_corners()` finds corners in path geometry
5. `PacenoteGenerator.generate()` creates callouts with distances
6. `AudioPlayer.say()` queues audio for playback

## Important Files

- `assets/gloucestershire-251127.osm.pbf` - OSM map data
- `assets/gloucestershire-251127.osm.roads.pkl` - Cached road network (auto-generated)
- `assets/codriver_Janne Laahanen/` - Audio samples (MIT licence, see Credits)
- `src/copepilot/config.py` - Configuration constants

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
- Road network is now cached to pickle file after first extraction
- Cache rebuilds automatically if PBF is newer

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
| junction | T-junction ahead (road ends) |
| chicane left/right | S-bend starting left or right |
| into | Links two corners called in quick succession |

## Dependencies

- `osmium` - OSM PBF parsing
- `sox` - Audio effects and sample extraction
- `matplotlib` - Visualization (optional)
- `pyserial` - Real GPS reading (optional)

## Credits

**Audio Samples**: Janne Laahanen and RaceRoom (MIT Licence)
- Co-driver voice samples from [CrewChiefV4](https://gitlab.com/mr_belern/CrewChiefV4)
- Original recordings by Janne Laahanen for RaceRoom Racing Experience

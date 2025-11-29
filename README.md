<p align="center">
  <img src="logo.png" alt="CopePilot">
</p>

# CopePilot

Rally pacenote style driving assistance for your daily commute. Uses GPS and OpenStreetMap data to call out upcoming corners like a rally co-driver.

```
"three hundred... left four... one fifty... right three tightens..."
```

## How It Works

1. GPS provides current position and heading
2. Loads road network from local OSM PBF file around your location
3. Projects path ahead based on current heading (assumes straight-on at junctions)
4. Detects corners using ASC (Automated Segmentation based on Curvature) algorithm
5. Calls out corners with rally-style severity (1=hairpin to 6=flat)
6. Warns about T-junctions where you'll need to stop

## Hardware Requirements

- Raspberry Pi (3B+ or newer recommended)
- USB GPS module (any NMEA-compatible device)
- Speaker or audio output
- OSM PBF map file for your region

## Installation

### Raspberry Pi

```bash
# Install system dependencies
sudo apt install espeak-ng sox libsox-fmt-all python3-pip

# For better quality voice (optional)
sudo apt install libttspico-utils

# Install CopePilot
pip install -e .
```

### macOS

```bash
# Install sox for audio effects
brew install sox

# Install CopePilot
pip install -e .
```

## Usage

```bash
# Run with real GPS
copepilot --gps-port /dev/ttyACM0

# Simulate driving from a location (lat,lon,heading)
copepilot --simulate 51.46,-2.46,0 --no-audio

# Simulate with visualization window
copepilot --simulate 51.46,-2.46,0 --visualize --no-audio

# Simulate at different speed (m/s)
copepilot --simulate 52.0,-1.0,180 --speed 20

# Replay a VBO file from lap-timing-system
copepilot --vbo /path/to/Driver1.vbo --speed-multiplier 3

# Adjust lookahead distance
copepilot --simulate 51.5,-0.1,90 --lookahead 500

# Specify a different map file
copepilot --simulate 51.46,-2.46,0 --map /path/to/region.osm.pbf
```

## Configuration

Edit `src/copepilot/config.py` to adjust:

| Setting | Default | Description |
|---------|---------|-------------|
| `LOOKAHEAD_DISTANCE_M` | 1000 | How far ahead to analyze |
| `CORNER_MIN_RADIUS_M` | 300 | Maximum radius to detect as corner |
| `CORNER_MIN_ANGLE_DEG` | 10 | Minimum angle to call out |
| `HEADING_TOLERANCE_DEG` | 30 | Angle tolerance for "straight on" |

## Pacenote Scale

Based on corner radius:

| Call | Severity | Radius |
|------|----------|--------|
| hairpin | 1 | < 15m |
| two | 2 | 15-30m |
| three | 3 | 30-50m |
| four | 4 | 50-80m |
| five | 5 | 80-120m |
| six | 6 | 120-200m |
| kink | 7 | > 200m |

### Modifiers

- **tightens** - corner gets tighter after apex
- **opens** - corner opens up after apex
- **long** - corner spans more than 50m

## Example Output

```
CopePilot starting...
Loading roads near 51.4600, -2.4600...
Loaded 1240 roads, 156 junctions
  [200m] two hundred right five tightens long
  [400m] four hundred left six long
  [624m] left five tightens long
  [797m] right three opens long
```

## Corner Detection

Uses the ASC (Automated Segmentation based on Curvature) algorithm ported from lap-timing-system:

1. **Peak Detection** - places cuts at curvature peaks
2. **Redundancy Reduction** - merges close cuts
3. **Straight Filling** - adds cuts in long straight sections
4. **Sign Changes** - adds cuts at left/right transitions
5. **Final Filtering** - removes remaining close cuts

This produces more reliable corner detection than simple threshold-based methods, especially with sparse OSM road data.

## Getting Map Data

Download OSM PBF files from [Geofabrik](https://download.geofabrik.de/):

```bash
# UK regions (smaller files recommended)
wget https://download.geofabrik.de/europe/great-britain/england/gloucestershire-latest.osm.pbf

# Or full Britain (large file, slower to parse)
wget https://download.geofabrik.de/europe/britain-and-ireland-latest.osm.pbf
```

Place the PBF file in the `assets/` directory or specify with `--map`.

## License

MIT

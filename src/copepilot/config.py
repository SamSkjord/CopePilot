"""CopePilot configuration."""

from pathlib import Path

# Paths
PROJECT_ROOT = Path(__file__).parent.parent.parent
ASSETS_DIR = PROJECT_ROOT / "assets"
MAP_FILE = ASSETS_DIR / "gloucestershire-251127.osm.pbf"

# Lookahead and navigation
LOOKAHEAD_DISTANCE_M = 1000  # How far ahead to analyze (meters)
ROAD_FETCH_RADIUS_M = 2000   # Radius to cache roads around current position
REFETCH_DISTANCE_M = 500     # Refetch roads when moved this far from last fetch

# Corner detection (from lap-timing-system)
CORNER_MIN_RADIUS_M = 300.0   # Minimum radius to consider a corner (meters)
CORNER_MIN_ANGLE_DEG = 10.0   # Minimum total angle to consider a corner (degrees)

# Junction detection
JUNCTION_WARN_DISTANCE_M = 200  # Warn about T-junctions this far ahead
HEADING_TOLERANCE_DEG = 30.0    # Roads within this angle of heading are "straight on"

# Audio
TTS_VOICE = "en-gb"
TTS_SPEED = 175

# GPS
GPS_PORT = "/dev/ttyUSB0"
GPS_BAUDRATE = 9600
UPDATE_INTERVAL_S = 0.5  # Main loop update rate

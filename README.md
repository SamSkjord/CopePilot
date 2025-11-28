# CopePilot

Rally pacenote style driving assistance for your daily commute. Uses GPS and OpenStreetMap data to call out upcoming corners like a rally co-driver.

```
"three hundred... left four... one fifty... right three tightens..."
```

## Hardware Requirements

- Raspberry Pi (3B+ or newer recommended)
- USB GPS module (any NMEA-compatible device)
- Speaker or audio output

## Installation

```bash
# Install system dependencies
sudo apt install espeak python3-pip

# Install CopePilot
pip install -e .
```

## Usage

```bash
# Run with default GPS port
copepilot

# Specify GPS port
copepilot --gps-port /dev/ttyACM0

# Adjust lookahead distance
copepilot --lookahead 300
```

## Pacenote Scale

| Call | Severity | Approximate Angle |
|------|----------|-------------------|
| hairpin | 1 | >150° |
| two | 2 | 120-150° |
| three | 3 | 80-120° |
| four | 4 | 50-80° |
| five | 5 | 25-50° |
| six | 6 | 10-25° |

## License

MIT

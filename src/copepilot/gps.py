"""GPS interface for reading position and heading."""

import serial
from dataclasses import dataclass
from typing import Optional


@dataclass
class Position:
    lat: float
    lon: float
    heading: float  # degrees, 0 = north
    speed: float  # m/s


class GPSReader:
    """Reads NMEA data from a GPS module via serial."""

    def __init__(self, port: str = "/dev/ttyUSB0", baudrate: int = 9600):
        self.port = port
        self.baudrate = baudrate
        self._serial: Optional[serial.Serial] = None

    def connect(self) -> None:
        self._serial = serial.Serial(self.port, self.baudrate, timeout=1)

    def disconnect(self) -> None:
        if self._serial:
            self._serial.close()
            self._serial = None

    def read_position(self) -> Optional[Position]:
        """Read current position from GPS. Returns None if no fix."""
        if not self._serial:
            return None

        # Read NMEA sentences and parse
        line = self._serial.readline().decode("ascii", errors="ignore").strip()

        if line.startswith("$GPRMC") or line.startswith("$GNRMC"):
            return self._parse_rmc(line)
        return None

    def _parse_rmc(self, sentence: str) -> Optional[Position]:
        """Parse RMC sentence for position, speed, and heading."""
        parts = sentence.split(",")
        if len(parts) < 10 or parts[2] != "A":  # A = valid fix
            return None

        try:
            lat = self._parse_coord(parts[3], parts[4])
            lon = self._parse_coord(parts[5], parts[6])
            speed_knots = float(parts[7]) if parts[7] else 0.0
            heading = float(parts[8]) if parts[8] else 0.0

            return Position(
                lat=lat,
                lon=lon,
                heading=heading,
                speed=speed_knots * 0.514444,  # knots to m/s
            )
        except (ValueError, IndexError):
            return None

    def _parse_coord(self, value: str, direction: str) -> float:
        """Convert NMEA coordinate to decimal degrees."""
        if not value:
            return 0.0
        # NMEA format: DDDMM.MMMM
        degrees = float(value[:2] if len(value) < 10 else value[:3])
        minutes = float(value[2:] if len(value) < 10 else value[3:])
        result = degrees + minutes / 60
        if direction in ("S", "W"):
            result = -result
        return result

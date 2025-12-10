"""Simulation mode for testing without GPS hardware."""

import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

from .gps import Position
from .geometry import haversine_distance, bearing, point_along_bearing
from .map_loader import RoadNetwork
from .path_projector import PathProjector
from . import config


@dataclass
class SimulatedRoute:
    """A route to simulate driving along."""
    points: List[Tuple[float, float]]  # (lat, lon) waypoints
    speed_mps: float = 13.4  # ~30 mph / 48 km/h default


class GPSSimulator:
    """Simulates GPS readings for testing."""

    def __init__(
        self,
        start_lat: float,
        start_lon: float,
        start_heading: float,
        speed_mps: float = 13.4,  # ~30 mph
    ):
        self.current_lat = start_lat
        self.current_lon = start_lon
        self.current_heading = start_heading
        self.speed = speed_mps

        self._network: Optional[RoadNetwork] = None
        self._projector: Optional[PathProjector] = None
        self._route_points: List[Tuple[float, float]] = []
        self._route_index = 0
        self._last_update = time.time()

    def connect(self) -> None:
        """Initialize the simulator."""
        # Don't load map here - let main app do it to avoid double-loading
        # Route will be built lazily when network is provided
        print(f"Simulator ready at {self.current_lat:.4f}, {self.current_lon:.4f}, heading {self.current_heading:.0f}°")

    def set_network(self, network: RoadNetwork) -> None:
        """Set the road network (called by main app after loading)."""
        if self._network is None:  # Only build route once
            self._network = network
            self._projector = PathProjector(network)
            self._build_route()
            # Reset time so first read_position doesn't jump due to loading delay
            self._last_update = time.time()

    def disconnect(self) -> None:
        """Clean up."""
        pass

    def get_route_bounds(self) -> tuple:
        """Get bounds of the simulation route (min_lat, max_lat, min_lon, max_lon)."""
        if not self._route_points:
            return None
        lats = [p[0] for p in self._route_points]
        lons = [p[1] for p in self._route_points]
        return (min(lats), max(lats), min(lons), max(lons))

    def _build_route(self) -> None:
        """Build a route by projecting path from current position."""
        if not self._projector:
            return

        path = self._projector.project_path(
            self.current_lat,
            self.current_lon,
            self.current_heading,
            max_distance=5000,  # 5km route
        )

        if path and path.points:
            # Start route from actual starting position (user-specified)
            # then continue along the projected road path
            self._route_points = [(self.current_lat, self.current_lon)]
            self._route_points.extend((p.lat, p.lon) for p in path.points)
            self._route_index = 0
            print(f"Built simulation route with {len(self._route_points)} points")
        else:
            print("Warning: Could not build route, using straight-line simulation")

    def read_position(self) -> Optional[Position]:
        """Simulate reading GPS position."""
        now = time.time()
        dt = now - self._last_update
        self._last_update = now

        # Cap dt to prevent large jumps from unexpected delays (but allow normal intervals)
        # Max 2s covers normal operation while preventing startup jumps
        dt = min(dt, 2.0)

        if self._route_points and self._route_index < len(self._route_points) - 1:
            # Follow the pre-built route
            return self._follow_route(dt)
        else:
            # Simple straight-line simulation
            return self._straight_line(dt)

    def _follow_route(self, dt: float) -> Position:
        """Follow the pre-built route."""
        distance_to_travel = self.speed * dt

        while distance_to_travel > 0 and self._route_index < len(self._route_points) - 1:
            next_pt = self._route_points[self._route_index + 1]

            # Distance from current position to next waypoint
            dist_to_next = haversine_distance(
                self.current_lat, self.current_lon, next_pt[0], next_pt[1]
            )

            if distance_to_travel >= dist_to_next:
                # Move to next waypoint
                distance_to_travel -= dist_to_next
                self._route_index += 1
                self.current_lat, self.current_lon = next_pt
            else:
                # Interpolate from current position toward next waypoint
                fraction = distance_to_travel / dist_to_next if dist_to_next > 0 else 0
                self.current_lat = self.current_lat + fraction * (next_pt[0] - self.current_lat)
                self.current_lon = self.current_lon + fraction * (next_pt[1] - self.current_lon)
                distance_to_travel = 0

            # Update heading toward next waypoint
            if self._route_index < len(self._route_points) - 1:
                next_pt = self._route_points[self._route_index + 1]
                self.current_heading = bearing(
                    self.current_lat, self.current_lon,
                    next_pt[0], next_pt[1]
                )

        return Position(
            lat=self.current_lat,
            lon=self.current_lon,
            heading=self.current_heading,
            speed=self.speed,
        )

    def _straight_line(self, dt: float) -> Position:
        """Simple straight-line movement."""
        distance = self.speed * dt

        new_lat, new_lon = point_along_bearing(
            self.current_lat, self.current_lon,
            self.current_heading, distance
        )

        self.current_lat = new_lat
        self.current_lon = new_lon

        return Position(
            lat=self.current_lat,
            lon=self.current_lon,
            heading=self.current_heading,
            speed=self.speed,
        )


class VBOSimulator:
    """Simulate GPS from a VBO file (from lap-timing-system)."""

    def __init__(self, vbo_path: str, speed_multiplier: float = 1.0):
        self.vbo_path = vbo_path
        self.speed_multiplier = speed_multiplier
        self._points: List[Position] = []
        self._index = 0
        self._last_update = time.time()

    def connect(self) -> None:
        """Load and parse the VBO file."""
        self._points = self._parse_vbo()
        print(f"Loaded {len(self._points)} GPS points from VBO")

    def disconnect(self) -> None:
        pass

    def read_position(self) -> Optional[Position]:
        """Return next position from VBO data."""
        if not self._points or self._index >= len(self._points):
            return None

        now = time.time()
        dt = now - self._last_update

        # Advance based on time (VBO is typically 10Hz)
        points_to_advance = int(dt * 10 * self.speed_multiplier)
        if points_to_advance > 0:
            self._index = min(self._index + points_to_advance, len(self._points) - 1)
            self._last_update = now

        return self._points[self._index]

    def _parse_vbo(self) -> List[Position]:
        """Parse VBO file format."""
        points = []
        in_data = False

        with open(self.vbo_path, 'r') as f:
            for line in f:
                line = line.strip()

                if line == '[data]':
                    in_data = True
                    continue

                if not in_data or not line:
                    continue

                if line.startswith('['):
                    break

                parts = line.split()
                if len(parts) < 6:
                    continue

                try:
                    # VBO format: sats time lat lon speed heading height ...
                    # Coordinates are in NMEA minutes format
                    lat_min = float(parts[2])
                    lon_min = float(parts[3])
                    speed_kmh = float(parts[4])
                    heading = float(parts[5])

                    # Convert from minutes to degrees
                    lat = lat_min / 60.0
                    lon = lon_min / 60.0

                    points.append(Position(
                        lat=lat,
                        lon=lon,
                        heading=heading,
                        speed=speed_kmh / 3.6,  # Convert to m/s
                    ))
                except (ValueError, IndexError):
                    continue

        return points


class GPXSimulator:
    """Simulate GPS by following a GPX track file."""

    def __init__(self, gpx_path: str, speed_mps: float = 13.4):
        """
        Initialize GPX simulator.

        Args:
            gpx_path: Path to GPX file
            speed_mps: Speed to simulate in meters per second
        """
        self.gpx_path = Path(gpx_path)
        self.speed = speed_mps
        self._route_points: List[Tuple[float, float]] = []
        self._route_index = 0
        self._last_update = time.time()
        self.current_lat = 0.0
        self.current_lon = 0.0
        self.current_heading = 0.0

    def connect(self) -> None:
        """Load and parse the GPX file."""
        self._route_points = self._parse_gpx()
        if self._route_points:
            self.current_lat, self.current_lon = self._route_points[0]
            # Calculate initial heading from first two points
            if len(self._route_points) > 1:
                self.current_heading = bearing(
                    self._route_points[0][0], self._route_points[0][1],
                    self._route_points[1][0], self._route_points[1][1]
                )
            print(f"Loaded GPX route with {len(self._route_points)} points")
            print(f"Start: {self.current_lat:.4f}, {self.current_lon:.4f}, heading {self.current_heading:.0f}°")
        else:
            print("Warning: No points found in GPX file")

    def disconnect(self) -> None:
        """Clean up."""
        pass

    def set_network(self, network: RoadNetwork) -> None:
        """Accept network (for compatibility) but don't use it - we follow GPX."""
        # Reset time so first read_position doesn't jump
        self._last_update = time.time()

    def get_route_bounds(self) -> Optional[Tuple[float, float, float, float]]:
        """Get bounds of the GPX route (min_lat, max_lat, min_lon, max_lon)."""
        if not self._route_points:
            return None
        lats = [p[0] for p in self._route_points]
        lons = [p[1] for p in self._route_points]
        return (min(lats), max(lats), min(lons), max(lons))

    def get_upcoming_path(self, max_distance: float = 1000) -> List[Tuple[float, float]]:
        """Get upcoming route points within max_distance meters.

        Returns list of (lat, lon) tuples for the path ahead.
        """
        if not self._route_points:
            return []

        # Start from current position
        path = [(self.current_lat, self.current_lon)]
        total_distance = 0.0

        # Add upcoming points
        for i in range(self._route_index + 1, len(self._route_points)):
            pt = self._route_points[i]
            prev_pt = path[-1]
            dist = haversine_distance(prev_pt[0], prev_pt[1], pt[0], pt[1])
            total_distance += dist

            if total_distance > max_distance:
                break
            path.append(pt)

        return path

    def read_position(self) -> Optional[Position]:
        """Return current position along GPX route."""
        if not self._route_points:
            return None

        now = time.time()
        dt = now - self._last_update
        self._last_update = now

        # Cap dt to prevent large jumps
        dt = min(dt, 2.0)

        distance_to_travel = self.speed * dt

        while distance_to_travel > 0 and self._route_index < len(self._route_points) - 1:
            next_pt = self._route_points[self._route_index + 1]

            dist_to_next = haversine_distance(
                self.current_lat, self.current_lon, next_pt[0], next_pt[1]
            )

            if distance_to_travel >= dist_to_next:
                # Move to next waypoint
                self.current_lat, self.current_lon = next_pt
                self._route_index += 1
                distance_to_travel -= dist_to_next

                # Update heading for next segment
                if self._route_index < len(self._route_points) - 1:
                    next_next = self._route_points[self._route_index + 1]
                    self.current_heading = bearing(
                        self.current_lat, self.current_lon,
                        next_next[0], next_next[1]
                    )
            else:
                # Move partway to next waypoint
                self.current_heading = bearing(
                    self.current_lat, self.current_lon,
                    next_pt[0], next_pt[1]
                )
                new_lat, new_lon = point_along_bearing(
                    self.current_lat, self.current_lon,
                    self.current_heading, distance_to_travel
                )
                self.current_lat, self.current_lon = new_lat, new_lon
                break

        return Position(
            lat=self.current_lat,
            lon=self.current_lon,
            heading=self.current_heading,
            speed=self.speed,
        )

    def _parse_gpx(self) -> List[Tuple[float, float]]:
        """Parse GPX file and extract track points."""
        points = []

        try:
            tree = ET.parse(self.gpx_path)
            root = tree.getroot()

            # Handle GPX namespace
            ns = {'gpx': 'http://www.topografix.com/GPX/1/1'}

            # Try to find trackpoints (trk/trkseg/trkpt)
            for trkpt in root.findall('.//gpx:trkpt', ns):
                lat = float(trkpt.get('lat'))
                lon = float(trkpt.get('lon'))
                points.append((lat, lon))

            # If no trackpoints, try route points (rte/rtept)
            if not points:
                for rtept in root.findall('.//gpx:rtept', ns):
                    lat = float(rtept.get('lat'))
                    lon = float(rtept.get('lon'))
                    points.append((lat, lon))

            # If still no points, try without namespace (some GPX files)
            if not points:
                for trkpt in root.findall('.//trkpt'):
                    lat = float(trkpt.get('lat'))
                    lon = float(trkpt.get('lon'))
                    points.append((lat, lon))

        except ET.ParseError as e:
            print(f"Error parsing GPX file: {e}")
        except Exception as e:
            print(f"Error reading GPX file: {e}")

        return points

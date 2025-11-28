"""Main CopePilot application loop."""

import argparse
import time
from typing import Optional

from .gps import GPSReader, Position
from .osm import OSMFetcher
from .corners import CornerDetector
from .pacenotes import PacenoteGenerator
from .audio import AudioPlayer


class CopePilot:
    """Main application coordinating all components."""

    def __init__(
        self,
        gps_port: str = "/dev/ttyUSB0",
        lookahead_m: float = 500,
        update_interval: float = 1.0,
    ):
        self.gps = GPSReader(port=gps_port)
        self.osm = OSMFetcher()
        self.corner_detector = CornerDetector()
        self.pacenote_gen = PacenoteGenerator(distance_threshold_m=lookahead_m)
        self.audio = AudioPlayer()

        self.lookahead = lookahead_m
        self.update_interval = update_interval

        self._road_cache: dict = {}
        self._last_fetch_pos: Optional[Position] = None
        self._called_corners: set = set()

    def run(self) -> None:
        """Main application loop."""
        print("CopePilot starting...")

        self.gps.connect()
        self.audio.start()

        print("GPS connected, waiting for fix...")

        try:
            while True:
                self._update_cycle()
                time.sleep(self.update_interval)
        except KeyboardInterrupt:
            print("\nShutting down...")
        finally:
            self.audio.stop()
            self.gps.disconnect()

    def _update_cycle(self) -> None:
        """Single update cycle: read GPS, detect corners, call pacenotes."""
        pos = self.gps.read_position()
        if not pos:
            return

        # Fetch new road data if we've moved significantly
        if self._should_fetch_roads(pos):
            self._fetch_roads(pos)

        # Detect corners on nearby roads
        all_corners = []
        for segment in self._road_cache.values():
            corners = self.corner_detector.detect_corners(
                segment.points, (pos.lat, pos.lon)
            )
            all_corners.extend(corners)

        # Generate and speak pacenotes
        notes = self.pacenote_gen.generate(all_corners)
        for note in notes:
            corner_key = f"{note.text}_{int(note.distance_m/50)}"
            if corner_key not in self._called_corners:
                self.audio.say(note.text, note.priority)
                self._called_corners.add(corner_key)

        # Clean up old called corners
        self._cleanup_called_corners()

    def _should_fetch_roads(self, pos: Position) -> bool:
        """Check if we need to fetch new road data."""
        if not self._last_fetch_pos:
            return True

        # Refetch if moved more than 500m from last fetch point
        from .corners import CornerDetector
        detector = CornerDetector()
        distance = detector._haversine_distance(
            (self._last_fetch_pos.lat, self._last_fetch_pos.lon),
            (pos.lat, pos.lon),
        )
        return distance > 500

    def _fetch_roads(self, pos: Position) -> None:
        """Fetch road data from OSM."""
        print(f"Fetching roads near {pos.lat:.4f}, {pos.lon:.4f}...")
        segments = self.osm.fetch_roads_around(pos.lat, pos.lon, radius_m=2000)
        self._road_cache = {s.way_id: s for s in segments}
        self._last_fetch_pos = pos
        print(f"Loaded {len(segments)} road segments")

    def _cleanup_called_corners(self) -> None:
        """Remove old entries from called corners set."""
        # Keep set from growing unbounded
        if len(self._called_corners) > 100:
            self._called_corners.clear()


def main():
    parser = argparse.ArgumentParser(description="CopePilot - Rally pacenote assistant")
    parser.add_argument(
        "--gps-port", default="/dev/ttyUSB0", help="GPS serial port"
    )
    parser.add_argument(
        "--lookahead", type=float, default=500, help="Lookahead distance in meters"
    )
    args = parser.parse_args()

    app = CopePilot(gps_port=args.gps_port, lookahead_m=args.lookahead)
    app.run()


if __name__ == "__main__":
    main()

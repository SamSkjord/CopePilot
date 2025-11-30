"""Main CopePilot application loop."""

import argparse
import time
from pathlib import Path
from typing import Optional, Protocol

from .gps import GPSReader, Position
from .map_loader import MapLoader, RoadNetwork
from .path_projector import PathProjector, ProjectedPath
from .corners import CornerDetector
from .pacenotes import PacenoteGenerator
from .audio import AudioPlayer
from .geometry import haversine_distance
from . import config


class GPSInterface(Protocol):
    """Protocol for GPS data sources."""
    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def read_position(self) -> Optional[Position]: ...


class CopePilot:
    """Main application coordinating all components."""

    def __init__(
        self,
        gps: GPSInterface,
        map_loader: MapLoader,
        lookahead_m: float = config.LOOKAHEAD_DISTANCE_M,
        update_interval: float = config.UPDATE_INTERVAL_S,
        audio_enabled: bool = True,
        visualize: bool = False,
        simulation_mode: bool = False,
    ):
        self.gps = gps
        self.map_loader = map_loader
        # Tune corner detection for better square/chicane detection
        self.corner_detector = CornerDetector(
            merge_same_direction=False,  # Don't merge consecutive same-direction turns
            min_cut_distance=10.0,        # More precise cuts (was 15)
            max_chicane_gap=15.0,         # Tighter chicane merging (was 30)
        )
        self.pacenote_gen = PacenoteGenerator(distance_threshold_m=lookahead_m)
        self.visualize = visualize
        self.simulation_mode = simulation_mode

        if audio_enabled:
            self.audio = AudioPlayer()
        else:
            self.audio = None

        self.lookahead = lookahead_m
        self.update_interval = update_interval

        self._network: Optional[RoadNetwork] = None
        self._projector: Optional[PathProjector] = None
        self._last_fetch_pos: Optional[Position] = None
        self._visualizer = None

    def run(self) -> None:
        """Main application loop."""
        print("CopePilot starting...")
        print(f"Lookahead: {self.lookahead}m")

        self.gps.connect()
        if self.audio:
            self.audio.start()
            time.sleep(0.1)  # Let audio thread fully initialize

        print("GPS ready, starting navigation...")

        try:
            while True:
                self._update_cycle()
                time.sleep(self.update_interval)
        except KeyboardInterrupt:
            print("\nShutting down...")
        finally:
            if self.audio:
                self.audio.stop()
            self.gps.disconnect()

    def _update_cycle(self) -> None:
        """Single update cycle: read GPS, project path, detect corners, call pacenotes."""
        pos = self.gps.read_position()
        if not pos:
            return

        # Fetch new road data if we've moved significantly
        if self._should_refetch(pos):
            self._fetch_roads(pos)

        if not self._network or not self._projector:
            return

        # Project path ahead based on heading
        path = self._projector.project_path(
            pos.lat, pos.lon, pos.heading, self.lookahead
        )

        if not path or len(path.points) < 5:
            return

        # Extract geometry from path
        points = [(p.lat, p.lon) for p in path.points]

        # Detect corners
        corners = self.corner_detector.detect_corners(points)

        # Generate pacenotes
        notes = self.pacenote_gen.generate(corners, path.junctions, path.bridges)

        # Speak/print notes that haven't been called yet
        for note in notes:
            should_call, filtered_note = self.pacenote_gen.should_call(note)
            if should_call and filtered_note:
                if self.audio:
                    self.audio.say(filtered_note.text, filtered_note.priority)
                print(f"  >>> [{filtered_note.distance_m:.0f}m] {filtered_note.text}")

        # Update visualization
        if self._visualizer:
            self._visualizer.update(pos.lat, pos.lon, pos.heading, path, corners)

        # Periodically clear old called notes
        self.pacenote_gen.clear_called()

    def _should_refetch(self, pos: Position) -> bool:
        """Check if we need to fetch new road data."""
        if not self._last_fetch_pos or not self._network:
            return True

        # In simulation mode, only refetch if we've moved very far
        # (basically never, since we load a large area initially)
        if self.simulation_mode:
            return False

        distance = haversine_distance(
            self._last_fetch_pos.lat, self._last_fetch_pos.lon,
            pos.lat, pos.lon,
        )
        return distance > config.REFETCH_DISTANCE_M

    def _fetch_roads(self, pos: Position) -> None:
        """Fetch road data from OSM PBF."""
        print(f"Loading roads near {pos.lat:.4f}, {pos.lon:.4f}...")

        # Use larger radius in simulation mode to avoid refetching
        radius = 10000 if self.simulation_mode else config.ROAD_FETCH_RADIUS_M

        try:
            self._network = self.map_loader.load_around(
                pos.lat, pos.lon, radius
            )
            self._projector = PathProjector(self._network)
            self._last_fetch_pos = pos

            print(f"Loaded {len(self._network.ways)} roads, "
                  f"{len(self._network.junctions)} junctions")

            # Share network with simulator if it needs it for route building
            if hasattr(self.gps, 'set_network'):
                self.gps.set_network(self._network)

            # Initialize visualizer if enabled
            if self.visualize and not self._visualizer:
                try:
                    from .visualizer import MapVisualizer
                    # Get route bounds from simulator if available
                    route_bounds = None
                    if hasattr(self.gps, 'get_route_bounds'):
                        route_bounds = self.gps.get_route_bounds()
                    self._visualizer = MapVisualizer(self._network, route_bounds)
                    print("Visualization window opened")
                except ImportError as e:
                    print(f"Visualization unavailable: {e}")
                    self.visualize = False
        except Exception as e:
            print(f"Error loading roads: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="CopePilot - Rally pacenote style driving assistance"
    )

    # GPS source options
    gps_group = parser.add_mutually_exclusive_group()
    gps_group.add_argument(
        "--gps-port",
        default=config.GPS_PORT,
        help=f"GPS serial port (default: {config.GPS_PORT})",
    )
    gps_group.add_argument(
        "--simulate",
        metavar="LAT,LON,HEADING",
        help="Simulate GPS at location (e.g., 51.5,-0.1,90)",
    )
    gps_group.add_argument(
        "--vbo",
        type=Path,
        help="Replay GPS from VBO file",
    )

    # General options
    parser.add_argument(
        "--lookahead",
        type=float,
        default=config.LOOKAHEAD_DISTANCE_M,
        help=f"Lookahead distance in meters (default: {config.LOOKAHEAD_DISTANCE_M})",
    )
    parser.add_argument(
        "--map",
        type=Path,
        default=config.MAP_FILE,
        help=f"OSM PBF map file (default: {config.MAP_FILE})",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=13.4,
        help="Simulation speed in m/s (default: 13.4 = 30mph)",
    )
    parser.add_argument(
        "--speed-multiplier",
        type=float,
        default=1.0,
        help="Playback speed multiplier for VBO (default: 1.0)",
    )
    parser.add_argument(
        "--no-audio",
        action="store_true",
        help="Disable audio output (print only)",
    )
    parser.add_argument(
        "--visualize",
        action="store_true",
        help="Show map visualization window",
    )

    args = parser.parse_args()

    # Create map loader
    map_loader = MapLoader(args.map)

    # Create GPS source
    if args.simulate:
        from .simulator import GPSSimulator
        try:
            parts = args.simulate.split(",")
            lat, lon, heading = float(parts[0]), float(parts[1]), float(parts[2])
        except (ValueError, IndexError):
            parser.error("--simulate requires LAT,LON,HEADING format")

        print(f"Simulation mode: {lat:.4f}, {lon:.4f}, heading {heading}°")
        print(f"Speed: {args.speed} m/s ({args.speed * 3.6:.1f} km/h)")

        gps = GPSSimulator(
            start_lat=lat,
            start_lon=lon,
            start_heading=heading,
            speed_mps=args.speed,
        )

    elif args.vbo:
        from .simulator import VBOSimulator
        if not args.vbo.exists():
            parser.error(f"VBO file not found: {args.vbo}")

        print(f"VBO playback mode: {args.vbo}")
        print(f"Speed multiplier: {args.speed_multiplier}x")

        gps = VBOSimulator(
            vbo_path=str(args.vbo),
            speed_multiplier=args.speed_multiplier,
        )

    else:
        print(f"GPS mode: {args.gps_port}")
        gps = GPSReader(port=args.gps_port)

    # Create and run app
    app = CopePilot(
        gps=gps,
        map_loader=map_loader,
        lookahead_m=args.lookahead,
        audio_enabled=not args.no_audio,
        visualize=args.visualize,
        simulation_mode=bool(args.simulate or args.vbo),
    )
    app.run()


if __name__ == "__main__":
    main()

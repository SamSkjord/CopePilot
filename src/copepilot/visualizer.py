"""Simple visualization for simulation mode."""

import math
from typing import List, Optional, Tuple

try:
    import matplotlib.pyplot as plt
    import matplotlib.patches as patches
    from matplotlib.collections import LineCollection
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False

from .map_loader import RoadNetwork
from .corners import Corner, Direction
from .path_projector import ProjectedPath


class MapVisualizer:
    """Real-time map visualization using matplotlib."""

    def __init__(self, network: RoadNetwork, route_bounds: tuple = None):
        """
        Initialize visualizer.

        Args:
            network: Road network to display
            route_bounds: Optional (min_lat, max_lat, min_lon, max_lon) for view
        """
        if not MATPLOTLIB_AVAILABLE:
            raise ImportError("matplotlib required: pip install matplotlib")

        self.network = network
        self.fig, self.ax = plt.subplots(1, 1, figsize=(10, 10))
        self.ax.set_aspect('equal')
        self.ax.set_facecolor('#1a1a1a')

        # Plot elements
        self._car_marker = None
        self._path_line = None
        self._corner_markers = []

        # Draw road network once
        self._draw_roads()

        # Set initial bounds if provided
        if route_bounds:
            margin = 0.0005
            self.ax.set_xlim(route_bounds[2] - margin, route_bounds[3] + margin)
            self.ax.set_ylim(route_bounds[0] - margin, route_bounds[1] + margin)
            self._bounds = route_bounds
        else:
            self._bounds = None

        plt.ion()  # Interactive mode
        plt.show(block=False)

    def _draw_roads(self) -> None:
        """Draw all roads in the network."""
        lines = []
        colors = []

        road_colors = {
            'motorway': '#ff6600',
            'trunk': '#ff9933',
            'primary': '#ffcc00',
            'secondary': '#ffff66',
            'tertiary': '#ffffff',
            'residential': '#888888',
            'unclassified': '#666666',
        }

        for way in self.network.ways.values():
            geometry = self.network.get_way_geometry(way.id)
            if len(geometry) < 2:
                continue

            # Convert to plot coordinates (lon, lat for x, y)
            coords = [(p[1], p[0]) for p in geometry]
            lines.append(coords)

            # Color by road type
            base_type = way.highway_type.replace('_link', '')
            color = road_colors.get(base_type, '#444444')
            colors.append(color)

        if lines:
            lc = LineCollection(lines, colors=colors, linewidths=1.5, alpha=0.7)
            self.ax.add_collection(lc)

        # Bounds will be set when we get path data
        self._bounds = None

    def update(
        self,
        lat: float,
        lon: float,
        heading: float,
        path: Optional[ProjectedPath] = None,
        corners: Optional[List[Corner]] = None,
    ) -> None:
        """Update visualization with current position."""
        # Remove old markers
        if self._car_marker:
            self._car_marker.remove()
        if self._path_line:
            self._path_line.remove()
        for marker in self._corner_markers:
            marker.remove()
        self._corner_markers = []

        # Draw projected path
        if path and path.points:
            path_lons = [p.lon for p in path.points]
            path_lats = [p.lat for p in path.points]
            self._path_line, = self.ax.plot(
                path_lons, path_lats, 'c-', linewidth=3, alpha=0.8
            )

        # Draw corners
        if corners:
            for corner in corners:
                color = 'red' if corner.severity <= 2 else 'orange' if corner.severity <= 4 else 'yellow'
                # Use different marker for chicanes
                marker_style = 's' if corner.is_chicane else 'o'
                marker = self.ax.plot(
                    corner.apex_lon, corner.apex_lat,
                    marker_style, color=color, markersize=10, alpha=0.8
                )[0]
                self._corner_markers.append(marker)

                # Add label - show chicane directions or corner severity
                if corner.is_chicane and corner.exit_direction:
                    label = f"{corner.direction.value[0].upper()}{corner.exit_direction.value[0].upper()}"
                elif corner.severity == 7:
                    label = f"K{corner.direction.value[0].upper()}"  # Kink
                else:
                    label = f"{corner.direction.value[0].upper()}{corner.severity}"
                text = self.ax.text(
                    corner.apex_lon, corner.apex_lat + 0.0002,
                    label, color='white', fontsize=8, ha='center'
                )
                self._corner_markers.append(text)

        # Draw car position (small dot)
        self._car_marker = self.ax.plot(
            lon, lat, 'o', color='lime', markersize=6
        )[0]

        # Set view bounds based on path if not already set
        if path and path.points and self._bounds is None:
            path_lons = [p.lon for p in path.points]
            path_lats = [p.lat for p in path.points]
            margin = 0.0005
            self._bounds = (min(path_lats), max(path_lats), min(path_lons), max(path_lons))
            self.ax.set_xlim(self._bounds[2] - margin, self._bounds[3] + margin)
            self.ax.set_ylim(self._bounds[0] - margin, self._bounds[1] + margin)

        self.fig.canvas.draw()
        self.fig.canvas.flush_events()

    def close(self) -> None:
        """Close the visualization window."""
        plt.close(self.fig)

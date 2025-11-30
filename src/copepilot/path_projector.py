"""Project path ahead based on current position and heading."""

import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .geometry import (
    haversine_distance,
    bearing,
    angle_difference,
    closest_point_on_segment,
    cumulative_distances,
)
from .map_loader import RoadNetwork, Junction, Way
from . import config


@dataclass
class PathPoint:
    """A point along the projected path."""
    lat: float
    lon: float
    distance_from_start: float  # Meters from current position
    way_id: int
    node_index: int  # Index within the way


@dataclass
class ProjectedPath:
    """The projected path ahead with detected features."""
    points: List[PathPoint]
    junctions: List["JunctionInfo"]
    bridges: List["BridgeInfo"]
    total_distance: float


@dataclass
class JunctionInfo:
    """Information about an upcoming junction."""
    lat: float
    lon: float
    distance_m: float
    is_t_junction: bool
    exit_bearings: List[float]  # Bearings of roads leaving junction
    straight_on_bearing: Optional[float]  # Which way is "straight on"
    node_id: int = 0  # Node ID for deduplication


@dataclass
class BridgeInfo:
    """Information about an upcoming bridge."""
    lat: float
    lon: float
    distance_m: float
    way_id: int


class PathProjector:
    """Projects the likely path ahead based on current heading."""

    def __init__(
        self,
        network: RoadNetwork,
        heading_tolerance: float = config.HEADING_TOLERANCE_DEG,
    ):
        self.network = network
        self.heading_tolerance = heading_tolerance

    # Road type priority (lower = prefer)
    ROAD_PRIORITY = {
        "motorway": 1, "motorway_link": 1,
        "trunk": 2, "trunk_link": 2,
        "primary": 3, "primary_link": 3,
        "secondary": 4, "secondary_link": 4,
        "tertiary": 5, "tertiary_link": 5,
        "unclassified": 6,
        "residential": 7,
        "living_street": 8,
        "service": 9,  # Driveways, parking lots - lowest priority
    }

    def find_current_way(
        self,
        lat: float,
        lon: float,
        heading: float,
    ) -> Optional[Tuple[int, int, bool]]:
        """
        Find which way the vehicle is currently on.

        Prefers main roads over service roads when both are nearby.
        Returns: (way_id, node_index, forward) where forward indicates
                 direction of travel along the way.
        """
        candidates = []

        for way_id, way in self.network.ways.items():
            geometry = self.network.get_way_geometry(way_id)
            if len(geometry) < 2:
                continue

            for i in range(len(geometry) - 1):
                p1, p2 = geometry[i], geometry[i + 1]

                # Find closest point on this segment
                closest, t = closest_point_on_segment((lat, lon), p1, p2)
                dist = haversine_distance(lat, lon, closest[0], closest[1])

                if dist > 100:  # More than 100m away, skip
                    continue

                # Check heading alignment
                seg_bearing = bearing(p1[0], p1[1], p2[0], p2[1])
                heading_diff = abs(angle_difference(heading, seg_bearing))

                # Could be going either direction on the road
                forward = heading_diff < 90
                if not forward:
                    heading_diff = 180 - heading_diff

                if heading_diff > self.heading_tolerance:
                    continue

                # Get road priority (prefer main roads)
                priority = self.ROAD_PRIORITY.get(way.highway_type, 10)

                # Score: heavily prioritize road type over distance
                # A primary road 100m away should beat a service road 30m away
                score = priority * 50 + dist

                candidates.append((score, way_id, i, forward))

        if not candidates:
            return None

        # Return best candidate (lowest score)
        candidates.sort(key=lambda x: x[0])
        _, way_id, node_idx, forward = candidates[0]
        return (way_id, node_idx, forward)

    def project_path(
        self,
        lat: float,
        lon: float,
        heading: float,
        max_distance: float = config.LOOKAHEAD_DISTANCE_M,
    ) -> Optional[ProjectedPath]:
        """
        Project the path ahead from current position.

        Follows the current road, choosing "straight on" at junctions.
        """
        # Find current way
        current = self.find_current_way(lat, lon, heading)
        if not current:
            return None

        way_id, node_idx, forward = current
        points: List[PathPoint] = []
        junctions: List[JunctionInfo] = []
        bridges: List[BridgeInfo] = []
        total_distance = 0.0

        # Start from current position
        visited_ways = {way_id}
        visited_bridges = set()  # Track bridge ways already recorded

        while total_distance < max_distance:
            way = self.network.ways.get(way_id)
            if not way:
                break

            geometry = self.network.get_way_geometry(way_id)
            if len(geometry) < 2:
                break

            # Check for bridge at start of this way
            if way.bridge and way_id not in visited_bridges:
                visited_bridges.add(way_id)
                # Use first point of this way segment as bridge location
                bridge_pt = geometry[node_idx] if node_idx < len(geometry) else geometry[0]
                bridges.append(BridgeInfo(
                    lat=bridge_pt[0],
                    lon=bridge_pt[1],
                    distance_m=total_distance,
                    way_id=way_id,
                ))

            # Add points along this way
            if forward:
                indices = range(node_idx, len(way.nodes))
            else:
                indices = range(node_idx, -1, -1)

            prev_point = (lat, lon) if not points else (points[-1].lat, points[-1].lon)

            for i in indices:
                if i < 0 or i >= len(geometry):
                    continue

                pt = geometry[i]
                dist = haversine_distance(prev_point[0], prev_point[1], pt[0], pt[1])
                total_distance += dist

                if total_distance > max_distance:
                    break

                points.append(PathPoint(
                    lat=pt[0],
                    lon=pt[1],
                    distance_from_start=total_distance,
                    way_id=way_id,
                    node_index=i,
                ))
                prev_point = pt

            if total_distance > max_distance:
                break

            # At end of way - find continuation
            end_node_id = way.nodes[-1] if forward else way.nodes[0]
            end_node = self.network.nodes.get(end_node_id)
            if not end_node:
                break

            # Check if this is a junction
            junction = self.network.junctions.get(end_node_id)
            if junction:
                # Record junction info
                exit_bearings = self._get_exit_bearings(
                    junction, way_id, forward
                )
                current_bearing = bearing(
                    prev_point[0], prev_point[1],
                    end_node.lat, end_node.lon
                )

                # Find straight-on bearing
                straight_bearing = self._find_straight_on(
                    current_bearing, exit_bearings,
                    current_way=way, junction=junction
                )

                junctions.append(JunctionInfo(
                    lat=junction.lat,
                    lon=junction.lon,
                    distance_m=total_distance,
                    is_t_junction=junction.is_t_junction,
                    exit_bearings=exit_bearings,
                    straight_on_bearing=straight_bearing,
                    node_id=junction.node_id,
                ))

                # Follow straight-on road
                if straight_bearing is not None:
                    next_way, next_forward = self._find_way_with_bearing(
                        junction, straight_bearing, way_id
                    )
                    if next_way and next_way not in visited_ways:
                        way_id = next_way
                        forward = next_forward
                        visited_ways.add(way_id)
                        node_idx = 0 if next_forward else len(self.network.ways[way_id].nodes) - 1
                        continue

                break  # No straight-on continuation found

            # Not a junction - try to find connecting way
            connected_ways = self.network.node_to_ways.get(end_node_id, [])
            next_way = None
            for wid in connected_ways:
                if wid != way_id and wid not in visited_ways:
                    next_way = wid
                    break

            if next_way:
                way_id = next_way
                visited_ways.add(way_id)
                new_way = self.network.ways[way_id]
                # Determine direction on new way
                if new_way.nodes[0] == end_node_id:
                    forward = True
                    node_idx = 0
                elif new_way.nodes[-1] == end_node_id:
                    forward = False
                    node_idx = len(new_way.nodes) - 1
                else:
                    break
            else:
                break

        return ProjectedPath(
            points=points,
            junctions=junctions,
            bridges=bridges,
            total_distance=total_distance,
        )

    def _get_exit_bearings(
        self,
        junction: Junction,
        arrival_way_id: int,
        forward: bool,
    ) -> List[float]:
        """Get bearings of all roads leaving this junction (excluding arrival)."""
        bearings = []
        node = self.network.nodes[junction.node_id]

        for way_id in junction.connected_ways:
            if way_id == arrival_way_id:
                continue

            way = self.network.ways.get(way_id)
            if not way:
                continue

            try:
                idx = way.nodes.index(junction.node_id)
            except ValueError:
                continue

            # Get bearing leaving the junction along this way
            if idx > 0:
                prev = self.network.nodes.get(way.nodes[idx - 1])
                if prev:
                    b = bearing(node.lat, node.lon, prev.lat, prev.lon)
                    bearings.append(b)

            if idx < len(way.nodes) - 1:
                next_n = self.network.nodes.get(way.nodes[idx + 1])
                if next_n:
                    b = bearing(node.lat, node.lon, next_n.lat, next_n.lon)
                    bearings.append(b)

        return bearings

    def _find_straight_on(
        self,
        arrival_bearing: float,
        exit_bearings: List[float],
        current_way: Optional[Way] = None,
        junction: Optional[Junction] = None,
    ) -> Optional[float]:
        """
        Find which exit is 'straight on' (closest to current bearing).

        If current_way is provided, checks if the road actually continues
        through the junction (same road) vs hitting a different road (T-junction).
        """
        if not exit_bearings:
            return None

        # If we have road info, check if current road continues through junction
        if current_way and junction:
            # Check if current road continues (node is in middle of way, not at end)
            if junction.node_id in current_way.nodes:
                idx = current_way.nodes.index(junction.node_id)
                road_continues = 0 < idx < len(current_way.nodes) - 1

                if not road_continues:
                    # Current road ends here - check if any exit is the same road name
                    same_road_exit = self._find_same_road_exit(
                        current_way, junction, arrival_bearing
                    )
                    if same_road_exit is not None:
                        return same_road_exit
                    # No same-road continuation - this is a true T-junction
                    return None

        # Default: find best aligned exit
        best_bearing = None
        best_diff = float("inf")

        for b in exit_bearings:
            diff = abs(angle_difference(arrival_bearing, b))
            if diff < best_diff and diff < self.heading_tolerance:
                best_diff = diff
                best_bearing = b

        return best_bearing

    def _find_same_road_exit(
        self,
        current_way: Way,
        junction: Junction,
        arrival_bearing: float,
    ) -> Optional[float]:
        """
        Find exit bearing that continues the same road (by name or type).

        Returns the bearing if found, None if no same-road continuation exists.
        """
        node = self.network.nodes[junction.node_id]

        for way_id in junction.connected_ways:
            if way_id == current_way.id:
                continue

            other_way = self.network.ways.get(way_id)
            if not other_way:
                continue

            # Check if this is the same road (same name, or both unnamed with same type)
            same_road = False
            if current_way.name and other_way.name:
                same_road = current_way.name == other_way.name
            elif not current_way.name and not other_way.name:
                # Both unnamed - only continue if same road type
                same_road = current_way.highway_type == other_way.highway_type

            if not same_road:
                continue

            # Get bearing of this exit
            try:
                idx = other_way.nodes.index(junction.node_id)
            except ValueError:
                continue

            # Check forward direction
            if idx < len(other_way.nodes) - 1:
                next_n = self.network.nodes.get(other_way.nodes[idx + 1])
                if next_n:
                    b = bearing(node.lat, node.lon, next_n.lat, next_n.lon)
                    if abs(angle_difference(arrival_bearing, b)) < self.heading_tolerance:
                        return b

            # Check backward direction
            if idx > 0:
                prev = self.network.nodes.get(other_way.nodes[idx - 1])
                if prev:
                    b = bearing(node.lat, node.lon, prev.lat, prev.lon)
                    if abs(angle_difference(arrival_bearing, b)) < self.heading_tolerance:
                        return b

        return None

    def _find_way_with_bearing(
        self,
        junction: Junction,
        target_bearing: float,
        exclude_way_id: int,
    ) -> Tuple[Optional[int], bool]:
        """Find way leaving junction with given bearing."""
        node = self.network.nodes[junction.node_id]

        for way_id in junction.connected_ways:
            if way_id == exclude_way_id:
                continue

            way = self.network.ways.get(way_id)
            if not way:
                continue

            try:
                idx = way.nodes.index(junction.node_id)
            except ValueError:
                continue

            # Check forward direction
            if idx < len(way.nodes) - 1:
                next_n = self.network.nodes.get(way.nodes[idx + 1])
                if next_n:
                    b = bearing(node.lat, node.lon, next_n.lat, next_n.lon)
                    if abs(angle_difference(target_bearing, b)) < self.heading_tolerance:
                        return way_id, True

            # Check backward direction
            if idx > 0:
                prev = self.network.nodes.get(way.nodes[idx - 1])
                if prev:
                    b = bearing(node.lat, node.lon, prev.lat, prev.lon)
                    if abs(angle_difference(target_bearing, b)) < self.heading_tolerance:
                        return way_id, False

        return None, False

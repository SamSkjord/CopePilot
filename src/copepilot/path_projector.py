"""Project path ahead based on current position and heading."""

import math
from dataclasses import dataclass, field
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
    tunnels: List["TunnelInfo"] = field(default_factory=list)
    railway_crossings: List["RailwayCrossingInfo"] = field(default_factory=list)
    fords: List["FordInfo"] = field(default_factory=list)
    speed_bumps: List["SpeedBumpInfo"] = field(default_factory=list)
    surface_changes: List["SurfaceChangeInfo"] = field(default_factory=list)
    barriers: List["BarrierInfo"] = field(default_factory=list)
    narrows: List["NarrowInfo"] = field(default_factory=list)
    total_distance: float = 0.0


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
    turn_direction: Optional[str] = None  # "left", "right", "straight" when route-guided


@dataclass
class BridgeInfo:
    """Information about an upcoming bridge."""
    lat: float
    lon: float
    distance_m: float
    way_id: int


@dataclass
class TunnelInfo:
    """Information about an upcoming tunnel."""
    lat: float
    lon: float
    distance_m: float
    way_id: int


@dataclass
class RailwayCrossingInfo:
    """Information about an upcoming railway level crossing."""
    lat: float
    lon: float
    distance_m: float
    node_id: int


@dataclass
class FordInfo:
    """Information about an upcoming ford (water crossing)."""
    lat: float
    lon: float
    distance_m: float
    way_id: int


@dataclass
class SpeedBumpInfo:
    """Information about an upcoming speed bump."""
    lat: float
    lon: float
    distance_m: float
    way_id: int
    bump_type: str  # bump, hump, table, etc.


@dataclass
class SurfaceChangeInfo:
    """Information about an upcoming surface change."""
    lat: float
    lon: float
    distance_m: float
    from_surface: str
    to_surface: str
    way_id: int


@dataclass
class BarrierInfo:
    """Information about an upcoming barrier (cattle grid, gate)."""
    lat: float
    lon: float
    distance_m: float
    node_id: int
    barrier_type: str  # cattle_grid, gate


@dataclass
class NarrowInfo:
    """Information about an upcoming narrow section."""
    lat: float
    lon: float
    distance_m: float
    way_id: int
    width: float  # Road width in meters, 0 if just tagged narrow


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
        route_waypoints: Optional[List[Tuple[float, float]]] = None,
    ) -> Optional[ProjectedPath]:
        """
        Project the path ahead from current position.

        Follows the current road, choosing "straight on" at junctions unless
        route_waypoints are provided, in which case follows the route direction.

        Args:
            lat, lon: Current position
            heading: Current heading in degrees
            max_distance: How far ahead to project
            route_waypoints: Optional list of (lat, lon) from GPX route to guide direction
        """
        # Find current way
        current = self.find_current_way(lat, lon, heading)
        if not current:
            return None

        way_id, node_idx, forward = current
        points: List[PathPoint] = []
        junctions: List[JunctionInfo] = []
        bridges: List[BridgeInfo] = []
        tunnels: List[TunnelInfo] = []
        railway_crossings: List[RailwayCrossingInfo] = []
        fords: List[FordInfo] = []
        speed_bumps: List[SpeedBumpInfo] = []
        surface_changes: List[SurfaceChangeInfo] = []
        barriers: List[BarrierInfo] = []
        narrows: List[NarrowInfo] = []
        total_distance = 0.0

        # Start from current position
        visited_ways = {way_id}
        visited_bridges = set()  # Track bridge ways already recorded
        visited_tunnels = set()
        visited_fords = set()
        visited_speed_bumps = set()
        visited_railway_crossings = set()
        visited_barriers = set()
        visited_narrows = set()
        current_surface = ""  # Track for surface change detection
        current_width = 0.0  # Track for narrow detection

        while total_distance < max_distance:
            way = self.network.ways.get(way_id)
            if not way:
                break

            geometry = self.network.get_way_geometry(way_id)
            if len(geometry) < 2:
                break

            # Check for way-level features at start of this way
            feature_pt = geometry[node_idx] if node_idx < len(geometry) else geometry[0]

            # Bridge
            if way.bridge and way_id not in visited_bridges:
                visited_bridges.add(way_id)
                bridges.append(BridgeInfo(
                    lat=feature_pt[0],
                    lon=feature_pt[1],
                    distance_m=total_distance,
                    way_id=way_id,
                ))

            # Tunnel
            if way.tunnel and way_id not in visited_tunnels:
                visited_tunnels.add(way_id)
                tunnels.append(TunnelInfo(
                    lat=feature_pt[0],
                    lon=feature_pt[1],
                    distance_m=total_distance,
                    way_id=way_id,
                ))

            # Ford
            if way.ford and way_id not in visited_fords:
                visited_fords.add(way_id)
                fords.append(FordInfo(
                    lat=feature_pt[0],
                    lon=feature_pt[1],
                    distance_m=total_distance,
                    way_id=way_id,
                ))

            # Speed bump / traffic calming
            if way.traffic_calming and way_id not in visited_speed_bumps:
                visited_speed_bumps.add(way_id)
                speed_bumps.append(SpeedBumpInfo(
                    lat=feature_pt[0],
                    lon=feature_pt[1],
                    distance_m=total_distance,
                    way_id=way_id,
                    bump_type=way.traffic_calming,
                ))

            # Surface change detection
            if way.surface and way.surface != current_surface:
                if current_surface:  # Only record if we had a previous surface
                    surface_changes.append(SurfaceChangeInfo(
                        lat=feature_pt[0],
                        lon=feature_pt[1],
                        distance_m=total_distance,
                        from_surface=current_surface,
                        to_surface=way.surface,
                        way_id=way_id,
                    ))
                current_surface = way.surface

            # Narrow section detection (width < 3m or explicit narrow tag)
            is_narrow = way.narrow or (way.width > 0 and way.width < 3.0)
            if is_narrow and way_id not in visited_narrows:
                visited_narrows.add(way_id)
                narrows.append(NarrowInfo(
                    lat=feature_pt[0],
                    lon=feature_pt[1],
                    distance_m=total_distance,
                    way_id=way_id,
                    width=way.width,
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

                # Check for railway crossing at this node
                node_id = way.nodes[i]
                if node_id in self.network.railway_crossings and node_id not in visited_railway_crossings:
                    visited_railway_crossings.add(node_id)
                    crossing = self.network.railway_crossings[node_id]
                    railway_crossings.append(RailwayCrossingInfo(
                        lat=crossing.lat,
                        lon=crossing.lon,
                        distance_m=total_distance,
                        node_id=node_id,
                    ))

                # Check for barrier (cattle grid, gate) at this node
                if node_id in self.network.barriers and node_id not in visited_barriers:
                    visited_barriers.add(node_id)
                    barrier = self.network.barriers[node_id]
                    barriers.append(BarrierInfo(
                        lat=barrier.lat,
                        lon=barrier.lon,
                        distance_m=total_distance,
                        node_id=node_id,
                        barrier_type=barrier.barrier_type,
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

                # Determine which way to go at junction
                chosen_bearing = None
                turn_direction = None

                if route_waypoints:
                    # Route-guided mode: find exit that leads toward next waypoint
                    chosen_bearing, turn_direction = self._find_route_guided_exit(
                        junction, current_bearing, exit_bearings, route_waypoints
                    )

                if chosen_bearing is None:
                    # Fall back to straight-on
                    chosen_bearing = self._find_straight_on(
                        current_bearing, exit_bearings,
                        current_way=way, junction=junction
                    )
                    if chosen_bearing is not None:
                        turn_direction = "straight"

                junctions.append(JunctionInfo(
                    lat=junction.lat,
                    lon=junction.lon,
                    distance_m=total_distance,
                    is_t_junction=junction.is_t_junction,
                    exit_bearings=exit_bearings,
                    straight_on_bearing=chosen_bearing,
                    node_id=junction.node_id,
                    turn_direction=turn_direction,
                ))

                # Follow chosen road
                if chosen_bearing is not None:
                    next_way, next_forward = self._find_way_with_bearing(
                        junction, chosen_bearing, way_id
                    )
                    if next_way and next_way not in visited_ways:
                        way_id = next_way
                        forward = next_forward
                        visited_ways.add(way_id)
                        node_idx = 0 if next_forward else len(self.network.ways[way_id].nodes) - 1
                        continue

                break  # No continuation found

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
            tunnels=tunnels,
            railway_crossings=railway_crossings,
            fords=fords,
            speed_bumps=speed_bumps,
            surface_changes=surface_changes,
            barriers=barriers,
            narrows=narrows,
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

    def _find_route_guided_exit(
        self,
        junction: Junction,
        arrival_bearing: float,
        exit_bearings: List[float],
        route_waypoints: List[Tuple[float, float]],
    ) -> Tuple[Optional[float], Optional[str]]:
        """
        Find which exit best matches the GPX route direction.

        Args:
            junction: The junction we're at
            arrival_bearing: Bearing we arrived from
            exit_bearings: Available exit bearings
            route_waypoints: GPX route points to follow

        Returns:
            (chosen_bearing, turn_direction) where turn_direction is "left", "right", or "straight"
        """
        if not exit_bearings or not route_waypoints:
            return None, None

        # Find the next route waypoint that's past the junction
        # Look for waypoints that are roughly ahead of us
        junction_lat, junction_lon = junction.lat, junction.lon

        # Find closest waypoint to junction to sync position
        best_idx = 0
        best_dist = float('inf')
        for i, (lat, lon) in enumerate(route_waypoints):
            dist = haversine_distance(junction_lat, junction_lon, lat, lon)
            if dist < best_dist:
                best_dist = dist
                best_idx = i

        # Look at waypoints after current position to determine direction
        # Use a waypoint that's 50-200m ahead for direction
        target_waypoint = None
        for i in range(best_idx + 1, min(best_idx + 20, len(route_waypoints))):
            lat, lon = route_waypoints[i]
            dist = haversine_distance(junction_lat, junction_lon, lat, lon)
            if dist > 50:  # At least 50m away
                target_waypoint = (lat, lon)
                break

        if not target_waypoint:
            # Use last available waypoint
            if best_idx + 1 < len(route_waypoints):
                target_waypoint = route_waypoints[best_idx + 1]
            else:
                return None, None

        # Calculate bearing from junction to target waypoint
        route_bearing = bearing(
            junction_lat, junction_lon,
            target_waypoint[0], target_waypoint[1]
        )

        # Find exit that best matches route direction
        best_exit = None
        best_diff = float('inf')
        for exit_b in exit_bearings:
            diff = abs(angle_difference(route_bearing, exit_b))
            if diff < best_diff:
                best_diff = diff
                best_exit = exit_b

        # Only accept if within 60 degrees of route direction
        if best_exit is None or best_diff > 60:
            return None, None

        # Determine turn direction relative to arrival bearing
        turn_angle = angle_difference(arrival_bearing, best_exit)

        if abs(turn_angle) < 30:
            turn_direction = "straight"
        elif turn_angle < 0:
            turn_direction = "left"
        else:
            turn_direction = "right"

        return best_exit, turn_direction

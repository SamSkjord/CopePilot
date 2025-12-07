"""Geometry utilities for GPS and road calculations."""

import math
from typing import Tuple, List, Union, Any


def _get_lat_lon(point: Any) -> Tuple[float, float]:
    """Extract lat/lon from a point (tuple or object with .lat/.lon)."""
    if hasattr(point, 'lat') and hasattr(point, 'lon'):
        return point.lat, point.lon
    return point[0], point[1]


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great circle distance between two GPS points in meters."""
    R = 6371000  # Earth's radius in meters

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c


def bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate bearing from point 1 to point 2 in degrees (0-360)."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_lambda = math.radians(lon2 - lon1)

    x = math.sin(delta_lambda) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(
        delta_lambda
    )

    bearing_rad = math.atan2(x, y)
    return (math.degrees(bearing_rad) + 360) % 360


def angle_difference(angle1: float, angle2: float) -> float:
    """Calculate smallest difference between two angles in degrees (-180 to 180)."""
    diff = (angle2 - angle1 + 180) % 360 - 180
    return diff


def point_along_bearing(
    lat: float, lon: float, bearing_deg: float, distance_m: float
) -> Tuple[float, float]:
    """Calculate point at given distance and bearing from start point."""
    R = 6371000  # Earth's radius

    lat_rad = math.radians(lat)
    lon_rad = math.radians(lon)
    bearing_rad = math.radians(bearing_deg)

    lat2 = math.asin(
        math.sin(lat_rad) * math.cos(distance_m / R)
        + math.cos(lat_rad) * math.sin(distance_m / R) * math.cos(bearing_rad)
    )

    lon2 = lon_rad + math.atan2(
        math.sin(bearing_rad) * math.sin(distance_m / R) * math.cos(lat_rad),
        math.cos(distance_m / R) - math.sin(lat_rad) * math.sin(lat2),
    )

    return math.degrees(lat2), math.degrees(lon2)


def closest_point_on_segment(
    point: Tuple[float, float],
    seg_start: Tuple[float, float],
    seg_end: Tuple[float, float],
) -> Tuple[Tuple[float, float], float]:
    """
    Find closest point on a line segment to a given point.

    Returns: (closest_point, distance_along_segment_fraction)
    """
    # Convert to approximate meters for calculation
    lat, lon = point
    x1 = (seg_start[1] - lon) * 111320 * math.cos(math.radians(lat))
    y1 = (seg_start[0] - lat) * 110540
    x2 = (seg_end[1] - lon) * 111320 * math.cos(math.radians(lat))
    y2 = (seg_end[0] - lat) * 110540

    # Vector from start to end
    dx = x2 - x1
    dy = y2 - y1

    if dx == 0 and dy == 0:
        return seg_start, 0.0

    # Parameter t for closest point on infinite line
    t = max(0, min(1, -((x1 * dx + y1 * dy) / (dx * dx + dy * dy))))

    # Interpolate back to lat/lon
    closest_lat = seg_start[0] + t * (seg_end[0] - seg_start[0])
    closest_lon = seg_start[1] + t * (seg_end[1] - seg_start[1])

    return (closest_lat, closest_lon), t


def calculate_curvature(
    p1: Any,
    p2: Any,
    p3: Any,
) -> float:
    """
    Calculate curvature at p2 using three-point circumcircle method.

    Points can be (lat, lon) tuples or objects with .lat/.lon attributes.
    Returns curvature in 1/meters (signed: positive=left, negative=right).
    """
    # Extract lat/lon from points (supports tuples and PathPoint objects)
    lat1, lon1 = _get_lat_lon(p1)
    lat2, lon2 = _get_lat_lon(p2)
    lat3, lon3 = _get_lat_lon(p3)

    # Convert to approximate meters using p2 as origin
    x1 = (lon1 - lon2) * 111320 * math.cos(math.radians(lat2))
    y1 = (lat1 - lat2) * 110540
    x2 = 0.0
    y2 = 0.0
    x3 = (lon3 - lon2) * 111320 * math.cos(math.radians(lat2))
    y3 = (lat3 - lat2) * 110540

    # Area of triangle
    area = abs((x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2)) / 2.0)

    if area < 1e-6:
        return 0.0  # Points are collinear

    # Side lengths
    a = math.sqrt((x2 - x3) ** 2 + (y2 - y3) ** 2)
    b = math.sqrt((x1 - x3) ** 2 + (y1 - y3) ** 2)
    c = math.sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2)

    # Circumradius
    radius = (a * b * c) / (4.0 * area)

    if radius < 0.1:
        return 0.0

    # Determine sign using cross product (left vs right turn)
    cross = (x3 - x1) * (y2 - y1) - (y3 - y1) * (x2 - x1)
    sign = 1.0 if cross > 0 else -1.0

    return sign / radius


def cumulative_distances(points: List[Any]) -> List[float]:
    """Calculate cumulative distance along a list of points.

    Points can be (lat, lon) tuples or objects with .lat/.lon attributes.
    """
    distances = [0.0]
    for i in range(1, len(points)):
        lat1, lon1 = _get_lat_lon(points[i - 1])
        lat2, lon2 = _get_lat_lon(points[i])
        d = haversine_distance(lat1, lon1, lat2, lon2)
        distances.append(distances[-1] + d)
    return distances

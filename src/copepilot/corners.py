"""Corner detection and classification for pacenotes."""

import math
from dataclasses import dataclass
from enum import Enum
from typing import List, Tuple


class Direction(Enum):
    LEFT = "left"
    RIGHT = "right"


@dataclass
class Corner:
    """A detected corner with rally-style classification."""

    lat: float
    lon: float
    direction: Direction
    severity: int  # 1 (hairpin) to 6 (flat/slight)
    distance_m: float  # distance from current position
    tightens: bool = False  # corner tightens
    opens: bool = False  # corner opens up
    long_corner: bool = False  # extended corner


# Rally pacenote severity scale:
# 1 = hairpin (very tight, ~180°)
# 2 = very tight (~120-150°)
# 3 = tight (~90°)
# 4 = medium (~60°)
# 5 = fast (~30-45°)
# 6 = flat/slight (~15-30°)

SEVERITY_THRESHOLDS = [
    (150, 1),  # > 150° = hairpin
    (120, 2),  # > 120° = very tight
    (80, 3),   # > 80° = tight
    (50, 4),   # > 50° = medium
    (25, 5),   # > 25° = fast
    (10, 6),   # > 10° = flat
]


class CornerDetector:
    """Detects and classifies corners from road geometry."""

    def __init__(self, min_angle: float = 10.0):
        self.min_angle = min_angle

    def detect_corners(
        self, points: List[Tuple[float, float]], current_pos: Tuple[float, float]
    ) -> List[Corner]:
        """Find all corners in a road segment."""
        if len(points) < 3:
            return []

        corners = []

        for i in range(1, len(points) - 1):
            angle = self._calculate_turn_angle(
                points[i - 1], points[i], points[i + 1]
            )

            if abs(angle) < self.min_angle:
                continue

            direction = Direction.RIGHT if angle > 0 else Direction.LEFT
            severity = self._classify_severity(abs(angle))
            distance = self._haversine_distance(current_pos, points[i])

            corners.append(
                Corner(
                    lat=points[i][0],
                    lon=points[i][1],
                    direction=direction,
                    severity=severity,
                    distance_m=distance,
                )
            )

        return corners

    def _calculate_turn_angle(
        self,
        p1: Tuple[float, float],
        p2: Tuple[float, float],
        p3: Tuple[float, float],
    ) -> float:
        """Calculate turn angle at p2. Positive = right, negative = left."""
        bearing1 = self._bearing(p1, p2)
        bearing2 = self._bearing(p2, p3)

        angle = bearing2 - bearing1

        # Normalize to -180 to 180
        while angle > 180:
            angle -= 360
        while angle < -180:
            angle += 360

        return angle

    def _bearing(self, p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
        """Calculate bearing from p1 to p2 in degrees."""
        lat1, lon1 = math.radians(p1[0]), math.radians(p1[1])
        lat2, lon2 = math.radians(p2[0]), math.radians(p2[1])

        dlon = lon2 - lon1
        x = math.sin(dlon) * math.cos(lat2)
        y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(
            lat2
        ) * math.cos(dlon)

        bearing = math.atan2(x, y)
        return math.degrees(bearing)

    def _classify_severity(self, angle: float) -> int:
        """Convert turn angle to rally severity (1-6)."""
        for threshold, severity in SEVERITY_THRESHOLDS:
            if angle >= threshold:
                return severity
        return 6

    def _haversine_distance(
        self, p1: Tuple[float, float], p2: Tuple[float, float]
    ) -> float:
        """Calculate distance between two points in meters."""
        R = 6371000  # Earth radius in meters

        lat1, lon1 = math.radians(p1[0]), math.radians(p1[1])
        lat2, lon2 = math.radians(p2[0]), math.radians(p2[1])

        dlat = lat2 - lat1
        dlon = lon2 - lon1

        a = (
            math.sin(dlat / 2) ** 2
            + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        )
        c = 2 * math.asin(math.sqrt(a))

        return R * c

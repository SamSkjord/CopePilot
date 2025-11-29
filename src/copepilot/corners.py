"""
Corner detection using ASC (Automated Segmentation based on Curvature).

Ported from lap-timing-system - uses 5-phase algorithm:
1. Peak Detection - place cuts at curvature peaks
2. Redundancy Reduction - merge close cuts
3. Straight Section Filling - add equidistant cuts in long sections
4. Curvature Sign Changes - add cuts at direction transitions
5. Final Filtering - remove remaining close cuts
"""

import math
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple

from .geometry import haversine_distance, calculate_curvature, cumulative_distances
from . import config


class Direction(Enum):
    LEFT = "left"
    RIGHT = "right"


@dataclass
class Corner:
    """A detected corner with rally-style classification."""

    entry_distance: float  # Distance from current position to entry
    apex_distance: float   # Distance to apex (tightest point)
    exit_distance: float   # Distance to exit
    apex_lat: float
    apex_lon: float
    direction: Direction   # Entry direction (or first turn direction for chicane)
    severity: int          # 1 (hairpin) to 6 (flat/slight)
    total_angle: float     # Total degrees turned
    min_radius: float      # Tightest radius in meters
    tightens: bool = False
    opens: bool = False
    long_corner: bool = False
    is_chicane: bool = False  # True if this is a merged chicane
    exit_direction: Optional[Direction] = None  # For chicanes: the second turn direction


# Rally pacenote severity scale based on minimum radius:
# 1 = hairpin (< 15m radius)
# 2 = very tight (15-30m)
# 3 = tight (30-50m)
# 4 = medium (50-80m)
# 5 = fast (80-120m)
# 6 = flat/slight (120-200m)
# 7 = kink (> 200m)

RADIUS_SEVERITY = [
    (15, 1),   # < 15m = hairpin
    (30, 2),   # 15-30m = very tight
    (50, 3),   # 30-50m = tight
    (80, 4),   # 50-80m = medium
    (120, 5),  # 80-120m = fast
    (200, 6),  # 120-200m = six
]


@dataclass
class Segment:
    """A segment of path between two cuts."""
    start_idx: int
    end_idx: int
    start_distance: float
    end_distance: float
    segment_type: str      # "corner" or "straight"
    avg_curvature: float
    max_curvature: float
    direction: Optional[str]


class CornerDetector:
    """
    Detect corners using ASC (Automated Segmentation based on Curvature).

    Based on VEHITS 2024 paper - segments path using a 5-phase algorithm
    that places "cuts" at key geometric features.
    """

    def __init__(
        self,
        curvature_peak_threshold: float = 0.005,  # 1/200m radius
        min_cut_distance: float = 15.0,           # Minimum meters between cuts
        straight_fill_distance: float = 100.0,    # Add cuts every N meters in straights
        min_corner_angle: float = config.CORNER_MIN_ANGLE_DEG,
        min_corner_radius: float = config.CORNER_MIN_RADIUS_M,
        merge_same_direction: bool = True,
        # Chicane detection parameters
        merge_chicanes: bool = True,
        max_chicane_gap: float = 30.0,            # Max gap between corners to merge as chicane
        max_chicane_length: float = 300.0         # Max total length of merged chicane
    ):
        self.curvature_peak_threshold = curvature_peak_threshold
        self.min_cut_distance = min_cut_distance
        self.straight_fill_distance = straight_fill_distance
        self.min_corner_angle = min_corner_angle
        self.min_corner_radius = min_corner_radius
        self.merge_same_direction = merge_same_direction
        self.merge_chicanes = merge_chicanes
        self.max_chicane_gap = max_chicane_gap
        self.max_chicane_length = max_chicane_length

    def detect_corners(
        self,
        points: List[Tuple[float, float]],
        start_distance: float = 0.0,
    ) -> List[Corner]:
        """
        Detect all corners in a path using ASC algorithm.

        Args:
            points: List of (lat, lon) points along the projected path
            start_distance: Distance offset for the first point

        Returns:
            List of detected corners with distances from path start
        """
        if len(points) < 5:
            return []

        # Calculate curvature at each point
        curvatures = self._calculate_curvatures(points)

        # Calculate cumulative distances
        distances = cumulative_distances(points)
        distances = [d + start_distance for d in distances]

        # ASC 5-phase algorithm
        cuts = self._phase1_peak_detection(curvatures)
        cuts = self._phase2_redundancy_reduction(cuts, distances)
        cuts = self._phase3_straight_filling(cuts, distances)
        cuts = self._phase4_sign_changes(cuts, distances, curvatures)
        cuts = self._phase5_final_filtering(cuts, distances)

        # Create segments from cuts
        segments = self._create_segments(cuts, points, curvatures, distances)

        # Optionally merge consecutive same-direction corner segments
        if self.merge_same_direction:
            segments = self._merge_corner_segments(segments, points, curvatures, distances)

        # Convert corner segments to Corner objects
        corners = self._segments_to_corners(segments, points, curvatures, distances)

        # Merge chicanes (consecutive opposite-direction corners) if enabled
        if self.merge_chicanes:
            corners = self._merge_chicanes(corners, points)

        return corners

    def _calculate_curvatures(
        self, points: List[Tuple[float, float]]
    ) -> List[float]:
        """Calculate curvature at each point using three-point method."""
        curvatures = [0.0]  # First point has no curvature

        for i in range(1, len(points) - 1):
            curv = calculate_curvature(points[i - 1], points[i], points[i + 1])
            curvatures.append(curv)

        curvatures.append(0.0)  # Last point has no curvature
        return curvatures

    def _phase1_peak_detection(self, curvatures: List[float]) -> List[int]:
        """
        Phase 1: Find curvature peaks and place cuts.

        A peak is a local maximum in |curvature| that exceeds the threshold.
        """
        cuts = []
        n = len(curvatures)

        for i in range(1, n - 1):
            curr = abs(curvatures[i])
            prev = abs(curvatures[i - 1])
            next_c = abs(curvatures[i + 1])

            # Local maximum above threshold
            if curr > prev and curr > next_c and curr > self.curvature_peak_threshold:
                cuts.append(i)

        return cuts

    def _phase2_redundancy_reduction(
        self, cuts: List[int], distances: List[float]
    ) -> List[int]:
        """
        Phase 2: Merge cuts that are too close together.
        """
        if len(cuts) <= 1:
            return cuts

        # Sort by distance
        cuts = sorted(cuts, key=lambda i: distances[i])

        merged = []
        i = 0

        while i < len(cuts):
            current = cuts[i]
            group = [current]
            j = i + 1

            while j < len(cuts):
                dist = abs(distances[cuts[j]] - distances[current])
                if dist < self.min_cut_distance:
                    group.append(cuts[j])
                    j += 1
                else:
                    break

            # Keep the middle cut in group
            merged.append(group[len(group) // 2])
            i = j

        return merged

    def _phase3_straight_filling(
        self, cuts: List[int], distances: List[float]
    ) -> List[int]:
        """
        Phase 3: Fill long cut-less sections with equidistant cuts.
        """
        if not distances:
            return cuts

        total_distance = distances[-1] - distances[0]

        if not cuts:
            # No cuts yet - create initial ones at regular intervals
            cuts = []
            num_cuts = int(total_distance / self.straight_fill_distance)

            for i in range(1, num_cuts):
                target_dist = distances[0] + i * self.straight_fill_distance
                idx = self._find_index_at_distance(distances, target_dist)
                if idx is not None:
                    cuts.append(idx)

            return sorted(cuts)

        # Sort existing cuts
        cuts = sorted(cuts, key=lambda i: distances[i])
        new_cuts = list(cuts)

        # Check gaps between consecutive cuts
        for i in range(len(cuts) - 1):
            start_dist = distances[cuts[i]]
            end_dist = distances[cuts[i + 1]]
            gap = end_dist - start_dist

            if gap > self.straight_fill_distance * 1.5:
                num_fills = max(1, int(gap / self.straight_fill_distance))
                for j in range(1, num_fills + 1):
                    target_dist = start_dist + j * (gap / (num_fills + 1))
                    idx = self._find_index_at_distance(distances, target_dist)
                    if idx is not None and idx not in new_cuts:
                        new_cuts.append(idx)

        # Check gap from start to first cut
        if cuts:
            first_dist = distances[cuts[0]]
            start_dist = distances[0]
            gap = first_dist - start_dist

            if gap > self.straight_fill_distance * 1.5:
                num_fills = max(1, int(gap / self.straight_fill_distance))
                for j in range(1, num_fills + 1):
                    target_dist = start_dist + j * (gap / (num_fills + 1))
                    idx = self._find_index_at_distance(distances, target_dist)
                    if idx is not None and idx not in new_cuts:
                        new_cuts.append(idx)

        # Check gap from last cut to end
        if cuts:
            last_dist = distances[cuts[-1]]
            end_dist = distances[-1]
            gap = end_dist - last_dist

            if gap > self.straight_fill_distance * 1.5:
                num_fills = max(1, int(gap / self.straight_fill_distance))
                for j in range(1, num_fills + 1):
                    target_dist = last_dist + j * (gap / (num_fills + 1))
                    idx = self._find_index_at_distance(distances, target_dist)
                    if idx is not None and idx not in new_cuts:
                        new_cuts.append(idx)

        return sorted(set(new_cuts))

    def _phase4_sign_changes(
        self, cuts: List[int], distances: List[float], curvatures: List[float]
    ) -> List[int]:
        """
        Phase 4: Add cuts where curvature sign changes.

        Sign changes indicate transitions between left and right turns.
        """
        cuts = sorted(cuts, key=lambda i: distances[i])
        new_cuts = list(cuts)

        for i in range(1, len(curvatures)):
            # Skip near-zero curvatures
            if abs(curvatures[i - 1]) < 0.001 or abs(curvatures[i]) < 0.001:
                continue

            prev_sign = 1 if curvatures[i - 1] > 0 else -1
            curr_sign = 1 if curvatures[i] > 0 else -1

            if prev_sign != curr_sign:
                dist = distances[i]

                # Find surrounding cuts
                prev_cut_dist = distances[0]
                next_cut_dist = distances[-1]

                for cut in cuts:
                    cut_dist = distances[cut]
                    if cut_dist < dist:
                        prev_cut_dist = max(prev_cut_dist, cut_dist)
                    else:
                        next_cut_dist = min(next_cut_dist, cut_dist)

                # Only add if not too close to existing cuts
                if (dist - prev_cut_dist > self.min_cut_distance and
                    next_cut_dist - dist > self.min_cut_distance):
                    if i not in new_cuts:
                        new_cuts.append(i)

        return sorted(set(new_cuts))

    def _phase5_final_filtering(
        self, cuts: List[int], distances: List[float]
    ) -> List[int]:
        """Phase 5: Final pass to remove any remaining close cuts."""
        return self._phase2_redundancy_reduction(cuts, distances)

    def _find_index_at_distance(
        self, distances: List[float], target_distance: float
    ) -> Optional[int]:
        """Find index closest to target distance."""
        best_idx = None
        best_diff = float('inf')

        for i, dist in enumerate(distances):
            diff = abs(dist - target_distance)
            if diff < best_diff:
                best_diff = diff
                best_idx = i

        return best_idx

    def _create_segments(
        self,
        cuts: List[int],
        points: List[Tuple[float, float]],
        curvatures: List[float],
        distances: List[float]
    ) -> List[Segment]:
        """Create segments from cut points."""
        if not cuts:
            return [self._analyze_segment(0, len(points) - 1, points, curvatures, distances)]

        cuts = sorted(cuts)
        segments = []

        # Segment from start to first cut
        if cuts[0] > 0:
            seg = self._analyze_segment(0, cuts[0], points, curvatures, distances)
            segments.append(seg)

        # Segments between cuts
        for i in range(len(cuts) - 1):
            seg = self._analyze_segment(cuts[i], cuts[i + 1], points, curvatures, distances)
            segments.append(seg)

        # Segment from last cut to end
        if cuts[-1] < len(points) - 1:
            seg = self._analyze_segment(cuts[-1], len(points) - 1, points, curvatures, distances)
            segments.append(seg)

        return segments

    def _analyze_segment(
        self,
        start_idx: int,
        end_idx: int,
        points: List[Tuple[float, float]],
        curvatures: List[float],
        distances: List[float]
    ) -> Segment:
        """Analyze a segment and classify as corner or straight."""
        if start_idx >= end_idx:
            end_idx = start_idx + 1
            if end_idx >= len(points):
                end_idx = len(points) - 1
                start_idx = end_idx - 1

        # Calculate segment properties
        segment_curvatures = curvatures[start_idx:end_idx + 1]

        if not segment_curvatures:
            avg_curvature = 0.0
            max_curvature = 0.0
        else:
            avg_curvature = sum(segment_curvatures) / len(segment_curvatures)
            max_curvature = max(abs(c) for c in segment_curvatures)

        # Calculate total angle
        total_angle = 0.0
        for i in range(start_idx, min(end_idx, len(distances) - 1)):
            arc_length = abs(distances[i + 1] - distances[i])
            total_angle += abs(curvatures[i]) * arc_length
        total_angle = math.degrees(total_angle)

        # Determine direction
        if avg_curvature > 0.001:
            direction = "left"
        elif avg_curvature < -0.001:
            direction = "right"
        else:
            direction = None

        # Classify as corner or straight
        min_radius = 1.0 / max_curvature if max_curvature > 0.0001 else float('inf')

        # For driving assistance, we're more lenient than racing:
        # - If angle is significant (>= min_corner_angle) AND radius is tight, it's a corner
        # - If radius is tight (< 250m) with any turn, warn (sparse OSM data has small angles)
        # - If radius is very tight (< 150m), warn even for tiny angles
        # - If angle is very significant (>= 30°), warn even for gentle radii
        is_tight_radius = min_radius <= self.min_corner_radius
        is_medium_tight = min_radius < 250
        is_very_tight = min_radius < 150
        is_significant_angle = total_angle >= self.min_corner_angle
        is_any_turn = total_angle >= 5  # At least 5° turn angle
        is_major_turn = total_angle >= 30

        if ((is_significant_angle and is_tight_radius) or
            (is_any_turn and is_medium_tight) or
            is_very_tight or
            is_major_turn):
            segment_type = "corner"
        else:
            segment_type = "straight"

        return Segment(
            start_idx=start_idx,
            end_idx=end_idx,
            start_distance=distances[start_idx],
            end_distance=distances[end_idx],
            segment_type=segment_type,
            avg_curvature=avg_curvature,
            max_curvature=max_curvature,
            direction=direction
        )

    def _merge_corner_segments(
        self,
        segments: List[Segment],
        points: List[Tuple[float, float]],
        curvatures: List[float],
        distances: List[float],
        max_straight_gap: float = 30.0,
        max_corner_span: float = 80.0
    ) -> List[Segment]:
        """Merge consecutive corner segments that turn the same direction."""
        if len(segments) <= 1:
            return segments

        merged = []
        i = 0

        while i < len(segments):
            current = segments[i]

            if current.segment_type != "corner":
                merged.append(current)
                i += 1
                continue

            # Try to merge with following segments
            merge_end_idx = current.end_idx

            j = i + 1
            while j < len(segments):
                next_seg = segments[j]

                if next_seg.segment_type == "corner":
                    if next_seg.direction == current.direction:
                        merge_end_idx = next_seg.end_idx
                        j += 1
                        continue
                    else:
                        break
                elif next_seg.segment_type == "straight":
                    straight_length = next_seg.end_distance - next_seg.start_distance

                    if straight_length <= max_straight_gap and j + 1 < len(segments):
                        following = segments[j + 1]
                        if (following.segment_type == "corner" and
                            following.direction == current.direction):
                            potential_span = following.end_distance - current.start_distance
                            if potential_span <= max_corner_span:
                                j += 1
                                continue
                    break
                else:
                    break

            # Create merged segment
            if j > i + 1:
                merged_seg = self._analyze_segment(
                    current.start_idx, merge_end_idx, points, curvatures, distances
                )
                merged_seg = Segment(
                    start_idx=merged_seg.start_idx,
                    end_idx=merged_seg.end_idx,
                    start_distance=merged_seg.start_distance,
                    end_distance=merged_seg.end_distance,
                    segment_type="corner",
                    avg_curvature=merged_seg.avg_curvature,
                    max_curvature=merged_seg.max_curvature,
                    direction=current.direction
                )
                merged.append(merged_seg)
            else:
                merged.append(current)

            i = j

        return merged

    def _segments_to_corners(
        self,
        segments: List[Segment],
        points: List[Tuple[float, float]],
        curvatures: List[float],
        distances: List[float]
    ) -> List[Corner]:
        """Convert corner segments to Corner objects."""
        corners = []

        for seg in segments:
            if seg.segment_type != "corner":
                continue

            # Find apex (max curvature point)
            apex_idx = seg.start_idx
            max_curv = 0.0
            for i in range(seg.start_idx, min(seg.end_idx + 1, len(curvatures))):
                if abs(curvatures[i]) > max_curv:
                    max_curv = abs(curvatures[i])
                    apex_idx = i

            # Calculate radii
            min_radius = 1.0 / max_curv if max_curv > 0 else float('inf')

            # Calculate total angle
            total_angle = 0.0
            for i in range(seg.start_idx, min(seg.end_idx, len(distances) - 1)):
                arc_length = abs(distances[i + 1] - distances[i])
                total_angle += abs(curvatures[i]) * arc_length
            total_angle = math.degrees(total_angle)

            # Direction (curvature sign is inverted for driving perspective)
            direction = Direction.RIGHT if seg.direction == "left" else Direction.LEFT

            # Severity
            severity = self._classify_severity(min_radius)

            # Check for tightening/opening
            segment_curvatures = curvatures[seg.start_idx:seg.end_idx + 1]
            tightens, opens = self._check_curvature_profile(segment_curvatures)

            # Long corner if > 50m
            corner_length = seg.end_distance - seg.start_distance
            long_corner = corner_length > 50

            corner = Corner(
                entry_distance=seg.start_distance,
                apex_distance=distances[apex_idx],
                exit_distance=seg.end_distance,
                apex_lat=points[apex_idx][0],
                apex_lon=points[apex_idx][1],
                direction=direction,
                severity=severity,
                total_angle=total_angle,
                min_radius=min_radius,
                tightens=tightens,
                opens=opens,
                long_corner=long_corner,
            )
            corners.append(corner)

        return corners

    def _classify_severity(self, radius: float) -> int:
        """Convert minimum radius to rally severity (1-7)."""
        for threshold, severity in RADIUS_SEVERITY:
            if radius < threshold:
                return severity
        return 7  # Kink - very gentle curve

    def _check_curvature_profile(
        self, curvatures: List[float]
    ) -> Tuple[bool, bool]:
        """
        Check if corner tightens or opens.

        Returns: (tightens, opens)
        """
        if len(curvatures) < 3:
            return False, False

        abs_curvatures = [abs(c) for c in curvatures]
        max_idx = abs_curvatures.index(max(abs_curvatures))

        entry_curv = sum(abs_curvatures[:max_idx]) / max(1, max_idx)
        exit_curv = sum(abs_curvatures[max_idx:]) / max(1, len(abs_curvatures) - max_idx)

        if entry_curv > 0 and exit_curv > 0:
            ratio = exit_curv / entry_curv
            if ratio > 1.5:
                return True, False
            elif ratio < 0.67:
                return False, True

        return False, False

    def _merge_chicanes(
        self,
        corners: List[Corner],
        points: List[Tuple[float, float]]
    ) -> List[Corner]:
        """
        Merge consecutive opposite-direction corners into chicanes.

        A chicane is defined as:
        - Two consecutive corners with opposite directions (left-right or right-left)
        - Gap between them is less than max_chicane_gap
        - Total span is less than max_chicane_length

        Args:
            corners: List of detected corners
            points: Path points for apex lookup

        Returns:
            List of corners with chicanes merged
        """
        if len(corners) < 2:
            return corners

        merged = []
        i = 0

        while i < len(corners):
            current = corners[i]

            # Check if we can merge with next corner as a chicane
            if i + 1 < len(corners):
                next_corner = corners[i + 1]

                # Check chicane conditions
                gap = next_corner.entry_distance - current.exit_distance
                total_length = next_corner.exit_distance - current.entry_distance
                opposite_directions = current.direction != next_corner.direction

                if (opposite_directions and
                    gap <= self.max_chicane_gap and
                    total_length <= self.max_chicane_length):

                    # Merge into chicane
                    # Use the tighter radius as min_radius
                    min_radius = min(current.min_radius, next_corner.min_radius)

                    # Total angle is sum of both corners
                    total_angle = current.total_angle + next_corner.total_angle

                    # Apex is the tighter of the two
                    if current.min_radius <= next_corner.min_radius:
                        apex_distance = current.apex_distance
                        apex_lat = current.apex_lat
                        apex_lon = current.apex_lon
                    else:
                        apex_distance = next_corner.apex_distance
                        apex_lat = next_corner.apex_lat
                        apex_lon = next_corner.apex_lon

                    # Severity based on the tighter corner
                    severity = self._classify_severity(min_radius)

                    # Long if total span > 50m
                    long_corner = total_length > 50

                    chicane = Corner(
                        entry_distance=current.entry_distance,
                        apex_distance=apex_distance,
                        exit_distance=next_corner.exit_distance,
                        apex_lat=apex_lat,
                        apex_lon=apex_lon,
                        direction=current.direction,  # Entry direction
                        severity=severity,
                        total_angle=total_angle,
                        min_radius=min_radius,
                        tightens=False,
                        opens=False,
                        long_corner=long_corner,
                        is_chicane=True,
                        exit_direction=next_corner.direction,  # Exit direction
                    )
                    merged.append(chicane)
                    i += 2  # Skip both corners
                    continue

            # No merge - add current corner as-is
            merged.append(current)
            i += 1

        return merged

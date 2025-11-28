"""Tests for corner detection."""

import pytest
from copepilot.corners import CornerDetector, Direction


class TestCornerDetector:
    def setup_method(self):
        self.detector = CornerDetector(min_radius=50.0, min_angle=15.0)

    def test_detects_right_turn(self):
        # Create a 90-degree right turn
        # Going north, then turning east
        points = [
            (51.0, 0.0),
            (51.0005, 0.0),
            (51.001, 0.0),
            (51.0015, 0.0),
            (51.002, 0.0),      # Start of turn
            (51.0022, 0.0002),
            (51.0023, 0.0005),  # Apex
            (51.0023, 0.0008),
            (51.0023, 0.0012),
            (51.0023, 0.0016),  # End of turn, now going east
        ]
        corners = self.detector.detect_corners(points)

        assert len(corners) >= 1
        # Find the main corner
        main_corner = max(corners, key=lambda c: c.total_angle)
        assert main_corner.direction == Direction.RIGHT

    def test_detects_left_turn(self):
        # Create a left turn
        points = [
            (51.0, 0.0),
            (51.0005, 0.0),
            (51.001, 0.0),
            (51.0015, 0.0),
            (51.002, 0.0),
            (51.0022, -0.0002),
            (51.0023, -0.0005),
            (51.0023, -0.0008),
            (51.0023, -0.0012),
            (51.0023, -0.0016),
        ]
        corners = self.detector.detect_corners(points)

        assert len(corners) >= 1
        main_corner = max(corners, key=lambda c: c.total_angle)
        assert main_corner.direction == Direction.LEFT

    def test_ignores_straight_road(self):
        # Straight road with no turns
        points = [
            (51.0 + i * 0.0005, 0.0)
            for i in range(10)
        ]
        corners = self.detector.detect_corners(points)

        assert len(corners) == 0

    def test_severity_hairpin(self):
        # Very tight hairpin (< 15m radius)
        assert self.detector._classify_severity(10) == 1

    def test_severity_medium(self):
        # Medium corner (50-80m radius)
        assert self.detector._classify_severity(65) == 4

    def test_severity_flat(self):
        # Very gentle curve (> 120m radius)
        assert self.detector._classify_severity(150) == 6

    def test_corner_distances(self):
        # Check that corners have correct distance from start
        points = [
            (51.0, 0.0),
            (51.0005, 0.0),
            (51.001, 0.0),
            (51.0015, 0.0),
            (51.002, 0.0),
            (51.0022, 0.0002),
            (51.0023, 0.0005),
            (51.0023, 0.0008),
            (51.0023, 0.0012),
            (51.0023, 0.0016),
        ]
        corners = self.detector.detect_corners(points, start_distance=100.0)

        if corners:
            # All distances should be >= start_distance
            for corner in corners:
                assert corner.entry_distance >= 100.0
                assert corner.apex_distance >= corner.entry_distance
                assert corner.exit_distance >= corner.apex_distance

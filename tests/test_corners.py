"""Tests for corner detection."""

import pytest
from copepilot.corners import CornerDetector, Direction


class TestCornerDetector:
    def setup_method(self):
        self.detector = CornerDetector(min_angle=10.0)

    def test_detects_right_turn(self):
        # Straight road then 90° right turn
        points = [
            (51.0, 0.0),
            (51.001, 0.0),
            (51.001, 0.001),  # Turn point
        ]
        corners = self.detector.detect_corners(points, (51.0, 0.0))

        assert len(corners) == 1
        assert corners[0].direction == Direction.RIGHT

    def test_detects_left_turn(self):
        # Straight road then 90° left turn
        points = [
            (51.0, 0.0),
            (51.001, 0.0),
            (51.001, -0.001),  # Turn point
        ]
        corners = self.detector.detect_corners(points, (51.0, 0.0))

        assert len(corners) == 1
        assert corners[0].direction == Direction.LEFT

    def test_ignores_slight_curves(self):
        # Very slight bend, below threshold
        points = [
            (51.0, 0.0),
            (51.001, 0.0),
            (51.002, 0.00001),
        ]
        corners = self.detector.detect_corners(points, (51.0, 0.0))

        assert len(corners) == 0

    def test_severity_classification(self):
        # Test hairpin classification
        assert self.detector._classify_severity(160) == 1
        # Test tight corner
        assert self.detector._classify_severity(90) == 3
        # Test fast corner
        assert self.detector._classify_severity(35) == 5

    def test_haversine_distance(self):
        # ~111km per degree at equator
        p1 = (0.0, 0.0)
        p2 = (0.0, 1.0)
        distance = self.detector._haversine_distance(p1, p2)

        assert 110000 < distance < 112000

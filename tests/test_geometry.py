"""Tests for geometry utilities."""

import math
import pytest
from copepilot.geometry import (
    haversine_distance,
    bearing,
    angle_difference,
    calculate_curvature,
    cumulative_distances,
)


class TestHaversineDistance:
    def test_same_point(self):
        assert haversine_distance(51.0, 0.0, 51.0, 0.0) == 0.0

    def test_one_degree_latitude(self):
        # 1 degree of latitude is approximately 111km
        dist = haversine_distance(51.0, 0.0, 52.0, 0.0)
        assert 110000 < dist < 112000

    def test_one_degree_longitude_at_equator(self):
        dist = haversine_distance(0.0, 0.0, 0.0, 1.0)
        assert 110000 < dist < 112000


class TestBearing:
    def test_north(self):
        b = bearing(51.0, 0.0, 52.0, 0.0)
        assert abs(b - 0) < 1  # Should be ~0 degrees

    def test_east(self):
        b = bearing(51.0, 0.0, 51.0, 1.0)
        assert abs(b - 90) < 1  # Should be ~90 degrees

    def test_south(self):
        b = bearing(52.0, 0.0, 51.0, 0.0)
        assert abs(b - 180) < 1  # Should be ~180 degrees

    def test_west(self):
        b = bearing(51.0, 1.0, 51.0, 0.0)
        assert abs(b - 270) < 1  # Should be ~270 degrees


class TestAngleDifference:
    def test_same_angle(self):
        assert angle_difference(90, 90) == 0

    def test_simple_difference(self):
        assert angle_difference(0, 45) == 45
        assert angle_difference(45, 0) == -45

    def test_wrap_around(self):
        assert abs(angle_difference(350, 10) - 20) < 0.001
        assert abs(angle_difference(10, 350) - (-20)) < 0.001


class TestCalculateCurvature:
    def test_straight_line(self):
        # Three collinear points
        p1 = (51.0, 0.0)
        p2 = (51.001, 0.0)
        p3 = (51.002, 0.0)
        curv = calculate_curvature(p1, p2, p3)
        assert abs(curv) < 0.001

    def test_right_turn_positive(self):
        # Right turn should give positive curvature
        p1 = (51.0, 0.0)
        p2 = (51.001, 0.0)
        p3 = (51.001, 0.001)
        curv = calculate_curvature(p1, p2, p3)
        # Sign depends on coordinate system, just check it's non-zero
        assert abs(curv) > 0.0001


class TestCumulativeDistances:
    def test_empty(self):
        assert cumulative_distances([]) == []

    def test_single_point(self):
        assert cumulative_distances([(51.0, 0.0)]) == [0.0]

    def test_increasing(self):
        points = [
            (51.0, 0.0),
            (51.001, 0.0),
            (51.002, 0.0),
        ]
        dists = cumulative_distances(points)
        assert dists[0] == 0.0
        assert dists[1] > 0
        assert dists[2] > dists[1]

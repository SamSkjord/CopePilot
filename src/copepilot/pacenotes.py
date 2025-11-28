"""Generate rally-style pacenote callouts from corners."""

from dataclasses import dataclass
from typing import List, Optional
from .corners import Corner, Direction


@dataclass
class Pacenote:
    """A spoken pacenote callout."""

    text: str
    distance_m: float
    priority: int  # 1 = most urgent


class PacenoteGenerator:
    """Generates rally-style pacenote text from detected corners."""

    # Distance callouts
    DISTANCE_CALLS = [
        (300, "three hundred"),
        (200, "two hundred"),
        (150, "one fifty"),
        (100, "one hundred"),
        (50, "fifty"),
        (30, "thirty"),
    ]

    # Severity names (index = severity number)
    SEVERITY_NAMES = [
        "",  # 0 - unused
        "hairpin",
        "two",
        "three",
        "four",
        "five",
        "six",
    ]

    def __init__(self, distance_threshold_m: float = 500):
        self.distance_threshold = distance_threshold_m
        self._last_called: dict = {}  # Track what we've called

    def generate(self, corners: List[Corner]) -> List[Pacenote]:
        """Generate pacenotes for upcoming corners."""
        notes = []

        # Filter to corners within threshold, sorted by distance
        upcoming = sorted(
            [c for c in corners if c.distance_m <= self.distance_threshold],
            key=lambda c: c.distance_m,
        )

        for corner in upcoming:
            note = self._corner_to_note(corner)
            if note:
                notes.append(note)

        return notes

    def _corner_to_note(self, corner: Corner) -> Optional[Pacenote]:
        """Convert a corner to a pacenote."""
        direction = corner.direction.value
        severity = self.SEVERITY_NAMES[corner.severity]

        # Build the callout
        parts = []

        # Distance (if far enough)
        distance_call = self._get_distance_call(corner.distance_m)
        if distance_call:
            parts.append(distance_call)

        # Main call: "left three" or "hairpin right"
        if corner.severity == 1:
            parts.append(f"{severity} {direction}")
        else:
            parts.append(f"{direction} {severity}")

        # Modifiers
        if corner.tightens:
            parts.append("tightens")
        if corner.opens:
            parts.append("opens")
        if corner.long_corner:
            parts.append("long")

        text = " ".join(parts)
        priority = self._calculate_priority(corner)

        return Pacenote(text=text, distance_m=corner.distance_m, priority=priority)

    def _get_distance_call(self, distance_m: float) -> Optional[str]:
        """Get distance callout if appropriate."""
        for threshold, call in self.DISTANCE_CALLS:
            if distance_m >= threshold - 20 and distance_m <= threshold + 20:
                return call
        return None

    def _calculate_priority(self, corner: Corner) -> int:
        """Calculate priority based on severity and distance."""
        # Tighter corners and closer corners are higher priority
        distance_factor = max(1, int(corner.distance_m / 100))
        return corner.severity * distance_factor

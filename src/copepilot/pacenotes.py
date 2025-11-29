"""Generate rally-style pacenote callouts from corners and junctions."""

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Set

from .corners import Corner, Direction
from .path_projector import JunctionInfo
from . import config


class NoteType(Enum):
    CORNER = "corner"
    JUNCTION = "junction"
    CAUTION = "caution"


@dataclass
class Pacenote:
    """A spoken pacenote callout."""

    text: str
    distance_m: float
    note_type: NoteType
    priority: int  # 1 = most urgent


class PacenoteGenerator:
    """Generates rally-style pacenote text from corners and junctions."""

    # Distance callouts
    DISTANCE_CALLS = [
        (400, "four hundred"),
        (300, "three hundred"),
        (200, "two hundred"),
        (150, "one fifty"),
        (100, "one hundred"),
        (80, "eighty"),
        (50, "fifty"),
        (30, "thirty"),
    ]

    # Severity names (index = severity number)
    SEVERITY_NAMES = [
        "",        # 0 - unused
        "hairpin",
        "two",
        "three",
        "four",
        "five",
        "six",
        "kink",
    ]

    def __init__(
        self,
        distance_threshold_m: float = config.LOOKAHEAD_DISTANCE_M,
        junction_warn_distance: float = config.JUNCTION_WARN_DISTANCE_M,
    ):
        self.distance_threshold = distance_threshold_m
        self.junction_warn_distance = junction_warn_distance
        self._called: Set[str] = set()

    def generate(
        self,
        corners: List[Corner],
        junctions: List[JunctionInfo],
    ) -> List[Pacenote]:
        """Generate pacenotes for upcoming corners and junctions."""
        notes = []

        # Process corners
        for corner in corners:
            if corner.entry_distance <= self.distance_threshold:
                note = self._corner_to_note(corner)
                if note:
                    notes.append(note)

        # Process junctions (warn if no straight-on option)
        for junction in junctions:
            if junction.distance_m <= self.junction_warn_distance:
                # Warn about junctions where driver must make a choice
                if junction.straight_on_bearing is None:
                    note = self._junction_to_note(junction)
                    if note:
                        notes.append(note)

        # Sort by distance
        notes.sort(key=lambda n: n.distance_m)

        return notes

    def should_call(self, note: Pacenote) -> bool:
        """
        Check if this note should be called (hasn't been called recently).

        Uses a key based on distance bucket to prevent repeat calls.
        """
        # Create key: round distance to 50m buckets
        bucket = int(note.distance_m / 50) * 50
        key = f"{note.text}_{bucket}"

        if key in self._called:
            return False

        self._called.add(key)
        return True

    def clear_called(self) -> None:
        """Clear the set of called notes (e.g., after significant movement)."""
        if len(self._called) > 100:
            self._called.clear()

    def _corner_to_note(self, corner: Corner) -> Optional[Pacenote]:
        """Convert a corner to a pacenote."""
        parts = []

        # Distance callout
        distance_call = self._get_distance_call(corner.entry_distance)
        if distance_call:
            parts.append(distance_call)

        if corner.is_chicane and corner.exit_direction:
            # Chicane: "chicane left right" or "chicane right left"
            entry_dir = corner.direction.value
            exit_dir = corner.exit_direction.value
            parts.append(f"chicane {entry_dir} {exit_dir}")
        else:
            # Regular corner
            direction = corner.direction.value
            severity = self.SEVERITY_NAMES[corner.severity]

            # Main call: "left three" or "hairpin right" or "kink left"
            if corner.severity == 1 or corner.severity == 7:
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

        return Pacenote(
            text=text,
            distance_m=corner.entry_distance,
            note_type=NoteType.CORNER,
            priority=priority,
        )

    def _junction_to_note(self, junction: JunctionInfo) -> Optional[Pacenote]:
        """Convert a junction to a warning pacenote."""
        parts = []

        # Distance
        distance_call = self._get_distance_call(junction.distance_m)
        if distance_call:
            parts.append(distance_call)

        # Warning - road ends, must turn
        parts.append("caution")

        text = " ".join(parts)

        return Pacenote(
            text=text,
            distance_m=junction.distance_m,
            note_type=NoteType.JUNCTION,
            priority=1,  # High priority - driver must act
        )

    def _get_distance_call(self, distance_m: float) -> Optional[str]:
        """Get distance callout for the given distance."""
        for threshold, call in self.DISTANCE_CALLS:
            if distance_m >= threshold - 25 and distance_m <= threshold + 25:
                return call
        return None

    def _calculate_priority(self, corner: Corner) -> int:
        """
        Calculate priority based on severity and distance.

        Lower number = higher priority.
        """
        # Tighter corners are higher priority
        severity_factor = corner.severity

        # Closer corners are higher priority
        distance_factor = max(1, int(corner.entry_distance / 100))

        return severity_factor + distance_factor

"""Generate rally-style pacenote callouts from corners and junctions."""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

from .corners import Corner, Direction
from .path_projector import JunctionInfo, BridgeInfo
from . import config


class NoteType(Enum):
    CORNER = "corner"
    JUNCTION = "junction"
    CAUTION = "caution"
    BRIDGE = "bridge"


@dataclass
class Pacenote:
    """A spoken pacenote callout."""

    text: str
    distance_m: float
    note_type: NoteType
    priority: int  # 1 = most urgent
    unique_key: str = ""  # For deduplication (based on location, not distance)


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
        "",  # 0 - unused
        "hairpin",
        "two",
        "three",
        "four",
        "five",
        "six",
        "flat",
    ]

    def __init__(
        self,
        distance_threshold_m: float = config.LOOKAHEAD_DISTANCE_M,
        junction_warn_distance: float = config.JUNCTION_WARN_DISTANCE_M,
        callout_distance_m: float = 100,  # Only call corners within this distance
    ):
        self.distance_threshold = distance_threshold_m
        self.junction_warn_distance = junction_warn_distance
        self.callout_distance = callout_distance_m
        self._called: Set[str] = set()
        # Cache corner classifications by position to prevent reclassification
        self._corner_cache: Dict[str, str] = {}  # position_key -> callout_text

    # Distance threshold for merging adjacent notes (meters)
    MERGE_DISTANCE_M = 50

    def generate(
        self,
        corners: List[Corner],
        junctions: List[JunctionInfo],
        bridges: Optional[List[BridgeInfo]] = None,
    ) -> List[Pacenote]:
        """Generate pacenotes for upcoming corners, junctions, and bridges."""
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

        # Process bridges
        if bridges:
            for bridge in bridges:
                if bridge.distance_m <= self.distance_threshold:
                    note = self._bridge_to_note(bridge)
                    if note:
                        notes.append(note)

        # Sort by distance
        notes.sort(key=lambda n: n.distance_m)

        # Merge adjacent notes that are within MERGE_DISTANCE_M of each other
        notes = self._merge_adjacent_notes(notes)

        return notes

    def _merge_adjacent_notes(self, notes: List[Pacenote]) -> List[Pacenote]:
        """
        Merge notes that are close together into single "into" chained notes.

        e.g., "over bridge" at 50m and "hairpin left" at 60m become
        "over bridge into hairpin left" at 50m.
        """
        if len(notes) < 2:
            return notes

        merged = []
        i = 0

        while i < len(notes):
            current = notes[i]
            chain_texts = [self._strip_distance(current.text)]
            chain_keys = [current.unique_key]
            best_priority = current.priority

            # Look ahead for notes within merge distance
            j = i + 1
            while j < len(notes):
                next_note = notes[j]
                if next_note.distance_m - current.distance_m <= self.MERGE_DISTANCE_M:
                    chain_texts.append(self._strip_distance(next_note.text))
                    chain_keys.append(next_note.unique_key)
                    best_priority = min(best_priority, next_note.priority)
                    j += 1
                else:
                    break

            # Create merged note
            if len(chain_texts) > 1:
                # Get distance prefix from first note
                distance_call = self._get_distance_call(current.distance_m)
                if distance_call:
                    merged_text = f"{distance_call} " + " into ".join(chain_texts)
                else:
                    merged_text = " into ".join(chain_texts)

                merged.append(Pacenote(
                    text=merged_text,
                    distance_m=current.distance_m,
                    note_type=current.note_type,
                    priority=best_priority,
                    unique_key="|".join(chain_keys),
                ))
            else:
                merged.append(current)

            i = j

        return merged

    def _strip_distance(self, text: str) -> str:
        """Remove distance prefix from pacenote text."""
        for _, call in self.DISTANCE_CALLS:
            if text.startswith(call + " "):
                return text[len(call) + 1:]
        return text

    # Minimum distance to call a corner (avoid calling corners we're already in)
    MIN_CALLOUT_DISTANCE_M = 20

    def should_call(self, note: Pacenote) -> Tuple[bool, Optional[Pacenote]]:
        """
        Check if this note should be called now.

        Only calls notes within callout_distance_m and beyond min distance.
        Uses deduplication to prevent repeat calls for the same corner.

        Returns: (should_call, filtered_note) where filtered_note may have
        already-called components removed from merged notes.
        """
        # Only call notes within callout distance
        if note.distance_m > self.callout_distance:
            return False, None

        # Don't call notes we're already on top of
        if note.distance_m < self.MIN_CALLOUT_DISTANCE_M:
            return False, None

        # Use unique_key for deduplication (based on position, not text)
        key = note.unique_key or note.text

        # For merged notes (key contains "|"), filter out already-called components
        if "|" in key:
            component_keys = key.split("|")

            # Extract distance prefix from text if present
            text = note.text
            distance_prefix = ""
            for _, call in self.DISTANCE_CALLS:
                if text.startswith(call + " "):
                    distance_prefix = call + " "
                    text = text[len(distance_prefix):]
                    break

            text_parts = text.split(" into ")

            # Filter to only uncalled components
            new_keys = []
            new_texts = []
            for k, t in zip(component_keys, text_parts):
                if k not in self._called:
                    new_keys.append(k)
                    new_texts.append(t)

            if not new_keys:
                return False, None

            # Mark new components as called
            for k in new_keys:
                self._called.add(k)

            # If we filtered some out, return modified note
            if len(new_keys) < len(component_keys):
                # Re-add distance prefix to filtered text
                filtered_text = " into ".join(new_texts)
                # Add new distance prefix based on current distance
                new_distance = self._get_distance_call(note.distance_m)
                if new_distance:
                    filtered_text = f"{new_distance} {filtered_text}"
                filtered_key = "|".join(new_keys)
                return True, Pacenote(
                    text=filtered_text,
                    distance_m=note.distance_m,
                    note_type=note.note_type,
                    priority=note.priority,
                    unique_key=filtered_key,
                )

            return True, note

        # Simple (non-merged) note
        if key in self._called:
            return False, None

        self._called.add(key)
        return True, note

    def clear_called(self) -> None:
        """Clear the set of called notes (e.g., after significant movement)."""
        if len(self._called) > 100:
            self._called.clear()
            self._corner_cache.clear()

    def _corner_to_note(self, corner: Corner) -> Optional[Pacenote]:
        """Convert a corner to a pacenote."""
        # Use apex position as unique key (doesn't change as car approaches)
        # Round to 4 decimal places (~11m) for stability across re-detections
        unique_key = f"{corner.apex_lat:.4f},{corner.apex_lon:.4f}"

        # Check cache for existing classification
        cached_text = self._corner_cache.get(unique_key)

        if cached_text:
            # Use cached classification, just update distance
            distance_call = self._get_distance_call(corner.entry_distance)
            if distance_call:
                text = f"{distance_call} {cached_text}"
            else:
                text = cached_text
        else:
            # Generate new classification
            parts = []

            if corner.is_chicane and corner.exit_direction:
                # Chicane: "chicane left right" or "chicane right left"
                entry_dir = corner.direction.value
                exit_dir = corner.exit_direction.value
                parts.append(f"chicane {entry_dir} {exit_dir}")
            else:
                # Regular corner
                direction = corner.direction.value
                severity = self.SEVERITY_NAMES[corner.severity]

                # Check for square corner: tight radius but ~90° angle (not hairpin's ~180°)
                is_square = (
                    corner.severity <= 2 and
                    60 <= abs(corner.total_angle) <= 120
                )

                if is_square:
                    # Square corner: "square left" or "square right"
                    parts.append(f"square {direction}")
                elif corner.severity == 1 or corner.severity == 7:
                    # Hairpin or flat: "hairpin right" or "flat left"
                    parts.append(f"{severity} {direction}")
                else:
                    # Regular: "left three"
                    parts.append(f"{direction} {severity}")

                # Modifiers
                if corner.tightens:
                    parts.append("tightens")
                if corner.opens:
                    parts.append("opens")
                if corner.long_corner:
                    parts.append("long")

            # Cache the classification (without distance)
            cached_text = " ".join(parts)
            self._corner_cache[unique_key] = cached_text

            # Add distance for output
            distance_call = self._get_distance_call(corner.entry_distance)
            if distance_call:
                text = f"{distance_call} {cached_text}"
            else:
                text = cached_text

        priority = self._calculate_priority(corner)

        return Pacenote(
            text=text,
            distance_m=corner.entry_distance,
            note_type=NoteType.CORNER,
            priority=priority,
            unique_key=unique_key,
        )

    def _junction_to_note(self, junction: JunctionInfo) -> Optional[Pacenote]:
        """Convert a junction to a warning pacenote."""
        parts = []

        # Distance
        distance_call = self._get_distance_call(junction.distance_m)
        if distance_call:
            parts.append(distance_call)

        # Warning - road ends, must turn
        parts.append("junction")

        text = " ".join(parts)

        # Use junction position as unique key
        unique_key = f"{junction.node_id}"

        return Pacenote(
            text=text,
            distance_m=junction.distance_m,
            note_type=NoteType.JUNCTION,
            priority=1,  # High priority - driver must act
            unique_key=unique_key,
        )

    def _bridge_to_note(self, bridge: BridgeInfo) -> Optional[Pacenote]:
        """Convert a bridge to a pacenote."""
        parts = []

        # Distance
        distance_call = self._get_distance_call(bridge.distance_m)
        if distance_call:
            parts.append(distance_call)

        parts.append("over bridge")

        text = " ".join(parts)

        # Use bridge way ID as unique key
        unique_key = f"bridge_{bridge.way_id}"

        return Pacenote(
            text=text,
            distance_m=bridge.distance_m,
            note_type=NoteType.BRIDGE,
            priority=5,  # Lower priority - informational
            unique_key=unique_key,
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

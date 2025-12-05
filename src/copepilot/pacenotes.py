"""Generate rally-style pacenote callouts from corners and junctions."""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

from .corners import Corner, Direction
from .path_projector import (
    JunctionInfo, BridgeInfo, TunnelInfo, RailwayCrossingInfo,
    FordInfo, SpeedBumpInfo, SurfaceChangeInfo
)
from . import config


class NoteType(Enum):
    CORNER = "corner"
    JUNCTION = "junction"
    CAUTION = "caution"
    BRIDGE = "bridge"
    TUNNEL = "tunnel"
    RAILWAY = "railway"
    FORD = "ford"
    SPEED_BUMP = "speed_bump"
    SURFACE = "surface"


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

    # Distance callouts (distance_m, spoken_text)
    DISTANCE_CALLS = [
        (1000, "one thousand"),
        (500, "five hundred"),
        (400, "four hundred"),
        (300, "three hundred"),
        (200, "two hundred"),
        (150, "one fifty"),
        (100, "one hundred"),
        (80, "eighty"),
        (50, "fifty"),
        (30, "thirty"),
    ]

    # Distance brackets for multi-callout features (called at each bracket)
    MULTI_CALLOUT_DISTANCES = [500, 300, 100]  # Hazards: far, medium, close
    CORNER_CALLOUT_DISTANCES = [1000, 500, 100]  # Corners: very far, far, close

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
        tunnels: Optional[List[TunnelInfo]] = None,
        railway_crossings: Optional[List[RailwayCrossingInfo]] = None,
        fords: Optional[List[FordInfo]] = None,
        speed_bumps: Optional[List[SpeedBumpInfo]] = None,
        surface_changes: Optional[List[SurfaceChangeInfo]] = None,
    ) -> List[Pacenote]:
        """Generate pacenotes for upcoming corners, junctions, and road features."""
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

        # Process tunnels
        if tunnels:
            for tunnel in tunnels:
                if tunnel.distance_m <= self.distance_threshold:
                    note = self._tunnel_to_note(tunnel)
                    if note:
                        notes.append(note)

        # Process railway crossings
        if railway_crossings:
            for crossing in railway_crossings:
                if crossing.distance_m <= self.distance_threshold:
                    note = self._railway_to_note(crossing)
                    if note:
                        notes.append(note)

        # Process fords
        if fords:
            for ford in fords:
                if ford.distance_m <= self.distance_threshold:
                    note = self._ford_to_note(ford)
                    if note:
                        notes.append(note)

        # Process speed bumps
        if speed_bumps:
            for bump in speed_bumps:
                if bump.distance_m <= self.distance_threshold:
                    note = self._speed_bump_to_note(bump)
                    if note:
                        notes.append(note)

        # Process surface changes
        if surface_changes:
            for change in surface_changes:
                if change.distance_m <= self.distance_threshold:
                    note = self._surface_change_to_note(change)
                    if note:
                        notes.append(note)

        # Sort by distance
        notes.sort(key=lambda n: n.distance_m)

        # Filter out long-distance corner callouts if there's something between us and the corner
        # (except for things within merge distance which will be combined with "into")
        notes = self._filter_blocked_corners(notes)

        # Merge adjacent notes that are within MERGE_DISTANCE_M of each other
        notes = self._merge_adjacent_notes(notes)

        return notes

    def _filter_blocked_corners(self, notes: List[Pacenote]) -> List[Pacenote]:
        """
        Remove long-distance corner callouts if there's something between us and the corner.

        Corners at 1000m or 500m brackets should only be called if they're the first thing
        ahead, or if everything before them is within merge distance (will be "into" chained).
        """
        if len(notes) < 2:
            return notes

        filtered = []
        for i, note in enumerate(notes):
            # Only filter corners at long-distance brackets (1000 or 500)
            if note.note_type != NoteType.CORNER:
                filtered.append(note)
                continue

            # Check if this is a long-distance bracket (key ends with _1000 or _500)
            if not (note.unique_key.endswith("_1000") or note.unique_key.endswith("_500")):
                filtered.append(note)
                continue

            # Check if there's anything closer that's outside merge distance
            blocked = False
            for j in range(i):  # All notes before this one (closer to us)
                closer_note = notes[j]
                distance_gap = note.distance_m - closer_note.distance_m
                if distance_gap > self.MERGE_DISTANCE_M:
                    # Something is between us and this corner, outside merge range
                    blocked = True
                    break

            if not blocked:
                filtered.append(note)

        return filtered

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

    # Note types that use multi-callout (called at multiple distance brackets)
    # Hazards: 500/300/100m, Corners: 1000/500/100m
    MULTI_CALLOUT_TYPES = {
        NoteType.CORNER,  # 1000/500/100m
        NoteType.TUNNEL, NoteType.RAILWAY, NoteType.FORD,
        NoteType.SPEED_BUMP, NoteType.SURFACE,  # 500/300/100m
    }

    def should_call(self, note: Pacenote) -> Tuple[bool, Optional[Pacenote]]:
        """
        Check if this note should be called now.

        Only calls notes within callout_distance_m and beyond min distance.
        Uses deduplication to prevent repeat calls for the same corner.

        Returns: (should_call, filtered_note) where filtered_note may have
        already-called components removed from merged notes.
        """
        # Multi-callout types can be called at longer distances
        # Corners: up to 1025m, Hazards: up to 525m, Others: callout_distance (100m)
        if note.note_type == NoteType.CORNER:
            max_distance = 1025
        elif note.note_type in self.MULTI_CALLOUT_TYPES:
            max_distance = 525
        else:
            max_distance = self.callout_distance
        if note.distance_m > max_distance:
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
        # Multi-callout: get distance bracket (1000, 500, or 100)
        bracket = self._get_corner_distance_bracket(corner.entry_distance)
        if bracket is None:
            return None

        # Use apex position as cache key for corner classification
        # Round to 4 decimal places (~11m) for stability across re-detections
        position_key = f"{corner.apex_lat:.4f},{corner.apex_lon:.4f}"

        # Unique key includes bracket for multi-callout deduplication
        unique_key = f"{position_key}_{bracket}"

        # Check cache for existing classification
        cached_text = self._corner_cache.get(position_key)

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
            self._corner_cache[position_key] = cached_text

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

    def _tunnel_to_note(self, tunnel: TunnelInfo) -> Optional[Pacenote]:
        """Convert a tunnel to a pacenote."""
        # Multi-callout: include distance bracket in unique key
        bracket = self._get_distance_bracket(tunnel.distance_m)
        if bracket is None:
            return None

        parts = []

        distance_call = self._get_distance_call(tunnel.distance_m)
        if distance_call:
            parts.append(distance_call)

        parts.append("tunnel")

        text = " ".join(parts)
        unique_key = f"tunnel_{tunnel.way_id}_{bracket}"

        return Pacenote(
            text=text,
            distance_m=tunnel.distance_m,
            note_type=NoteType.TUNNEL,
            priority=4,  # Informational
            unique_key=unique_key,
        )

    def _railway_to_note(self, crossing: RailwayCrossingInfo) -> Optional[Pacenote]:
        """Convert a railway crossing to a pacenote."""
        # Multi-callout: include distance bracket in unique key
        bracket = self._get_distance_bracket(crossing.distance_m)
        if bracket is None:
            return None

        parts = []

        distance_call = self._get_distance_call(crossing.distance_m)
        if distance_call:
            parts.append(distance_call)

        parts.append("over rails")

        text = " ".join(parts)
        unique_key = f"railway_{crossing.node_id}_{bracket}"

        return Pacenote(
            text=text,
            distance_m=crossing.distance_m,
            note_type=NoteType.RAILWAY,
            priority=3,  # Safety - need to slow down
            unique_key=unique_key,
        )

    def _ford_to_note(self, ford: FordInfo) -> Optional[Pacenote]:
        """Convert a ford (water crossing) to a pacenote."""
        # Multi-callout: include distance bracket in unique key
        bracket = self._get_distance_bracket(ford.distance_m)
        if bracket is None:
            return None

        parts = []

        distance_call = self._get_distance_call(ford.distance_m)
        if distance_call:
            parts.append(distance_call)

        parts.append("water")

        text = " ".join(parts)
        unique_key = f"ford_{ford.way_id}_{bracket}"

        return Pacenote(
            text=text,
            distance_m=ford.distance_m,
            note_type=NoteType.FORD,
            priority=3,  # Safety - need to slow down
            unique_key=unique_key,
        )

    def _speed_bump_to_note(self, bump: SpeedBumpInfo) -> Optional[Pacenote]:
        """Convert a speed bump to a pacenote."""
        # Multi-callout: include distance bracket in unique key
        bracket = self._get_distance_bracket(bump.distance_m)
        if bracket is None:
            return None

        parts = []

        distance_call = self._get_distance_call(bump.distance_m)
        if distance_call:
            parts.append(distance_call)

        # Use "bump" for single bumps, "bumps" for tables/humps (often multiple)
        if bump.bump_type in ("table", "hump"):
            parts.append("bumps")
        else:
            parts.append("bump")

        text = " ".join(parts)
        unique_key = f"bump_{bump.way_id}_{bracket}"

        return Pacenote(
            text=text,
            distance_m=bump.distance_m,
            note_type=NoteType.SPEED_BUMP,
            priority=4,  # Need to slow down
            unique_key=unique_key,
        )

    # Surface type mappings for callouts
    SURFACE_CALLOUTS = {
        "asphalt": "tarmac",
        "paved": "tarmac",
        "concrete": "concrete",
        "gravel": "gravel",
        "unpaved": "gravel",
        "dirt": "gravel",
        "ground": "gravel",
        "grass": "gravel",
        "sand": "gravel",
        "mud": "gravel",
    }

    def _surface_change_to_note(self, change: SurfaceChangeInfo) -> Optional[Pacenote]:
        """Convert a surface change to a pacenote."""
        # Multi-callout: include distance bracket in unique key
        bracket = self._get_distance_bracket(change.distance_m)
        if bracket is None:
            return None

        # Map surface types to callout words
        to_surface = self.SURFACE_CALLOUTS.get(change.to_surface, "")
        if not to_surface:
            return None  # Unknown surface type, skip

        parts = []

        distance_call = self._get_distance_call(change.distance_m)
        if distance_call:
            parts.append(distance_call)

        parts.append(f"onto {to_surface}")

        text = " ".join(parts)
        unique_key = f"surface_{change.way_id}_{bracket}"

        return Pacenote(
            text=text,
            distance_m=change.distance_m,
            note_type=NoteType.SURFACE,
            priority=4,  # Informational but important for grip
            unique_key=unique_key,
        )

    def _get_distance_call(self, distance_m: float) -> Optional[str]:
        """Get distance callout for the given distance."""
        for threshold, call in self.DISTANCE_CALLS:
            if distance_m >= threshold - 25 and distance_m <= threshold + 25:
                return call
        return None

    def _get_distance_bracket(self, distance_m: float) -> Optional[int]:
        """
        Get the distance bracket for multi-callout hazard features.

        Returns the bracket (e.g., 500, 300, 100) if within range, None otherwise.
        Features are called when entering each bracket's range.
        """
        for bracket in self.MULTI_CALLOUT_DISTANCES:
            # Bracket triggers when distance is within bracket to bracket-100m
            # e.g., 500 bracket triggers from 500m down to 400m
            #       300 bracket triggers from 300m down to 200m
            #       100 bracket triggers from 100m down to 0m
            lower_bound = max(0, bracket - 100)
            if lower_bound <= distance_m <= bracket + 25:
                return bracket
        return None

    def _get_corner_distance_bracket(self, distance_m: float) -> Optional[int]:
        """
        Get the distance bracket for corner callouts.

        Returns the bracket (e.g., 1000, 500, 100) if within range, None otherwise.
        """
        for bracket in self.CORNER_CALLOUT_DISTANCES:
            if bracket == 1000:
                # 1000 bracket: 900-1025m
                if 900 <= distance_m <= 1025:
                    return bracket
            elif bracket == 500:
                # 500 bracket: 400-525m
                if 400 <= distance_m <= 525:
                    return bracket
            else:
                # 100 bracket: 20-125m (MIN_CALLOUT_DISTANCE_M to 125)
                if self.MIN_CALLOUT_DISTANCE_M <= distance_m <= 125:
                    return bracket
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

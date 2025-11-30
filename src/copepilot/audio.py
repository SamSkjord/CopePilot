"""Audio output for pacenotes using Janne Laahanen samples or TTS fallback."""

import os
import platform
import random
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from queue import Queue, Empty
from typing import Dict, List, Optional

from . import config


class JanneSampleLibrary:
    """
    Sample library for Janne Laahanen / CrewChief style packs.

    These packs have individual WAV files organized in folders:
    - corner_3_left/ -> 1.wav, 2.wav, subtitles.csv
    - detail_into/ -> 1.wav, 2.wav
    - number_100/ -> 1.wav, 2.wav
    """

    # Mapping from pacenote text to sample folder names
    CORNER_MAP = {
        "left_hairpin": "corner_hairpin_left",
        "right_hairpin": "corner_hairpin_right",
        "left_square": "corner_square_left_descriptive",
        "right_square": "corner_square_right_descriptive",
        "left_two": "corner_2_left",
        "right_two": "corner_2_right",
        "left_three": "corner_3_left",
        "right_three": "corner_3_right",
        "left_four": "corner_4_left",
        "right_four": "corner_4_right",
        "left_five": "corner_5_left",
        "right_five": "corner_5_right",
        "left_six": "corner_6_left",
        "right_six": "corner_6_right",
        "left_flat": "corner_flat_left",
        "right_flat": "corner_flat_right",
    }

    DETAIL_MAP = {
        "tightens": "detail_tightens",
        "opens": "detail_opens",
        "long": "detail_long",
        "caution": "detail_caution",
        "over_bridge": "detail_over_bridge",
        "into": "detail_into",
        "and": "detail_and",
        "bridge": "detail_bridge",
        "junction": "detail_junction",
        "left_entry_chicane": "detail_left_entry_chicane",
        "right_entry_chicane": "detail_right_entry_chicane",
    }

    NUMBER_MAP = {
        "30": "number_30",
        "40": "number_40",
        "50": "number_50",
        "60": "number_60",
        "70": "number_70",
        "80": "number_80",
        "100": "number_100",
        "120": "number_120",
        "140": "number_140",
        "150": "number_150",
        "160": "number_160",
        "180": "number_180",
        "200": "number_200",
        "250": "number_250",
        "300": "number_300",
        "350": "number_350",
        "400": "number_400",
    }

    def __init__(self, sample_dir: Path):
        self.sample_dir = sample_dir
        self._cache: Dict[str, List[Path]] = {}  # folder -> list of wav files
        self._scan_samples()

    def _scan_samples(self) -> None:
        """Scan directory for available sample folders."""
        if not self.sample_dir.exists():
            return

        for folder in self.sample_dir.iterdir():
            if folder.is_dir() and not folder.name.startswith('.'):
                wavs = sorted(folder.glob("*.wav"))
                if wavs:
                    self._cache[folder.name] = wavs

    def get_sample_file(self, folder_name: str) -> Optional[Path]:
        """Get a random WAV file from the named folder."""
        if folder_name not in self._cache:
            return None
        wavs = self._cache[folder_name]
        return random.choice(wavs) if wavs else None

    def has_sample(self, folder_name: str) -> bool:
        """Check if a sample folder exists."""
        return folder_name in self._cache

    def get_folder_for_key(self, key: str) -> Optional[str]:
        """Map a sample key to folder name."""
        if key in self.CORNER_MAP:
            return self.CORNER_MAP[key]
        if key in self.DETAIL_MAP:
            return self.DETAIL_MAP[key]
        if key in self.NUMBER_MAP:
            return self.NUMBER_MAP[key]
        return None


class AudioPlayer:
    """
    Plays pacenote callouts using Janne Laahanen samples with TTS fallback.

    Priority:
    1. Janne Laahanen samples (CrewChief style, supports "into" chaining)
    2. TTS with sox effects (synthetic voice with rally effect)

    Chaining: When multiple callouts arrive in quick succession, they are
    combined with "into" between them (e.g., "left four into right three").
    """

    # Time window to collect items for chaining (seconds)
    CHAIN_WINDOW_S = 0.3

    def __init__(
        self,
        sample_dir: Optional[Path] = None,
        voice: str = config.TTS_VOICE,
        speed: int = config.TTS_SPEED,
        enable_effects: bool = True,
    ):
        self.voice = voice
        self.speed = speed
        self.enable_effects = enable_effects
        self._queue: Queue = Queue()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._temp_dir = tempfile.mkdtemp(prefix="copepilot_")
        self._platform = platform.system()

        # Load Janne Laahanen samples
        if sample_dir is None:
            sample_dir = Path(__file__).parent.parent.parent / "assets" / "codriver_Janne Laahanen"
        self.samples = JanneSampleLibrary(sample_dir) if sample_dir.exists() else None

        # Check available tools
        self._has_sox = shutil.which("sox") is not None
        self._has_say = shutil.which("say") is not None
        self._has_espeak = shutil.which("espeak-ng") or shutil.which("espeak")
        self._has_aplay = shutil.which("aplay") is not None
        self._has_afplay = shutil.which("afplay") is not None

    def start(self) -> None:
        """Start the audio playback thread."""
        self._running = True
        self._thread = threading.Thread(target=self._playback_loop, daemon=True)
        self._thread.start()
        # Warm up sox to avoid delay on first audio (loads libraries)
        if self._has_sox:
            try:
                warmup_file = os.path.join(self._temp_dir, "warmup.wav")
                subprocess.run(
                    ["sox", "-n", "-r", "44100", "-c", "1", warmup_file, "trim", "0", "0.01"],
                    capture_output=True,
                    timeout=1,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
                pass

    def stop(self) -> None:
        """Stop the audio playback thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=1)
        # Clean up temp files
        try:
            for f in os.listdir(self._temp_dir):
                os.remove(os.path.join(self._temp_dir, f))
            os.rmdir(self._temp_dir)
        except OSError:
            pass

    def say(self, text: str, priority: int = 5) -> None:
        """Queue text to be spoken."""
        self._queue.put((priority, text, time.time()))

    def _playback_loop(self) -> None:
        """
        Background thread that processes the speech queue.

        Drains all queued items and chains them with "into" between each.
        This ensures items queued while audio is playing get chained properly.
        """
        while self._running:
            try:
                priority, text, timestamp = self._queue.get(timeout=0.1)
            except Empty:
                continue

            # Drain all remaining items from queue immediately
            # This catches items queued while previous audio was playing
            chain = [text]
            while True:
                try:
                    _, next_text, _ = self._queue.get_nowait()
                    chain.append(next_text)
                except Empty:
                    break

            # Speak the chain
            self._speak_chain(chain)

    def _speak_chain(self, chain: List[str]) -> None:
        """Speak one or more pacenotes, chained with 'into' if multiple."""
        # Expand any pre-merged "into" chains from pacenote generator
        expanded = []
        for text in chain:
            if " into " in text:
                expanded.extend(text.split(" into "))
            else:
                expanded.append(text)

        # Try Janne samples first (supports chaining natively)
        if self.samples:
            if self._speak_with_samples(expanded):
                return

        # Fall back to TTS (join with "into" in text)
        combined = " into ".join(expanded) if len(expanded) > 1 else expanded[0]

        if self.enable_effects and self._has_sox:
            self._speak_with_effects(combined)
        else:
            self._speak_plain(combined)

    def _speak_with_samples(self, chain: List[str]) -> bool:
        """
        Build and play pacenotes using Janne Laahanen samples.

        Chains multiple notes with 'into' between them.
        Returns True if successful, False to fall back.
        """
        wav_files: List[Path] = []

        for idx, text in enumerate(chain):
            # Add "into" sample between notes
            if idx > 0:
                into_file = self.samples.get_sample_file("detail_into")
                if into_file:
                    wav_files.append(into_file)

            # Parse this pacenote into sample keys
            keys = self._parse_to_sample_keys(text)
            if not keys:
                return False

            # Get WAV file for each key
            for key in keys:
                folder = self.samples.get_folder_for_key(key)
                if not folder or not self.samples.has_sample(folder):
                    return False
                wav_file = self.samples.get_sample_file(folder)
                if not wav_file:
                    return False
                wav_files.append(wav_file)

        if not wav_files:
            return False

        # Concatenate and play
        try:
            output_file = os.path.join(self._temp_dir, "chain.wav")
            subprocess.run(
                ["sox"] + [str(f) for f in wav_files] + [output_file],
                check=True,
                capture_output=True,
            )
            self._play_file(output_file)
            return True
        except subprocess.CalledProcessError:
            return False

    def _parse_to_sample_keys(self, text: str) -> List[str]:
        """
        Parse pacenote text into sample keys.

        e.g., "two hundred left four tightens" -> ["200", "left_four", "tightens"]
        """
        parts = text.lower().split()
        keys = []
        i = 0

        while i < len(parts):
            # Distance callouts
            if parts[i] in ("one", "two", "three", "four") and i + 1 < len(parts) and parts[i + 1] == "hundred":
                num = {"one": "100", "two": "200", "three": "300", "four": "400"}[parts[i]]
                keys.append(num)
                i += 2
                continue
            if parts[i] == "one" and i + 1 < len(parts) and parts[i + 1] == "fifty":
                keys.append("150")
                i += 2
                continue
            if parts[i] in ("thirty", "forty", "fifty", "sixty", "seventy", "eighty"):
                num = {"thirty": "30", "forty": "40", "fifty": "50",
                       "sixty": "60", "seventy": "70", "eighty": "80"}[parts[i]]
                keys.append(num)
                i += 1
                continue

            # Direction + severity (e.g., "left four")
            if parts[i] in ("left", "right"):
                direction = parts[i]
                if i + 1 < len(parts):
                    severity = parts[i + 1]
                    if severity == "hairpin":
                        keys.append(f"{direction}_hairpin")
                        i += 2
                        continue
                    elif severity == "square":
                        keys.append(f"{direction}_square")
                        i += 2
                        continue
                    elif severity in ("two", "three", "four", "five", "six"):
                        keys.append(f"{direction}_{severity}")
                        i += 2
                        continue
                    elif severity == "flat":
                        keys.append(f"{direction}_flat")
                        i += 2
                        continue

            # Hairpin direction (e.g., "hairpin left")
            if parts[i] == "hairpin" and i + 1 < len(parts) and parts[i + 1] in ("left", "right"):
                keys.append(f"{parts[i + 1]}_hairpin")
                i += 2
                continue

            # Square direction (e.g., "square left")
            if parts[i] == "square" and i + 1 < len(parts) and parts[i + 1] in ("left", "right"):
                keys.append(f"{parts[i + 1]}_square")
                i += 2
                continue

            # Flat direction (e.g., "flat left")
            if parts[i] == "flat" and i + 1 < len(parts) and parts[i + 1] in ("left", "right"):
                keys.append(f"{parts[i + 1]}_flat")
                i += 2
                continue

            # Chicane (e.g., "chicane left right" -> left entry chicane)
            if parts[i] == "chicane" and i + 2 < len(parts):
                entry_dir = parts[i + 1]
                if entry_dir in ("left", "right"):
                    keys.append(f"{entry_dir}_entry_chicane")
                    i += 3  # Skip "chicane left right"
                    continue

            # Junction (T-junction warning)
            if parts[i] == "junction":
                keys.append("junction")
                i += 1
                continue

            # Modifiers
            if parts[i] == "tightens":
                keys.append("tightens")
                i += 1
                continue
            if parts[i] == "opens":
                keys.append("opens")
                i += 1
                continue
            if parts[i] == "long":
                keys.append("long")
                i += 1
                continue
            if parts[i] == "caution":
                keys.append("caution")
                i += 1
                continue

            # "over bridge"
            if parts[i] == "over" and i + 1 < len(parts) and parts[i + 1] == "bridge":
                keys.append("over_bridge")
                i += 2
                continue

            # Skip unknown words
            i += 1

        return keys

    def _speak_with_effects(self, text: str) -> None:
        """Speak with helmet/intercom effect using TTS + sox."""
        raw_file = os.path.join(self._temp_dir, "raw.wav")
        processed_file = os.path.join(self._temp_dir, "processed.wav")

        try:
            if not self._generate_speech_file(text, raw_file):
                self._speak_plain(text)
                return

            subprocess.run(
                [
                    "sox", raw_file, processed_file,
                    "highpass", "400",
                    "lowpass", "3200",
                    "compand", "0.1,0.3", "-70,-60,-20", "-8", "-90", "0.1",
                    "overdrive", "3",
                    "gain", "-5",
                ],
                check=True,
                capture_output=True,
            )

            self._play_file(processed_file)

        except subprocess.CalledProcessError:
            self._speak_plain(text)

    def _generate_speech_file(self, text: str, output_file: str) -> bool:
        """Generate speech to a WAV file using available TTS engine."""
        try:
            if self._platform == "Darwin" and self._has_say:
                aiff_file = output_file.replace(".wav", ".aiff")
                subprocess.run(
                    ["say", "-v", self.voice, "-r", str(self.speed), "-o", aiff_file, text],
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    ["sox", aiff_file, output_file],
                    check=True,
                    capture_output=True,
                )
                return True

            elif self._has_espeak:
                espeak_cmd = "espeak-ng" if shutil.which("espeak-ng") else "espeak"
                subprocess.run(
                    [espeak_cmd, "-v", "en-gb", "-s", str(self.speed), "-w", output_file, text],
                    check=True,
                    capture_output=True,
                )
                return True

        except subprocess.CalledProcessError:
            pass

        return False

    def _play_file(self, filepath: str) -> None:
        """Play an audio file using available player."""
        try:
            if self._platform == "Darwin" and self._has_afplay:
                subprocess.run(["afplay", filepath], check=True, capture_output=True)
            elif self._has_aplay:
                subprocess.run(["aplay", "-q", filepath], check=True, capture_output=True)
        except subprocess.CalledProcessError:
            pass

    def _speak_plain(self, text: str) -> None:
        """Speak text without effects (fallback)."""
        try:
            if self._platform == "Darwin" and self._has_say:
                subprocess.run(
                    ["say", "-v", self.voice, "-r", str(self.speed), text],
                    check=True,
                    capture_output=True,
                )
            elif self._has_espeak:
                espeak_cmd = "espeak-ng" if shutil.which("espeak-ng") else "espeak"
                subprocess.run(
                    [espeak_cmd, "-v", "en-gb", "-s", str(self.speed), text],
                    check=True,
                    capture_output=True,
                )
        except subprocess.CalledProcessError:
            pass

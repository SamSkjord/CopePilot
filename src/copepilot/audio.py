"""Audio output for pacenotes using Nicky Grist samples or TTS fallback."""

import os
import platform
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from queue import Queue, Empty
from typing import Dict, Optional, Tuple

from . import config


class SampleLibrary:
    """
    Loads and manages audio samples from a sample pack.

    Sample packs contain an MP3 file and a TXT file with timing markers.
    """

    def __init__(self, sample_dir: Path):
        self.sample_dir = sample_dir
        self.samples: Dict[str, Tuple[float, float]] = {}  # name -> (start, duration)
        self.mp3_path: Optional[Path] = None
        self._load_samples()

    def _load_samples(self) -> None:
        """Load sample timing data from the .txt file."""
        txt_files = list(self.sample_dir.glob("*.txt"))
        mp3_files = list(self.sample_dir.glob("*.mp3"))

        if not txt_files or not mp3_files:
            return

        self.mp3_path = mp3_files[0]

        with open(txt_files[0], "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("mode:") or line.startswith("fade:"):
                    continue

                parts = line.split(", ")
                if len(parts) >= 5:
                    name = parts[0]  # e.g., "left/three" or "straight/200 m"
                    start = float(parts[3])
                    end = float(parts[4])
                    duration = end - start

                    # Normalize name for lookup
                    key = self._normalize_name(name)
                    self.samples[key] = (start, duration)

    def _normalize_name(self, name: str) -> str:
        """Convert sample path to lookup key."""
        # "left/three" -> "left_three"
        # "straight/200 m" -> "200"
        # "hints/tightens" -> "tightens"
        if "/" in name:
            category, phrase = name.split("/", 1)
            if category == "straight":
                # Extract just the number
                return phrase.replace(" m", "").strip()
            elif category in ("left", "right"):
                return f"{category}_{phrase}"
            else:
                return phrase
        return name

    def get_sample(self, key: str) -> Optional[Tuple[float, float]]:
        """Get sample timing by key."""
        return self.samples.get(key)

    def has_sample(self, key: str) -> bool:
        """Check if a sample exists."""
        return key in self.samples


class AudioPlayer:
    """
    Plays pacenote callouts using Nicky Grist samples with TTS fallback.

    Priority:
    1. Nicky Grist sample pack (real co-driver voice)
    2. TTS with sox effects (synthetic voice with rally effect)
    """

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

        # Load sample library
        if sample_dir is None:
            sample_dir = Path(__file__).parent.parent.parent / "assets" / "NickyGrist"
        self.samples = SampleLibrary(sample_dir) if sample_dir.exists() else None

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
        self._queue.put((priority, text))

    def _playback_loop(self) -> None:
        """Background thread that processes the speech queue."""
        while self._running:
            try:
                _, text = self._queue.get(timeout=0.1)
                self._speak(text)
            except Empty:
                continue

    def _speak(self, text: str) -> None:
        """Speak the pacenote text."""
        # Try samples first
        if self.samples and self.samples.mp3_path:
            if self._speak_with_samples(text):
                return

        # Fall back to TTS
        if self.enable_effects and self._has_sox:
            self._speak_with_effects(text)
        else:
            self._speak_plain(text)

    def _speak_with_samples(self, text: str) -> bool:
        """
        Build and play pacenote from samples.

        Returns True if successful, False to fall back to TTS.
        """
        # Parse the pacenote text into components
        # e.g., "two hundred left four tightens" -> ["200", "left_four", "tightens"]
        parts = text.lower().split()

        sample_keys = []
        i = 0
        while i < len(parts):
            # Check for distance callouts
            if parts[i] in ("twenty", "thirty", "forty", "fifty", "seventy", "one", "two", "three", "four"):
                if parts[i] == "one" and i + 1 < len(parts) and parts[i + 1] == "fifty":
                    sample_keys.append("50")  # No 150m sample, use 50
                    i += 2
                    continue
                elif parts[i] == "one" and i + 1 < len(parts) and parts[i + 1] == "hundred":
                    sample_keys.append("100")
                    i += 2
                    continue
                elif parts[i] == "two" and i + 1 < len(parts) and parts[i + 1] == "hundred":
                    sample_keys.append("200")
                    i += 2
                    continue
                elif parts[i] == "three" and i + 1 < len(parts) and parts[i + 1] == "hundred":
                    sample_keys.append("300")
                    i += 2
                    continue
                elif parts[i] == "four" and i + 1 < len(parts) and parts[i + 1] == "hundred":
                    sample_keys.append("400")
                    i += 2
                    continue
                elif parts[i] == "twenty":
                    sample_keys.append("20")
                    i += 1
                    continue
                elif parts[i] == "thirty":
                    sample_keys.append("30")
                    i += 1
                    continue
                elif parts[i] == "forty":
                    sample_keys.append("40")
                    i += 1
                    continue
                elif parts[i] == "fifty":
                    sample_keys.append("50")
                    i += 1
                    continue
                elif parts[i] == "seventy":
                    sample_keys.append("70")
                    i += 1
                    continue

            # Check for direction + severity
            if parts[i] in ("left", "right"):
                direction = parts[i]
                if i + 1 < len(parts):
                    severity = parts[i + 1]
                    if severity == "hairpin":
                        sample_keys.append(f"{direction}_hairpin")
                        i += 2
                        continue
                    elif severity in ("two", "three", "four", "five", "six"):
                        sample_keys.append(f"{direction}_{severity}")
                        i += 2
                        continue
                    elif severity == "kink":
                        sample_keys.append(f"{direction}_six")  # Use six for kink
                        i += 2
                        continue

            # Check for modifiers
            if parts[i] == "tightens":
                sample_keys.append("tightens")
                i += 1
                continue
            elif parts[i] == "opens":
                sample_keys.append("open")
                i += 1
                continue
            elif parts[i] == "long":
                sample_keys.append("long")
                i += 1
                continue
            elif parts[i] == "caution":
                sample_keys.append("caution")
                i += 1
                continue

            # Skip unknown words
            i += 1

        # Check if we have all samples
        if not sample_keys:
            return False

        for key in sample_keys:
            if not self.samples.has_sample(key):
                return False  # Missing sample, fall back to TTS

        # Extract and concatenate samples
        try:
            clip_files = []
            for idx, key in enumerate(sample_keys):
                start, duration = self.samples.get_sample(key)
                clip_file = os.path.join(self._temp_dir, f"clip_{idx}.wav")
                subprocess.run(
                    [
                        "sox", str(self.samples.mp3_path), clip_file,
                        "trim", str(start), str(duration)
                    ],
                    check=True,
                    capture_output=True,
                )
                clip_files.append(clip_file)

            # Concatenate clips
            output_file = os.path.join(self._temp_dir, "pacenote.wav")
            subprocess.run(
                ["sox"] + clip_files + [output_file],
                check=True,
                capture_output=True,
            )

            # Play
            self._play_file(output_file)

            # Clean up clips
            for f in clip_files:
                try:
                    os.remove(f)
                except OSError:
                    pass

            return True

        except subprocess.CalledProcessError:
            return False

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

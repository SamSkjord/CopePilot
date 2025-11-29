"""Text-to-speech audio output for pacenotes with rally co-driver effects."""

import os
import subprocess
import tempfile
import threading
from queue import Queue, Empty
from typing import Optional

from . import config


class AudioPlayer:
    """
    Plays pacenote callouts using text-to-speech with rally co-driver effects.

    Uses macOS 'say' command with sox post-processing to simulate
    the sound of a co-driver speaking through an intercom/helmet mic.
    """

    def __init__(
        self,
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
        """Speak text with rally co-driver voice effect."""
        if self.enable_effects:
            self._speak_with_effects(text)
        else:
            self._speak_plain(text)

    def _speak_with_effects(self, text: str) -> None:
        """
        Speak with helmet/intercom effect using macOS say + sox.

        Effects applied:
        - High-pass filter at 400Hz (removes low rumble)
        - Low-pass filter at 3200Hz (radio bandwidth limitation)
        - Compression (makes speech more punchy and consistent)
        - Light overdrive (adds slight distortion like intercom)
        - Gain reduction (prevents clipping)
        """
        raw_file = os.path.join(self._temp_dir, "raw.aiff")
        processed_file = os.path.join(self._temp_dir, "processed.wav")

        try:
            # Generate speech with macOS say
            subprocess.run(
                [
                    "say",
                    "-v", self.voice,
                    "-r", str(self.speed),
                    "-o", raw_file,
                    text,
                ],
                check=True,
                capture_output=True,
            )

            # Apply effects with sox
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

            # Play processed audio
            subprocess.run(
                ["afplay", processed_file],
                check=True,
                capture_output=True,
            )

        except FileNotFoundError:
            # say or sox not available, fall back to plain speech
            self._speak_plain(text)
        except subprocess.CalledProcessError:
            # Something failed, try plain speech
            self._speak_plain(text)

    def _speak_plain(self, text: str) -> None:
        """Speak text without effects (fallback)."""
        try:
            # Try macOS say first
            subprocess.run(
                [
                    "say",
                    "-v", self.voice,
                    "-r", str(self.speed),
                    text,
                ],
                check=True,
                capture_output=True,
            )
        except FileNotFoundError:
            # Fall back to espeak
            self._speak_espeak(text)
        except subprocess.CalledProcessError:
            pass

    def _speak_espeak(self, text: str) -> None:
        """Speak text using espeak (Linux fallback)."""
        try:
            subprocess.run(
                [
                    "espeak",
                    "-v", "en-gb",
                    "-s", str(self.speed),
                    text,
                ],
                check=True,
                capture_output=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass

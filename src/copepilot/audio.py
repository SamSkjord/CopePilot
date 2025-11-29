"""Text-to-speech audio output for pacenotes with rally co-driver effects."""

import os
import platform
import shutil
import subprocess
import tempfile
import threading
from queue import Queue, Empty
from typing import Optional

from . import config


class AudioPlayer:
    """
    Plays pacenote callouts using text-to-speech with rally co-driver effects.

    Cross-platform support:
    - macOS: Uses 'say' command with Daniel voice
    - Linux/Raspberry Pi: Uses espeak-ng or pico2wave

    Sox post-processing simulates helmet mic/intercom sound.
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
        self._platform = platform.system()

        # Check available tools
        self._has_sox = shutil.which("sox") is not None
        self._has_say = shutil.which("say") is not None
        self._has_espeak = shutil.which("espeak-ng") or shutil.which("espeak")
        self._has_pico = shutil.which("pico2wave") is not None
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
        """Speak text with rally co-driver voice effect."""
        if self.enable_effects and self._has_sox:
            self._speak_with_effects(text)
        else:
            self._speak_plain(text)

    def _speak_with_effects(self, text: str) -> None:
        """
        Speak with helmet/intercom effect using TTS + sox.

        Effects applied:
        - High-pass filter at 400Hz (removes low rumble)
        - Low-pass filter at 3200Hz (radio bandwidth limitation)
        - Compression (makes speech more punchy and consistent)
        - Light overdrive (adds slight distortion like intercom)
        - Gain reduction (prevents clipping)
        """
        raw_file = os.path.join(self._temp_dir, "raw.wav")
        processed_file = os.path.join(self._temp_dir, "processed.wav")

        try:
            # Generate speech to file
            if not self._generate_speech_file(text, raw_file):
                self._speak_plain(text)
                return

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
            self._play_file(processed_file)

        except subprocess.CalledProcessError:
            # Sox failed, try plain speech
            self._speak_plain(text)

    def _generate_speech_file(self, text: str, output_file: str) -> bool:
        """Generate speech to a WAV file using available TTS engine."""
        try:
            if self._platform == "Darwin" and self._has_say:
                # macOS: use 'say' command
                # Output as AIFF then convert to WAV with sox for consistency
                aiff_file = output_file.replace(".wav", ".aiff")
                subprocess.run(
                    ["say", "-v", self.voice, "-r", str(self.speed), "-o", aiff_file, text],
                    check=True,
                    capture_output=True,
                )
                # Convert to WAV
                subprocess.run(
                    ["sox", aiff_file, output_file],
                    check=True,
                    capture_output=True,
                )
                return True

            elif self._has_pico:
                # Linux: pico2wave (better quality)
                subprocess.run(
                    ["pico2wave", "-w", output_file, text],
                    check=True,
                    capture_output=True,
                )
                return True

            elif self._has_espeak:
                # Linux: espeak-ng or espeak
                espeak_cmd = "espeak-ng" if shutil.which("espeak-ng") else "espeak"
                subprocess.run(
                    [
                        espeak_cmd,
                        "-v", "en-gb",
                        "-s", str(self.speed),
                        "-w", output_file,
                        text,
                    ],
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

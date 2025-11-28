"""Text-to-speech audio output for pacenotes."""

import subprocess
import threading
from queue import Queue, Empty
from typing import Optional


class AudioPlayer:
    """Plays pacenote callouts using text-to-speech."""

    def __init__(self, voice: str = "en-gb", speed: int = 175):
        self.voice = voice
        self.speed = speed
        self._queue: Queue = Queue()
        self._thread: Optional[threading.Thread] = None
        self._running = False

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
        """Speak text using espeak."""
        try:
            subprocess.run(
                [
                    "espeak",
                    "-v", self.voice,
                    "-s", str(self.speed),
                    text,
                ],
                check=True,
                capture_output=True,
            )
        except FileNotFoundError:
            # espeak not installed, try pico2wave + aplay
            self._speak_pico(text)
        except subprocess.CalledProcessError:
            pass  # Ignore TTS errors

    def _speak_pico(self, text: str) -> None:
        """Fallback TTS using pico2wave."""
        try:
            subprocess.run(
                ["pico2wave", "-w", "/tmp/copepilot.wav", text],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["aplay", "-q", "/tmp/copepilot.wav"],
                check=True,
                capture_output=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass

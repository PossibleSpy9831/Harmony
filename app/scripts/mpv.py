import os
import sys
import json
import math
import time
import socket
import shutil
import array
import tempfile
import threading
import subprocess

from collections import deque


class MPV:
    def __init__(self):
        self.process = None
        self._media_controls = None

        self.socket_path = os.path.join(
            tempfile.gettempdir(),
            f"play-{os.getpid()}.sock",
        )

        self.paused = False
        self.time_pos = 0.0
        self.duration = 0.0
        self.volume = 75

        self.position_lock = threading.Lock()
        self.socket = None
        self.buffer = b""
        self.socket_lock = threading.Lock()

        self._pcm_process = None
        self._pcm_thread = None
        self._pcm_lock = threading.Lock()
        self._pcm_samples = deque(maxlen=16384)
        self._pcm_stop = threading.Event()
        self._pcm_control_lock = threading.RLock()

        self._current_path = None
        self._current_track = None
        self._pending_start_position = None

    @property
    def media_controls(self):
        return self._media_controls

    @media_controls.setter
    def media_controls(self, controls):
        self._media_controls = controls

        if controls is not None:
            controls.set_seek_handler(self.seek)

    def start(self):
        if self.process is not None:
            return

        try:
            os.unlink(self.socket_path)
        except FileNotFoundError:
            pass

        self.process = subprocess.Popen(
            [
                "mpv",
                "--no-video",
                "--really-quiet",
                "--idle=yes",
                "--force-window=no",
                "--input-media-keys=no",
                "--input-ipc-server=" + self.socket_path,
                "--audio-samplerate=44100",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        for _ in range(100):
            if os.path.exists(self.socket_path):
                break

            if self.process.poll() is not None:
                self.process = None
                raise RuntimeError("mpv stopped himself at launch")

            time.sleep(0.05)
        else:
            self.process.terminate()
            self.process = None
            raise RuntimeError("mpv didn't start.")

        self.socket = socket.socket(
            socket.AF_UNIX,
            socket.SOCK_STREAM,
        )
        self.socket.connect(self.socket_path)

        self.command("observe_property", 1, "time-pos")
        self.command("observe_property", 2, "duration")
        self.command("set_property", "volume", self.volume)

    def command(self, *args):
        if self.socket is None:
            return

        message = json.dumps({"command": list(args)}) + "\n"

        try:
            with self.socket_lock:
                self.socket.sendall(message.encode())
        except (OSError, ConnectionError):
            pass

    def _clear_pcm(self):
        with self._pcm_lock:
            self._pcm_samples.clear()

    def _start_pcm_capture(self, path, position=0.0):
        with self._pcm_control_lock:
            self._stop_pcm_capture()

            if not path or not shutil.which("ffmpeg"):
                return False

            self._clear_pcm()
            stop_event = threading.Event()
            self._pcm_stop = stop_event

            try:
                process = subprocess.Popen(
                    [
                        "ffmpeg",
                        "-nostdin",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-ss",
                        str(max(0.0, position)),
                        "-re",
                        "-i",
                        path,
                        "-vn",
                        "-ac",
                        "1",
                        "-ar",
                        "44100",
                        "-f",
                        "f32le",
                        "-acodec",
                        "pcm_f32le",
                        "pipe:1",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    bufsize=0,
                )

                self._pcm_process = process
                self._pcm_thread = threading.Thread(
                    target=self._pcm_reader,
                    args=(process, stop_event),
                    daemon=True,
                )
                self._pcm_thread.start()
                return True

            except (OSError, ValueError):
                self._pcm_process = None
                return False

    def _pcm_reader(self, process, stop_event):
        if process.stdout is None:
            return

        try:
            while not stop_event.is_set():
                raw = process.stdout.read(16384)

                if not raw:
                    break

                usable = len(raw) - len(raw) % 4

                if not usable:
                    continue

                samples = array.array("f")
                samples.frombytes(raw[:usable])

                if sys.byteorder != "little":
                    samples.byteswap()

                with self._pcm_lock:
                    if not stop_event.is_set():
                        self._pcm_samples.extend(samples)

        except (OSError, ValueError):
            pass

    def read_audio_samples(self, count=2048):
        with self._pcm_lock:
            if len(self._pcm_samples) < count:
                return None

            return list(self._pcm_samples)[-count:]

    def _stop_pcm_capture(self):
        with self._pcm_control_lock:
            process = self._pcm_process
            thread = self._pcm_thread
            stop_event = self._pcm_stop

            stop_event.set()
            self._pcm_process = None
            self._pcm_thread = None

            if process is not None:
                if process.poll() is None:
                    process.terminate()

                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()

                if process.stdout is not None:
                    try:
                        process.stdout.close()
                    except OSError:
                        pass

            if thread is not None:
                if thread is not threading.current_thread():
                    thread.join(timeout=0.5)

            self._clear_pcm()

    def play(self, path, track=None, start_position=0.0):
        if not path:
            return

        start_position = max(0.0, float(start_position or 0.0))
        self._stop_pcm_capture()

        self._current_path = path
        self._current_track = track

        with self.position_lock:
            self.time_pos = start_position
            self.duration = 0.0

        self.paused = False
        self._pending_start_position = start_position or None

        if self.media_controls is not None and track is not None:
            self.media_controls.update(
                track,
                paused=False,
                position=start_position,
                duration=track.get("duration"),
            )

        self.command("loadfile", path, "replace")
        self._start_pcm_capture(path, start_position)

    def pause(self):
        self.set_paused(not self.paused)

    def set_paused(self, paused):
        paused = bool(paused)

        if paused == self.paused:
            return

        if paused:
            self._stop_pcm_capture()
            self.command("set", "pause", "yes")
            self.paused = True
        else:
            self.command("set", "pause", "no")
            self.paused = False

            if self._current_path:
                self._start_pcm_capture(
                    self._current_path,
                    self.get_time_pos(),
                )

        if self.media_controls is not None:
            self.media_controls.set_position(self.get_time_pos())
            self.media_controls.set_paused(self.paused)

    def set_volume(self, volume):
        self.volume = max(0, min(100, int(volume)))
        self.command("set_property", "volume", self.volume)
        return self.volume

    def seek(self, position):
        try:
            position = float(position)
        except (TypeError, ValueError):
            return

        if not math.isfinite(position) or position < 0:
            return

        duration = self.get_duration()

        if duration > 0:
            position = min(position, duration)

        if self.process is None or self.socket is None:
            return

        self._debug_seek(position)
        self.command("seek", position, "absolute+exact")

        with self.position_lock:
            self.time_pos = position

        if self._current_path and not self.paused:
            self._start_pcm_capture(
                self._current_path,
                position,
            )
        else:
            self._clear_pcm()

        if self.media_controls is not None:
            self.media_controls.set_position(position)

    def _debug_seek(self, position):
        controls = self.media_controls

        if controls is not None:
            controls._debug(f"MPV seek requested: {position:.3f}s")

    def stop(self):
        self._stop_pcm_capture()
        self.command("stop")
        self.paused = False
        self._current_path = None
        self._current_track = None

        with self.position_lock:
            self.time_pos = 0.0
            self.duration = 0.0

        if self.media_controls is not None:
            self.media_controls.set_position(0.0)
            self.media_controls.set_paused(True)

    def get_time_pos(self):
        with self.position_lock:
            return self.time_pos

    def get_duration(self):
        with self.position_lock:
            return self.duration

    def events(self):
        if self.socket is None:
            return []

        events = []

        try:
            self.socket.settimeout(0.02)

            while True:
                try:
                    data = self.socket.recv(65536)

                    if not data:
                        break

                    self.buffer += data

                    if len(data) < 65536:
                        break

                except socket.timeout:
                    break

        except (OSError, ConnectionError):
            return []

        finally:
            try:
                self.socket.settimeout(None)
            except OSError:
                pass

        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)

            if not line:
                continue

            try:
                message = json.loads(line.decode())
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue

            events.append(message)

        return events

    def process_events(self, events):
        position_changed = False

        for event in events:
            if (
                event.get("event") == "file-loaded"
                and self._pending_start_position is not None
            ):
                position = self._pending_start_position
                self._pending_start_position = None
                if position > 0:
                    self.command("seek", position, "absolute+exact")
                    with self.position_lock:
                        self.time_pos = position
                    position_changed = True
                continue

            if event.get("event") != "property-change":
                continue

            name = event.get("name")
            data = event.get("data")

            if name == "time-pos":
                if isinstance(data, (int, float)):
                    with self.position_lock:
                        self.time_pos = float(data)

                    position_changed = True

            elif name == "duration":
                if isinstance(data, (int, float)):
                    with self.position_lock:
                        self.duration = float(data)

        if position_changed and self.media_controls is not None:
            self.media_controls.set_position(self.get_time_pos())

    def quit(self):
        self._stop_pcm_capture()

        if self.process:
            try:
                self.command("quit")
            except Exception:
                pass

            try:
                if self.socket:
                    self.socket.close()
            except OSError:
                pass

            try:
                self.process.terminate()
            except OSError:
                pass

            self.process = None
            self.socket = None

        try:
            os.unlink(self.socket_path)
        except FileNotFoundError:
            pass

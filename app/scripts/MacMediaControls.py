import sys
import os
import select
import threading
import time
import math
from pathlib import Path
from app.scripts.library_config import LIBRARY_PATH
from app.scripts.helpers import title_of, artist_of, album_of, clean_path

MEDIA_CONTROLS_LOG_PATH = Path(LIBRARY_PATH).parent / "mac_media_controls.log"


class MacMediaControls:

    def __init__(
        self,
        on_next,
        on_previous,
        on_toggle_pause,
        on_play,
        on_pause,
        console,
    ):
        self.console = console
        self._on_next = on_next
        self._on_previous = on_previous
        self._on_toggle_pause = on_toggle_pause
        self._on_play = on_play
        self._on_pause = on_pause
        self._on_seek = None

        self._media_player = None
        self._now_playing = None

        self._next_command = None
        self._previous_command = None
        self._toggle_command = None
        self._play_command = None
        self._pause_command = None
        self._position_command = None

        self._next_handler = None
        self._previous_handler = None
        self._toggle_handler = None
        self._play_handler = None
        self._pause_handler = None
        self._position_handler = None

        self._next_target = None
        self._previous_target = None
        self._toggle_target = None
        self._play_target = None
        self._pause_target = None
        self._position_target = None

        self._stop_event = threading.Event()
        self._ready = threading.Event()
        self._log_lock = threading.Lock()

        self._current_track = None
        self._position = 0.0
        self._duration = None
        self._paused = True

        if sys.platform != "darwin":
            return

        self._debug("created")

    def _debug(self, message):
        try:
            MEDIA_CONTROLS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

            with self._log_lock:
                with MEDIA_CONTROLS_LOG_PATH.open("a", encoding="utf-8") as log:
                    log.write(
                        f"{time.strftime('%Y-%m-%d %H:%M:%S')} "
                        f"[{threading.current_thread().name}] "
                        f"{message}\n"
                    )
        except OSError:
            pass

    @property
    def enabled(self):
        return self._media_player is not None

    def set_seek_handler(self, callback):
        """Branche le callback de déplacement du lecteur audio."""
        self._on_seek = callback

    def start(self):
        if sys.platform != "darwin":
            return

        self._debug("starting")
        self._register()
        self._debug(f"ready enabled={self.enabled}")

    def _register(self):
        try:
            import MediaPlayer
        except ImportError as error:
            self._debug(f"MediaPlayer import failed: {error!r}")
            self.console.print(
                f"[yellow]macosx media controls are unavailable: " f"{error}[/yellow]"
            )
            self._ready.set()
            return

        from Foundation import NSDate, NSDefaultRunLoopMode, NSRunLoop

        self._media_player = MediaPlayer
        self._now_playing = MediaPlayer.MPNowPlayingInfoCenter.defaultCenter()

        center = MediaPlayer.MPRemoteCommandCenter.sharedCommandCenter()

        self._next_command = center.nextTrackCommand()
        self._previous_command = center.previousTrackCommand()
        self._toggle_command = center.togglePlayPauseCommand()
        self._play_command = center.playCommand()
        self._pause_command = center.pauseCommand()

        # Commande native macOS de changement de position.
        self._position_command = center.changePlaybackPositionCommand()

        self._next_handler = lambda event: self._handle("next", event, self._on_next)
        self._previous_handler = lambda event: self._handle(
            "previous", event, self._on_previous
        )
        self._toggle_handler = lambda event: self._handle(
            "toggle_pause", event, self._on_toggle_pause
        )
        self._play_handler = lambda event: self._handle("play", event, self._on_play)
        self._pause_handler = lambda event: self._handle("pause", event, self._on_pause)
        self._position_handler = self._handle_seek

        self._next_target = self._next_command.addTargetWithHandler_(self._next_handler)
        self._previous_target = self._previous_command.addTargetWithHandler_(
            self._previous_handler
        )
        self._toggle_target = self._toggle_command.addTargetWithHandler_(
            self._toggle_handler
        )
        self._play_target = self._play_command.addTargetWithHandler_(self._play_handler)
        self._pause_target = self._pause_command.addTargetWithHandler_(
            self._pause_handler
        )
        self._position_target = self._position_command.addTargetWithHandler_(
            self._position_handler
        )

        for command in (
            self._next_command,
            self._previous_command,
            self._toggle_command,
            self._play_command,
            self._pause_command,
            self._position_command,
        ):
            command.setEnabled_(True)

        self._debug(
            "registered "
            f"next={self._next_command.hasTargets()} "
            f"previous={self._previous_command.hasTargets()} "
            f"toggle={self._toggle_command.hasTargets()} "
            f"play={self._play_command.hasTargets()} "
            f"pause={self._pause_command.hasTargets()} "
            f"seek={self._position_command.hasTargets()}"
        )

        self._ready.set()
        self._run_loop = NSRunLoop.mainRunLoop()
        self._run_loop_mode = NSDefaultRunLoopMode
        self._date_type = NSDate

    def pump(self):
        if not self.enabled:
            return

        self._run_loop.runMode_beforeDate_(
            self._run_loop_mode,
            self._date_type.dateWithTimeIntervalSinceNow_(0.0),
        )

    def _handle(self, command_name, event, action):
        self._debug(f"RECEIVED {command_name}: {event!r}")

        try:
            action()
        except Exception as error:
            self._debug(f"CALLBACK ERROR {command_name}: {error!r}")
            return self._media_player.MPRemoteCommandHandlerStatusCommandFailed

        return self._media_player.MPRemoteCommandHandlerStatusSuccess

    def _handle_seek(self, event):
        try:
            position = float(event.positionTime())

            if not math.isfinite(position) or position < 0:
                raise ValueError(f"Invalid seek position: {position!r}")

            if self._duration is not None:
                position = min(position, self._duration)

            if self._on_seek is None:
                self._debug("SEEK rejected: no seek callback configured")
                return self._media_player.MPRemoteCommandHandlerStatusCommandFailed

            self._debug(f"SEEK requested position={position:.3f}")

            # Le lecteur audio est la source de vérité.
            self._on_seek(position)

            return self._media_player.MPRemoteCommandHandlerStatusSuccess

        except Exception as error:
            self._debug(f"SEEK ERROR: {error!r}")
            return self._media_player.MPRemoteCommandHandlerStatusCommandFailed

    def _artwork_for(self, track):
        cover_path = clean_path(track.get("_cover"))

        if not cover_path:
            return None

        try:
            from AppKit import NSImage

            image = NSImage.alloc().initWithContentsOfFile_(cover_path)

            if image is None or not image.isValid():
                self._debug(f"Invalid artwork: {cover_path!r}")
                return None

            size = image.size()

            def handler(requested_size, img=image):
                return img

            return self._media_player.MPMediaItemArtwork.alloc().initWithBoundsSize_requestHandler_(
                size, handler
            )

        except Exception as error:
            self._debug(f"ARTWORK ERROR: {error!r}")
            return None

    def update(self, track, paused=False, position=None, duration=None):
        if not self.enabled:
            return

        if track is not self._current_track:
            self._position = max(0.0, float(position)) if position is not None else 0.0
        elif position is not None:
            self._position = max(0.0, float(position))

        self._current_track = track
        self._paused = bool(paused)

        track_duration = track.get("duration")

        if duration is not None:
            self._duration = max(0.0, float(duration))
        elif isinstance(track_duration, (int, float)):
            self._duration = max(0.0, float(track_duration))
        else:
            self._duration = None

        info = {
            self._media_player.MPMediaItemPropertyTitle: title_of(track),
            self._media_player.MPMediaItemPropertyArtist: artist_of(track),
            self._media_player.MPMediaItemPropertyAlbumTitle: album_of(track),
            self._media_player.MPNowPlayingInfoPropertyPlaybackRate: (
                0.0 if self._paused else 1.0
            ),
            self._media_player.MPNowPlayingInfoPropertyElapsedPlaybackTime: (
                self._position
            ),
        }

        if self._duration is not None:
            info[self._media_player.MPMediaItemPropertyPlaybackDuration] = (
                self._duration
            )

        artwork = self._artwork_for(track)

        if artwork is not None:
            info[self._media_player.MPMediaItemPropertyArtwork] = artwork

        self._now_playing.setNowPlayingInfo_(info)

        self._debug(
            f"update title={title_of(track)!r} "
            f"paused={self._paused} position={self._position} "
            f"duration={self._duration}"
        )

    def set_position(self, position):
        if not self.enabled:
            return

        position = max(0.0, float(position))

        if self._duration is not None:
            position = min(position, self._duration)

        self._position = position

        info = self._now_playing.nowPlayingInfo()

        if info is None:
            return

        info[self._media_player.MPNowPlayingInfoPropertyElapsedPlaybackTime] = (
            self._position
        )

        self._now_playing.setNowPlayingInfo_(info)

    def set_paused(self, paused):
        if not self.enabled:
            return

        self._paused = bool(paused)

        info = self._now_playing.nowPlayingInfo()

        if info is None:
            return

        info[self._media_player.MPNowPlayingInfoPropertyPlaybackRate] = (
            0.0 if self._paused else 1.0
        )

        self._now_playing.setNowPlayingInfo_(info)

        self._debug(f"paused={self._paused} position={self._position}")

    def close(self):
        if not self.enabled:
            return

        self._debug("closing")
        self._stop_event.set()

        for command in (
            self._next_command,
            self._previous_command,
            self._toggle_command,
            self._play_command,
            self._pause_command,
            self._position_command,
        ):
            if command is not None:
                command.setEnabled_(False)

        self._now_playing.setNowPlayingInfo_(None)

        self._current_track = None
        self._position = 0.0
        self._duration = None

        self._debug("closed")

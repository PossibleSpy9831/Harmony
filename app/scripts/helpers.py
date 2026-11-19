from rich.console import Console
from rich.panel import Panel
from datetime import datetime
import time
from pathlib import Path
import threading
import json
import random
import sys
import os
from app.data.database import Database
from app.core.event_bus import event_bus


try:
    from library_config import DATABASE_PATH, LEGACY_DATABASE_PATH, LIBRARY_PATH
except ImportError:
    from app.scripts.library_config import (
        DATABASE_PATH,
        LEGACY_DATABASE_PATH,
        LIBRARY_PATH,
    )
console = Console()
STATE_PATH = Path(".play/player_state.json")
GLOBAL_LOG_PATH = Path(".log/play.log")
MEDIA_CONTROLS_LOG_PATH = Path(".play/media_controls.log")
MAX_HISTORY = 30
PLAYER_STATE = None

def log(message):
    try:
        GLOBAL_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with GLOBAL_LOG_PATH.open("a", encoding="utf-8") as log_file:
            log_file.write(
                f"{time.strftime('%Y-%m-%d %H:%M:%S')} "
                f"[{threading.current_thread().name}] {message}\n"
            )
    except OSError:
        pass


def touch_id():
    import LocalAuthentication

    context = LocalAuthentication.LAContext.alloc().init()

    success, error = context.canEvaluatePolicy_error_(
        LocalAuthentication.LAPolicyDeviceOwnerAuthenticationWithBiometrics, None
    )

    if not success:
        print(" Touch ID not available")
        if error:
            print(error)
        return False

    result = {"success": False}

    def callback(success, error):
        result["success"] = success

    context.evaluatePolicy_localizedReason_reply_(
        LocalAuthentication.LAPolicyDeviceOwnerAuthenticationWithBiometrics,
        "launch harmony player check",
        callback,
    )

    # Garde le programme en vie pendant le callback
    import time

    with console.status(
        Panel("Authentication in progress ...", border_style="red"), spinner="dots"
    ):
        count = 0

        while result["success"] is False and count < 5:
            time.sleep(0.1)
            count += 0.1

    return result["success"]


def tracks_from_album(album):
    result = []

    for album_track in album.get("tracks", []):
        track = dict(album_track)

        track["_album"] = album_name(album)
        track["_cover"] = album.get("cover")
        track["_year"] = album.get("year") or album.get("date")

        result.append(track)

    return result


def fill_giga_playlist(
    playlist,
    library,
):
    tracks = []

    for album in library.get("albums", []):
        tracks.extend(tracks_from_album(album))

    random.shuffle(tracks)

    playlist.set(
        tracks,
        "GIGA RANDOM",
        0,
    )


def clean_path(path):
    if not path:
        return None

    path = Path(str(path))

    if path.is_file():
        return str(path.resolve())

    raw = str(path)

    if raw.startswith("/"):
        relative = Path(raw.lstrip("/"))

        if relative.is_file():
            return str(relative.resolve())

        relative = Path.cwd() / raw.lstrip("/")

        if relative.is_file():
            return str(relative.resolve())

    return None


def title_of(track):
    return track.get("title") or Path(track["path"]).stem


def artist_of(track):
    return track.get("artist") or "Unknown Artist"


def album_of(track):
    return track.get("_album") or track.get("album") or "Unknown Album"


def album_name(album):
    return album.get("name") or "Unknown Album"


def shorten(text, max_length):
    text = str(text)

    if len(text) <= max_length:
        return text

    if max_length <= 1:
        return "…"

    return text[: max_length - 1] + "…"


# LIBRARY
def load_library():
    if not LIBRARY_PATH.exists():
        console.print(
            Panel(
                "[red]library.json not found.[/red]\n\n"
                "Lance : [bold]python scanner.py[/bold]",
                title="HARMONY",
                border_style="red",
            )
        )
        sys.exit(1)

    try:
        with open(
            LIBRARY_PATH,
            "r",
            encoding="utf-8",
        ) as f:
            return json.load(f)

    except Exception as e:
        console.print(
            Panel(
                f"[red]unable to read library.json[/red]\n\n{e}",
                title="HARMONY",
                border_style="red",
            )
        )
        sys.exit(1)


def track_key(track):
    return str(track.get("path") or track.get("title") or "")


def track_reference(track):
    return {
        "key": track_key(track),
        "path": track.get("path", ""),
        "title": title_of(track),
        "artist": artist_of(track),
        "album": album_of(track),
    }


def load_player_state():
    return Database(
        DATABASE_PATH,
        migrate_from=LEGACY_DATABASE_PATH,
    ).load_player_state(STATE_PATH)


def save_player_state(state):
    Database(
        DATABASE_PATH,
        migrate_from=LEGACY_DATABASE_PATH,
    ).save_player_state(state)


def library_tracks(library):
    tracks = []

    for album in library.get("albums", []):
        tracks.extend(tracks_from_album(album))

    return tracks


def resolve_references(references, library):
    by_key = {track_key(track): track for track in library_tracks(library)}

    return [
        by_key[reference.get("key")]
        for reference in references
        if isinstance(reference, dict) and reference.get("key") in by_key
    ]


def record_history(state, track):
    reference = track_reference(track)
    history = state["history"]
    history[:] = [item for item in history if item.get("key") != reference["key"]]
    history.insert(0, reference)
    del history[MAX_HISTORY:]
    track["_play_event_id"] = Database(
        DATABASE_PATH,
        migrate_from=LEGACY_DATABASE_PATH,
    ).record_track_started(
        track, datetime.now().astimezone().isoformat(timespec="seconds")
    )
    save_player_state(state)


def toggle_favorite(state, track):
    key = track_key(track)
    favorites = state["favorites"]

    if key in favorites:
        favorites.remove(key)
        is_favorite = False
    else:
        favorites.append(key)
        is_favorite = True

    save_player_state(state)
    event_bus.publish("track_favorited", track=track, is_favorite=is_favorite)
    return is_favorite


def create_playlist(state, name):
    name = str(name).strip()

    if not name:
        return False

    if name in state["playlists"]:
        return False

    state["playlists"][name] = []
    save_player_state(state)
    event_bus.publish("playlist_updated", name=name, action="created")
    return True


def add_to_playlist(state, name, track):
    if name not in state["playlists"]:
        return False

    reference = track_reference(track)
    tracks = state["playlists"][name]

    if any(item.get("key") == reference["key"] for item in tracks):
        return False

    tracks.append(reference)
    save_player_state(state)
    event_bus.publish("playlist_updated", name=name, action="track_added")
    return True


def play_current(
    mpv,
    playlist,
    state=None,
    start_position=0.0,
):
    state = state or PLAYER_STATE
    track = playlist.current

    if not track:
        return False

    path = clean_path(track.get("path"))

    if not path:
        return False

    if not os.path.isfile(path):
        return False

    if state is not None:
        record_history(state, track)

    mpv.play(path, track, start_position=start_position)
    if state is not None:
        event_bus.publish("track_started", track=track, key=track_key(track))

    return True


def play_next_valid(mpv, playlist, state=None):
    state = state or PLAYER_STATE

    tracks, index, name = playlist.snapshot()

    if not tracks:
        log("[AUTO NEXT] Playlist vide")
        return False

    for _ in range(len(tracks)):
        track = playlist.next()

        if not track:
            return False

        path = clean_path(track.get("path"))


        if path and os.path.isfile(path):
            if state is not None:
                record_history(state, track)

            log(f"[AUTO NEXT] Lecture de {track.get('title')!r}")
            mpv.play(path, track)
            if state is not None:
                event_bus.publish("track_started", track=track, key=track_key(track))
            return True
        
    return False


# AUTO NEXT
def mpv_watcher(
    mpv,
    playlist,
    state,
    stop_event,
):
    database = Database(DATABASE_PATH, migrate_from=LEGACY_DATABASE_PATH)
    active_event_id = None
    last_position = None
    last_sample = 0.0
    unflushed_seconds = 0.0

    def flush_listening_time():
        nonlocal unflushed_seconds
        if active_event_id is not None and unflushed_seconds > 0:
            database.add_listened_seconds(active_event_id, unflushed_seconds)
            unflushed_seconds = 0.0

    while not stop_event.is_set():
        current = playlist.current
        event_id = current.get("_play_event_id") if current else None
        if event_id != active_event_id:
            flush_listening_time()
            active_event_id = event_id
            last_position = None
            last_sample = 0.0

        now = time.monotonic()
        if now - last_sample >= 1:
            if active_event_id is not None and not getattr(mpv, "paused", False):
                position = mpv.get_time_pos()
                if position is not None and last_position is not None:
                    elapsed = position - last_position
                    if 0 < elapsed <= 2.5:
                        unflushed_seconds += elapsed
                        if unflushed_seconds >= 5:
                            flush_listening_time()
                last_position = position
            else:
                last_position = None
            last_sample = now

        events = mpv.events()

        mpv.process_events(events)

        for event in events:

            if event.get("event") != "end-file":
                continue

            reason = event.get("reason")

            if reason != "eof":
                continue

            play_next_valid(
                mpv,
                playlist,
                state,
            )

        time.sleep(0.02)
    flush_listening_time()


def prompt_playlist_name(live, keyboard):
    live.stop()
    keyboard.stop()

    try:
        return console.input("\n Playlist name (empty cancels) : ").strip()
    finally:
        keyboard.start()
        live.start(refresh=False)

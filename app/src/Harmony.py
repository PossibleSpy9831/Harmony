from app.scripts.MacMediaControls import MacMediaControls
from app.scripts.helpers import (
    DATABASE_PATH,
    LEGACY_DATABASE_PATH,
    load_library,
    log,
    touch_id,
    load_player_state,
    save_player_state,
    clean_path,
    album_name,
    track_key,
    album_of,
    title_of,
    artist_of,
    resolve_references,
    tracks_from_album,
    play_current,
    fill_giga_playlist,
    toggle_favorite,
    add_to_playlist,
    create_playlist,
    prompt_playlist_name,
    mpv_watcher,
)
from app.scripts.Keyboard import Keyboard
from app.scripts.Spectrum import Spectrum
from app.scripts.Playlist import Playlist
from app.scripts.mpv import MPV
from app.scripts.Search import Search
from app.core.navigation import Navigation
from app.core.event_bus import event_bus
from app.data.database import Database
from app.features.home import (
    album_for_track,
    album_of_the_day,
    next_album_of_the_day,
    recently_played,
    rediscover_tracks,
)
from app.features.themes import THEMES, ThemeManager
from app.features.smart_shuffle import FLOW_MODES, build_flow_queue
from app.features.stats import STAT_PERIODS, period_start
from app.scripts.ui import (
    build_home_screen,
    build_now_playing_screen,
    build_flow_screen,
    build_theme_screen,
    build_stats_screen,
    build_album_screen,
    build_track_screen,
    build_spectrum_screen,
    build_collection_screen,
    build_playlists_screen,
    build_search_screen,
    build_artists_screen,
    build_queue_screen,
    build_playlist_picker_screen,
    group_by_artist,
    header,
    columns,
)

from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

import json
import os
import math
import random
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import termios
import threading
import time
import tty
from pathlib import Path

try:
    from app.scripts.library_config import LIBRARY_PATH
except ImportError:
    from app.scripts.library_config import LIBRARY_PATH

console = Console()
GLOBAL_LOG_PATH = Path(".log/play.log")
MEDIA_CONTROLS_LOG_PATH = Path(".play/media_controls.log")
MAX_HISTORY = 30
PLAYER_STATE = None


def restore_playback_queue(database, playlist, library):
    saved_queue = database.load_playback_queue()
    if not saved_queue or not isinstance(saved_queue.get("tracks"), list):
        return None

    tracks_by_path = {}
    for album in library.get("albums", []):
        for track in tracks_from_album(album):
            path = clean_path(track.get("path"))
            if path:
                track["path"] = path
                tracks_by_path[path] = track

    restored_tracks = []
    original_positions = []
    missing_tracks = 0
    for position, saved_path in enumerate(saved_queue["tracks"]):
        path = clean_path(saved_path) if isinstance(saved_path, str) else None
        track = tracks_by_path.get(path) if path else None
        if track is None or not os.path.isfile(path):
            missing_tracks += 1
            continue
        restored_tracks.append(track)
        original_positions.append(position)

    if missing_tracks:
        log(f"[QUEUE] {missing_tracks} saved track(s) are unavailable")
    if not restored_tracks:
        return None

    saved_index = saved_queue.get("index", 0)
    if not isinstance(saved_index, int) or isinstance(saved_index, bool):
        saved_index = 0
    saved_index = max(0, min(saved_index, len(saved_queue["tracks"]) - 1))
    current_index = next(
        (
            index
            for index, original_position in enumerate(original_positions)
            if original_position >= saved_index
        ),
        len(restored_tracks) - 1,
    )
    playlist.set(
        restored_tracks,
        saved_queue.get("name", "Restored Queue")
        if isinstance(saved_queue.get("name"), str)
        else "Restored Queue",
        current_index,
    )
    playlist.repeat = (
        saved_queue["repeat"]
        if isinstance(saved_queue.get("repeat"), bool)
        else True
    )

    position = saved_queue.get("position", 0.0)
    if (
        not isinstance(position, (int, float))
        or isinstance(position, bool)
        or not math.isfinite(position)
        or position < 0
    ):
        position = 0.0
    return {
        "position": float(position),
        "paused": saved_queue.get("paused", False) is True,
    }


def main():

    # Vérifie mpv

    if shutil.which("mpv") is None:

        console.print(
            Panel(
                "[red]mpv is required.[/red]\n\n" "brew install mpv",
                title="HARMONY",
                border_style="red",
            )
        )

        return

    # Library

    library = load_library()
    log(library)
    state = load_player_state()

    global PLAYER_STATE
    PLAYER_STATE = state

    albums = library.get(
        "albums",
        [],
    )

    if not albums:

        console.print("[yellow]No  album.[/yellow]")

        return

    # MPV

    mpv = MPV()
    spectrum = Spectrum(mpv)
    search = Search(library)
    artists = group_by_artist(library)
    artist_index = 0


    # PLAYLIST

    playlist = Playlist()

    # AUTO NEXT

    stop_auto_next = threading.Event()
    watcher = None

    # UI STATE

    navigation = Navigation("home")

    album_index = 0

    track_index = 0

    collection_index = 0

    playlist_index = 0

    playlist_track_index = 0
    database = Database(DATABASE_PATH, migrate_from=LEGACY_DATABASE_PATH)
    resumed_queue = restore_playback_queue(database, playlist, library)
    daily_album = album_of_the_day(albums, database)
    theme_manager = ThemeManager(database)
    theme_index = next(
        index
        for index, option in enumerate(THEMES)
        if option.name == theme_manager.base_theme.name
    )
    saved_flow_mode = database.get_setting("flow_mode", "smart")
    flow_mode_index = next(
        (
            index
            for index, option in enumerate(FLOW_MODES)
            if option[0] == saved_flow_mode
        ),
        1,
    )
    art_modes = ("classic", "color", "detailed")
    art_mode = database.get_setting("art_mode", "color")
    if art_mode not in art_modes:
        art_mode = "color"
    home_selected_track = 0
    rediscover_refresh = 0
    home_rediscover = rediscover_tracks(
        library,
        state["history"],
        state["favorites"],
        limit=10,
        seed=time.strftime("%Y-%m-%d"),
    )

    keyboard = Keyboard()

    #PLAYLIST HELPERS

    pending_track = None
    return_mode = "albums"
    now_playing_return_mode = "home"
    theme_return_mode = "home"
    stats_return_mode = "home"
    stats_period_index = len(STAT_PERIODS) - 1

    def persist_playback_queue():
        tracks, current_index, name = playlist.snapshot()
        position = mpv.get_time_pos() or 0.0
        if not math.isfinite(position) or position < 0:
            position = 0.0
        database.save_playback_queue(
            {
                "tracks": [str(track.get("path") or "") for track in tracks],
                "index": current_index,
                "name": name,
                "repeat": playlist.repeat,
                "position": position,
                "paused": bool(mpv.paused),
            }
        )

    def play_next_from_controls():
        if not playlist.has_tracks():
            album = albums[album_index]
            playlist.set(
                tracks_from_album(album),
                album_name(album),
                track_index if navigation.current == "tracks" else 0,
            )
        else:
            playlist.next()

        play_current(mpv, playlist, state)

    def play_previous_from_controls():
        if not playlist.has_tracks():
            album = albums[album_index]
            playlist.set(
                tracks_from_album(album),
                album_name(album),
                track_index if navigation.current == "tracks" else 0,
            )
        else:
            playlist.previous()

        play_current(mpv, playlist, state)

    def play_artist(index, shuffle=True):
        if not artists:
            return
        name, tracks = artists[index]
        playlist.set(list(tracks), f"MIX {name}", 0)
        if shuffle:
            playlist.randomize(0)
        play_current(mpv, playlist, state)

    def toggle_pause_from_controls():
        mpv.pause()

    def play_from_controls():
        mpv.set_paused(False)

    def pause_from_controls():
        mpv.set_paused(True)

    def handle_search_action(action):
        if action == "exit":
            navigation.navigate("albums")

        elif action in ("queue_next", "queue_last") and search.selected:
            track = search.selected
            album = search.selected_album

            if not playlist.has_tracks():
                playlist.set([track], album_name(album), 0)
                play_current(mpv, playlist, state)

            elif action == "queue_next":
                playlist.add_next(track, album)

            else:
                playlist.add_last(track, album)

            search.move(1)

        elif action == "favorite" and search.selected:
            toggle_favorite(state, search.selected)

        elif action == "playlist" and search.selected and state["playlists"]:
            add_to_playlist(
                state,
                next(iter(state["playlists"])),
                search.selected,
            )

        elif action == "next":
            play_next_from_controls()

        elif action == "pause":
            mpv.pause()

    def request_playlist_selection(track):
        nonlocal pending_track, return_mode, playlist_index

        if not state["playlists"]:
            return

        pending_track = track
        return_mode = navigation.current
        playlist_index = 0
        navigation.navigate("choose_playlist")

    media_controls = MacMediaControls(
        play_next_from_controls,
        play_previous_from_controls,
        toggle_pause_from_controls,
        play_from_controls,
        pause_from_controls,
        console,
    )
    mpv.media_controls = media_controls
    log("mpv media controls ok")

    try:

        mpv.start()
        media_controls.start()

        watcher = threading.Thread(
            target=mpv_watcher,
            args=(
                mpv,
                playlist,
                state,
                stop_auto_next,
            ),
            daemon=True,
        )

        watcher.start()

        if resumed_queue is not None and play_current(
            mpv,
            playlist,
            state,
            start_position=resumed_queue["position"],
        ):
            if resumed_queue["paused"]:
                mpv.set_paused(True)

        # TERMINAL

        keyboard.start()

        # LIVE
        #
        # IMPORTANT :
        #
        # Le rendu Rich est maintenant effectué UNIQUEMENT
        # depuis ce thread.
        #
        # Il n'y a plus de ui_renderer() parallèle.

        with Live(
            console=console,
            screen=True,
            refresh_per_second=20,
            transient=False,
            auto_refresh=False,
        ) as live:

            running = True
            last_queue_checkpoint = time.monotonic()

            while running:

                now = time.monotonic()
                if now - last_queue_checkpoint >= 5:
                    persist_playback_queue()
                    last_queue_checkpoint = now

                media_controls.pump()
                current_track = playlist.current
                artwork_path = (
                    current_track.get("_cover")
                    if current_track
                    else daily_album.get("cover") if daily_album else None
                )
                theme = theme_manager.current(artwork_path)

                # RENDER

                if navigation.current == "home":
                    recent_tracks = recently_played(state["history"], library)
                    favorite_tracks = resolve_references(
                        [{"key": key} for key in state["favorites"]],
                        library,
                    )
                    home_selected_track = min(
                        home_selected_track,
                        max(0, len(home_rediscover) - 1),
                    )
                    screen = build_home_screen(
                        library,
                        daily_album,
                        recent_tracks,
                        home_rediscover,
                        favorite_tracks,
                        home_selected_track,
                        playlist,
                        mpv,
                        console,
                        time.localtime().tm_hour,
                        theme,
                    )

                elif navigation.current == "now_playing":
                    spectrum.update()
                    screen = build_now_playing_screen(
                        playlist,
                        mpv,
                        console,
                        theme,
                        art_mode,
                        spectrum,
                    )

                elif navigation.current == "theme_settings":
                    screen = build_theme_screen(
                        theme_manager,
                        theme_index,
                        theme,
                        console,
                    )

                elif navigation.current == "flow":
                    screen = build_flow_screen(flow_mode_index, theme, console)

                elif navigation.current == "stats":
                    period_key = STAT_PERIODS[stats_period_index][0]
                    stats = database.get_listening_stats(
                        period_start(period_key)
                    )
                    screen = build_stats_screen(
                        stats,
                        stats_period_index,
                        playlist,
                        mpv,
                        console,
                        theme,
                    )

                elif navigation.current == "albums":

                    screen = build_album_screen(
                        albums,
                        album_index,
                        playlist,
                        mpv,
                        console,
                        theme,
                    )

                elif navigation.current == "tracks":

                    screen = build_track_screen(
                        albums, album_index, track_index, playlist, mpv, console, theme
                    )

                elif navigation.current == "Spectrum_Visualizer":

                    screen = build_spectrum_screen(
                        playlist, mpv, console, spectrum, theme
                    )

                elif navigation.current in ("favorites", "history"):

                    references = (
                        [{"key": key} for key in state["favorites"]]
                        if navigation.current == "favorites"
                        else state["history"]
                    )
                    collection = resolve_references(
                        references,
                        library,
                    )
                    collection_index = min(
                        collection_index,
                        max(0, len(collection) - 1),
                    )

                    screen = build_collection_screen(
                        "FAVORITES" if navigation.current == "favorites" else "HISTORY",
                        collection,
                        collection_index,
                        playlist,
                        mpv,
                        navigation.current,
                        console,
                        theme,
                    )

                elif navigation.current == "artists":

                    screen = build_artists_screen(
                        artists, artist_index, playlist, mpv, console, theme
                    )

                elif navigation.current == "search":

                    screen = build_search_screen(search, playlist, mpv, console, theme)

                elif navigation.current == "queue":
                    screen = build_queue_screen(
                        playlist, queue_index, move_mode, mpv, console, theme
                    )

                elif navigation.current == "choose_playlist":

                    screen = build_playlist_picker_screen(
                        state,
                        playlist_index,
                        pending_track,
                        playlist,
                        mpv,
                        console,
                        theme,
                    )

                else:

                    screen = build_playlists_screen(
                        state,
                        library,
                        playlist_index,
                        playlist_track_index,
                        playlist,
                        mpv,
                        console,
                        theme,
                    )

                live.update(
                    screen,
                    refresh=True,
                )

                # INPUT NON BLOQUÉ
                #
                # On attend au maximum 50 ms.
                #
                # Cela permet :
                #   - aux paroles de bouger
                #   - à la barre de progresser
                #   - aux touches de ne plus être perdues

                readable, _, _ = select.select(
                    [sys.stdin],
                    [],
                    [],
                    0.05,
                )

                if not readable:
                    continue

                # On vide les touches disponibles.
                #
                # Ça évite également l'impression de 
                # "mange" des touches lorsque plusieurs sont
                # pressées rapidement.

                while True:

                    key = keyboard.read_key()

                    if key is None:
                        break

                    # SEARCH : en saisie, tout est capturé (q, s, v... sont des lettres)

                    if navigation.current == "search" and search.typing:
                        handle_search_action(search.handle_key(key))
                        continue

                    # QUIT

                    if key in (
                        "q",
                        "Q",
                    ):

                        running = False
                        break

                    if key in ("w", "W"):
                        pending_track = None
                        navigation.navigate("home")
                        continue

                    if key in ("b", "B"):
                        if navigation.current == "stats":
                            navigation.navigate(stats_return_mode)
                        else:
                            stats_return_mode = navigation.current
                            navigation.navigate("stats")
                        continue

                    if navigation.current == "choose_playlist":

                        names = list(state["playlists"])

                        if key == "ESC":
                            pending_track = None
                            navigation.navigate(return_mode)

                        elif names and key in ("LEFT", "UP", "["):
                            playlist_index = (playlist_index - 1) % len(names)

                        elif names and key in ("RIGHT", "DOWN", "]"):
                            playlist_index = (playlist_index + 1) % len(names)

                        elif names and key in ("\r", "\n") and pending_track is not None:
                            selected_name = names[playlist_index]

                            added = add_to_playlist(
                                state,
                                selected_name,
                                pending_track,
                            )

                            pending_track = None
                            navigation.navigate(return_mode)

                            if added:
                                log(f"track added to playlist {selected_name!r}")

                        continue

                    if navigation.current == "theme_settings":
                        if key == "ESC":
                            navigation.navigate(theme_return_mode)
                        elif key in ("UP", "DOWN"):
                            delta = -1 if key == "UP" else 1
                            theme_index = (theme_index + delta) % len(THEMES)
                        elif key in ("\r", "\n"):
                            selected_theme = THEMES[theme_index]
                            theme_manager.select(selected_theme.name)
                            event_bus.publish(
                                "theme_changed",
                                name=selected_theme.name,
                            )
                        elif key in ("d", "D"):
                            enabled = theme_manager.toggle_dynamic()
                            event_bus.publish(
                                "theme_changed",
                                name=theme_manager.base_theme.name,
                                dynamic=enabled,
                            )
                        continue

                    if navigation.current == "flow":
                        if key == "ESC":
                            navigation.navigate("home")
                        elif key in ("UP", "DOWN"):
                            delta = -1 if key == "UP" else 1
                            flow_mode_index = (
                                flow_mode_index + delta
                            ) % len(FLOW_MODES)
                        elif key in ("\r", "\n"):
                            mode_key, mode_name, _ = FLOW_MODES[flow_mode_index]
                            queued_tracks, current_index, _ = playlist.snapshot()
                            current = playlist.current
                            flow_tracks = build_flow_queue(
                                library,
                                state["favorites"],
                                state["history"],
                                mode=mode_key,
                                current=current,
                                queued_tracks=(
                                    queued_tracks[current_index + 1 :]
                                    if queued_tracks
                                    else []
                                ),
                            )
                            if flow_tracks:
                                database.set_setting("flow_mode", mode_key)
                                playlist.set(
                                    flow_tracks,
                                    f"HARMONY FLOW · {mode_name}",
                                    0,
                                )
                                if current is None:
                                    play_current(mpv, playlist, state)
                                event_bus.publish(
                                    "queue_updated",
                                    mode=mode_key,
                                    size=len(flow_tracks),
                                )
                                now_playing_return_mode = "home"
                                navigation.navigate("now_playing")
                        continue

                    if navigation.current == "stats":
                        if key == "ESC":
                            navigation.navigate(stats_return_mode)
                        elif key in ("UP", "DOWN"):
                            delta = -1 if key == "UP" else 1
                            stats_period_index = (
                                stats_period_index + delta
                            ) % len(STAT_PERIODS)
                        continue

                    if navigation.current == "now_playing":
                        if key == "ESC":
                            navigation.navigate(now_playing_return_mode)
                        elif key == " ":
                            mpv.pause()
                        elif key in ("n", "N"):
                            play_next_from_controls()
                        elif key in ("p", "P"):
                            play_previous_from_controls()
                        elif key in ("f", "F") and playlist.current:
                            toggle_favorite(state, playlist.current)
                        elif key in ("s", "S"):
                            playlist.shuffle_upcoming()
                        elif key in ("e", "E"):
                            playlist.toggle_repeat()
                        elif key in ("+", "="):
                            mpv.set_volume(mpv.volume + 5)
                        elif key == "-":
                            mpv.set_volume(mpv.volume - 5)
                        elif key in ("c", "C"):
                            art_mode = art_modes[
                                (art_modes.index(art_mode) + 1) % len(art_modes)
                            ]
                            database.set_setting("art_mode", art_mode)
                        elif key in ("y", "Y"):
                            theme_return_mode = navigation.current
                            theme_index = next(
                                index
                                for index, option in enumerate(THEMES)
                                if option.name == theme_manager.base_theme.name
                            )
                            navigation.navigate("theme_settings")
                        continue

                    if navigation.current == "home":
                        if home_rediscover and key in (
                            "UP",
                            "DOWN",
                            "LEFT",
                            "RIGHT",
                        ):
                            if console.size.width >= 58:
                                grid_columns = 2
                                row = home_selected_track // grid_columns
                                column = home_selected_track % grid_columns
                                row_count = (
                                    len(home_rediscover) + grid_columns - 1
                                ) // grid_columns
                                if key in ("UP", "DOWN"):
                                    row = (
                                        row + (-1 if key == "UP" else 1)
                                    ) % row_count
                                else:
                                    column = (
                                        column
                                        + (-1 if key == "LEFT" else 1)
                                    ) % grid_columns
                                home_selected_track = min(
                                    row * grid_columns + column,
                                    len(home_rediscover) - 1,
                                )
                            elif key in ("UP", "DOWN"):
                                delta = -1 if key == "UP" else 1
                                home_selected_track = (
                                    home_selected_track + delta
                                ) % len(home_rediscover)
                            continue

                        if key in ("\r", "\n") and home_rediscover:
                            selected = home_rediscover[home_selected_track]
                            selected_album = album_for_track(selected, albums)
                            playlist.set(
                                [selected],
                                album_name(selected_album)
                                if selected_album
                                else album_of(selected),
                                0,
                            )
                            play_current(mpv, playlist, state)
                            continue

                        if key in ("d", "D") and daily_album:
                            playlist.set(
                                tracks_from_album(daily_album),
                                album_name(daily_album),
                                0,
                            )
                            play_current(mpv, playlist, state)
                            continue

                        if key in ("s", "S") and daily_album:
                            playlist.set(
                                tracks_from_album(daily_album),
                                album_name(daily_album),
                                0,
                            )
                            playlist.randomize(0)
                            play_current(mpv, playlist, state)
                            continue

                        if key in ("o", "O") and daily_album:
                            album_index = albums.index(daily_album)
                            track_index = 0
                            navigation.navigate("tracks")
                            continue

                        if key in ("n", "N"):
                            daily_album = next_album_of_the_day(
                                albums,
                                database,
                            )
                            continue

                        if key in ("r", "R"):
                            rediscover_refresh += 1
                            home_rediscover = rediscover_tracks(
                                library,
                                state["history"],
                                state["favorites"],
                                limit=10,
                                seed=(
                                    time.strftime("%Y-%m-%d")
                                    + f":{rediscover_refresh}"
                                ),
                            )
                            home_selected_track = 0
                            continue

                        if key in ("a", "A") and home_rediscover:
                            selected = home_rediscover[home_selected_track]
                            selected_album = album_for_track(selected, albums)
                            if playlist.has_tracks():
                                playlist.add_next(selected, selected_album)
                            else:
                                playlist.set([selected], album_of(selected), 0)
                                play_current(mpv, playlist, state)
                            continue

                        if key in ("m", "M") and home_rediscover:
                            request_playlist_selection(
                                home_rediscover[home_selected_track]
                            )
                            continue

                        if key in ("f", "F") and home_rediscover:
                            toggle_favorite(
                                state,
                                home_rediscover[home_selected_track],
                            )
                            continue

                        if key in ("p", "P"):
                            if playlist.current:
                                mpv.set_paused(False)
                            elif state["history"]:
                                recent = recently_played(
                                    state["history"],
                                    library,
                                    limit=1,
                                )
                                if recent:
                                    playlist.set(
                                        recent,
                                        album_of(recent[0]),
                                        0,
                                    )
                                    play_current(mpv, playlist, state)
                            elif daily_album:
                                playlist.set(
                                    tracks_from_album(daily_album),
                                    album_name(daily_album),
                                    0,
                                )
                                play_current(mpv, playlist, state)
                            continue

                        if key in ("x", "X"):
                            fill_giga_playlist(playlist, library)
                            play_current(mpv, playlist, state)
                            continue

                    if key in ("i", "I"):
                        now_playing_return_mode = navigation.current
                        navigation.navigate("now_playing")
                        continue

                    if key in ("g", "G"):
                        navigation.navigate("flow")
                        continue

                    if key in ("y", "Y"):
                        theme_return_mode = navigation.current
                        theme_index = next(
                            index
                            for index, option in enumerate(THEMES)
                            if option.name == theme_manager.base_theme.name
                        )
                        navigation.navigate("theme_settings")
                        continue

                    if key in ("v", "V"):
                        navigation.navigate("favorites")
                        collection_index = 0
                        continue

                    if key in ("t", "T"):
                        navigation.navigate("artists")
                        continue

                    if key in ("u", "U"):
                        navigation.navigate("queue")
                        queue_index = playlist.current_index
                        move_mode = False
                        continue

                    if key in ("s", "S") and navigation.current != "queue":
                        navigation.navigate("Spectrum_Visualizer")
                        continue

                    if key in ("h", "H"):
                        navigation.navigate("history")
                        collection_index = 0
                        continue

                    if key in ("l", "L"):
                        navigation.navigate("playlists")
                        playlist_index = 0
                        playlist_track_index = 0
                        continue

                    if key == "/":
                        search.open(reset=(navigation.current != "search"))
                        navigation.navigate("search")
                        continue

                    # ALBUM MODE

                    if navigation.current == "albums":

                        cols = columns(console)

                        # ----------------------------------------
                        # LEFT
                        # ----------------------------------------

                        if key == "LEFT":

                            album_index = max(
                                0,
                                album_index - 1,
                            )

                        # ----------------------------------------
                        # RIGHT
                        # ----------------------------------------

                        elif key == "RIGHT":

                            album_index = min(
                                len(albums) - 1,
                                album_index + 1,
                            )

                        # ----------------------------------------
                        # UP
                        # ----------------------------------------

                        elif key == "UP":

                            album_index = max(
                                0,
                                album_index - cols,
                            )

                        # ----------------------------------------
                        # DOWN
                        # ----------------------------------------

                        elif key == "DOWN":

                            album_index = min(
                                len(albums) - 1,
                                album_index + cols,
                            )

                        # ----------------------------------------
                        # OPEN ALBUM
                        # ----------------------------------------

                        elif key in (
                            "\r",
                            "\n",
                        ):

                            navigation.navigate("tracks")

                            track_index = 0

                        # ----------------------------------------
                        # GIGA RANDOM
                        # ----------------------------------------

                        elif key in (
                            "r",
                            "R",
                        ):

                            fill_giga_playlist(
                                playlist,
                                library,
                            )

                            play_current(
                                mpv,
                                playlist,
                            )

                        # ----------------------------------------
                        # NEXT
                        # ----------------------------------------

                        elif key in (
                            "n",
                            "N",
                        ):
                            play_next_from_controls()

                        # ----------------------------------------
                        # PREVIOUS
                        # ----------------------------------------

                        elif key in (
                            "p",
                            "P",
                        ):
                            play_previous_from_controls()

                        # ----------------------------------------
                        # PAUSE
                        # ----------------------------------------

                        elif key == " ":

                            mpv.pause()

                    # SPECTRUM MODE
                    elif navigation.current == "Spectrum_Visualizer":

                        if key == "ESC":
                            navigation.navigate("albums")

                        elif key == " ":
                            mpv.pause()

                        elif key in ("n", "N"):
                            play_next_from_controls()

                        elif key in ("p", "P"):
                            play_previous_from_controls()

                    # TRACK MODE

                    elif navigation.current == "tracks":

                        album = albums[album_index]

                        tracks = album.get(
                            "tracks",
                            [],
                        )

                        # ----------------------------------------
                        # BACK
                        # ----------------------------------------

                        if key == "ESC":

                            navigation.navigate("albums")

                        # ----------------------------------------
                        # UP
                        # ----------------------------------------

                        elif key == "UP":

                            if tracks:

                                track_index = max(
                                    0,
                                    track_index - 1,
                                )

                        # ----------------------------------------
                        # DOWN
                        # ----------------------------------------

                        elif key == "DOWN":

                            if tracks:

                                track_index = min(
                                    len(tracks) - 1,
                                    track_index + 1,
                                )

                        # ----------------------------------------
                        # PLAY
                        # ----------------------------------------

                        elif key in ("\r", "\n"):
                            if tracks:
                                if not playlist.has_tracks():
                                    playlist.set(
                                        [tracks[track_index]],
                                        album_name(album),
                                        0,
                                    )
                                    play_current(mpv, playlist, state)
                                else:
                                    playlist.add_next(tracks[track_index], album)

                                if track_index < len(tracks) - 1:
                                    track_index += 1

                        elif key in ("p", "P") and tracks:
                            if not playlist.has_tracks():
                                playlist.set(
                                    [tracks[track_index]],
                                    album_name(album),
                                    0,
                                )
                                play_current(mpv, playlist, state)
                            else:
                                playlist.add_last(tracks[track_index], album)

                            if track_index < len(tracks) - 1:
                                track_index += 1

                        # ----------------------------------------
                        # GIGA RANDOM
                        # ----------------------------------------

                        elif key in (
                            "r",
                            "R",
                        ):

                            fill_giga_playlist(
                                playlist,
                                library,
                            )

                            play_current(
                                mpv,
                                playlist,
                            )

                        # ----------------------------------------
                        # NEXT
                        # ----------------------------------------

                        elif key in (
                            "n",
                            "N",
                        ):
                            play_next_from_controls()

                        elif key in ("f", "F") and tracks:

                            toggle_favorite(
                                state,
                                tracks[track_index],
                            )

                        elif key in ("a", "A") and tracks:
                            request_playlist_selection(tracks[track_index])

                        # ----------------------------------------
                        # PAUSE
                        # ----------------------------------------

                        elif key == " ":

                            mpv.pause()

                    elif navigation.current == "artists":

                        if key == "ESC":
                            navigation.navigate("albums")

                        elif key == "UP" and artists:
                            artist_index = max(0, artist_index - 1)

                        elif key == "DOWN" and artists:
                            artist_index = min(len(artists) - 1, artist_index + 1)

                        elif key in ("\r", "\n"):
                            play_artist(artist_index, shuffle=True)

                        elif key in ("o", "O"):
                            play_artist(artist_index, shuffle=False)

                        elif key in ("n", "N"):
                            play_next_from_controls()

                        elif key in ("p", "P"):
                            play_previous_from_controls()

                        elif key == " ":
                            mpv.pause()

                    elif navigation.current == "queue":

                        total = len(playlist.tracks)

                        if key == "ESC":
                            navigation.navigate("albums")
                            move_mode = False

                        elif key == "UP" and total:
                            if move_mode:
                                queue_index = playlist.move(queue_index, -1)
                            else:
                                queue_index = max(0, queue_index - 1)

                        elif key == "DOWN" and total:
                            if move_mode:
                                queue_index = playlist.move(queue_index, +1)
                            else:
                                queue_index = min(total - 1, queue_index + 1)

                        elif key in ("m", "M") and total:
                            if queue_index > playlist.current_index:
                                move_mode = not move_mode
                            else:
                                move_mode = False

                        elif key in ("\r", "\n") and total:
                            if playlist.promote(queue_index) is not None:
                                queue_index = playlist.current_index
                                play_current(mpv, playlist, state)

                        elif key in ("d", "D") and total:
                            removed_current = playlist.remove(queue_index)
                            queue_index = max(0, min(queue_index, len(playlist.tracks) - 1))
                            if removed_current:
                                if playlist.has_tracks():
                                    play_current(mpv, playlist, state)
                                else:
                                    mpv.set_paused(True)

                        elif key in ("x", "X"):
                            playlist.clear_upcoming()
                            queue_index = 0
                            move_mode = False

                        elif key in ("e", "E"):
                            playlist.toggle_repeat()

                        elif key in ("s", "S"):
                            playlist.shuffle_upcoming()

                        elif key == " ":
                            mpv.pause()

                    elif navigation.current == "search":

                        handle_search_action(search.handle_key(key))

                    elif navigation.current in ("favorites", "history"):

                        references = (
                            [{"key": key} for key in state["favorites"]]
                            if navigation.current == "favorites"
                            else state["history"]
                        )
                        tracks = resolve_references(
                            references,
                            library,
                        )

                        if key == "ESC":
                            navigation.navigate("albums")

                        elif key == "UP" and tracks:
                            collection_index = max(
                                0,
                                collection_index - 1,
                            )

                        elif key == "DOWN" and tracks:
                            collection_index = min(
                                len(tracks) - 1,
                                collection_index + 1,
                            )

                        elif key in ("\r", "\n") and tracks:
                            selected = tracks[collection_index]
                            selected_album = next(
                                album
                                for album in albums
                                if album_name(album) == album_of(selected)
                                or any(
                                    track_key(item) == track_key(selected)
                                    for item in album.get("tracks", [])
                                )
                            )
                            selected_index = next(
                                index
                                for index, item in enumerate(
                                    selected_album.get("tracks", [])
                                )
                                if track_key(item) == track_key(selected)
                            )
                            playlist.set(
                                tracks,
                                navigation.current.upper(),
                                collection_index,
                            )
                            play_current(mpv, playlist)
                            album_index = albums.index(selected_album)
                            track_index = selected_index

                        elif key in ("f", "F") and tracks:
                            toggle_favorite(
                                state,
                                tracks[collection_index],
                            )

                        elif key in ("a", "A") and tracks:
                            request_playlist_selection(tracks[collection_index])

                    else:

                        names = list(state["playlists"])
                        if names:
                            playlist_index %= len(names)
                        else:
                            playlist_index = 0

                        selected_name = (
                            names[playlist_index]
                            if names and playlist_index < len(names)
                            else None
                        )
                        playlist_tracks = (
                            resolve_references(
                                state["playlists"].get(selected_name, []),
                                library,
                            )
                            if selected_name
                            else []
                        )

                        if key == "ESC":
                            navigation.navigate("albums")

                        elif key == "UP" and playlist_tracks:
                            playlist_track_index = max(
                                0,
                                playlist_track_index - 1,
                            )

                        elif key == "DOWN" and playlist_tracks:
                            playlist_track_index = min(
                                len(playlist_tracks) - 1,
                                playlist_track_index + 1,
                            )

                        elif key in ("LEFT", "[") and names:
                            playlist_index = (playlist_index - 1) % len(names)
                            playlist_track_index = 0

                        elif key in ("RIGHT", "]") and names:
                            playlist_index = (playlist_index + 1) % len(names)
                            playlist_track_index = 0

                        elif key in ("c", "C"):
                            name = prompt_playlist_name(live, keyboard)
                            create_playlist(state, name)

                        elif key in ("a", "A") and tracks:
                            request_playlist_selection(tracks[collection_index])

                        elif key in ("f", "F") and playlist_tracks:
                            toggle_favorite(
                                state,
                                playlist_tracks[playlist_track_index],
                            )

                        elif key in ("a", "A") and selected_name and playlist.current:
                            if add_to_playlist(state, selected_name, playlist.current):
                                log(f"Track added to playlist {selected_name!r}")

                        elif key in ("\r", "\n") and playlist_tracks:
                            selected = playlist_tracks[playlist_track_index]
                            playlist.set(
                                playlist_tracks,
                                selected_name,
                                playlist_track_index,
                            )
                            playlist.randomize(playlist_track_index)
                            play_current(mpv, playlist)

                    readable, _, _ = select.select(
                        [sys.stdin],
                        [],
                        [],
                        0,
                    )

                    if not readable:
                        break

    except KeyboardInterrupt:

        pass

    finally:

        # Restaure le terminal AVANT de quitter.

        keyboard.stop()

        # Arrête le watcher avant mpv.

        stop_auto_next.set()
        if watcher is not None:
            watcher.join(timeout=2)

        try:
            persist_playback_queue()
        finally:
            media_controls.close()
            mpv.quit()


if __name__ == "__main__":
    console.clear()
    header()
    if touch_id():
        main()
    else:
        header()
        console.print(
            Panel(
                "[red]Touch ID denied.[/red]",
                title="HARMONY",
                border_style="red",
            )
        )

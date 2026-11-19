"""Home dashboard selections based exclusively on the local music library."""

from __future__ import annotations

import hashlib
import os
import random
from datetime import date
from typing import Any

from app.data.database import Database


def _album_name(album):
    return album.get("name") or "Unknown Album"


def _artist(track):
    return track.get("artist") or "Unknown Artist"


def _track_key(track):
    return str(track.get("path") or track.get("title") or "")


def _tracks_from_album(album):
    return [
        {
            **track,
            "_album": _album_name(album),
            "_cover": album.get("cover"),
        }
        for track in album.get("tracks", [])
    ]


def _day_key(day):
    if day is None:
        return date.today().isoformat()
    return day.isoformat() if isinstance(day, date) else str(day)


def album_key(album: dict[str, Any]):
    identity = album.get("folder") or (
        f"{_album_name(album)}\0"
        + "\0".join(
            sorted(
                {
                    _artist(track)
                    for track in album.get("tracks", [])
                }
            )
        )
    )
    return hashlib.sha256(str(identity).encode("utf-8")).hexdigest()


def album_of_the_day(albums: list[dict[str, Any]], database: Database, day=None):
    available = [album for album in albums if album.get("tracks")]
    if not available:
        return None

    current_day = _day_key(day)
    by_key = {album_key(album): album for album in available}
    selected_key = database.get_daily_album(list(by_key), current_day)
    return by_key.get(selected_key)


def next_album_of_the_day(albums: list[dict[str, Any]], database: Database, day=None):
    available = [album for album in albums if album.get("tracks")]
    if len(available) < 2:
        return album_of_the_day(available, database, day)

    current_day = _day_key(day)
    order = sorted(
        available,
        key=lambda album: hashlib.sha256(
            f"{current_day}:{album_key(album)}".encode("utf-8")
        ).hexdigest(),
    )
    current = album_of_the_day(available, database, day)
    index = next(
        (i for i, album in enumerate(order) if album_key(album) == album_key(current)),
        -1,
    )
    selected = order[(index + 1) % len(order)]
    database.set_daily_album(album_key(selected), current_day)
    return selected


def recently_played(history, library, limit=5):
    tracks = {
        _track_key(track): track
        for album in library.get("albums", [])
        for track in _tracks_from_album(album)
    }
    return [
        tracks[reference["key"]]
        for reference in history[:limit]
        if isinstance(reference, dict) and reference.get("key") in tracks
    ]


def rediscover_tracks(
    library: dict[str, Any],
    history: list[dict[str, Any]],
    favorites: list[str],
    limit=10,
    seed: str = "",
):
    """Rank playable tracks by low recent exposure, with deterministic variety."""
    tracks = [
        track
        for album in library.get("albums", [])
        for track in _tracks_from_album(album)
        if track.get("path") and os.path.isfile(str(track["path"]))
    ]
    if not tracks:
        return []

    recent_positions = {}
    play_counts = {}
    for position, reference in enumerate(history):
        key = reference.get("key") if isinstance(reference, dict) else None
        if not key:
            continue
        recent_positions.setdefault(key, position)
        play_counts[key] = play_counts.get(key, 0) + 1

    recent_keys = set(recent_positions)
    candidates = [track for track in tracks if _track_key(track) not in recent_keys]
    if len(candidates) < min(limit, len(tracks)):
        candidates = tracks

    rng = random.Random(seed)
    ranked = []
    for track in candidates:
        key = _track_key(track)
        rank = recent_positions.get(key)
        recency_score = (rank + 1) if rank is not None else len(history) + 10
        exposure_score = 1 / (play_counts.get(key, 0) + 1)
        favorite_score = 0.1 if key in favorites else 0
        score = recency_score + exposure_score + favorite_score
        ranked.append((score, rng.random(), track))

    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)

    selected = []
    artists = set()
    albums = set()
    remaining = ranked[:]
    while remaining and len(selected) < limit:
        best_index = max(
            range(len(remaining)),
            key=lambda index: (
                remaining[index][0]
                + (1.5 if _artist(remaining[index][2]) not in artists else 0)
                + (0.75 if remaining[index][2].get("_album") not in albums else 0),
                remaining[index][1],
            ),
        )
        _, _, track = remaining.pop(best_index)
        selected.append(track)
        artists.add(_artist(track))
        albums.add(track.get("_album"))

    return selected


def greeting(hour: int):
    if 5 <= hour < 12:
        return "Good morning."
    if 12 <= hour < 17:
        return "Good afternoon."
    if 17 <= hour < 22:
        return "Good evening."
    return "Good night."


def album_artists(album):
    return ", ".join(
        dict.fromkeys(
            _artist(track)
            for track in album.get("tracks", [])
        )
    ) or "Unknown Artist"


def library_track_count(library):
    return sum(len(album.get("tracks", [])) for album in library.get("albums", []))


def library_artist_count(library):
    return len(
        {
            _artist(track)
            for album in library.get("albums", [])
            for track in album.get("tracks", [])
        }
    )


def album_for_track(track, albums):
    key = _track_key(track)
    for album in albums:
        if any(_track_key(item) == key for item in album.get("tracks", [])):
            return album
    return None

"""Local-only queue generation for Harmony Flow shuffle modes."""

from __future__ import annotations

import os
import random
from collections import deque
from typing import Any, Callable

FLOW_MODES = (
    ("classic", "Classic Shuffle", "Random ordering across playable tracks."),
    ("smart", "Smart Shuffle", "Rotate artists and albums; cool down recent plays."),
    ("discovery", "Discovery", "Prefer tracks missing from recent listening history."),
    ("favorites_mix", "Favorites Mix", "Blend favorites with unfamiliar tracks."),
)

# --- Smart Shuffle tuning -------------------------------------------------
SMART_UNSEEN_BONUS = 0.15  # tracks absent from recent history
SMART_FAVORITE_BONUS = 0.35  # favorites
SMART_COOLDOWN = 25  # history entries during which a track is "cooling down"
SMART_COOLDOWN_PUSH = (
    0.6  # how far back (fraction of the queue) a cooling track is pushed
)
SMART_SCORE_BIAS = 0.15  # how much a high score pulls a track earlier in the queue
SMART_ARTIST_RECENCY = 8  # last N history entries used to penalise artists just heard
SMART_ARTIST_PENALTY = 0.4
SMART_ARTIST_GAP = 3  # try not to repeat an artist within this many tracks
SMART_ALBUM_GAP = 2  # try not to repeat an album within this many tracks
SMART_WINDOW = 15  # lookahead used when fixing local repetitions


def _key(track: dict[str, Any]):
    return str(track.get("path") or track.get("title") or "")


def _name(track: dict[str, Any], key: str):
    return str(track.get(key) or "Unknown").casefold()


# --- Smart Shuffle helpers ------------------------------------------------


def _artist(track: dict[str, Any]) -> str | None:
    """Artist used for spreading; unknown artists are never grouped together."""
    value = track.get("artist")
    return str(value).casefold() if value else None


def _album(track: dict[str, Any]) -> str | None:
    value = track.get("_album")
    if not value or str(value) == "Unknown Album":
        return None
    return str(value).casefold()


def _group(
    items: list[dict[str, Any]], group_of: Callable
) -> list[list[dict[str, Any]]]:
    groups: dict[Any, list[dict[str, Any]]] = {}
    for item in items:
        name = group_of(item)
        groups.setdefault(name if name is not None else ("", id(item)), []).append(item)
    return list(groups.values())


def _slots(members, shift_of, rng):
    """Give each member a target position evenly spread over [0, 1)."""
    n = len(members)
    return [((i + rng.random()) / n + shift_of(m), m) for i, m in enumerate(members)]


def _interleave(members, group_of, score_of, shift_of, rng):
    """Order members so each group is evenly spread, best-scored tracks first."""
    placed = []
    for group in _group(members, group_of):
        group.sort(key=score_of, reverse=True)
        placed.extend(_slots(group, shift_of, rng))
    placed.sort(key=lambda item: item[0])
    return [member for _, member in placed]


def _separate(ordered, lead):
    """Fix local repetitions by pulling a better track from a small lookahead window."""
    remaining = list(reversed(ordered))
    recent_artists = deque((_artist(t) for t in lead), maxlen=SMART_ARTIST_GAP)
    recent_albums = deque((_album(t) for t in lead), maxlen=SMART_ALBUM_GAP)
    result = []

    while remaining:
        window = min(SMART_WINDOW, len(remaining))
        previous_artist = recent_artists[-1] if recent_artists else None
        pick = None
        for strict in (True, False):
            for offset in range(window):
                track = remaining[-1 - offset]
                artist, album = _artist(track), _album(track)
                if artist is not None and artist == previous_artist:
                    continue
                if strict and (
                    (artist is not None and artist in recent_artists)
                    or (album is not None and album in recent_albums)
                ):
                    continue
                pick = offset
                break
            if pick is not None:
                break
        if pick is None:
            pick = 0

        track = remaining.pop(len(remaining) - 1 - pick)
        result.append(track)
        recent_artists.append(_artist(track))
        recent_albums.append(_album(track))
    return result


def _smart_order(candidates, favorites, history, available_by_key, lead, rng):
    if not candidates:
        return []

    favorite_keys = set(favorites)
    recent_position: dict[str, int] = {}
    for index, reference in enumerate(history):
        if isinstance(reference, dict) and reference.get("key"):
            recent_position.setdefault(reference["key"], index)

    # Artists heard very recently get a small penalty (variety vs. what was just played).
    artist_recency: dict[str, float] = {}
    for key, index in recent_position.items():
        if index >= SMART_ARTIST_RECENCY:
            continue
        heard = available_by_key.get(key)
        artist = _artist(heard) if heard else None
        if artist is not None:
            artist_recency[artist] = max(
                artist_recency.get(artist, 0.0), 1.0 / (index + 1)
            )

    scores: dict[str, float] = {}
    cooldown: dict[str, float] = {}
    for track in candidates:
        key = _key(track)
        score = rng.random()
        recent_index = recent_position.get(key)
        if recent_index is None:
            score += SMART_UNSEEN_BONUS
        else:
            score -= 3.0 / (recent_index + 1)
            if recent_index < SMART_COOLDOWN:
                cooldown[key] = 1.0 - recent_index / SMART_COOLDOWN
        if key in favorite_keys:
            score += SMART_FAVORITE_BONUS
        artist = _artist(track)
        if artist is not None:
            score -= SMART_ARTIST_PENALTY * artist_recency.get(artist, 0.0)
        scores[key] = score

    low, high = min(scores.values()), max(scores.values())
    span = (high - low) or 1.0

    def score_of(track):
        return scores[_key(track)]

    def shift_of(track):
        key = _key(track)
        normalized = (scores[key] - low) / span
        return -SMART_SCORE_BIAS * normalized + SMART_COOLDOWN_PUSH * cooldown.get(
            key, 0.0
        )

    # 1) Spread every artist evenly over the queue (albums spread inside each artist),
    #    so big artists no longer pile up at the end.
    placed = []
    for group in _group(candidates, _artist):
        inner = _interleave(group, _album, score_of, shift_of, rng)
        placed.extend(_slots(inner, shift_of, rng))
    placed.sort(key=lambda item: item[0])
    ordered = [track for _, track in placed]

    # 2) Clean up remaining artist/album repetitions, including with the current track.
    return _separate(ordered, lead)


def build_flow_queue(
    library: dict[str, Any],
    favorites: list[str],
    history: list[dict[str, Any]],
    mode: str = "smart",
    current: dict[str, Any] | None = None,
    queued_tracks: list[dict[str, Any]] | None = None,
    seed: str | int | None = None,
):
    if mode not in {option[0] for option in FLOW_MODES}:
        raise ValueError(f"Unknown Harmony Flow mode: {mode}")

    tracks = []
    for album in library.get("albums", []):
        for item in album.get("tracks", []):
            path = item.get("path")
            if not path or not os.path.isfile(str(path)):
                continue
            track = dict(item)
            track["_album"] = album.get("name") or track.get("album") or "Unknown Album"
            track["_cover"] = album.get("cover")
            track["_year"] = album.get("year") or album.get("date")
            tracks.append(track)

    if not tracks:
        return []

    current_key = _key(current) if current else None
    available_by_key = {_key(track): track for track in tracks}
    if current_key in available_by_key:
        current = available_by_key[current_key]
    else:
        current = None

    priority = []
    priority_keys = set()
    for queued in queued_tracks or []:
        key = _key(queued)
        if key == current_key or key in priority_keys:
            continue
        track = available_by_key.get(key)
        if track is None and queued.get("path") and os.path.isfile(str(queued["path"])):
            track = dict(queued)
        if track is not None:
            priority.append(track)
            priority_keys.add(key)

    candidates = [
        track
        for track in tracks
        if _key(track) != current_key and _key(track) not in priority_keys
    ]
    rng = random.Random(seed)
    if mode == "classic":
        rng.shuffle(candidates)
    elif mode == "smart":
        lead = ([current] if current else []) + priority
        candidates = _smart_order(
            candidates, favorites, history, available_by_key, lead, rng
        )
    else:
        favorite_keys = set(favorites)
        recent_position = {}
        for index, reference in enumerate(history):
            if isinstance(reference, dict) and reference.get("key"):
                recent_position.setdefault(reference["key"], index)

        scored = []
        for track in candidates:
            key = _key(track)
            recent_index = recent_position.get(key)
            unseen = recent_index is None
            if mode == "discovery":
                score = rng.random() + (5.0 if unseen else 0)
                score += (recent_index or 0) * 0.05
                score += 0.25 if key in favorite_keys else 0
            else:
                score = rng.random() + (0.65 if key in favorite_keys else 0)
                score += 0.8 if unseen else 0
            scored.append((score, track))
        scored.sort(key=lambda item: item[0], reverse=True)
        candidates = [track for _, track in scored]

    return ([current] if current else []) + priority + candidates

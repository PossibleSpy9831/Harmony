"""SQLite persistence for Harmony's local library and player state."""

from __future__ import annotations

import json
import hashlib
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any


DEFAULT_PLAYER_STATE = {
    "favorites": [],
    "playlists": {},
    "history": [],
}
SCHEMA_VERSION = 3


class Database:
    """Owns the local SQLite database and imports legacy JSON player state."""

    def __init__(
        self,
        path: Path | str,
        migrate_from: Path | str | None = None,
    ):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate_database(migrate_from)
        self._initialize()

    def _migrate_database(self, source_path: Path | str | None):
        if source_path is None:
            return

        source_path = Path(source_path)
        if (
            self.path.exists()
            or not source_path.is_file()
            or self.path.resolve() == source_path.resolve()
        ):
            return

        with tempfile.NamedTemporaryFile(
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".migrating",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)

        try:
            with (
                sqlite3.connect(source_path) as source,
                sqlite3.connect(temporary_path) as destination,
            ):
                source.backup(destination)
            if not self.path.exists():
                os.replace(temporary_path, self.path)
        finally:
            temporary_path.unlink(missing_ok=True)

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self):
        with self._connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"Database schema version {version} is newer than supported "
                    f"version {SCHEMA_VERSION}"
                )
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS artists (
                    name TEXT PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS albums (
                    album_key TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    artist TEXT,
                    cover TEXT,
                    year TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS tracks (
                    track_key TEXT PRIMARY KEY,
                    path TEXT,
                    title TEXT,
                    artist TEXT,
                    album TEXT,
                    duration REAL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS library_tracks (
                    track_key TEXT PRIMARY KEY REFERENCES tracks(track_key)
                        ON DELETE CASCADE,
                    album_key TEXT NOT NULL REFERENCES albums(album_key)
                        ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS playlists (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS playlist_tracks (
                    playlist_id INTEGER NOT NULL REFERENCES playlists(id)
                        ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    track_key TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    PRIMARY KEY (playlist_id, position)
                );
                CREATE TABLE IF NOT EXISTS favorites (
                    track_key TEXT PRIMARY KEY,
                    position INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS history (
                    position INTEGER PRIMARY KEY,
                    track_key TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS listening_stats (
                    track_key TEXT PRIMARY KEY,
                    play_count INTEGER NOT NULL DEFAULT 0,
                    listening_seconds REAL NOT NULL DEFAULT 0,
                    last_played_at TEXT
                );
                CREATE TABLE IF NOT EXISTS play_events (
                    id INTEGER PRIMARY KEY,
                    track_key TEXT NOT NULL,
                    played_at TEXT NOT NULL,
                    listened_seconds REAL NOT NULL DEFAULT 0,
                    metadata_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS daily_albums (
                    date TEXT PRIMARY KEY,
                    album_key TEXT NOT NULL
                );
                """
            )
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def sync_library(self, library: dict[str, Any]) -> dict[str, int]:
        """Replace the scanned library catalog without deleting player history."""
        albums = library.get("albums") if isinstance(library, dict) else None
        if not isinstance(albums, list):
            raise ValueError("Library must contain an albums list")

        prepared_albums = []
        seen_tracks = set()
        artists = set()
        track_count = 0

        for album in albums:
            if not isinstance(album, dict):
                raise ValueError("Each library album must be an object")

            folder = album.get("folder")
            name = album.get("name")
            tracks = album.get("tracks")
            if not isinstance(folder, (str, Path)) or not str(folder):
                raise ValueError("Each library album must have a folder path")
            if not isinstance(name, str) or not name:
                raise ValueError("Each library album must have a name")
            if not isinstance(tracks, list):
                raise ValueError(f"Album {name!r} must contain a tracks list")

            album_key = str(Path(folder).expanduser().resolve())
            cover = album.get("cover")
            album_artist = album.get("artist")
            if not album_artist:
                album_artist = next(
                    (
                        track.get("artist")
                        for track in tracks
                        if isinstance(track, dict) and track.get("artist")
                    ),
                    None,
                )
            prepared_tracks = []

            for track in tracks:
                if not isinstance(track, dict):
                    raise ValueError(f"Tracks in album {name!r} must be objects")
                path = track.get("path")
                if not isinstance(path, (str, Path)) or not str(path):
                    raise ValueError(f"Tracks in album {name!r} must have a path")

                track_key = str(Path(path).expanduser().resolve())
                if track_key in seen_tracks:
                    raise ValueError(f"Duplicate library track path: {track_key}")
                seen_tracks.add(track_key)

                artist = track.get("artist") or album_artist
                track_album = track.get("album") or name
                title = track.get("title")
                duration = track.get("duration")
                if duration is not None and (
                    isinstance(duration, bool)
                    or not isinstance(duration, (int, float))
                ):
                    raise ValueError(f"Invalid duration for track {track_key}")
                if artist:
                    artists.add(str(artist))

                prepared_tracks.append(
                    {
                        "track_key": track_key,
                        "path": track_key,
                        "title": str(title) if title is not None else None,
                        "artist": str(artist) if artist else None,
                        "album": str(track_album) if track_album else None,
                        "duration": duration,
                        "metadata_json": json.dumps(
                            self._json_safe(track), ensure_ascii=False
                        ),
                    }
                )

            if album_artist:
                artists.add(str(album_artist))
            prepared_albums.append(
                {
                    "album_key": album_key,
                    "name": name,
                    "artist": str(album_artist) if album_artist else None,
                    "cover": str(cover) if cover else None,
                    "year": str(album["year"]) if album.get("year") is not None else None,
                    "metadata_json": json.dumps(
                        self._json_safe(
                            {key: value for key, value in album.items() if key != "tracks"}
                        ),
                        ensure_ascii=False,
                    ),
                    "tracks": prepared_tracks,
                }
            )
            track_count += len(prepared_tracks)

        with self._connect() as connection:
            connection.execute("DELETE FROM library_tracks")
            connection.execute("DELETE FROM albums")
            connection.execute("DELETE FROM artists")

            for album in prepared_albums:
                connection.execute(
                    """
                    INSERT INTO albums
                        (album_key, name, artist, cover, year, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        album["album_key"],
                        album["name"],
                        album["artist"],
                        album["cover"],
                        album["year"],
                        album["metadata_json"],
                    ),
                )
                for track in album["tracks"]:
                    connection.execute(
                        """
                        INSERT INTO tracks
                            (track_key, path, title, artist, album, duration,
                             metadata_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(track_key) DO UPDATE SET
                            path = excluded.path,
                            title = excluded.title,
                            artist = excluded.artist,
                            album = excluded.album,
                            duration = excluded.duration,
                            metadata_json = excluded.metadata_json
                        """,
                        (
                            track["track_key"],
                            track["path"],
                            track["title"],
                            track["artist"],
                            track["album"],
                            track["duration"],
                            track["metadata_json"],
                        ),
                    )
                    connection.execute(
                        "INSERT INTO library_tracks (track_key, album_key) "
                        "VALUES (?, ?)",
                        (track["track_key"], album["album_key"]),
                    )

            connection.executemany(
                "INSERT INTO artists (name) VALUES (?)",
                ((artist,) for artist in sorted(artists)),
            )

        return {"albums": len(prepared_albums), "tracks": track_count}

    @staticmethod
    def _json_safe(value: Any):
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {key: Database._json_safe(item) for key, item in value.items()}
        if isinstance(value, list):
            return [Database._json_safe(item) for item in value]
        return value

    def load_player_state(self, legacy_path: Path | str | None = None):
        with self._connect() as connection:
            imported = connection.execute(
                "SELECT value_json FROM settings WHERE key = 'legacy_state_imported'"
            ).fetchone()
            if imported is None:
                legacy_state = self._read_legacy_state(legacy_path)
                if legacy_state is not None:
                    self._write_player_state(connection, legacy_state)
                else:
                    self._write_player_state(connection, DEFAULT_PLAYER_STATE)
                connection.execute(
                    "INSERT OR REPLACE INTO settings (key, value_json) "
                    "VALUES ('legacy_state_imported', 'true')"
                )

            favorites = [
                row["track_key"]
                for row in connection.execute(
                    "SELECT track_key FROM favorites ORDER BY position"
                )
            ]
            playlists = {}
            for playlist in connection.execute(
                "SELECT id, name FROM playlists ORDER BY id"
            ):
                playlists[playlist["name"]] = [
                    json.loads(row["metadata_json"])
                    for row in connection.execute(
                        "SELECT metadata_json FROM playlist_tracks "
                        "WHERE playlist_id = ? ORDER BY position",
                        (playlist["id"],),
                    )
                ]
            history = [
                json.loads(row["metadata_json"])
                for row in connection.execute(
                    "SELECT metadata_json FROM history ORDER BY position"
                )
            ]

        return {
            "favorites": favorites,
            "playlists": playlists,
            "history": history,
        }

    def save_player_state(self, state: dict[str, Any]):
        normalized = self._normalize_state(state)
        with self._connect() as connection:
            self._write_player_state(connection, normalized)
            connection.execute(
                "INSERT OR REPLACE INTO settings (key, value_json) "
                "VALUES ('legacy_state_imported', 'true')"
            )

    def get_daily_album(self, album_keys: list[str], day: str):
        if not album_keys:
            return None

        with self._connect() as connection:
            row = connection.execute(
                "SELECT album_key FROM daily_albums WHERE date = ?", (day,)
            ).fetchone()
            if row and row["album_key"] in album_keys:
                return row["album_key"]

            recent = {
                item["album_key"]
                for item in connection.execute(
                    "SELECT album_key FROM daily_albums "
                    "WHERE date < ? ORDER BY date DESC LIMIT 7",
                    (day,),
                )
            }
            candidates = [key for key in album_keys if key not in recent]
            if not candidates:
                candidates = album_keys
            selected = min(
                candidates,
                key=lambda key: hashlib.sha256(f"{day}:{key}".encode()).hexdigest(),
            )
            connection.execute(
                "INSERT OR REPLACE INTO daily_albums (date, album_key) VALUES (?, ?)",
                (day, selected),
            )
            return selected

    def set_daily_album(self, album_key: str, day: str):
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO daily_albums (date, album_key) VALUES (?, ?)",
                (day, album_key),
            )

    def get_setting(self, key: str, default: Any = None):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value_json FROM settings WHERE key = ?", (key,)
            ).fetchone()
        return json.loads(row["value_json"]) if row else default

    def set_setting(self, key: str, value: Any):
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO settings (key, value_json) VALUES (?, ?)",
                (key, json.dumps(value, ensure_ascii=False)),
            )

    def load_playback_queue(self) -> dict[str, Any] | None:
        queue = self.get_setting("playback_queue")
        return queue if isinstance(queue, dict) else None

    def save_playback_queue(self, queue: dict[str, Any]):
        self.set_setting("playback_queue", queue)

    def record_track_started(self, track: dict[str, Any], played_at: str):
        track_key = str(track.get("path") or track.get("title") or "")
        if not track_key:
            raise ValueError("A track path or title is required to record playback")
        metadata = {
            "path": str(track.get("path") or ""),
            "title": str(track.get("title") or "Unknown"),
            "artist": str(track.get("artist") or "Unknown Artist"),
            "album": str(track.get("album") or track.get("_album") or "Unknown Album"),
            "duration": track.get("duration"),
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO tracks
                    (track_key, path, title, artist, album, duration, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(track_key) DO UPDATE SET
                    path = excluded.path,
                    title = excluded.title,
                    artist = excluded.artist,
                    album = excluded.album,
                    duration = excluded.duration,
                    metadata_json = excluded.metadata_json
                """,
                (
                    track_key,
                    metadata["path"],
                    metadata["title"],
                    metadata["artist"],
                    metadata["album"],
                    metadata["duration"],
                    json.dumps(metadata, ensure_ascii=False),
                ),
            )
            cursor = connection.execute(
                "INSERT INTO play_events "
                "(track_key, played_at, metadata_json) VALUES (?, ?, ?)",
                (track_key, played_at, json.dumps(metadata, ensure_ascii=False)),
            )
            connection.execute(
                """
                INSERT INTO listening_stats (track_key, play_count, last_played_at)
                VALUES (?, 1, ?)
                ON CONFLICT(track_key) DO UPDATE SET
                    play_count = play_count + 1,
                    last_played_at = excluded.last_played_at
                """,
                (track_key, played_at),
            )
            connection.execute("""
                    INSERT OR IGNORE INTO favorites (track_key, position)
                    SELECT track_key,
                        COALESCE((SELECT MAX(position) + 1 FROM favorites), 0)
                    FROM listening_stats
                    WHERE play_count > 0
                    ORDER BY play_count DESC, listening_seconds DESC
                    LIMIT 10
                    """)
            return cursor.lastrowid

    def add_listened_seconds(self, play_event_id: int, seconds: float):
        if seconds <= 0:
            return
        with self._connect() as connection:
            row = connection.execute(
                "SELECT track_key FROM play_events WHERE id = ?",
                (play_event_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Unknown playback event: {play_event_id}")
            connection.execute(
                "UPDATE play_events SET listened_seconds = "
                "listened_seconds + ? WHERE id = ?",
                (seconds, play_event_id),
            )
            connection.execute(
                "UPDATE listening_stats SET listening_seconds = "
                "listening_seconds + ? WHERE track_key = ?",
                (seconds, row["track_key"]),
            )

    def get_listening_stats(self, start_at: str | None = None):
        where = "WHERE played_at >= ?" if start_at else ""
        parameters = (start_at,) if start_at else ()
        with self._connect() as connection:
            summary = connection.execute(
                f"""
                SELECT COUNT(*) AS played_tracks,
                    COUNT(DISTINCT track_key) AS unique_tracks,
                    COALESCE(SUM(listened_seconds), 0) AS listening_seconds
                FROM play_events {where}
                """,
                parameters,
            ).fetchone()

            def top_list(group_column: str):
                return [
                    {"name": row["name"], "plays": row["plays"]}
                    for row in connection.execute(
                        f"""
                        SELECT COALESCE(t.{group_column}, 'Unknown') AS name,
                            COUNT(*) AS plays
                        FROM play_events p
                        LEFT JOIN tracks t ON t.track_key = p.track_key
                        {where}
                        GROUP BY name
                        ORDER BY plays DESC, name COLLATE NOCASE
                        LIMIT 10
                        """,
                        parameters,
                    )
                ]

            daily_activity = [
                {"day": row["day"], "plays": row["plays"]}
                for row in connection.execute(
                    f"""
                    SELECT substr(played_at, 1, 10) AS day, COUNT(*) AS plays
                    FROM play_events {where}
                    GROUP BY day
                    ORDER BY day DESC
                    LIMIT 7
                    """,
                    parameters,
                )
            ]
            return {
                "played_tracks": summary["played_tracks"],
                "unique_tracks": summary["unique_tracks"],
                "listening_seconds": summary["listening_seconds"],
                "top_artists": top_list("artist"),
                "top_albums": top_list("album"),
                "top_tracks": top_list("title"),
                "daily_activity": list(reversed(daily_activity)),
            }

    @staticmethod
    def _read_legacy_state(path: Path | str | None):
        if path is None or not Path(path).exists():
            return None

        with Path(path).open("r", encoding="utf-8") as state_file:
            return Database._normalize_state(json.load(state_file))

    @staticmethod
    def _normalize_state(state: Any):
        if not isinstance(state, dict):
            raise ValueError("Player state must be a JSON object")

        favorites = state.get("favorites", [])
        playlists = state.get("playlists", {})
        history = state.get("history", [])
        if (
            not isinstance(favorites, list)
            or any(not isinstance(item, str) for item in favorites)
        ):
            raise ValueError("Player state favorites must be a list of track keys")
        if not isinstance(playlists, dict):
            raise ValueError("Player state playlists must be an object")
        if not isinstance(history, list):
            raise ValueError("Player state history must be a list")

        normalized_playlists = {}
        for name, references in playlists.items():
            if not isinstance(name, str) or not isinstance(references, list):
                raise ValueError("Each playlist must have a name and a list of tracks")
            normalized_playlists[name] = [
                Database._normalize_reference(reference)
                for reference in references
            ]

        return {
            "favorites": list(dict.fromkeys(favorites)),
            "playlists": normalized_playlists,
            "history": [
                Database._normalize_reference(reference)
                for reference in history
            ][:30],
        }

    @staticmethod
    def _normalize_reference(reference: Any):
        if not isinstance(reference, dict) or not isinstance(
            reference.get("key"), str
        ):
            raise ValueError("Track references must be objects with a string key")
        return dict(reference)

    @staticmethod
    def _write_player_state(connection, state):
        connection.execute("DELETE FROM favorites")
        connection.execute("DELETE FROM history")
        connection.execute("DELETE FROM playlists")

        for position, track_key in enumerate(state["favorites"]):
            connection.execute(
                "INSERT INTO favorites (track_key, position) VALUES (?, ?)",
                (track_key, position),
            )

        for name, references in state["playlists"].items():
            playlist_id = connection.execute(
                "INSERT INTO playlists (name) VALUES (?)", (name,)
            ).lastrowid
            for position, reference in enumerate(references):
                connection.execute(
                    "INSERT INTO playlist_tracks "
                    "(playlist_id, position, track_key, metadata_json) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        playlist_id,
                        position,
                        reference["key"],
                        json.dumps(reference, ensure_ascii=False),
                    ),
                )

        for position, reference in enumerate(state["history"]):
            connection.execute(
                "INSERT INTO history (position, track_key, metadata_json) "
                "VALUES (?, ?, ?)",
                (
                    position,
                    reference["key"],
                    json.dumps(reference, ensure_ascii=False),
                ),
            )

    def auto_favorite_top_tracks(self, limit: int = 10):
        """Automatically add the most played tracks to favorites."""
        with self._connect() as connection:
            top_tracks = connection.execute(
                """
                SELECT track_key
                FROM listening_stats
                WHERE play_count > 0
                ORDER BY play_count DESC, listening_seconds DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

            for row in top_tracks:
                track_key = row["track_key"]

                connection.execute(
                    """
                    INSERT OR IGNORE INTO favorites (track_key, position)
                    VALUES (
                        ?,
                        COALESCE((SELECT MAX(position) + 1 FROM favorites), 0)
                    )
                    """,
                    (track_key,),
                )

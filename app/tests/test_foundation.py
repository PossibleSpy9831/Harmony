import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app.core.event_bus import EventBus
from app.core.navigation import Navigation
from app.data.database import Database
from app.scripts.library_config import DATABASE_PATH
from app.scripts.Playlist import Playlist
from app.src.Harmony import restore_playback_queue


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database = Database(self.root / "harmony.sqlite3")

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_default_database_is_stored_in_home_harmony_directory(self):
        self.assertEqual(
            DATABASE_PATH,
            Path.home() / ".harmony" / "harmony.sqlite3",
        )

    def test_imports_legacy_state_and_keeps_order(self):
        legacy_path = self.root / "player_state.json"
        state = {
            "favorites": ["/music/a.mp3", "/music/b.mp3"],
            "playlists": {
                "Road trip": [
                    {"key": "/music/b.mp3", "title": "B"},
                    {"key": "/music/a.mp3", "title": "A"},
                ]
            },
            "history": [{"key": "/music/a.mp3", "artist": "Artist"}],
        }
        legacy_path.write_text(json.dumps(state), encoding="utf-8")

        self.assertEqual(self.database.load_player_state(legacy_path), state)
        legacy_path.unlink()
        self.assertEqual(self.database.load_player_state(legacy_path), state)

    def test_save_and_reload_state(self):
        state = {
            "favorites": ["/music/a.mp3"],
            "playlists": {"Mix": [{"key": "/music/a.mp3", "title": "A"}]},
            "history": [{"key": "/music/a.mp3", "title": "A"}],
        }

        self.database.save_player_state(state)

        self.assertEqual(self.database.load_player_state(), state)

    def test_migrates_existing_database_to_new_path_and_preserves_source(self):
        legacy_path = self.root / "old" / "harmony.sqlite3"
        target_path = self.root / "new" / "harmony.sqlite3"
        legacy_database = Database(legacy_path)
        legacy_database.save_playback_queue(
            {"tracks": ["/music/song.mp3"], "index": 0}
        )

        migrated_database = Database(target_path, migrate_from=legacy_path)

        self.assertEqual(
            migrated_database.load_playback_queue(),
            {"tracks": ["/music/song.mp3"], "index": 0},
        )
        self.assertTrue(legacy_path.is_file())
        self.assertTrue(target_path.is_file())

    def test_playback_queue_is_saved_and_restored_at_its_current_track(self):
        first_path = self.root / "first.mp3"
        second_path = self.root / "second.mp3"
        first_path.touch()
        second_path.touch()
        queue = {
            "tracks": [str(first_path), str(second_path)],
            "index": 1,
            "name": "Road trip",
            "repeat": False,
            "position": 42.5,
            "paused": True,
        }
        self.database.save_playback_queue(queue)

        restored_playlist = Playlist()
        resume_state = restore_playback_queue(
            self.database,
            restored_playlist,
            {
                "albums": [
                    {
                        "name": "Album",
                        "tracks": [
                            {"path": str(first_path), "title": "First"},
                            {"path": str(second_path), "title": "Second"},
                        ],
                    }
                ]
            },
        )

        tracks, current_index, name = restored_playlist.snapshot()
        self.assertEqual(
            [track["path"] for track in tracks],
            [str(first_path.resolve()), str(second_path.resolve())],
        )
        self.assertEqual(current_index, 1)
        self.assertEqual(name, "Road trip")
        self.assertFalse(restored_playlist.repeat)
        self.assertEqual(resume_state, {"position": 42.5, "paused": True})

    def test_malformed_legacy_state_is_reported(self):
        legacy_path = self.root / "player_state.json"
        legacy_path.write_text("{", encoding="utf-8")

        with self.assertRaises(json.JSONDecodeError):
            self.database.load_player_state(legacy_path)

    def test_rejects_a_database_from_a_newer_schema(self):
        path = self.root / "future.sqlite3"
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("PRAGMA user_version = 4")

        with self.assertRaisesRegex(RuntimeError, "newer than supported"):
            Database(path)

    def test_listening_stats_track_play_events_and_listened_time(self):
        track = {
            "path": "/music/a.mp3",
            "title": "Track A",
            "artist": "Artist A",
            "album": "Album A",
            "duration": 120,
        }
        event_id = self.database.record_track_started(
            track,
            "2026-11-02T10:00:00+01:00",
        )
        self.database.record_track_started(
            track,
            "2026-11-02T11:00:00+01:00",
        )
        self.database.add_listened_seconds(event_id, 42.5)

        stats = self.database.get_listening_stats("2026-11-02T00:00:00+01:00")

        self.assertEqual(stats["played_tracks"], 2)
        self.assertEqual(stats["unique_tracks"], 1)
        self.assertEqual(stats["listening_seconds"], 42.5)
        self.assertEqual(stats["top_artists"], [{"name": "Artist A", "plays": 2}])
        self.assertEqual(stats["top_albums"], [{"name": "Album A", "plays": 2}])
        self.assertEqual(stats["top_tracks"], [{"name": "Track A", "plays": 2}])
        self.assertEqual(stats["daily_activity"], [
            {"day": "2026-11-02", "plays": 2}
        ])

    def test_sync_library_replaces_catalog_without_erasing_player_state(self):
        self.database.save_player_state(
            {"favorites": ["/music/favorite.mp3"], "playlists": {}, "history": []}
        )
        self.database.save_playback_queue({"tracks": ["/music/keep.mp3"], "index": 0})
        self.database.record_track_started(
            {"path": "/music/keep.mp3", "title": "Keep", "artist": "Artist"},
            "2026-11-02T10:00:00+01:00",
        )
        player_state_before = self.database.load_player_state()
        library = {
            "albums": [
                {
                    "folder": self.root / "Album",
                    "name": "Album",
                    "cover": "cover.jpg",
                    "tracks": [
                        {
                            "path": self.root / "Album" / "track.mp3",
                            "title": "Track",
                            "artist": "Artist",
                            "album": "Album",
                            "duration": 120,
                            "lyrics": [{"time": 1.0, "text": "Line"}],
                        }
                    ],
                }
            ]
        }

        self.assertEqual(
            self.database.sync_library(library), {"albums": 1, "tracks": 1}
        )
        self.assertEqual(self.database.load_player_state(), player_state_before)
        self.assertEqual(
            self.database.load_playback_queue(),
            {"tracks": ["/music/keep.mp3"], "index": 0},
        )
        self.assertEqual(self.database.get_listening_stats()["played_tracks"], 1)

        with closing(sqlite3.connect(self.database.path)) as connection:
            album = connection.execute(
                "SELECT name, cover, artist FROM albums"
            ).fetchone()
            track = connection.execute(
                "SELECT title, artist, duration, metadata_json FROM tracks "
                "WHERE track_key = ?",
                (str((self.root / "Album" / "track.mp3").resolve()),),
            ).fetchone()
            membership = connection.execute(
                "SELECT album_key FROM library_tracks WHERE track_key = ?",
                (str((self.root / "Album" / "track.mp3").resolve()),),
            ).fetchone()
            artists = connection.execute("SELECT name FROM artists").fetchall()
        self.assertEqual(album, ("Album", "cover.jpg", "Artist"))
        self.assertEqual(track[:3], ("Track", "Artist", 120.0))
        self.assertEqual(json.loads(track[3])["lyrics"][0]["text"], "Line")
        self.assertEqual(membership[0], str((self.root / "Album").resolve()))
        self.assertEqual([row[0] for row in artists], ["Artist"])

        self.database.sync_library({"albums": []})
        with closing(sqlite3.connect(self.database.path)) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM library_tracks").fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM albums").fetchone()[0], 0
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM tracks WHERE track_key = ?",
                    ("/music/keep.mp3",),
                ).fetchone()[0],
                1,
            )

    def test_sync_library_rejects_duplicate_track_paths_without_partial_updates(self):
        existing = {
            "albums": [
                {
                    "folder": "/music/Existing",
                    "name": "Existing",
                    "tracks": [{"path": "/music/existing.mp3", "title": "Existing"}],
                }
            ]
        }
        self.database.sync_library(existing)

        invalid = {
            "albums": [
                {
                    "folder": "/music/First",
                    "name": "First",
                    "tracks": [{"path": "/music/duplicate.mp3"}],
                },
                {
                    "folder": "/music/Second",
                    "name": "Second",
                    "tracks": [{"path": "/music/duplicate.mp3"}],
                },
            ]
        }
        with self.assertRaisesRegex(ValueError, "Duplicate library track"):
            self.database.sync_library(invalid)

        with closing(sqlite3.connect(self.database.path)) as connection:
            albums = connection.execute("SELECT name FROM albums").fetchall()
        self.assertEqual([row[0] for row in albums], ["Existing"])

    def test_upgrades_existing_database_schema(self):
        path = self.root / "older.sqlite3"
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("PRAGMA user_version = 1")

        Database(path)

        with closing(sqlite3.connect(path)) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertEqual(version, 3)
        self.assertIn("play_events", tables)
        self.assertIn("library_tracks", tables)


class EventBusTests(unittest.TestCase):
    def test_publish_and_unsubscribe(self):
        bus = EventBus()
        received = []
        unsubscribe = bus.subscribe("track_started", received.append)

        bus.publish("track_started", path="/music/a.mp3")
        unsubscribe()
        bus.publish("track_started", path="/music/b.mp3")

        self.assertEqual(received, [{"path": "/music/a.mp3"}])


class NavigationTests(unittest.TestCase):
    def test_navigates_known_screens_and_rejects_unknown_screens(self):
        navigation = Navigation()
        navigation.navigate("favorites")
        self.assertEqual(navigation.current, "favorites")
        navigation.navigate("stats")
        self.assertEqual(navigation.current, "stats")

        with self.assertRaises(ValueError):
            navigation.navigate("not-a-screen")


if __name__ == "__main__":
    unittest.main()

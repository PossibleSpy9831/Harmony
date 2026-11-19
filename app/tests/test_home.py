import tempfile
import unittest
from pathlib import Path

from app.data.database import Database
from app.features.home import (
    album_key,
    album_of_the_day,
    greeting,
    next_album_of_the_day,
    rediscover_tracks,
)


class HomeFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database = Database(self.root / "harmony.sqlite3")
        self.tracks = []
        self.albums = []
        for album_number in range(3):
            album_tracks = []
            for track_number in range(2):
                path = self.root / f"{album_number}-{track_number}.mp3"
                path.touch()
                track = {
                    "path": str(path),
                    "title": f"Track {album_number}-{track_number}",
                    "artist": f"Artist {album_number}",
                    "album": f"Album {album_number}",
                }
                album_tracks.append(track)
                self.tracks.append(track)
            self.albums.append(
                {
                    "name": f"Album {album_number}",
                    "folder": str(self.root / f"album-{album_number}"),
                    "tracks": album_tracks,
                }
            )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_daily_album_is_stable_until_explicitly_advanced(self):
        selected = album_of_the_day(self.albums, self.database, "2026-11-02")
        self.assertEqual(
            album_key(selected),
            album_key(album_of_the_day(self.albums, self.database, "2026-11-02")),
        )

        next_selected = next_album_of_the_day(
            self.albums,
            self.database,
            "2026-11-02",
        )
        self.assertNotEqual(album_key(selected), album_key(next_selected))
        self.assertEqual(
            album_key(next_selected),
            album_key(album_of_the_day(self.albums, self.database, "2026-11-02")),
        )

    def test_rediscover_uses_playable_tracks_and_is_seeded(self):
        history = [{"key": self.tracks[0]["path"]}]
        first = rediscover_tracks(
            {"albums": self.albums},
            history,
            [],
            limit=3,
            seed="today",
        )
        repeat = rediscover_tracks(
            {"albums": self.albums},
            history,
            [],
            limit=3,
            seed="today",
        )

        self.assertEqual(
            [track["path"] for track in first],
            [track["path"] for track in repeat],
        )
        self.assertEqual(len(first), 3)
        self.assertNotIn(self.tracks[0]["path"], [track["path"] for track in first])

    def test_rediscover_defaults_to_ten_playable_tracks(self):
        extra_tracks = []
        for index in range(12):
            path = self.root / f"extra-{index}.mp3"
            path.touch()
            extra_tracks.append(
                {
                    "path": str(path),
                    "title": f"Extra {index}",
                    "artist": f"Artist {index}",
                }
            )

        result = rediscover_tracks(
            {"albums": [{"name": "Extra", "tracks": extra_tracks}]},
            [],
            [],
            seed="ten",
        )

        self.assertEqual(len(result), 10)

    def test_greeting_covers_time_of_day(self):
        self.assertEqual(greeting(8), "Good morning.")
        self.assertEqual(greeting(14), "Good afternoon.")
        self.assertEqual(greeting(19), "Good evening.")
        self.assertEqual(greeting(2), "Good night.")


if __name__ == "__main__":
    unittest.main()

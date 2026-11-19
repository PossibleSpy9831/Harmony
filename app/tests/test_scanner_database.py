import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.tools import scannerv2


class ScannerDatabaseTests(unittest.TestCase):
    def test_scan_updates_sqlite_and_keeps_the_json_library(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            album_folder = root / "Album"
            album_folder.mkdir()
            track_path = album_folder / "Track.mp3"
            track_path.touch()
            json_path = root / "library.json"
            database_path = root / "harmony.sqlite3"
            scanner = scannerv2.Scanner()
            track_data = {
                "track": 1,
                "title": "Track",
                "artist": "Artist",
                "album": "Album",
                "path": str(track_path.resolve()),
                "duration": 100,
                "lyrics": [],
                "lyrics_status": "not_found",
            }

            with (
                patch.object(scannerv2, "LIBRARY_PATH", json_path),
                patch.object(scannerv2, "DATABASE_PATH", database_path),
                patch.object(
                    scannerv2,
                    "LEGACY_DATABASE_PATH",
                    root / "legacy-harmony.sqlite3",
                ),
                patch.object(scanner, "find_cover", return_value=None),
                patch.object(scanner, "extract_cover", return_value=None),
                patch.object(scanner, "build_track", return_value=track_data),
            ):
                library = scanner.scan_music_folder(root)

            saved_json = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(saved_json["albums"][0]["tracks"][0]["title"], "Track")
            self.assertEqual(library["albums"][0]["name"], "Album")
            with sqlite3.connect(database_path) as connection:
                row = connection.execute(
                    """
                    SELECT albums.name, tracks.title
                    FROM library_tracks
                    JOIN albums ON albums.album_key = library_tracks.album_key
                    JOIN tracks ON tracks.track_key = library_tracks.track_key
                    """
                ).fetchone()
            self.assertEqual(row, ("Album", "Track"))


if __name__ == "__main__":
    unittest.main()

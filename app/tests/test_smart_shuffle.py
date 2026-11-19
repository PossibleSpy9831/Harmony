import tempfile
import unittest
from pathlib import Path

from app.features.smart_shuffle import build_flow_queue


class SmartShuffleTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.albums = []
        self.tracks = []
        for album_index in range(4):
            album_tracks = []
            for track_index in range(3):
                path = self.root / f"{album_index}-{track_index}.mp3"
                path.touch()
                track = {
                    "path": str(path),
                    "title": f"Track {album_index}-{track_index}",
                    "artist": f"Artist {album_index % 3}",
                }
                album_tracks.append(track)
                self.tracks.append(track)
            self.albums.append(
                {"name": f"Album {album_index}", "tracks": album_tracks}
            )
        self.library = {"albums": self.albums}

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_smart_mode_rotates_artists_and_preserves_current_and_queued_tracks(self):
        current = self.tracks[0]
        queued = self.tracks[1]
        result = build_flow_queue(
            self.library,
            [],
            [],
            mode="smart",
            current=current,
            queued_tracks=[queued],
            seed=12,
        )
        paths = [track["path"] for track in result]

        self.assertEqual(paths[0], current["path"])
        self.assertEqual(paths[1], queued["path"])
        self.assertEqual(len(paths), len(set(paths)))
        for before, after in zip(result[1:], result[2:]):
            self.assertNotEqual(before["artist"], after["artist"])

    def test_discovery_prefers_tracks_missing_from_recent_history(self):
        history = [{"key": track["path"]} for track in self.tracks[:8]]
        result = build_flow_queue(
            self.library,
            [],
            history,
            mode="discovery",
            seed="discovery",
        )
        self.assertTrue(
            all(track["path"] not in {item["path"] for item in self.tracks[:8]}
                for track in result[:4])
        )

    def test_classic_mode_is_repeatable_with_seed_and_keeps_all_tracks(self):
        first = build_flow_queue(
            self.library, [], [], mode="classic", seed=4
        )
        second = build_flow_queue(
            self.library, [], [], mode="classic", seed=4
        )
        first_paths = [track["path"] for track in first]

        self.assertEqual(first_paths, [track["path"] for track in second])
        self.assertEqual(len(first_paths), len(self.tracks))

    def test_favorites_mix_blends_favorites_and_discovery(self):
        favorite = self.tracks[0]["path"]
        result = build_flow_queue(
            self.library,
            [favorite],
            [],
            mode="favorites_mix",
            seed=8,
        )
        first_half = {track["path"] for track in result[:6]}
        self.assertIn(favorite, first_half)
        self.assertTrue(any(track["path"] != favorite for track in result[:6]))

    def test_unknown_mode_is_reported(self):
        with self.assertRaisesRegex(ValueError, "Unknown Harmony Flow mode"):
            build_flow_queue(self.library, [], [], mode="unknown")


if __name__ == "__main__":
    unittest.main()

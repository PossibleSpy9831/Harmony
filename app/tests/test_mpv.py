import unittest
from unittest.mock import patch

from app.scripts.mpv import MPV


class MPVPlaybackTests(unittest.TestCase):
    def test_play_starts_at_a_saved_position(self):
        player = MPV()
        with (
            patch.object(player, "_stop_pcm_capture"),
            patch.object(player, "_start_pcm_capture") as capture,
            patch.object(player, "command") as command,
        ):
            player.play("/music/track.mp3", start_position=42.5)

        command.assert_called_once_with(
            "loadfile",
            "/music/track.mp3",
            "replace",
        )
        capture.assert_called_once_with("/music/track.mp3", 42.5)
        self.assertEqual(player._pending_start_position, 42.5)

    def test_saved_position_seeks_after_mpv_reports_file_loaded(self):
        player = MPV()
        player._pending_start_position = 42.5

        with patch.object(player, "command") as command:
            player.process_events([{"event": "file-loaded"}])

        command.assert_called_once_with("seek", 42.5, "absolute+exact")
        self.assertIsNone(player._pending_start_position)
        self.assertEqual(player.get_time_pos(), 42.5)

    def test_normal_playback_does_not_seek_after_file_load(self):
        player = MPV()
        player._pending_start_position = None

        with patch.object(player, "command") as command:
            player.process_events([{"event": "file-loaded"}])

        command.assert_not_called()


if __name__ == "__main__":
    unittest.main()

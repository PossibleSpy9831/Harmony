from io import StringIO
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from rich.console import Console

from app.data.database import Database
from app.features.artwork import render_artwork
from app.features.themes import THEMES, ThemeManager, dominant_color
from app.scripts.Spectrum import Spectrum
from app.scripts.ui import (
    build_album_screen,
    build_home_screen,
    build_now_playing_screen,
    build_stats_screen,
    build_theme_screen,
    album_grid,
    footer,
    mini_player,
)


class VisualFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database = Database(self.root / "harmony.sqlite3")

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_theme_selection_and_dynamic_setting_persist(self):
        manager = ThemeManager(self.database)
        self.assertEqual(manager.select("Matrix").name, "Matrix")
        self.assertEqual(manager.base_theme.accent, "#39ff14")
        self.assertTrue(manager.toggle_dynamic())

        restored = ThemeManager(self.database)
        self.assertEqual(restored.base_theme.name, "Matrix")
        self.assertTrue(restored.dynamic)

    def test_dynamic_theme_uses_dominant_cover_color(self):
        cover = self.root / "cover.png"
        Image.new("RGB", (16, 16), (20, 120, 220)).save(cover)

        self.assertEqual(dominant_color(cover), "#1478dc")
        manager = ThemeManager(self.database)
        manager.toggle_dynamic()
        self.assertEqual(manager.current(cover).accent, "#1478dc")
        self.assertNotEqual(manager.current(cover).secondary, THEMES[3].secondary)

    def test_artwork_modes_render_and_missing_art_has_placeholder(self):
        cover = self.root / "cover.png"
        Image.new("RGB", (16, 16), (240, 40, 20)).save(cover)
        manager = ThemeManager(self.database)

        for mode in ("classic", "color", "detailed"):
            result = render_artwork(cover, 12, 6, mode, manager.current())
            self.assertEqual(len(result.plain.splitlines()), 6)
            self.assertTrue(all(len(line) == 12 for line in result.plain.splitlines()))

        self.assertIn(
            "HARMONY",
            render_artwork(None, 16, 5, "color", manager.current()).plain,
        )

    def test_album_cards_render_ascii_artwork_for_every_album(self):
        manager = ThemeManager(self.database)
        albums = [
            {
                "name": f"Album {index}",
                "cover": None,
                "tracks": [
                    {"artist": f"Artist {index}"}
                ],
            }
            for index in range(1, 4)
        ]
        console = Console(width=100, height=34, record=True, file=StringIO())

        with patch(
            "app.scripts.ui.render_artwork", wraps=render_artwork
        ) as artwork_renderer:
            console.print(album_grid(albums, 1, console, manager.current()))

        output = console.export_text()
        self.assertEqual(artwork_renderer.call_count, len(albums))
        self.assertEqual(
            [call.args[0] for call in artwork_renderer.call_args_list],
            [None, None, None],
        )
        self.assertTrue(all(call.kwargs["height"] == 3 for call in artwork_renderer.call_args_list))
        self.assertIn("Album 1", output)
        self.assertIn("Album 2", output)
        self.assertIn("Album 3", output)
        self.assertIn("♫ HARMON", output)
        self.assertNotIn("HARMONArtist", output)

    def test_album_screen_passes_theme_as_lyrics_theme_not_width(self):
        manager = ThemeManager(self.database)
        theme = manager.current()
        track = {
            "path": str(self.root / "track.mp3"),
            "title": "Track",
            "artist": "Artist",
            "_album": "Album",
            "lyrics": {
                "synced": True,
                "lines": [{"timestamp": 0, "text": "Test lyric"}],
            },
        }
        playlist = type(
            "Playlist",
            (),
            {
                "current": track,
                "tracks": [track],
                "current_index": 0,
                "repeat": False,
                "snapshot": lambda self: ([track], 0, "Test queue"),
            },
        )()
        mpv = type(
            "MPV",
            (),
            {
                "get_time_pos": lambda self: 0,
                "get_duration": lambda self: 180,
                "paused": False,
                "volume": 75,
            },
        )()
        console = Console(width=100, height=34, record=True, file=StringIO())

        console.print(
            build_album_screen([], 0, playlist, mpv, console, theme)
        )

        self.assertIn("Test lyric", console.export_text())

    def test_home_layout_fills_console_and_exposes_dashboard_sections(self):
        manager = ThemeManager(self.database)
        library = {
            "albums": [
                {
                    "name": "Album",
                    "folder": str(self.root),
                    "cover": None,
                    "tracks": [
                        {
                            "path": str(self.root / "track.mp3"),
                            "title": "Track",
                            "artist": "Artist",
                        }
                    ],
                }
            ]
        }
        player = type("Player", (), {"current": None})()
        mpv = type(
            "MPV",
            (),
            {
                "get_time_pos": lambda self: 0,
                "get_duration": lambda self: 0,
                "paused": False,
            },
        )()
        for width, height in ((100, 34), (60, 28)):
            console = Console(
                width=width,
                height=height,
                record=True,
                file=StringIO(),
            )
            recent_tracks = [
                {"title": "Recent Song", "artist": "Recent Artist"}
            ]
            discover_tracks = [
                {"title": f"Discovery {index:02}", "artist": f"Artist {index}"}
                for index in range(1, 11)
            ]
            favorite_tracks = [
                {"title": f"Favorite {index:02}", "artist": f"Artist {index}"}
                for index in range(1, 21)
            ]
            screen = build_home_screen(
                library,
                library["albums"][0],
                recent_tracks,
                discover_tracks,
                favorite_tracks,
                9,
                player,
                mpv,
                console,
                10,
                manager.current(),
            )
            self.assertEqual(
                screen["welcome"].renderable.border_style,
                manager.current().border,
            )
            console.print(screen)
            output = console.export_text()
            self.assertIn("ALBUM OF THE DAY", output)
            self.assertIn("REDISCOVER", output)
            self.assertIn("RECENT", output)
            self.assertIn("MINI PLAYER", output)
            self.assertIn("H OPEN", output)
            self.assertIn("V OPEN", output)
            self.assertIn("Enter Play", output)
            self.assertIn("H History", output)
            self.assertIn("V Favorites", output)
            self.assertIn("D Day" if width < 90 else "D Play", output)
            self.assertIn("REDISCOVER · 10", output)
            self.assertIn("Recent Song", output)
            self.assertIn("20 TRACKS" if width >= 80 else "FAVORITES", output)
            if width >= 80:
                self.assertIn("Discovery 10", output)
                self.assertIn("Favorite 20", output)
            self.assertGreaterEqual(len(output.splitlines()), height - 2)

    def test_now_playing_and_theme_settings_render(self):
        manager = ThemeManager(self.database)
        theme = manager.current()
        track = {
            "path": str(self.root / "song.mp3"),
            "title": "The Song",
            "artist": "The Artist",
            "_album": "The Album",
            "_cover": None,
            "_year": 2024,
            "lyrics": {
                "synced": True,
                "lines": [
                    {"timestamp": 0, "text": "Opening lyric line"},
                    {"timestamp": 15, "text": "Current lyric line"},
                    {"timestamp": 30, "text": "Following lyric line"},
                ],
            },
        }
        upcoming = [
            {
                "path": str(self.root / f"next-{index}.mp3"),
                "title": f"Next Song {index}",
                "artist": f"Next Artist {index}",
            }
            for index in range(1, 13)
        ]
        player = type(
            "Player",
            (),
            {
                "current": track,
                "repeat": False,
                "snapshot": lambda self: ([track, *upcoming], 0, "Test Queue"),
            },
        )()
        mpv = type(
            "MPV",
            (),
            {
                "get_time_pos": lambda self: 17,
                "get_duration": lambda self: 180,
                "paused": False,
                "volume": 75,
            },
        )()
        spectrum = Spectrum(mpv, bars=24, height=8)
        spectrum.levels[3] = 0.8
        spectrum.peaks[3] = 0.9
        console = Console(width=100, height=34, record=True, file=StringIO())
        with patch(
            "app.scripts.ui.render_artwork", wraps=render_artwork
        ) as artwork_renderer:
            console.print(
                build_now_playing_screen(
                    player,
                    mpv,
                    console,
                    theme,
                    spectrum=spectrum,
                )
            )
        artwork_width = artwork_renderer.call_args.kwargs["width"]
        artwork_height = artwork_renderer.call_args.kwargs["height"]
        self.assertEqual(artwork_width, console.size.width * 3 // 5 - 4)
        self.assertEqual(
            artwork_height,
            console.size.height - 4 - footer("now_playing", console, theme).height - 2,
        )
        now_playing_output = console.export_text()
        self.assertIn("The Song", now_playing_output)
        self.assertIn("VOLUME", now_playing_output)
        self.assertIn("75% +/-", now_playing_output)
        self.assertIn("LIVE SPECTRUM", now_playing_output)
        self.assertIn("LYRICS", now_playing_output)
        self.assertIn("Current lyric", now_playing_output)
        self.assertIn("NEXT UP", now_playing_output)
        self.assertLess(
            now_playing_output.index("LIVE SPECTRUM"),
            now_playing_output.index("LYRICS"),
        )
        self.assertLess(
            now_playing_output.index("LYRICS"),
            now_playing_output.index("NEXT UP"),
        )
        self.assertIn("Next Song 1", now_playing_output)
        self.assertIn("Next Song 6", now_playing_output)
        self.assertNotIn("Next Song 7", now_playing_output)
        self.assertIn("HARMONY", now_playing_output)

        compact_console = Console(width=100, height=24, record=True, file=StringIO())
        compact_console.print(
            build_now_playing_screen(
                player,
                mpv,
                compact_console,
                theme,
                spectrum=spectrum,
            )
        )
        compact_output = compact_console.export_text()
        self.assertIn("LIVE SPECTRUM", compact_output)
        self.assertIn("LYRICS", compact_output)
        self.assertIn("NEXT UP", compact_output)
        self.assertIn("Next Song 1", compact_output)

        console = Console(width=100, height=34, record=True, file=StringIO())
        console.print(build_theme_screen(manager, 3, theme, console))
        theme_output = console.export_text()
        self.assertIn("HARMONY THEMES", theme_output)
        self.assertIn("Dynamic artwork palette", theme_output)
        self.assertIn("Enter Apply", theme_output)
        self.assertIn("Matrix", theme_output)

    def test_theme_footer_lists_controls_available_on_the_theme_screen(self):
        manager = ThemeManager(self.database)
        console = Console(width=100, height=20, record=True, file=StringIO())
        console.print(footer("theme_settings", console, manager.current()))
        output = console.export_text()

        self.assertIn("Select theme", output)
        self.assertIn("Enter Apply", output)
        self.assertIn("D Toggle dynamic colors", output)
        self.assertIn("B Stats", output)
        self.assertIn("W Home", output)
        self.assertIn("Q Quit", output)

    def test_mini_player_shows_track_state_progress_and_queue(self):
        manager = ThemeManager(self.database)
        theme = manager.current()
        track = {
            "path": str(self.root / "song.mp3"),
            "title": "The Song",
            "artist": "The Artist",
            "_album": "The Album",
        }
        player = type(
            "Player",
            (),
            {
                "current": track,
                "repeat": True,
                "snapshot": lambda self: ([track], 0, "Test Queue"),
            },
        )()
        mpv = type(
            "MPV",
            (),
            {
                "get_time_pos": lambda self: 17,
                "get_duration": lambda self: 180,
                "paused": False,
                "volume": 75,
            },
        )()
        console = Console(width=100, height=20, record=True, file=StringIO())
        console.print(mini_player(player, mpv, console, theme))
        output = console.export_text()

        self.assertIn("MINI PLAYER", output)
        self.assertIn("I FOR FULL VIEW", output)
        self.assertIn("The Song", output)
        self.assertIn("The Artist", output)
        self.assertIn("QUEUE 1/1", output)
        self.assertIn("REPEAT ON", output)
        self.assertIn("VOL 75%", output)

    def test_stats_screen_and_global_home_shortcut_render(self):
        manager = ThemeManager(self.database)
        theme = manager.select("Matrix")
        stats = {
            "played_tracks": 12,
            "unique_tracks": 8,
            "listening_seconds": 3720,
            "top_artists": [{"name": "Artist", "plays": 5}],
            "top_albums": [{"name": "Album", "plays": 4}],
            "top_tracks": [{"name": "Track", "plays": 3}],
            "daily_activity": [{"day": "2026-11-02", "plays": 5}],
        }
        player = type("Player", (), {"current": None})()
        mpv = type(
            "MPV",
            (),
            {
                "get_time_pos": lambda self: 0,
                "get_duration": lambda self: 0,
            },
        )()
        console = Console(width=100, height=34, record=True, file=StringIO())
        console.print(build_stats_screen(stats, 4, player, mpv, console, theme))
        console.print(footer("albums", console, theme))
        output = console.export_text()

        self.assertIn("HARMONY STATS", output)
        self.assertIn("1h 2m", output)
        self.assertIn("TOP ARTISTS", output)
        self.assertIn("W Home", output)


if __name__ == "__main__":
    unittest.main()

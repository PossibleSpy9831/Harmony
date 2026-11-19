"""Centralized screen navigation for the terminal application."""


class Navigation:
    SCREENS = frozenset(
        {
            "home",
            "now_playing",
            "theme_settings",
            "flow",
            "stats",
            "albums",
            "tracks",
            "Spectrum_Visualizer",
            "favorites",
            "history",
            "artists",
            "search",
            "queue",
            "choose_playlist",
            "playlists",
        }
    )

    def __init__(self, initial_screen: str = "albums"):
        self._current_screen = ""
        self.navigate(initial_screen)

    @property
    def current(self):
        return self._current_screen

    def navigate(self, screen: str):
        if screen not in self.SCREENS:
            raise ValueError(f"Unknown Harmony screen: {screen}")
        self._current_screen = screen

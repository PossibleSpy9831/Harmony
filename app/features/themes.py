"""Persistent terminal palettes, including palettes derived from album art."""

from __future__ import annotations

import colorsys
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from app.data.database import Database


@dataclass(frozen=True)
class Theme:
    name: str
    accent: str
    secondary: str
    highlight: str
    border: str
    muted: str
    surface: str
    dynamic: bool = False


THEMES = (
    Theme("Midnight", "#ad86ff", "#7355b8", "#f0e9ff", "#6742a8", "#77728b", "#251c37"),
    Theme("Obsidian", "#b8bcc8", "#777d8a", "#f0f1f5", "#5a5e68", "#777b84", "#202126"),
    Theme("Ocean", "#52a9ff", "#3974c6", "#e4f2ff", "#27639c", "#71869c", "#13293e"),
    Theme("Aurora", "#55e6d2", "#a580ff", "#f0fffc", "#378e9e", "#6f8e93", "#17363c"),
    Theme("Sunset", "#ff9c67", "#e75b64", "#fff1df", "#b85a42", "#987665", "#3b2525"),
    Theme("Emerald", "#66d99a", "#279d72", "#ecfff2", "#287a58", "#718c7a", "#19372b"),
    Theme("Rose", "#ff83ba", "#b06dff", "#fff0f8", "#a64f81", "#91758a", "#38253a"),
    Theme("Classic", "#00d787", "#00a86b", "#effff7", "#168354", "#789486", "#142820"),
    Theme("Matrix", "#39ff14", "#00c853", "#d7ffd9", "#087f23", "#61a36b", "#071a0b"),
)


@lru_cache(maxsize=64)
def _dominant_color(path: str, modified_ns: int):
    with Image.open(path) as source:
        image = source.convert("RGB")
        image.thumbnail((48, 48))
        palette = image.quantize(colors=6, method=Image.Quantize.MEDIANCUT)
        colors = palette.convert("RGB").getcolors(48 * 48) or []
    if not colors:
        raise ValueError(f"No colors found in artwork: {path}")

    color = max(colors, key=lambda item: item[0])[1]
    return "#{:02x}{:02x}{:02x}".format(*color)


def dominant_color(path: str | Path | None):
    if not path:
        return None
    artwork = Path(path).expanduser()
    if not artwork.is_file():
        return None
    try:
        return _dominant_color(str(artwork.resolve()), artwork.stat().st_mtime_ns)
    except (OSError, ValueError, UnidentifiedImageError):
        return None


def _complementary_color(color: str):
    red, green, blue = (
        int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)
    )
    hue, saturation, value = colorsys.rgb_to_hsv(red, green, blue)
    rgb = colorsys.hsv_to_rgb((hue + 0.42) % 1, max(0.35, saturation), value)
    return "#{:02x}{:02x}{:02x}".format(*(round(channel * 255) for channel in rgb))


class ThemeManager:
    def __init__(self, database: Database):
        self.database = database
        saved = database.get_setting("theme_name", "Aurora")
        self._index = next(
            (index for index, theme in enumerate(THEMES) if theme.name == saved),
            3,
        )
        self.dynamic = bool(database.get_setting("dynamic_theme", False))

    @property
    def base_theme(self):
        return THEMES[self._index]

    def current(self, artwork: str | Path | None = None):
        theme = self.base_theme
        if not self.dynamic:
            return theme

        accent = dominant_color(artwork)
        if accent is None:
            return theme
        secondary = _complementary_color(accent)
        return replace(
            theme,
            accent=accent,
            secondary=secondary,
            border=secondary,
            dynamic=True,
        )

    def select(self, name: str):
        index = next(
            (index for index, theme in enumerate(THEMES) if theme.name == name),
            None,
        )
        if index is None:
            raise ValueError(f"Unknown Harmony theme: {name}")
        self._index = index
        self.database.set_setting("theme_name", name)
        return self.base_theme

    def next(self):
        self._index = (self._index + 1) % len(THEMES)
        self.database.set_setting("theme_name", self.base_theme.name)
        return self.base_theme

    def toggle_dynamic(self):
        self.dynamic = not self.dynamic
        self.database.set_setting("dynamic_theme", self.dynamic)
        return self.dynamic


def default_theme():
    return THEMES[3]

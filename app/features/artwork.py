"""Cached ANSI artwork rendering for terminal playback screens."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from rich.text import Text

from app.features.themes import Theme


CHARACTERS = {
    "classic": " .:-=+*#%@",
    "color": " .,:;irsXA253hMHGS#9B&@",
    "detailed": " .'`^\",:;Il!i~+_-?][}{1)(|\\/*tfjrxnuvczXYUJCLQ0OZmwqpdbkhao*#MW&8%B@$",
}


@lru_cache(maxsize=64)
def _artwork_rows(path: str, modified_ns: int, width: int, height: int, mode: str):
    if mode not in CHARACTERS:
        raise ValueError(f"Unknown artwork mode: {mode}")

    with Image.open(path) as source:
        image = source.convert("RGB")
        canvas_height = height * 2
        image.thumbnail((width, canvas_height))
        canvas = Image.new("RGB", (width, canvas_height), (0, 0, 0))
        offset = ((width - image.width) // 2, (canvas_height - image.height) // 2)
        canvas.paste(image, offset)

    ramp = CHARACTERS[mode]
    rows = []
    for y in range(height):
        row = []
        for x in range(width):
            top = canvas.getpixel((x, y * 2))
            bottom = canvas.getpixel((x, y * 2 + 1))
            red, green, blue = (
                round((top[channel] + bottom[channel]) / 2)
                for channel in range(3)
            )
            luminance = round(0.2126 * red + 0.7152 * green + 0.0722 * blue)
            index = round(luminance * (len(ramp) - 1) / 255)
            row.append((ramp[index], red, green, blue))
        rows.append(row)
    return tuple(tuple(row) for row in rows)


def render_artwork(
    path: str | Path | None,
    width: int,
    height: int,
    mode: str,
    theme: Theme,
):
    if width <= 0 or height <= 0:
        raise ValueError("Artwork dimensions must be positive")
    if mode not in CHARACTERS:
        raise ValueError(f"Unknown artwork mode: {mode}")

    image_path = Path(path).expanduser() if path else None
    if image_path is None or not image_path.is_file():
        return _placeholder(width, height, theme)

    try:
        rows = _artwork_rows(
            str(image_path.resolve()),
            image_path.stat().st_mtime_ns,
            width,
            height,
            mode,
        )
    except (OSError, UnidentifiedImageError):
        return _placeholder(width, height, theme)

    art = Text()
    for row_index, row in enumerate(rows):
        for char, red, green, blue in row:
            style = f"#{red:02x}{green:02x}{blue:02x}" if mode != "classic" else theme.accent
            art.append(char, style=style)
        if row_index != len(rows) - 1:
            art.append("\n")
    return art


def _placeholder(width: int, height: int, theme: Theme):
    lines = []
    for row in range(height):
        if row == height // 2:
            message = "♫ HARMONY ♫"
            line = message[:width].center(width)
        else:
            line = ("·" * width) if row in (0, height - 1) else (" " * width)
        lines.append(line)
    art = Text()
    for index, line in enumerate(lines):
        art.append(line, style=theme.accent)
        if index != len(lines) - 1:
            art.append("\n")
    return art

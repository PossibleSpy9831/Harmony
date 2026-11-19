import textwrap

from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text
from rich.align import Align
from rich.table import Table
from rich.layout import Layout
from rich import box

from app.features.artwork import render_artwork
from app.features.themes import THEMES, Theme, ThemeManager, default_theme
from app.features.smart_shuffle import FLOW_MODES
from app.features.stats import STAT_PERIODS
from app.scripts.helpers import (
    shorten,
    title_of,
    artist_of,
    album_of,
    album_name,
    resolve_references,
)


def get_lyrics_lines(track):
    if not track:
        return []

    lyrics = track.get("lyrics")

    if not lyrics or not lyrics.get("synced"):
        return []

    raw_lines = lyrics.get("lines", [])

    if not isinstance(raw_lines, list):
        return []

    lines = []

    for line in raw_lines:
        if not isinstance(line, dict):
            continue

        text = str(line.get("text", "")).strip()

        if not text:
            continue

        try:
            timestamp = float(line.get("timestamp", 0))
        except (TypeError, ValueError):
            continue

        lines.append({
            "timestamp": timestamp,
            "text": text,
        })

    lines.sort(key=lambda item: item["timestamp"])

    return lines


def current_lyric_index(track, position):
    lines = get_lyrics_lines(track)

    if not lines:
        return -1

    current_index = -1

    for i, line in enumerate(lines):
        if line["timestamp"] <= position:
            current_index = i
        else:
            break

    return current_index


def format_lyrics(track, position, max_width=70, theme=None):
    lines = get_lyrics_lines(track)
    theme = theme or default_theme

    if not lines:
        content = Align.center(
            Text(
                "♪no lyrics yet♪",
                style="dim",
            ),
            vertical="middle",
        )

        return Panel(
            content,
            title="♫ LYRICS",
            border_style=theme.secondary,
            box=box.ROUNDED,
            height=7,
            padding=(0, 1),
        )

    index = current_lyric_index(track, position)

    previous = ""
    current = ""
    following = ""

    if index >= 0:
        current = lines[index]["text"]

    if index > 0:
        previous = lines[index - 1]["text"]

    if index + 1 < len(lines):
        following = lines[index + 1]["text"]

    text = Text(no_wrap=True, overflow="ellipsis")

    if previous:
        text.append(
            shorten(previous, max_width),
            style="dim grey50",
        )

    text.append("\n")

    if current:
        text.append(
            shorten(current, max_width),
            style=theme.highlight,
        )

    text.append("\n")

    if following:
        text.append(
            shorten(following, max_width),
            style="dim cyan",
        )

    return Panel(
        Align.center(text, vertical="middle"),
        title="♫ LYRICS",
        border_style=theme.secondary,
        box=box.ROUNDED,
        height=7,
        padding=(0, 1),
    )


def format_time(seconds):
    try:
        seconds = max(0, int(seconds or 0))
    except (TypeError, ValueError):
        seconds = 0

    minutes = seconds // 60
    secs = seconds % 60

    return f"{minutes}:{secs:02d}"


def progress_bar(mpv, width=70, theme=None):
    theme = theme or default_theme()
    if mpv is None:
        position = 0
        duration = 0
    else:
        position = mpv.get_time_pos() or 0
        duration = mpv.get_duration() or 0

    if duration <= 0:
        ratio = 0.0
    else:
        ratio = max(0.0, min(1.0, position / duration))

    filled = int(ratio * width)
    empty = width - filled

    bar = "━" * filled + "─" * empty

    text = Text()
    text.append(format_time(position), style="dim")
    text.append(" ")
    text.append(bar, style=theme.accent)
    text.append(" ")
    text.append(format_time(duration), style="dim")

    return Align.center(text)


def terminal_width(console):
    return console.size.width


def terminal_height(console):
    return console.size.height


def columns(console):
    width = terminal_width(console)

    if width >= 130:
        return 4
    if width >= 90:
        return 3
    if width >= 55:
        return 2

    return 1


def card_width(console):
    width = terminal_width(console)
    cols = columns(console)
    spacing = 2 * cols

    return max(18, (width - spacing - 4) // cols)


def header(theme=None):
    theme = theme or default_theme()
    logo = Text("HARMONY", style=f"bold {theme.accent}")
    subtitle = Text("YOUR MUSIC LIBRARY", style="dim")

    return Panel(
        Align.center(
            Text.assemble(logo, "\n", subtitle)
        ),
        border_style=theme.border,
        box=box.ROUNDED,
        height=4,
        padding=(0, 1),
    )


def album_card(album, selected, console, theme=None):
    theme = theme or default_theme()
    width = card_width(console)
    content_width = max(1, width - 4)
    artwork_width = min(8, max(5, content_width // 3))
    details_width = max(1, content_width - artwork_width - 4)
    name = album_name(album)
    tracks = album.get("tracks", [])

    artists = []

    for track in tracks:
        artist = track.get("artist")

        if artist and artist not in artists:
            artists.append(artist)

    artist = ", ".join(artists) if artists else "Unknown Artist"

    name = shorten(name, max(1, details_width - (2 if selected else 0)))
    artist = shorten(artist, details_width)

    text = Text()

    if selected:
        text.append("▶ ", style=f"bold {theme.accent}")

    text.append(name, style="bold white")
    text.append("\n" + artist, style="dim")
    text.append(
        "\n" + str(len(tracks)) + " tracks",
        style="dim cyan",
    )

    content = Table.grid(padding=(0, 1))
    content.add_column(width=artwork_width, no_wrap=True)
    content.add_column(width=details_width, no_wrap=True)
    content.add_row(
        render_artwork(
            album.get("cover"),
            width=artwork_width,
            height=3,
            mode="color",
            theme=theme,
        ),
        text,
    )

    return Panel(
        content,
        width=width,
        height=6,
        padding=(0, 1),
        box=box.ROUNDED,
        border_style=theme.accent if selected else theme.border,
    )


def album_grid(albums, selected, console, theme=None):
    cols = columns(console)
    width = card_width(console)
    height = terminal_height(console)

    rows = max(1, (height - 28) // 7)
    per_page = rows * cols

    page = selected // per_page
    start = page * per_page
    visible = albums[start:start + per_page]

    table = Table(
        show_header=False,
        show_edge=False,
        box=None,
        expand=False,
        padding=(0, 1),
        collapse_padding=True,
    )

    for _ in range(cols):
        table.add_column(width=width, no_wrap=True)

    row = []

    for i, album in enumerate(visible):
        real_index = start + i

        row.append(
            album_card(
                album,
                real_index == selected,
                console,
                theme,
            )
        )

        if len(row) == cols:
            table.add_row(*row)
            row = []

    if row:
        while len(row) < cols:
            row.append("")

        table.add_row(*row)

    return table


def track_list(album, selected):
    tracks = album.get("tracks", [])

    table = Table(
        show_header=True,
        header_style="bold dim",
        box=box.ROUNDED,
        border_style="grey30",
        expand=True,
        padding=(0, 1),
    )

    table.add_column("#", width=5, justify="right", no_wrap=True)
    table.add_column("TITLE", ratio=4, no_wrap=True)
    table.add_column("ARTIST", ratio=2, no_wrap=True)

    for i, track in enumerate(tracks):
        title = shorten(title_of(track), 50)
        artist = shorten(artist_of(track), 30)

        if i == selected:
            table.add_row(
                f"▶ {i + 1}",
                title,
                artist,
                style="bold black on cyan",
            )
        else:
            table.add_row(str(i + 1), title, artist)

    return table


def now_playing(playlist, mpv=None, theme=None):
    theme = theme or default_theme()
    track = playlist.current

    if not track:
        content = Align.center(
            Text("Nothing playing", style="dim"),
            vertical="middle",
        )
    else:
        title = shorten(title_of(track), 60)
        artist = shorten(artist_of(track), 50)
        album = shorten(album_of(track), 50)

        is_paused = mpv.paused if mpv is not None else False
        play_icon = "▶" if is_paused else "❚❚"

        content = Group(
            Align.center(
                Text.assemble(
                    (title + "\n", "bold white"),
                    (artist + "\n", "cyan"),
                    (album + "\n", "dim"),
                    ("◀    ", "bold white"),
                    (play_icon, "bold bright_cyan"),
                    ("    ▶", "bold white"),
                )
            ),
            progress_bar(mpv, theme=theme),
        )

    return Panel(
        content,
        title="♫ NOW PLAYING",
        border_style=theme.secondary,
        box=box.ROUNDED,
        height=9,
        padding=(0, 1),
    )


def mini_player(playlist, mpv, console, theme=None, height=None):
    theme = theme or default_theme()
    track = playlist.current
    if not track:
        idle = Text()
        idle.append("♫  ", style=f"bold {theme.accent}")
        idle.append("Nothing playing", style=f"bold {theme.highlight}")
        idle.append(
            (
                "  ·  D album  ·  Enter rediscover"
                if console.size.width < 80
                else "  ·  D play album  ·  Enter rediscover"
            ),
            style=theme.muted,
        )
        content = Align.center(idle, vertical="middle")
    elif console.size.width < 52:
        play_state = "PAUSED" if getattr(mpv, "paused", False) else "PLAYING"
        title_width = max(10, console.size.width - 8)
        content = Group(
            Text.assemble(
                ("▶ " if play_state == "PLAYING" else "Ⅱ ", f"bold {theme.accent}"),
                (shorten(title_of(track), title_width), f"bold {theme.highlight}"),
            ),
            Text.assemble(
                (shorten(artist_of(track), max(8, console.size.width - 6)), theme.accent),
                ("  ·  " + shorten(album_of(track), 18), theme.muted),
            ),
            progress_bar(mpv, width=max(4, console.size.width - 18), theme=theme),
        )
    else:
        tracks, current_index, queue_name = playlist.snapshot()
        queue_position = f"{current_index + 1}/{len(tracks)}" if tracks else "0/0"
        play_state = "PAUSED" if getattr(mpv, "paused", False) else "PLAYING"
        repeat_state = "REPEAT ON" if getattr(playlist, "repeat", False) else "REPEAT OFF"
        volume = max(0, min(100, int(getattr(mpv, "volume", 75))))
        details_width = max(18, console.size.width - 35)
        cover = render_artwork(
            track.get("_cover"),
            width=12,
            height=3,
            mode="color",
            theme=theme,
        )
        details = Table.grid(expand=True, padding=(0, 1))
        details.add_column(width=14)
        details.add_column(ratio=1)
        details.add_row(
            cover,
            Group(
                Text(
                    shorten(title_of(track), details_width),
                    style=f"bold {theme.highlight}",
                    no_wrap=True,
                    overflow="ellipsis",
                ),
                Text(
                    shorten(artist_of(track), details_width),
                    style=f"bold {theme.accent}",
                    no_wrap=True,
                    overflow="ellipsis",
                ),
                Text(
                    shorten(album_of(track), details_width),
                    style=theme.muted,
                    no_wrap=True,
                    overflow="ellipsis",
                ),
            ),
        )
        details.add_row(
            "",
            progress_bar(
                mpv,
                width=max(4, min(52, console.size.width - 28)),
                theme=theme,
            ),
        )
        details.add_row(
            "",
            Text.assemble(
                (f"● {play_state}", f"bold {theme.accent}"),
                (f"   QUEUE {queue_position}", theme.muted),
                (f"   {repeat_state}", theme.muted),
                (f"   VOL {volume}%", theme.muted),
                (f"   {shorten(queue_name, 18)}", theme.secondary),
            ),
        )
        content = details
    return Panel(
        content,
        title="♫ MINI PLAYER  ·  I FOR FULL VIEW",
        border_style=theme.accent,
        box=box.ROUNDED,
        height=height or (7 if console.size.width >= 52 else 6),
        padding=(0, 1),
    )


def build_now_playing_screen(
    playlist,
    mpv,
    console,
    theme,
    art_mode="color",
    spectrum=None,
):
    track = playlist.current
    compact = console.size.width < 52
    cover = track.get("_cover") if track else None
    title = Text(
        title_of(track) if track else "NOTHING PLAYING",
        style=f"bold {theme.highlight if track else theme.accent}",
    )
    lyric_lines = get_lyrics_lines(track)
    lyric_index = (
        current_lyric_index(track, mpv.get_time_pos())
        if track
        else -1
    )
    detail_rows = []
    if track:
        volume = max(0, min(100, int(getattr(mpv, "volume", 75))))
        volume_segments = 6 if compact else 10
        volume_bar = Text("VOL " if compact else "VOLUME ", style=theme.muted)
        filled_segments = volume * volume_segments // 100
        volume_bar.append("█" * filled_segments, style=f"bold {theme.highlight}")
        volume_bar.append("░" * (volume_segments - filled_segments), style=theme.muted)
        volume_bar.append(f" {volume}% +/-", style=theme.accent)
        detail_rows = [
            Text(
                shorten(f"{artist_of(track)} · {album_of(track)}", max(16, console.size.width // 2)),
                style=theme.accent,
                no_wrap=True,
                overflow="ellipsis",
            ),
            Text(
                f"{track.get('year') or track.get('_year') or 'Unknown year'}"
                f"  ·  Repeat {'On' if getattr(playlist, 'repeat', False) else 'Off'}",
                style=theme.muted,
                no_wrap=True,
                overflow="ellipsis",
            ),
            volume_bar,
            progress_bar(
                mpv,
                width=max(8, min(24, console.size.width // 5)),
                theme=theme,
            ),
        ]

    content_height = max(
        12,
        console.size.height - 4 - footer("now_playing", console, theme).height,
    )
    wide_layout = (
        console.size.width >= 76
        and content_height >= 18
        and spectrum is not None
    )
    if wide_layout:
        metadata_height = min(7, max(4, content_height // 3))
        spectrum_height = min(4, max(1, content_height // 8))
        spectrum_panel_height = spectrum_height + 2
        lyrics_panel_height = min(5, max(3, content_height // 5))
        queue_height = max(
            2,
            content_height
            - metadata_height
            - spectrum_panel_height
            - lyrics_panel_height,
        )
    else:
        spectrum_height = min(5, max(1, content_height // 8)) if spectrum else 0
        spectrum_panel_height = spectrum_height + 2 if spectrum is not None else 0
        lyrics_panel_height = min(5, max(3, content_height // 5))
        top_height = min(
            7,
            max(
                3,
                content_height
                - spectrum_panel_height
                - lyrics_panel_height
                - 3,
            ),
        )
        queue_height = max(
            3,
            content_height
            - top_height
            - spectrum_panel_height
            - lyrics_panel_height,
        )

    lyric_content = Text()
    lyric_width = max(
        12,
        console.size.width * 2 // 5 - 8
        if wide_layout
        else console.size.width - 10,
    )
    if lyric_index >= 0:
        if lyrics_panel_height >= 5 and lyric_index > 0:
            lyric_content.append(
                shorten(lyric_lines[lyric_index - 1]["text"], lyric_width) + "\n",
                style=theme.muted,
            )
        lyric_content.append(
            shorten(lyric_lines[lyric_index]["text"], lyric_width),
            style=f"bold {theme.highlight}",
        )
        if lyrics_panel_height >= 5 and lyric_index + 1 < len(lyric_lines):
            lyric_content.append(
                "\n" + shorten(lyric_lines[lyric_index + 1]["text"], lyric_width),
                style=theme.accent,
            )
    else:
        lyric_content.append("Lyrics unavailable", style=theme.muted)

    artwork_width = (
        max(1, console.size.width * 3 // 5 - 4)
        if wide_layout
        else max(11, min(32, console.size.width // 3 - 4))
    )
    artwork_height = (
        max(3, content_height - 2)
        if wide_layout
        else max(1, top_height - 2)
    )
    artwork = Panel(
        render_artwork(
            cover,
            width=artwork_width,
            height=artwork_height,
            mode=art_mode,
            theme=theme,
        ),
        title=f"ARTWORK · {art_mode.upper()}",
        border_style=theme.secondary,
        box=box.ROUNDED,
        padding=(0, 1),
    )
    metadata = Panel(
        Group(title, *detail_rows),
        title="NOW PLAYING",
        border_style=theme.accent,
        box=box.ROUNDED,
        padding=(0, 1),
    )
    lyrics_panel = Panel(
        Align(
            lyric_content,
            vertical="middle",
        ),
        title="♫ LYRICS",
        border_style=theme.accent,
        box=box.ROUNDED,
        padding=(0, 1),
        height=lyrics_panel_height,
    )

    tracks, current_index, _ = playlist.snapshot()
    upcoming = tracks[current_index + 1 :]
    visible_upcoming = upcoming[: max(0, queue_height - 2)]
    queue_rows = Table.grid(expand=True, padding=(0, 1))
    queue_rows.add_column(width=3, justify="right")
    queue_rows.add_column(ratio=1, no_wrap=True)
    queue_rows.add_column(ratio=1, no_wrap=True)
    if visible_upcoming:
        for index, next_track in enumerate(visible_upcoming, start=1):
            queue_rows.add_row(
                str(index),
                shorten(title_of(next_track), max(10, console.size.width // 3)),
                shorten(artist_of(next_track), max(8, console.size.width // 4)),
            )
    else:
        queue_rows.add_row("", "No upcoming tracks", "")
    next_tracks_panel = Panel(
        queue_rows,
        title=f"NEXT UP · {len(upcoming)} TRACKS",
        border_style=theme.secondary,
        box=box.ROUNDED,
        padding=(0, 1),
    )

    root = Layout(name="now-playing")
    controls = footer("now_playing", console, theme)
    if spectrum is not None:
        spectrum_panel = spectrum.render_compact(
            bars=max(
                8,
                min(30, (console.size.width - artwork_width - 16) // 2),
            ),
            height=spectrum_height,
        )
    root.split_column(
        Layout(header(theme), size=4, name="header"),
        Layout(name="details", ratio=1),
        Layout(controls, size=controls.height, name="controls"),
    )
    if wide_layout:
        right_column = Layout(name="now-playing-details")
        root["details"].split_row(
            Layout(artwork, ratio=3, name="artwork"),
            Layout(right_column, ratio=2),
        )
        right_column.split_column(
            Layout(metadata, size=metadata_height, name="metadata"),
            *(
                [Layout(spectrum_panel, size=spectrum_height + 2, name="spectrum")]
                if spectrum is not None
                else []
            ),
            Layout(lyrics_panel, size=lyrics_panel_height, name="lyrics"),
            Layout(next_tracks_panel, size=queue_height, name="next-tracks"),
        )
    else:
        root["details"].split_column(
            Layout(name="top", size=top_height),
            *(
                [Layout(spectrum_panel, size=spectrum_height + 2, name="spectrum")]
                if spectrum is not None
                else []
            ),
            Layout(lyrics_panel, size=lyrics_panel_height, name="lyrics"),
            Layout(next_tracks_panel, size=queue_height, name="next-tracks"),
        )
        root["top"].split_row(
            Layout(artwork, ratio=1, name="artwork"),
            Layout(metadata, ratio=2, name="metadata"),
        )
    return root


def build_stats_screen(stats, selected_period, playlist, mpv, console, theme):
    _, period_name = STAT_PERIODS[selected_period]
    listening_seconds = max(0, int(stats["listening_seconds"]))
    hours, seconds = divmod(listening_seconds, 3600)
    minutes = seconds // 60
    listening_label = f"{hours}h {minutes}m" if hours else f"{minutes}m"

    metrics = Table.grid(expand=True, padding=(1, 2))
    for _ in range(3):
        metrics.add_column(ratio=1)
    metrics.add_row(
        Panel(
            Text(listening_label, style=f"bold {theme.highlight}"),
            title="LISTENING TIME",
            border_style=theme.accent,
            box=box.ROUNDED,
        ),
        Panel(
            Text(str(stats["played_tracks"]), style=f"bold {theme.highlight}"),
            title="TRACK PLAYS",
            border_style=theme.secondary,
            box=box.ROUNDED,
        ),
        Panel(
            Text(str(stats["unique_tracks"]), style=f"bold {theme.highlight}"),
            title="UNIQUE TRACKS",
            border_style=theme.border,
            box=box.ROUNDED,
        ),
    )

    def ranking(title, items):
        table = Table(show_header=False, box=None, expand=True, padding=(0, 1))
        table.add_column("#", width=3, justify="right")
        table.add_column("NAME", ratio=1, no_wrap=True)
        table.add_column("PLAYS", justify="right")
        if items:
            for index, item in enumerate(items[:5], start=1):
                table.add_row(
                    str(index),
                    shorten(item["name"], max(12, console.size.width // 5)),
                    str(item["plays"]),
                )
        else:
            table.add_row("", "No plays in this period", "")
        return Panel(table, title=title, border_style=theme.border, box=box.ROUNDED)

    rankings = Table.grid(expand=True, padding=(0, 1))
    for _ in range(3):
        rankings.add_column(ratio=1)
    rankings.add_row(
        ranking("TOP ARTISTS", stats["top_artists"]),
        ranking("TOP ALBUMS", stats["top_albums"]),
        ranking("TOP TRACKS", stats["top_tracks"]),
    )

    activity = Text()
    if stats["daily_activity"]:
        max_plays = max(day["plays"] for day in stats["daily_activity"])
        for day in stats["daily_activity"]:
            bar = "█" * max(1, round(18 * day["plays"] / max_plays))
            activity.append(f"{day['day']}  ", style=theme.muted)
            activity.append(bar, style=theme.accent)
            activity.append(f"  {day['plays']}\n", style=theme.highlight)
    else:
        activity.append("No listening activity recorded yet", style=theme.muted)

    content = Group(
        Panel(
            metrics,
            title=f"HARMONY STATS · {period_name.upper()}",
            border_style=theme.accent,
            box=box.ROUNDED,
        ),
        rankings,
        Panel(
            activity,
            title="LISTENING ACTIVITY",
            border_style=theme.secondary,
            box=box.ROUNDED,
        ),
    )
    root = Layout(name="stats")
    root.split_column(
        Layout(header(theme), size=4),
        Layout(content, ratio=1),
        Layout(mini_player(playlist, mpv, console, theme), size=7),
        Layout(
            footer("stats", console, theme),
            size=footer("stats", console, theme).height,
        ),
    )
    return root


def build_theme_screen(theme_manager, selected, theme, console):
    rows = []
    themes_table = Table(show_header=False, box=None, expand=True)
    themes_table.add_column("SELECTED", width=3)
    themes_table.add_column("THEME", ratio=1)
    themes_table.add_column("PALETTE", ratio=2)
    for index, option in enumerate(THEMES):
        palette = Text()
        for color in (option.accent, option.secondary, option.highlight):
            palette.append("  ●  ", style=color)
        themes_table.add_row(
            "▶" if index == selected else "",
            option.name,
            palette,
            style=f"bold {theme.accent}" if index == selected else None,
        )
    rows.append(
        Panel(
            themes_table,
            title="HARMONY THEMES",
            border_style=theme.border,
            box=box.ROUNDED,
        )
    )
    dynamic_status = "ON" if theme_manager.dynamic else "OFF"
    rows.append(
        Panel(
            Text.assemble(
                ("Dynamic artwork palette: ", "white"),
                (dynamic_status, f"bold {theme.accent}"),
                ("\n\nCurrent: " + theme.name, "dim"),
            ),
            title="PERSONALIZATION",
            border_style=theme.secondary,
            box=box.ROUNDED,
            padding=(1, 2),
        )
    )
    root = Layout(name="themes")
    theme_footer = footer("theme_settings", console, theme)
    root.split_column(
        Layout(header(theme), size=4),
        Layout(Group(*rows), ratio=1),
        Layout(theme_footer, size=theme_footer.height),
    )
    return root


def build_flow_screen(selected, theme, console):
    modes = Table.grid(expand=True, padding=(1, 2))
    modes.add_column(width=3, justify="right")
    modes.add_column(ratio=1)
    modes.add_column(ratio=3)
    for index, (_, name, description) in enumerate(FLOW_MODES):
        modes.add_row(
            "▶" if index == selected else str(index + 1),
            name,
            description,
            style=f"bold {theme.highlight} on {theme.surface}" if index == selected else None,
        )
    root = Layout(name="flow")
    flow_footer = footer("flow", console, theme)
    root.split_column(
        Layout(header(theme), size=4),
        Layout(
            Panel(
                modes,
                title="HARMONY FLOW · SMART SHUFFLE",
                border_style=theme.accent,
                box=box.ROUNDED,
            ),
            ratio=1,
        ),
        Layout(flow_footer, size=flow_footer.height),
    )
    return root


def build_playlist_picker_screen(
    state,
    selected,
    track,
    playlist,
    mpv,
    console,
    theme=None,
):
    theme = theme or default_theme()
    names = list(state.get("playlists", {}).keys())

    if names:
        selected = max(0, min(selected, len(names) - 1))
        selected_name = names[selected]
    else:
        selected_name = None

    layout = Table.grid(expand=True)

    layout.add_row(header(theme))

    # Morceau que l'utilisateur souhaite ajouter
    if track:
        track_info = Text()
        track_info.append("Track to add\n", style=theme.accent)
        track_info.append(f"{title_of(track)}\n", style=theme.secondary)
        track_info.append(f"{album_of(track)}\n", style=theme.secondary)
        track_info.append("\n" + artist_of(track), style=theme.secondary)

        layout.add_row(
            Panel(
                Align.center(track_info),
                border_style=theme.accent,
                box=box.ROUNDED,
                padding=(1, 1),
            )
        )

    selector = Table(
        show_header=False,
        box=box.ROUNDED,
        border_style=theme.accent,
        expand=True,
        padding=(0, 1),
    )
    selector.add_column("PLAYLIST", ratio=1)
    selector.add_column("ACTION", justify="right", no_wrap=True)

    if names:
        for i, name in enumerate(names):
            selector.add_row(
                ("▶ " if i == selected else "  ") + shorten(name, 45),
                "ENTRÉE" if i == selected else "",
                style="bold black on cyan" if i == selected else None,
            )
    else:
        selector.add_row("no playlist available", "")

    layout.add_row(
        Panel(
            selector,
            title="choose a playlist",
            border_style=theme.secondary,
            box=box.ROUNDED,
        )
    )

    if not names:
        message = "no playlist. Press ESC to back"
    else:
        message = "← → or ↑ ↓ : choose    ENTER : add    ESC : cancel"

    layout.add_row(
        Panel(
            Align.center(Text(message, style=theme.accent)),
            box=box.ROUNDED,
            height=3,
        )
    )

    layout.add_row(footer("choose_playlist", console, theme))
    layout.add_row(mini_player(playlist, mpv, console, theme))

    return layout


def footer(mode, console=None, theme=None):
    theme = theme or default_theme()
    compact = console is not None and console.size.width < 90
    controls_by_mode = {
        "home": (
            (
                "D Day  S Shuffle  O Tracks  N Album",
                "↑↓←→ Browse  Enter Play  A Queue  M List  F Favorite  R Refresh",
                "P Resume  X Library  H History  V Favorites  I Now  Y Themes",
                "B Stats  G Flow  T Artists  U Queue  L Lists  / Search  W Home  Q Quit",
            )
            if compact
            else (
                "D Play album  S Shuffle album  O Tracks  N Next album",
                "↑↓←→ Browse  Enter Play  A Queue  M Playlist  F Favorite  R Refresh",
                "P Resume  X Library  H History  V Favorites  I Now  Y Themes  B Stats",
                "G Flow  T Artists  U Queue  L Playlists  / Search  W Home  Q Quit",
            )
        ),
        "now_playing": (
            "SPACE Play/Pause  N/P Next/Previous  F Favorite  S Shuffle  E Repeat",
            "+/- Volume  C Artwork  Y Themes  ESC Back",
            "B Stats  W Home  Q Quit",
        ),
        "albums": (
            "↑↓←→ Browse  Enter Open album  R Shuffle library  N/P Next/Previous  SPACE Pause",
            "S Visualizer  I Now  Y Themes  B Stats  G Flow  V Favorites  H History",
            "T Artists  U Queue  L Playlists  / Search  W Home  Q Quit",
        ),
        "tracks": (
            "↑↓ Browse  Enter Queue next  P Queue last  R Shuffle library  N Next  SPACE Pause",
            "F Favorite  A Add to playlist  ESC Albums",
            "S Visualizer  I Now  Y Themes  B Stats  G Flow  V Favorites  H History",
            "T Artists  U Queue  L Playlists  / Search  W Home  Q Quit",
        ),
        "playlists": (
            "↑↓ Track  ←→/[ ] Playlist  Enter Play playlist  C Create  A Add current",
            "F Favorite  ESC Albums",
            "S Visualizer  I Now  Y Themes  B Stats  G Flow  V Favorites  H History",
            "T Artists  U Queue  / Search  W Home  Q Quit",
        ),
        "Spectrum_Visualizer": (
            "SPACE Pause  N/P Next/Previous  ESC Albums",
            "I Now  Y Themes  B Stats  G Flow  V Favorites  H History",
            "T Artists  U Queue  L Playlists  / Search  W Home  Q Quit",
        ),
        "queue": (
            "↑↓ Browse  Enter Play selected  D Remove  M Move mode  X Clear upcoming",
            "E Repeat  S Shuffle upcoming  SPACE Pause  ESC Albums",
            "I Now  Y Themes  B Stats  G Flow  V Favorites  H History",
            "T Artists  L Playlists  / Search  W Home  Q Quit",
        ),
        "search": (
            "↑↓ Browse  Enter Queue next  P Queue last  N Next  SPACE Pause",
            "F Favorite  A Add to playlist  / Edit search  ESC Back",
            "S Visualizer  I Now  Y Themes  B Stats  G Flow  V Favorites  H History",
            "T Artists  U Queue  L Playlists  W Home  Q Quit",
        ),
        "search_typing": (
            "Type to search  Enter Queue first result  ↑↓ Select  ESC Browse/exit typing",
        ),
        "artists": (
            "↑↓ Browse  Enter Mix artist  O Play artist in order  N/P Next/Previous",
            "SPACE Pause  ESC Albums",
            "S Visualizer  I Now  Y Themes  B Stats  G Flow  V Favorites  H History",
            "U Queue  L Playlists  / Search  W Home  Q Quit",
        ),
        "theme_settings": (
            "↑↓ Select theme  Enter Apply  D Toggle dynamic colors  ESC Back",
            "B Stats  W Home  Q Quit",
        ),
        "flow": (
            "↑↓ Select mode  Enter Start Flow  Queued tracks stay next  ESC Home",
            "B Stats  W Home  Q Quit",
        ),
        "stats": ("↑↓ Change period  ESC/B Back  W Home  Q Quit",),
        "choose_playlist": (
            "↑↓/←→ Select playlist  Enter Add track  ESC Cancel",
            "B Stats  W Home  Q Quit",
        ),
        "favorites": (
            "↑↓ Select  Enter Play favorites from selected track  F Favorite  A Add to playlist",
            "ESC Albums  S Visualizer  I Now  Y Themes  B Stats  G Flow  H History",
            "T Artists  U Queue  L Playlists  / Search  W Home  Q Quit",
        ),
        "history": (
            "↑↓ Select  Enter Play history from selected track  F Favorite  A Add to playlist",
            "ESC Albums  S Visualizer  I Now  Y Themes  B Stats  G Flow  V Favorites",
            "T Artists  U Queue  L Playlists  / Search  W Home  Q Quit",
        ),
    }

    lines = controls_by_mode.get(
        mode,
        ("ESC Back  B Stats  W Home  Q Quit",),
    )

    # Utilise au maximum la largeur disponible
    wrap_width = max(16, console.size.width - 4) if console else 80

    # Fusionne les commandes pour limiter les retours à la ligne
    controls = "  │  ".join(lines)

    # Wrap intelligent : conserve les groupes de commandes
    wrapped = textwrap.wrap(
        controls,
        width=wrap_width,
        break_long_words=False,
        break_on_hyphens=False,
    )

    controls = "\n".join(wrapped)

    # Hauteur minimale + lignes réellement utilisées
    height = len(wrapped) + 2

    return Panel(
        Align.center(Text(controls, style=theme.muted)),
        box=box.SIMPLE_HEAVY,
        height=height,
        padding=(0, 1),
    )


def build_album_screen(
    albums,
    selected,
    playlist,
    mpv,
    console,
    theme=None,
):
    theme = theme or default_theme()
    layout = Table.grid(expand=True)

    layout.add_row(header(theme))
    layout.add_row(album_grid(albums, selected, console, theme))
    layout.add_row(mini_player(playlist, mpv, console, theme))
    layout.add_row(
        format_lyrics(playlist.current, mpv.get_time_pos(), theme=theme)
    )
    layout.add_row(build_nexts_tracks_screen(playlist, mpv, console, theme))
    layout.add_row(footer("albums", console, theme))

    return layout

def build_home_screen(
    library,
    daily_album,
    recent_tracks,
    discover_tracks,
    favorite_tracks,
    selected_track,
    playlist,
    mpv,
    console,
    hour,
    theme=None,
):
    from app.features.home import (
        album_artists,
        greeting,
        library_artist_count,
        library_track_count,
    )
    theme = theme or default_theme()

    albums = library.get("albums", [])
    track_count = library_track_count(library)
    welcome = Text()
    welcome.append(greeting(hour), style=f"bold {theme.highlight}")
    welcome.append(
        f"   {len(albums)} albums  ·  {library_artist_count(library)} artists"
        f"  ·  {track_count} tracks",
        style=theme.muted,
    )

    compact_dashboard = console.size.width < 80 or console.size.height < 30
    daily_content = Text()
    if daily_album:
        tracks = daily_album.get("tracks", [])
        daily_content.append(
            album_name(daily_album) + "\n",
            style=f"bold {theme.highlight}",
        )
        if compact_dashboard:
            year = daily_album.get("year") or daily_album.get("date")
            details = [album_artists(daily_album), f"{len(tracks)} tracks"]
            if year:
                details.append(str(year)[:4])
            daily_content.append(
                shorten("  ·  ".join(details), max(18, console.size.width - 22)),
                style=theme.accent,
            )
        else:
            daily_content.append(
                album_artists(daily_album) + "\n",
                style=f"bold {theme.accent}",
            )
            year = daily_album.get("year") or daily_album.get("date")
            total_duration = sum(
                float(track.get("duration") or 0)
                for track in tracks
                if isinstance(track.get("duration"), (int, float))
            )
            details = [f"{len(tracks)} tracks"]
            if year:
                details.append(str(year)[:4])
            if total_duration:
                details.append(format_time(total_duration))
            daily_content.append(
                "  ·  ".join(details) + "\n",
                style=theme.muted,
            )
            if tracks:
                daily_content.append(
                    "01  " + shorten(title_of(tracks[0]), 26),
                    style=theme.highlight,
                )
            daily_content.append(
                "\nEnter play  ·  O browse tracks",
                style=theme.secondary,
            )
    else:
        daily_content.append("No albums available", style=theme.muted)
    daily_artwork = render_artwork(
        daily_album.get("cover") if daily_album else None,
        width=max(8, min(28, console.size.width // 3 - 4)),
        height=max(2, min(8, (console.size.height - 20) // 2)),
        mode="color",
        theme=theme,
    )
    daily_panel = daily_content

    discover = Table(
        show_header=False,
        box=None,
        expand=True,
        padding=(0, 1),
    )
    discover_columns = 2 if console.size.width >= 58 else 1
    for _ in range(discover_columns):
        discover.add_column("TRACK", ratio=1, no_wrap=True)
    if discover_tracks:
        visible_rows = (
            5
            if console.size.height >= 34
            else 4
            if console.size.height >= 26 and console.size.width >= 80
            else 3
            if console.size.height >= 26
            else 2
        )
        total_rows = (min(len(discover_tracks), 10) + discover_columns - 1) // discover_columns
        first_row = min(
            selected_track // discover_columns,
            max(0, total_rows - visible_rows),
        )
        first_index = first_row * discover_columns
        visible_discover = discover_tracks[
            first_index : min(first_index + visible_rows * discover_columns, 10)
        ]
        for offset in range(0, len(visible_discover), discover_columns):
            cells = []
            for index in range(
                offset,
                min(offset + discover_columns, len(visible_discover)),
            ):
                track_index = first_index + index
                track = visible_discover[index]
                label = f"{track_index + 1:02d} {title_of(track)}"
                cells.append(
                    Text(
                        shorten(
                            label,
                            max(14, console.size.width // (discover_columns * 2) - 3),
                        ),
                        style=(
                            f"bold {theme.highlight} on {theme.surface}"
                            if track_index == selected_track
                            else theme.muted
                        ),
                        no_wrap=True,
                        overflow="ellipsis",
                    )
                )
            while len(cells) < discover_columns:
                cells.append(Text(""))
            discover.add_row(*cells)
    else:
        empty_cells = [Text("No playable tracks to rediscover", style=theme.muted)]
        if discover_columns == 2:
            empty_cells.append(Text(""))
        discover.add_row(*empty_cells)
    discover_panel = Panel(
        discover,
        title=f"REDISCOVER · {min(len(discover_tracks), 10)} TRACKS",
        border_style=theme.accent,
        box=box.ROUNDED,
        padding=(0, 1),
    )

    recent = Text()
    if recent_tracks:
        for index, track in enumerate(recent_tracks[:3], 1):
            recent.append(
                f"{index:02d}  {shorten(title_of(track), 24)}",
                style=f"bold {theme.highlight}",
            )
            recent.append(
                f"  ·  {shorten(artist_of(track), 20)}\n",
                style=theme.muted,
            )
    else:
        recent.append("Nothing played yet", style=theme.muted)
    recent_panel = Panel(
            recent,
            title=(
                "RECENTLY PLAYED · H OPEN"
                if console.size.width >= 80
                else "RECENT · H OPEN"
            ),
            border_style=theme.border,
            box=box.ROUNDED,
            padding=(0, 1),
        )
    favorites = Table.grid(expand=True, padding=(0, 1))
    favorite_columns = 2 if console.size.width >= 58 else 1
    for _ in range(favorite_columns):
        favorites.add_column(ratio=1, no_wrap=True)
    if favorite_tracks:
        visible_favorites = favorite_tracks[:20]
        for start in range(0, len(visible_favorites), favorite_columns):
            cells = []
            for index in range(
                start,
                min(start + favorite_columns, len(visible_favorites)),
            ):
                track = visible_favorites[index]
                label = f"{index + 1:02d}  {title_of(track)}"
                cells.append(
                    Text(
                        shorten(
                            label,
                            max(12, console.size.width // (favorite_columns * 2) - 3),
                        ),
                        style=f"bold {theme.highlight}",
                        no_wrap=True,
                        overflow="ellipsis",
                    )
                )
            while len(cells) < favorite_columns:
                cells.append(Text(""))
            favorites.add_row(*cells)
    else:
        favorites.add_row(Text("No favorites yet", style=theme.muted))
    favorites_panel = Panel(
            favorites,
            title=(
                f"YOUR FAVORITES · {min(len(favorite_tracks), 20)} TRACKS · V OPEN"
                if console.size.width >= 80
                else f"FAVORITES · {min(len(favorite_tracks), 20)} · V OPEN"
            ),
            border_style=theme.border,
            box=box.ROUNDED,
            padding=(0, 1),
        )

    daily_feature = Table.grid(expand=True, padding=(0, 1))
    daily_feature.add_column(width=max(14, min(30, console.size.width // 3)))
    daily_feature.add_column(ratio=1)
    daily_feature.add_row(daily_artwork, daily_panel)
    hero = Panel(
        daily_feature,
        title="ALBUM OF THE DAY",
        border_style=theme.secondary,
        box=box.ROUNDED,
        padding=(0, 0),
    )

    root = Layout(name="home")
    home_footer = footer("home", console, theme)
    player_height = (
        3
        if playlist.current is None
        else 5
        if console.size.height < 32
        else 7
        if console.size.width >= 80
        else 6
    )
    root.split_column(
        Layout(
            Panel(
                welcome,
                title="HARMONY  ·  HOME",
                border_style=theme.border,
                box=box.ROUNDED,
                padding=(0, 1),
            ),
            size=3,
            name="welcome",
        ),
        Layout(name="content", ratio=1),
        Layout(
            mini_player(playlist, mpv, console, theme, height=player_height),
            size=player_height,
            name="player",
        ),
        Layout(
            home_footer,
            size=home_footer.height,
            name="footer",
        ),
    )
    if console.size.width < 80:
        root["content"].split_column(
            Layout(hero, ratio=2, name="daily"),
            Layout(discover_panel, ratio=3, name="rediscover"),
            Layout(name="library", ratio=3),
        )
    else:
        root["content"].split_column(
            Layout(name="features", ratio=10),
            Layout(name="library", ratio=11),
        )
        root["features"].split_row(
            Layout(hero, ratio=2, name="daily"),
            Layout(discover_panel, ratio=3, name="rediscover"),
        )
    root["library"].split_row(
        Layout(recent_panel, ratio=1, name="recent"),
        Layout(favorites_panel, ratio=1, name="favorites"),
    )
    return root


def build_spectrum_screen(
    playlist,
    mpv,
    console,
    spectrum,
    theme=None,
    ):
    theme = theme or default_theme()
    spectrum.update()

    layout = Table.grid(expand=True)

    layout.add_row(spectrum.render())
    layout.add_row(mini_player(playlist, mpv, console, theme))
    layout.add_row(format_lyrics(playlist.current, mpv.get_time_pos(), theme=theme))
    layout.add_row(build_nexts_tracks_screen(playlist, mpv, console, theme))
    layout.add_row(footer("Spectrum_Visualizer", console, theme))
    
    return layout


def build_track_screen(
    albums,
    album_index,
    track_index,
    playlist,
    mpv,
    console,
    theme=None,
):
    theme = theme or default_theme()
    album = albums[album_index]

    title = Text(album_name(album), style=f"bold {theme.accent}")

    album_header = Panel(
        Align.center(title),
        border_style=theme.border,
        box=box.ROUNDED,
        height=3,
    )

    layout = Table.grid(expand=True)

    layout.add_row(album_header)
    layout.add_row(track_list(album, track_index))
    layout.add_row(mini_player(playlist, mpv, console, theme))
    layout.add_row(format_lyrics(playlist.current, mpv.get_time_pos(), theme=theme))
    layout.add_row(build_nexts_tracks_screen(playlist, mpv, console, theme))
    layout.add_row(footer("tracks", console, theme))

    return layout


def build_collection_screen(
    title,
    tracks,
    selected,
    playlist,
    mpv,
    mode,
    console,
    theme=None,
):
    theme = theme or default_theme()
    collection = {
        "name": title,
        "tracks": tracks,
    }

    layout = Table.grid(expand=True)

    layout.add_row(
        Panel(
            Align.center(Text(title, style=f"bold {theme.accent}")),
            border_style=theme.border,
            box=box.ROUNDED,
            height=3,
        )
    )

    if tracks:
        layout.add_row(track_list(collection, selected))
    else:
        layout.add_row(
            Panel(
                Align.center(
                    Text("no tracks", style="dim"),
                    vertical="middle",
                ),
                height=5,
                border_style="grey30",
            )
        )

    layout.add_row(mini_player(playlist, mpv, console, theme))
    layout.add_row(format_lyrics(playlist.current, mpv.get_time_pos(), theme=theme))
    layout.add_row(footer(mode, console, theme))

    return layout


def build_playlists_screen(
    state,
    library,
    selected_playlist,
    selected_track,
    playlist,
    mpv,
    console,
    theme=None,
):
    theme = theme or default_theme()
    names = list(state["playlists"])

    # Sélection de la playlist
    selected_playlist = max(0, min(selected_playlist, len(names) - 1)) if names else 0

    selected_name = names[selected_playlist] if names else None

    # Résolution des morceaux de la playlist
    tracks = (
        resolve_references(
            state["playlists"].get(selected_name, []),
            library,
        )
        if selected_name
        else []
    )

    # Sections fixes de l'écran
    head = header(theme)
    now = mini_player(playlist, mpv, console, theme)
    lyrics = format_lyrics(
        playlist.current,
        mpv.get_time_pos(),
        theme=theme
    )
    foot = footer("playlists", console, theme)

    selector = Table(
        show_header=False,
        box=box.ROUNDED,
        title="PLAYLISTS",
        border_style="grey30",
        expand=True,
        padding=(0, 1),
    )

    selector.add_column("PLAYLISTS", no_wrap=True)

    # Hauteur réservée aux playlists
    selector_visible = max(
        1,
        min(len(names), console.size.height // 5),
    )

    selector_start = max(
        0,
        min(
            selected_playlist - selector_visible // 2,
            len(names) - selector_visible,
        ),
    )

    visible_names = names[selector_start : selector_start + selector_visible]

    if visible_names:
        for i, name in enumerate(visible_names):
            real_index = selector_start + i

            selector.add_row(
                shorten(name, 40),
                style=(
                    "bold black on cyan" if real_index == selected_playlist else None
                ),
            )
    else:
        selector.add_row("No Playlist")

    # Titre de la playlist sélectionnée
    playlist_title = (
        Text(
            f"  {selected_name} ({len(tracks)} tracks)",
            style="bold bright_cyan",
        )
        if selected_name
        else Text(
            "C to create a playlist",
            style="dim",
        )
    )

    fixed_height = sum(
        _height(item, console)
        for item in (
            head,
            selector,
            playlist_title,
            now,
            lyrics,
            foot,
        )
    )

    # Marge pour les bordures et les en-têtes des tableaux
    visible_tracks = max(
        1,
        console.size.height - fixed_height - 5,
    )

    total_tracks = len(tracks)

    selected_track = (
        max(0, min(selected_track, total_tracks - 1)) if total_tracks else 0
    )

    # Fenêtre glissante centrée sur le morceau sélectionné
    track_start = max(
        0,
        min(
            selected_track - visible_tracks // 2,
            max(0, total_tracks - visible_tracks),
        ),
    )

    visible = tracks[track_start : track_start + visible_tracks]

    track_table = Table(
        show_header=True,
        header_style="bold dim",
        box=box.ROUNDED,
        border_style="grey30",
        expand=True,
        padding=(0, 1),
        title=(
            f"♫ TRACKS ({selected_track + 1}/{total_tracks})"
            if total_tracks
            else "♫ TRACKS (0/0)"
        ),
        title_style=f"bold {theme.accent}",
    )

    track_table.add_column(
        "TRACK",
        ratio=4,
        no_wrap=True,
    )
    track_table.add_column(
        "ARTIST",
        ratio=3,
        no_wrap=True,
    )
    track_table.add_column(
        "ALBUM",
        ratio=3,
        no_wrap=True,
    )

    if not visible:
        track_table.add_row(
            "No Tracks",
            "",
            "",
        )

    for i, track in enumerate(visible):
        real_index = track_start + i

        # Les morceaux sont des dictionnaires
        if isinstance(track, dict):
            track_name = track.get("title") or track.get("name") or "Unknown title"

            artist_name = track.get("artist") or "Unknown Artist"

            album_name = track.get("album") or "Unknown Album"
        else:
            # Compatibilité si un morceau est un objet
            track_name = getattr(track, "title", str(track))
            artist_name = getattr(track, "artist", "Unknown Artist")
            album_name = getattr(track, "album", "Unknown Album")

        # Indicateur du morceau sélectionné
        title_cell = ("▶ " if real_index == selected_track else "") + shorten(
            str(track_name), 45
        )

        artist_cell = shorten(str(artist_name), 30)
        album_cell = shorten(str(album_name), 30)

        track_table.add_row(
            title_cell,
            artist_cell,
            album_cell,
            style=("bold black on cyan" if real_index == selected_track else None),
        )

    layout = Table.grid(expand=True)

    layout.add_row(head)
    layout.add_row(selector)
    layout.add_row(playlist_title)
    layout.add_row(track_table)
    layout.add_row(now)
    layout.add_row(lyrics)
    layout.add_row(foot)

    return layout


def build_nexts_tracks_screen(playlist, mpv, console, theme=None):
    theme = theme or default_theme()
    tracks_to_show = 9

    tracks = playlist.tracks[
        playlist.current_index + 1:
        playlist.current_index + 1 + tracks_to_show
    ]

    layout = Table.grid(expand=True)

    layout.add_row(
        Panel(
            Align.center(Text("Next Tracks", style=f"bold {theme.accent}")),
            border_style=theme.accent,
            box=box.ROUNDED,
            height=3,
        )
    )

    if tracks:
        layout.add_row(
            track_list(
                {"name": "Next Tracks", "tracks": tracks},
                0,
            )
        )
    else:
        layout.add_row(
            Panel(
                Align.center(
                    Text("Aucun morceau à venir", style="dim"),
                    vertical="middle",
                ),
                height=5,
                border_style="grey30",
            )
        )

    return layout


def group_by_artist(library):
    """Retourne [(artiste, [tracks...]), ...] trié alphabétiquement."""
    groups = {}

    for album in library.get("albums", []):
        for track in album.get("tracks", []):
            name = artist_of(track) or "Unknown Artist"
            groups.setdefault(name, []).append(track)

    return sorted(groups.items(), key=lambda item: item[0].casefold())


def build_artists_screen(
    artists,
    selected,
    playlist,
    mpv,
    console,
    theme=None,
):
    theme = theme or default_theme()
    now = mini_player(playlist, mpv, console, theme)
    lyrics = format_lyrics(playlist.current, mpv.get_time_pos(),theme=theme)
    nexts = build_nexts_tracks_screen(playlist, mpv, console, theme)
    foot = footer("artists", console, theme)
    head = header(theme)

    # place restante pour la liste (bordures + en-tête de table = 4)
    used = sum(_height(r, console) for r in (head, now, lyrics, nexts, foot))
    visible = max(3, console.size.height - used - 4)

    total = len(artists)
    selected = max(0, min(selected, total - 1)) if total else 0

    # fenêtre glissante centrée sur la sélection
    start = max(0, min(selected - visible // 2, total - visible))
    window = artists[start : start + visible]

    table = Table(
        show_header=True,
        header_style="bold dim",
        box=box.ROUNDED,
        border_style="grey30",
        title=f"♫ ARTISTS ({selected + 1 if total else 0}/{total})",
        title_style="bold bright_cyan",
        expand=True,
        padding=(0, 1),
    )

    table.add_column("ARTIST", ratio=4, no_wrap=True)
    table.add_column("ALBUMS", width=8, justify="right", no_wrap=True)
    table.add_column("TRACKS", width=8, justify="right", no_wrap=True)

    if not window:
        table.add_row("Aucun artiste", "", "")

    for i, (name, tracks) in enumerate(window):
        real_index = start + i
        albums_count = len({album_of(t) for t in tracks})

        cells = (
            ("▶ " if real_index == selected else "") + shorten(name, 50),
            str(albums_count),
            str(len(tracks)),
        )

        if real_index == selected:
            table.add_row(*cells, style="bold black on cyan")
        else:
            table.add_row(*cells)

    layout = Table.grid(expand=True)
    layout.add_row(head)
    layout.add_row(table)
    layout.add_row(now)
    layout.add_row(lyrics)
    layout.add_row(nexts)
    layout.add_row(foot)

    return layout


def _height(renderable, console):
    return len(console.render_lines(renderable, pad=False))


def build_search_screen(
    search,
    playlist,
    mpv,
    console,
    theme=None,
):
    theme = theme or default_theme()
    now = mini_player(playlist, mpv, console, theme)
    lyrics = format_lyrics(playlist.current, mpv.get_time_pos(), theme=theme)
    nexts = build_nexts_tracks_screen(playlist, mpv, console, theme)
    foot = footer("search_typing" if search.typing else "search", console, theme)

    # on mesure ce qui est sous le panneau pour que la liste de résultats
    # prenne exactement la place restante (6 = bordures, saisie, en-tête, compteur)
    bottom = sum(_height(r, console) for r in (now, lyrics, nexts, foot))
    visible = console.size.height - bottom - 6

    layout = Table.grid(expand=True)

    layout.add_row(search.render(visible))
    layout.add_row(now)
    layout.add_row(lyrics)
    layout.add_row(nexts)
    layout.add_row(foot)

    return layout


def build_queue_screen(playlist, selected, move_mode, mpv, console, theme=None):
    theme = theme or default_theme()
    tracks, current, _ = playlist.snapshot()

    now = mini_player(playlist, mpv, console, theme)
    lyrics = format_lyrics(playlist.current, mpv.get_time_pos(), theme=theme)
    foot = footer("queue", console, theme)
    head = header(theme)

    used = sum(_height(r, console) for r in (head, now, lyrics, foot))
    visible = max(3, console.size.height - used - 6)

    total = len(tracks)
    selected = max(0, min(selected, total - 1)) if total else 0
    start = max(0, min(selected - visible // 2, max(0, total - visible)))
    window = tracks[start : start + visible]

    status = Text()
    status.append("  Repeat: ", style="dim")
    status.append(
        "ON" if playlist.repeat else "OFF",
        style="bold green" if playlist.repeat else "bold red",
    )
    if move_mode:
        status.append("    MOVE MODE (↑↓ to move, M to exit)", style="bold yellow")

    table = Table(
        show_header=True,
        header_style="bold dim",
        box=box.ROUNDED,
        border_style=theme.secondary if move_mode else theme.border,
        title=f"♫ QUEUE ({selected + 1 if total else 0}/{total})",
        title_style="bold bright_cyan",
        expand=True,
        padding=(0, 1),
    )
    table.add_column("#", width=7, justify="right", no_wrap=True)
    table.add_column("TITLE", ratio=4, no_wrap=True)
    table.add_column("ARTIST", ratio=2, no_wrap=True)

    if not window:
        table.add_row("", "Queue is empty", "")

    for i, track in enumerate(window):
        real = start + i
        marker = "♪ " if real == current else ""
        cursor = "▶ " if real == selected else ""
        cells = (
            f"{marker}{real + 1}",
            cursor + shorten(title_of(track), 50),
            shorten(artist_of(track), 30),
        )
        if real == selected:
            table.add_row(*cells, style="bold black on cyan")
        elif real == current:
            table.add_row(*cells, style="bold bright_cyan")
        else:
            table.add_row(*cells)

    layout = Table.grid(expand=True)
    layout.add_row(head)
    layout.add_row(status)
    layout.add_row(table)
    layout.add_row(now)
    layout.add_row(lyrics)
    layout.add_row(foot)
    return layout

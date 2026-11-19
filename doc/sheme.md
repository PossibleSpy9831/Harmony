# Data Schema

This document describes the JSON structures used by the current Harmony implementation.

## Library file

The scanner writes one root object:

```json
{
  "albums": []
}
```

Each album contains:

| Field | Type | Description |
| --- | --- | --- |
| `name` | string | Display name of the album or source folder |
| `folder` | string | Album folder path |
| `cover` | string or null | Cover path, if found or extracted |
| `tracks` | array | Tracks belonging to the album |

Each track may contain:

| Field | Type | Description |
| --- | --- | --- |
| `track` | integer | Track number when available |
| `title` | string | Track title |
| `artist` | string | Track artist |
| `album` | string | Album metadata |
| `path` | string | Path to the audio file |
| `duration` | number | Duration in seconds when available |
| `lyrics` | object or null | Synchronized lyric data when found |

The players use `path` as the stable track identity. Generated library entries should always include a path.

## Synchronized lyrics

The scanner stores lyrics as:

```json
{
  "synced": true,
  "lrc": "[00:04.20]Example line",
  "lines": [
    {
      "timestamp": 4.2,
      "text": "Example line"
    }
  ]
}
```

The terminal player sorts `lines` by `timestamp` and displays the previous, current, and next line based on mpv's current position.

## Terminal player state

The terminal player saves state in `~/.harmony/harmony.sqlite3`. On first startup,
it imports the legacy `.play/player_state.json` file if one exists. The legacy
file is retained as a backup. The SQLite database includes tables for artists,
albums, tracks, playlists, playlist tracks, favorites, history, listening
statistics, settings, and daily album selections; the current library JSON
remains authoritative for the catalog during this migration phase.

The legacy JSON shape is:

```json
{
  "favorites": ["/music/album/track.mp3"],
  "playlists": {
    "Road trip": [
      {
        "key": "/music/album/track.mp3",
        "path": "/music/album/track.mp3",
        "title": "Track title",
        "artist": "Artist",
        "album": "Album name"
      }
    ]
  },
  "history": []
}
```

The terminal player keeps at most 30 history entries. Playlist and history
entries are references rather than full library tracks; they are resolved
against the current library when the application starts. SQLite records a
schema version and preserves ordering for favorites, playlist tracks, and
history. The Home screen also stores its selected album by calendar date in
`daily_albums`; an explicit "next album" action updates the selection for the
rest of that day. Theme name, dynamic-theme toggle, and artwork mode are saved
as JSON values in `settings`.

## Compatibility rules

When changing the schema:

1. Keep `path` stable for existing tracks whenever possible.
2. Treat missing optional metadata as valid input.
3. Keep unknown fields so newer scanners do not break older players.
4. Add a version field before introducing a breaking format change.

# Harmony

Harmony is a local music library prototype. It scans MP3 folders, creates a JSON library, 

The library remains JSON-based. Terminal favorites, playlists, and history are
stored in `~/.harmony/harmony.sqlite3`; 

## Current features

### Library scanning

`app/scannerv2.py` recursively scans a music folder and can:

- find MP3 files while ignoring macOS `._` files;
- read ID3 metadata with Mutagen;
- find a local cover or extract an embedded cover;
- fetch synchronized lyrics from LRCLIB;
- parse LRC timestamps;
- write the resulting library to JSON.


### Playback

The terminal player uses `mpv` through its IPC socket. It supports playback, pause, previous/next track, a progress bar, synchronized lyrics, album navigation, random playback, history, favorites, and playlists.

The terminal player starts on Home, which shows library counts, a daily album
selection persisted in SQLite, recent plays, favorites, and available tracks
to rediscover. Rediscover ranks tracks using the current recent-history list
and favors varied artists and albums. Home supports playing or shuffling the
daily album, opening its track list, playing/queueing Rediscover picks, adding
them to playlists/favorites, refreshing the selection, and resuming playback.
Press `W` on any screen to return Home. Its Rich layout allocates terminal
height for the album feature, Rediscover, listening cards, and a persistent
mini-player.

### Visual experience and Harmony Flow

The terminal UI includes nine persistent named palettes, including a neon
green Matrix palette, and an optional
dynamic palette extracted from local cover art. `I` opens immersive Now
Playing, including a cached, aspect-correct ASCII cover and synchronized
lyrics. `Y` opens theme settings; `D` toggles artwork-driven colors there.

`G` opens Harmony Flow: Classic Shuffle, Smart Shuffle, Discovery, or
Favorites Mix. The generator filters unavailable files, avoids immediate
artist/album repetition when possible, cools down recent tracks in smart mode,
and keeps already queued upcoming tracks in front of generated tracks.

`B` opens listening statistics for today, this week, this month, this year, or
all time. Harmony records play starts and sampled playback time locally in
SQLite; statistics begin accumulating after this version starts playing
tracks and are not inferred from older limited history.

The terminal player's active queue is also saved in SQLite. On the next launch,
it restores the order, current track and position, repeat setting, and whether
playback was paused.


## Installation

Install Python dependencies from the project root:

```bash
python3 -m pip install -r requirements.txt
```

Install mpv separately for the terminal player:

```bash
brew install mpv
```

## Generate a library

Run the scanner from the project root:

```bash
python3 app/tools/scannerv2.py
```


```bash
HARMONY_LIBRARY_PATH=/path/to/library.json python3 app/scannerv2.py
```

## Run the terminal player

```bash
python3 -m app.src.Harmony
```

The terminal player requires a working terminal and `mpv` in `PATH`.

| Key | Action |
| --- | --- |
| `D` / `S` | Play or shuffle the daily album |
| `O` / `N` | Open the daily album or select the next one |
| Arrow keys / `Enter` | Select or play a Rediscover track |
| `A` / `M` / `F` | Queue, add to playlist, or favorite the selected Rediscover track |
| `R` / `P` / `X` | Refresh Rediscover, resume, or shuffle the library |
| `W` | Return Home from another screen |
| `B` | Open listening statistics; use arrows to change the period |
| `G` / `I` / `Y` | Open Harmony Flow, Now Playing, or Themes |
| `V` / `H` / `L` / `/` | Open favorites, history, playlists, or search |
| `C` | Create a playlist |
| Space | Pause or resume |
| `Esc` | Return to albums from a detail screen |
| `Q` | Quit |

Favorites, playlists, and up to 30 recent tracks are saved in
`~/.harmony/harmony.sqlite3`
by the terminal player. Existing JSON state is imported once from
`.play/player_state.json` and is not deleted.

## Library format

The generated JSON has this shape:

```json
{
  "albums": [
    {
      "name": "Album name",
      "folder": "/music/album",
      "cover": "/music/album/cover.jpg",
      "tracks": [
        {
          "track": 1,
          "title": "Track title",
          "artist": "Artist",
          "album": "Album name",
          "path": "/music/album/01-track.mp3",
          "duration": 245,
          "lyrics": {
            "synced": true,
            "lrc": "[00:00.00]Lyrics",
            "lines": [{"timestamp": 0.0, "text": "Lyrics"}]
          }
        }
      ]
    }
  ]
}
```

`duration` and `lyrics` may be absent when the scanner cannot read or retrieve them.

## Project layout

```text
app/
├── core/       # Navigation and event coordination
├── data/       # SQLite database and persistence
├── features/   # Home, artwork, themes, statistics, and shuffle logic
├── scripts/    # Rich UI, mpv integration, playlists, and helpers
├── src/        # Main terminal player
├── tests/      # Unit and integration tests
├── tools/      # Library scanner
└── windows/    # Windows-specific player
```


## Known limitations
- Scanner network requests to LRCLIB can be slow or unavailable and are not cached independently.
- There is no automated test suite yet.

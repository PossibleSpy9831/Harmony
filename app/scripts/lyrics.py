# lyrics.py

import requests
import time
import os
import re


def get_lyrics(artist, title, album=None, duration=None):
    params = {
        "artist_name": artist,
        "track_name": title,
    }

    if album:
        params["album_name"] = album

    if duration:
        params["duration"] = duration

    r = requests.get("https://lrclib.net/api/get", params=params, timeout=10)
    r.raise_for_status()

    data = r.json()
    return data.get("syncedLyrics")


def parse_lrc(lrc):
    lyrics = []

    for line in lrc.splitlines():
        # Récupère [01:23.45]
        match = re.match(r"\[(\d+):(\d+(?:\.\d+)?)\](.*)", line)

        if match:
            minutes = int(match.group(1))
            seconds = float(match.group(2))
            text = match.group(3).strip()

            timestamp = minutes * 60 + seconds
            lyrics.append((timestamp, text))

    return sorted(lyrics)


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def center_text(text):
    width = os.get_terminal_size().columns
    return text.center(width)


def play_lyrics(lyrics):
    start = time.monotonic()

    for timestamp, text in lyrics:
        # Attendre jusqu'au timestamp de cette ligne
        target = start + timestamp
        delay = target - time.monotonic()

        if delay > 0:
            time.sleep(delay)

        clear_screen()

        print()
        print()
        print(center_text(text))
        print()


# -------------------------
# Configuration
# -------------------------

artist = "Daft Punk"
title = "Get Lucky"
album = "Random Access Memories"
duration = 248


# -------------------------
# Récupération
# -------------------------

print("Recherche des paroles...")

lrc = get_lyrics(artist=artist, title=title, album=album, duration=duration)

if not lrc:
    print("No lyrics found")
    exit()

lyrics = parse_lrc(lrc)

print(f"{len(lyrics)} lines found")
print("Starting in 3 secs...")

time.sleep(3)

play_lyrics(lyrics)

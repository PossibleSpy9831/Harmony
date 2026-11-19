import random
import threading
from app.scripts.helpers import album_name, log


class Playlist:

    def __init__(self):
        self.tracks = []
        self.index = 0
        self.name = "Queue"
        self.lock = threading.Lock()
        self.current_index = 0
        self.repeat = True  # True = same behaviour as before (wraps around)

    @property
    def current(self):
        with self.lock:
            if not self.tracks:
                return None
            return self.tracks[self.index]

    @staticmethod
    def prepare_track(track, album=None):
        prepared = dict(track)
        if album is not None:
            prepared["_album"] = album_name(album)
            prepared["_cover"] = album.get("cover")
            prepared["_year"] = album.get("year") or album.get("date")
        return prepared

    def has_tracks(self):
        with self.lock:
            return bool(self.tracks)

    def set(self, tracks, name="Queue", index=0):
        with self.lock:
            self.tracks = list(tracks)
            if self.tracks:
                self.index = max(0, min(int(index), len(self.tracks) - 1))
            else:
                self.index = 0
            self.name = name
            self.current_index = self.index

    def clear(self):
        with self.lock:
            self.tracks = []
            self.index = 0
            self.current_index = 0
            self.name = "Queue"

    def next(self):
        """Returns the next track, or None if the end is reached and repeat is off."""
        with self.lock:
            if not self.tracks:
                return None

            if self.index + 1 >= len(self.tracks):
                if not self.repeat:
                    return None
                self.index = 0
            else:
                self.index += 1

            self.current_index = self.index
            return self.tracks[self.index]

    def previous(self):
        with self.lock:
            if not self.tracks:
                return None
            self.index = (self.index - 1) % len(self.tracks)
            self.current_index = self.index
            return self.tracks[self.index]

    def snapshot(self):
        with self.lock:
            return list(self.tracks), self.index, self.name

    def randomize(self, track_index):
        with self.lock:
            if not self.tracks:
                self.index = 0
                self.current_index = 0
                return

            track_index = max(0, min(track_index, len(self.tracks) - 1))
            selected_track = self.tracks[track_index]
            rest_tracks = self.tracks[:track_index] + self.tracks[track_index + 1 :]
            random.shuffle(rest_tracks)

            self.tracks = [selected_track] + rest_tracks
            self.index = 0
            self.current_index = 0

    def add_next(self, track, album=None):
        track = self.prepare_track(track, album)
        with self.lock:
            if not self.tracks:
                self.tracks.append(track)
                self.index = 0
                self.current_index = 0
            else:
                self.tracks.insert(self.index + 1, track)
                self.current_index = self.index

    def add_last(self, track, album=None):
        track = self.prepare_track(track, album)
        with self.lock:
            self.tracks.append(track)
            if len(self.tracks) == 1:
                self.index = 0
                self.current_index = 0

    # ------------------------------------------------------------------
    # SMART QUEUE
    # ------------------------------------------------------------------

    def remove(self, i):
        """Removes track i. Returns True if the CURRENT track was removed
        (the caller must then start the track that slid into its place)."""
        with self.lock:
            if not 0 <= i < len(self.tracks):
                return False

            del self.tracks[i]

            if not self.tracks:
                self.index = 0
                self.current_index = 0
                return True

            removed_current = False

            if i < self.index:
                self.index -= 1
            elif i == self.index:
                removed_current = True
                self.index = min(self.index, len(self.tracks) - 1)

            self.current_index = self.index
            return removed_current

    def move(self, i, delta):
        """Déplace un morceau À VENIR de delta. Le morceau en cours et les
        morceaux déjà joués sont verrouillés. Retourne la nouvelle position."""
        with self.lock:
            j = i + delta

            if i <= self.index or j <= self.index or j >= len(self.tracks):
                return i

            self.tracks[i], self.tracks[j] = self.tracks[j], self.tracks[i]
            return j

    def clear_upcoming(self):
        """Empties the queue but keeps the track currently playing."""
        with self.lock:
            if not self.tracks:
                return
            self.tracks = [self.tracks[self.index]]
            self.index = 0
            self.current_index = 0

    def toggle_repeat(self):
        with self.lock:
            self.repeat = not self.repeat
            return self.repeat

    def shuffle_upcoming(self):
        """Shuffles only the tracks after the current one: the current track
        and the already-played ones stay where they are."""
        with self.lock:
            upcoming = self.tracks[self.index + 1 :]
            random.shuffle(upcoming)
            self.tracks[self.index + 1 :] = upcoming

    def jump(self, i):
        """Makes track i the current one and returns it."""
        with self.lock:
            if not 0 <= i < len(self.tracks):
                return None
            self.index = i
            self.current_index = i
            return self.tracks[i]

    def promote(self, i):
        """Place le morceau i tout en haut de la file (position 0)
        et le rend courant. Retourne le morceau, ou None si i est invalide."""
        with self.lock:
            if not 0 <= i < len(self.tracks):
                return None

            track = self.tracks.pop(i)
            self.tracks.insert(0, track)
            self.index = 0
            self.current_index = 0

            return track

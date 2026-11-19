import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from rich import box
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

FIELDS = ("title", "artist", "album", "lyrics")

WEIGHTS = {"title": 10, "artist": 6, "album": 4, "lyrics": 1}

ALIASES = {
    "title": "title",
    "titre": "title",
    "artist": "artist",
    "artiste": "artist",
    "album": "album",
    "lyrics": "lyrics",
    "paroles": "lyrics",
}

TERM_RE = re.compile(r'(?:(\w+):)?(?:"([^"]+)"|(\S+))')
LRC_TS_RE = re.compile(r"\[\d+:\d+(?:\.\d+)?\]")

# En dessous de cette longueur, un terme n'est pas cherché dans les paroles
# (sauf filtre explicite paroles:xx) : évite du bruit et du CPU en live.
MIN_LYRICS_LEN = 3

BACKSPACE_KEYS = ("\x7f", "\b", "BACKSPACE")
CLEAR_KEYS = ("\x15",)  # Ctrl+U


def normalize(text):
    """minuscules, sans accents, ponctuation -> espaces."""
    text = unicodedata.normalize("NFKD", str(text or "").lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class SearchResult:
    track: dict
    album: dict
    score: int
    matched: set
    lyric_line: Optional[tuple] = None  # (timestamp, texte)


class _Entry:
    __slots__ = ("track", "album", "texts", "lyric_lines")

    def __init__(self, track, album, texts, lyric_lines):
        self.track = track
        self.album = album
        self.texts = texts
        self.lyric_lines = lyric_lines


class Search:
    def __init__(self, library=None, limit=200):
        self.entries = []
        self.limit = limit

        # état de l'écran
        self.query = ""
        self.results = []
        self.index = 0
        self.typing = True
        self._dirty = False

        if library:
            self.build(library)

    # INDEX

    def build(self, library):
        self.entries = []

        for album in library.get("albums", []):
            album_title = album.get("name") or "Unknown Album"

            for raw in album.get("tracks", []):
                # même forme que helpers.tracks_from_album()
                track = dict(raw)
                track["_album"] = album_title
                track["_cover"] = album.get("cover")

                lines = self._lyric_lines(raw.get("lyrics"))

                texts = {
                    "title": normalize(
                        raw.get("title") or Path(raw.get("path", "")).stem
                    ),
                    "artist": normalize(raw.get("artist")),
                    "album": normalize(album_title),
                    "lyrics": " ".join(n for _, _, n in lines),
                }

                self.entries.append(_Entry(track, album, texts, lines))

        return len(self.entries)

    @staticmethod
    def _lyric_lines(lyrics):
        """-> [(timestamp, texte, texte_normalisé)]"""
        if not isinstance(lyrics, dict):
            return []

        result = []

        if lyrics.get("lines"):
            for line in lyrics["lines"]:
                text = line.get("text", "")
                if text.strip():
                    result.append((line.get("timestamp"), text, normalize(text)))
        elif lyrics.get("lrc"):
            for raw in lyrics["lrc"].splitlines():
                text = LRC_TS_RE.sub("", raw).strip()
                if text:
                    result.append((None, text, normalize(text)))

        return result

    # MOTEUR

    @staticmethod
    def parse(query):
        """'artist:daft "one more" titre:time' -> [(champ|None, terme)]"""
        terms = []

        for field, phrase, word in TERM_RE.findall(query or ""):
            value = normalize(phrase or word)
            if not value:
                continue

            target = ALIASES.get(field.lower()) if field else None

            # champ inconnu ("foo:bar") : on cherche le texte tel quel
            if field and target is None:
                value = normalize(f"{field} {value}")

            terms.append((target, value))

        return terms

    @staticmethod
    def _level(text, term):
        if not text:
            return 0
        if text == term:
            return 3  # égalité exacte
        if re.search(r"\b" + re.escape(term), text):
            return 2  # début de mot
        if term in text:
            return 1  # sous-chaîne
        return 0

    def search(self, query, limit=50, in_lyrics=True):
        terms = self.parse(query)

        if not terms:
            return []

        results = []

        for entry in self.entries:
            score = 0
            matched = set()

            for target, term in terms:
                best, best_field = 0, None

                for field in (target,) if target else FIELDS:
                    if field == "lyrics":
                        if not in_lyrics:
                            continue
                        if not target and len(term) < MIN_LYRICS_LEN:
                            continue

                    value = self._level(entry.texts[field], term) * WEIGHTS[field]

                    if value > best:
                        best, best_field = value, field

                if not best:
                    break  # AND : chaque terme doit matcher quelque part

                score += best
                matched.add(best_field)
            else:
                lyric_line = None

                if "lyrics" in matched:
                    lyric_line = self._find_lyric_line(entry, terms)

                results.append(
                    SearchResult(
                        entry.track,
                        entry.album,
                        score,
                        matched,
                        lyric_line,
                    )
                )

        results.sort(
            key=lambda r: (-r.score, normalize(r.track.get("title", ""))),
        )

        return results[:limit] if limit else results

    @staticmethod
    def _find_lyric_line(entry, terms):
        wanted = [t for f, t in terms if f in (None, "lyrics")]

        best = None

        for timestamp, text, norm in entry.lyric_lines:
            hits = sum(1 for t in wanted if t in norm)

            if hits == len(wanted):
                return (timestamp, text)  # ligne contenant tous les termes

            if hits and best is None:
                best = (timestamp, text)

        return best

    def tracks(self, query, **kwargs):
        """Juste les pistes, directement utilisables par playlist.set()."""
        return [r.track for r in self.search(query, **kwargs)]

    # ÉTAT / ENTRÉES CLAVIER

    def open(self, reset=True):
        """Entre dans l'écran de recherche en mode saisie."""
        if reset:
            self.query = ""
            self.results = []
            self.index = 0
            self._dirty = False

        self.typing = True

    def _set_query(self, query):
        self.query = query
        self.index = 0
        self._dirty = True  # recalcul groupé dans render()

    def _refresh(self):
        self._dirty = False

        if self.query.strip():
            self.results = self.search(self.query, limit=self.limit)
        else:
            self.results = []

        self.index = min(self.index, max(0, len(self.results) - 1))

    def move(self, delta):
        if self.results:
            self.index = max(0, min(len(self.results) - 1, self.index + delta))

    @property
    def selected(self):
        return self.results[self.index].track if self.results else None

    @property
    def selected_album(self):
        return self.results[self.index].album if self.results else None

    def result_tracks(self):
        return [r.track for r in self.results]

    def handle_key(self, key):
        """
        Traite une touche. Retourne une action pour playv2.py, ou None :
        "exit", "queue_next", "queue_last", "favorite", "playlist",
        "next", "pause"
        """
        if self.typing:
            return self._handle_typing(key)

        return self._handle_browse(key)

    def _handle_typing(self, key):
        if key == "ESC":
            if not self.query:
                return "exit"

            self.typing = False
            return None

        if key in ("\r", "\n"):
            self._ensure_fresh()
            return "queue_next" if self.results else None

        if key == "UP":
            self._ensure_fresh()
            self.move(-1)

        elif key == "DOWN":
            self._ensure_fresh()
            self.move(1)

        elif key in BACKSPACE_KEYS:
            if self.query:
                self._set_query(self.query[:-1])

        elif key in CLEAR_KEYS:
            self._set_query("")

        elif isinstance(key, str) and len(key) == 1 and key.isprintable():
            self._set_query(self.query + key)

        return None

    def _handle_browse(self, key):
        self._ensure_fresh()

        if key == "ESC":
            return "exit"

        if key == "UP":
            self.move(-1)

        elif key == "DOWN":
            self.move(1)

        elif key in ("\r", "\n"):
            return "queue_next" if self.results else None

        elif key in ("p", "P"):
            return "queue_last" if self.results else None

        elif key in ("f", "F"):
            return "favorite" if self.results else None

        elif key in ("a", "A"):
            return "playlist" if self.results else None

        elif key in ("n", "N"):
            return "next"

        elif key == " ":
            return "pause"

        return None

    def _ensure_fresh(self):
        if self._dirty:
            self._refresh()

    # RENDU

    @staticmethod
    def _fmt_ts(seconds):
        if seconds is None:
            return ""
        minutes, secs = divmod(int(seconds), 60)
        return f"{minutes}:{secs:02d}"

    def render(self, visible):
        """Panneau de recherche seul. `visible` = nombre de lignes de résultats."""
        self._ensure_fresh()

        total = len(self.results)
        visible = max(3, visible)

        title = Text()
        title.append("[", style="#4d774d")
        title.append("HARMONY SEARCH", style="#65ff65")
        title.append("]", style="#4d774d")

        # ligne de saisie
        prompt = Text()
        prompt.append("› ", style="#39ff14")
        prompt.append(self.query, style="bold #b6ffb6")
        if self.typing:
            prompt.append("▌", style="#39ff14")
        elif not self.query:
            prompt.append("(vide)", style="#4d774d")

        if not total:
            if self.query.strip():
                body = Text(f'Aucun résultat pour "{self.query}".', style="yellow")
            else:
                body = Text(
                    "Exemples :  daft punk  ·  artist:daft titre:one  ·  "
                    'paroles:"i wanna dance"',
                    style="#4d774d",
                )

            return Panel(
                Group(prompt, Text(""), body),
                title=title,
                border_style="#237a32",
                box=box.ROUNDED,
                padding=(0, 1),
            )

        start = max(0, min(self.index - visible // 2, total - visible))
        end = min(total, start + visible)

        table = Table(
            box=box.SIMPLE_HEAD,
            expand=True,
            header_style="bold #65ff65",
            border_style="#245c2b",
            pad_edge=False,
        )
        table.add_column("#", justify="right", width=4, style="#4d774d")
        table.add_column("Titre", ratio=3, no_wrap=True, overflow="ellipsis")
        table.add_column("Artiste", ratio=2, no_wrap=True, overflow="ellipsis")
        table.add_column("Album", ratio=2, no_wrap=True, overflow="ellipsis")
        table.add_column("Match", ratio=4, no_wrap=True, overflow="ellipsis")

        for i in range(start, end):
            result = self.results[i]
            track = result.track

            if result.lyric_line:
                timestamp, text = result.lyric_line
                match = f"♪ {self._fmt_ts(timestamp)} {text}".replace("  ", " ")
            else:
                match = ", ".join(sorted(result.matched))

            table.add_row(
                str(i + 1),
                track.get("title") or Path(track.get("path", "")).stem,
                track.get("artist") or "Unknown Artist",
                track.get("_album") or track.get("album") or "",
                match,
                style="bold black on #39ff14" if i == self.index else "#b6ffb6",
            )

        counter = Text(f"{self.index + 1}/{total}", style="#4d774d")

        return Panel(
            Group(prompt, table, counter),
            title=title,
            border_style="#237a32",
            box=box.ROUNDED,
            padding=(0, 1),
        )
    

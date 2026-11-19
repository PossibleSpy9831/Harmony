"""
Scanner musical avec récupération des paroles synchronisées.

Version incrémentale :

- Scanne récursivement un dossier musical
- Lit les métadonnées MP3
- Cherche/extrait les pochettes
- Récupère les paroles synchronisées depuis LRCLIB
- Parse les timestamps LRC
- Génère library.json
- Réutilise les données déjà présentes dans library.json
- Ne recalcule que les nouveaux fichiers ou fichiers modifiés
- Supprime du JSON les fichiers qui n'existent plus

Gestion des paroles (corrigée) :

- 429 / erreurs réseau : nouvelle tentative avec attente (Retry-After)
- 404 sur /api/get : repli sur /api/search
- Statut "lyrics_status" stocké par piste :
    "ok"         -> paroles trouvées
    "not_found"  -> confirmé sans paroles synchronisées (pas retenté)
    "error"      -> erreur temporaire (retenté au prochain scan)
"""

from pathlib import Path

import json
import re
import time

import requests
from mutagen.id3 import ID3
from mutagen.mp3 import MP3

from rich.progress import track
from rich.console import Console
from rich.panel import Panel

try:
    from ..data.database import Database
    from ..scripts.library_config import (
        DATABASE_PATH,
        LEGACY_DATABASE_PATH,
        LIBRARY_PATH,
        ensure_library_parent,
    )
except ImportError:
    from app.data.database import Database
    from app.scripts.library_config import (
        DATABASE_PATH,
        LEGACY_DATABASE_PATH,
        LIBRARY_PATH,
        ensure_library_parent,
    )

console = Console()


# ============================================================
# CONFIGURATION
# ============================================================

LIBRARY_FILENAME = "library.json"

LRCLIB_GET_URL = "https://lrclib.net/api/get"
LRCLIB_SEARCH_URL = "https://lrclib.net/api/search"

# LRCLIB recommande un User-Agent explicite : adapte-le à ton projet.
USER_AGENT = "HarmonyScanner/1.0 (https://github.com/ton-compte/ton-projet)"

# Délai minimal entre deux requêtes réseau vers LRCLIB (secondes)
MIN_REQUEST_INTERVAL = 1.0

# Nombre de tentatives par requête (429, 5xx, erreurs réseau)
MAX_RETRIES = 4

# Écart de durée maximal accepté pour un résultat /api/search (secondes)
MAX_DURATION_DIFF = 5

# Statuts des paroles
LYRICS_OK = "ok"
LYRICS_NOT_FOUND = "not_found"
LYRICS_ERROR = "error"


# ============================================================
# ECRAN D'ACCUEIL
# ============================================================


class Scanner:
    def __init__(self):
        self.console = Console()
        self.LIBRARY_FILENAME = "library.json"
        self._last_request = 0.0

    def welcome_screen(self):
        self.console.print(
            Panel(
                r"""
                            ██╗  ██╗ █████╗ ██████╗ ███╗   ███╗ ██████╗ ███╗   ██╗██╗   ██╗    ███████╗ ██████╗ █████╗ ███╗   ██╗███╗   ██╗███████╗██████╗ 
                            ██║  ██║██╔══██╗██╔══██╗████╗ ████║██╔═══██╗████╗  ██║╚██╗ ██╔╝    ██╔════╝██╔════╝██╔══██╗████╗  ██║████╗  ██║██╔════╝██╔══██╗
                            ███████║███████║██████╔╝██╔████╔██║██║   ██║██╔██╗ ██║ ╚████╔╝     ███████╗██║     ███████║██╔██╗ ██║██╔██╗ ██║█████╗  ██████╔╝
                            ██╔══██║██╔══██║██╔══██╗██║╚██╔╝██║██║   ██║██║╚██╗██║  ╚██╔╝      ╚════██║██║     ██╔══██║██║╚██╗██║██║╚██╗██║██╔══╝  ██╔══██╗
                            ██║  ██║██║  ██║██║  ██║██║ ╚═╝ ██║╚██████╔╝██║ ╚████║   ██║       ███████║╚██████╗██║  ██║██║ ╚████║██║ ╚████║███████╗██║  ██║
                            ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝     ╚═╝ ╚═════╝╚═╝  ╚═══╝   ╚═╝       ╚══════╝╚═════╝╚═╝  ╚═╝╚═╝  ╚═══╝╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝
                """,
                title="PLAY",
                border_style="bright_green",
            )
        )

        time.sleep(0.1)

    # ============================================================
    # RESEAU LRCLIB
    # ============================================================

    def _throttle(self):
        """
        Garantit un délai minimal entre deux requêtes réseau.
        """

        elapsed = time.monotonic() - self._last_request

        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)

        self._last_request = time.monotonic()

    @staticmethod
    def _retry_delay(response, attempt):
        """
        Délai d'attente avant une nouvelle tentative.

        Respecte l'en-tête Retry-After s'il est présent,
        sinon backoff exponentiel (2, 4, 8, 16 s... plafonné à 30 s).
        """

        retry_after = response.headers.get("Retry-After") if response else None

        if retry_after:
            try:
                return min(float(retry_after), 60)
            except ValueError:
                pass

        return min(2 ** (attempt + 1), 30)

    def _request(self, url, params):
        """
        Requête GET vers LRCLIB avec gestion des erreurs.

        Retourne un tuple (statut, données) :

            ("ok", json)          -> succès
            ("not_found", None)   -> 404
            ("error", None)       -> échec temporaire après les tentatives
        """

        headers = {"User-Agent": USER_AGENT}

        for attempt in range(MAX_RETRIES):

            self._throttle()

            try:
                response = requests.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=10,
                )

            except requests.RequestException as e:
                delay = min(2 ** (attempt + 1), 30)
                print(f"  Network error({e}), next try in {delay:.0f}s")
                time.sleep(delay)
                continue

            if response.status_code == 404:
                return "not_found", None

            if response.status_code == 429 or response.status_code >= 500:
                delay = self._retry_delay(response, attempt)
                print(
                    f"    HTTP {response.status_code}, "
                    f"next try in  {delay:.0f}s "
                    f"({attempt + 1}/{MAX_RETRIES})"
                )
                time.sleep(delay)
                continue

            try:
                response.raise_for_status()
                return "ok", response.json()

            except (requests.RequestException, ValueError):
                return "error", None

        return "error", None

    # ============================================================
    # PAROLES
    # ============================================================

    def get_lyrics(self, artist, title, album=None, duration=None):
        """
        Récupère les paroles synchronisées depuis LRCLIB.

        Retourne un tuple (lrc, statut) :

            (str,  "ok")         -> paroles trouvées
            (None, "not_found")  -> confirmé sans paroles synchronisées
            (None, "error")      -> erreur temporaire, à retenter plus tard
        """

        if not artist or not title:
            return None, LYRICS_NOT_FOUND

        # --- 1. Recherche exacte -------------------------------

        params = {
            "artist_name": artist,
            "track_name": title,
        }

        if album:
            params["album_name"] = album

        if duration:
            params["duration"] = duration

        status, data = self._request(LRCLIB_GET_URL, params)

        if status == "error":
            return None, LYRICS_ERROR

        if status == "ok" and isinstance(data, dict):

            lrc = data.get("syncedLyrics")

            if lrc:
                return lrc, LYRICS_OK

        # --- 2. Repli : recherche plus souple ------------------
        # (404, ou piste trouvée mais sans syncedLyrics)

        status, results = self._request(
            LRCLIB_SEARCH_URL,
            {
                "artist_name": artist,
                "track_name": title,
            },
        )

        if status == "error":
            return None, LYRICS_ERROR

        if status == "ok" and isinstance(results, list):

            candidates = [
                r for r in results if isinstance(r, dict) and r.get("syncedLyrics")
            ]

            if duration:
                candidates = [
                    r
                    for r in candidates
                    if r.get("duration") is not None
                    and abs(r["duration"] - duration) <= MAX_DURATION_DIFF
                ]

                candidates.sort(key=lambda r: abs(r["duration"] - duration))

            if candidates:
                return candidates[0]["syncedLyrics"], LYRICS_OK

        return None, LYRICS_NOT_FOUND

    def parse_lrc(self, lrc):
        """
        Transforme un texte LRC en liste de lignes.
        """

        lyrics = []

        if not lrc:
            return lyrics

        for line in lrc.splitlines():

            match = re.match(
                r"\[(\d+):(\d+(?:\.\d+)?)\](.*)",
                line,
            )

            if not match:
                continue

            minutes = int(match.group(1))
            seconds = float(match.group(2))
            text = match.group(3).strip()

            timestamp = minutes * 60 + seconds

            lyrics.append(
                {
                    "timestamp": timestamp,
                    "text": text,
                }
            )

        return sorted(
            lyrics,
            key=lambda x: x["timestamp"],
        )

    def get_track_lyrics(self, mp3_path, metadata):
        """
        Récupère les paroles synchronisées d'un MP3.

        Retourne un tuple (lyrics, statut).
        """

        artist = metadata.get("artist")
        title = metadata.get("title")
        album = metadata.get("album")

        if not artist or not title:
            return None, LYRICS_NOT_FOUND

        duration = None

        try:
            audio = MP3(mp3_path)
            duration = round(audio.info.length)

        except Exception:
            pass

        print(f"  lyrics search  : {artist} - {title}")

        lrc, status = self.get_lyrics(
            artist=artist,
            title=title,
            album=album,
            duration=duration,
        )

        if status == LYRICS_ERROR:
            print("    -> Temporary Error (will be try again on next scan)")
            return None, LYRICS_ERROR

        if not lrc:
            print("    -> No lyric sychronized")
            return None, LYRICS_NOT_FOUND

        parsed_lyrics = self.parse_lrc(lrc)

        if not parsed_lyrics:
            print("    -> LRC found but unable to parse")
            return None, LYRICS_NOT_FOUND

        print(f"    -> {len(parsed_lyrics)} lines found")

        return (
            {
                "synced": True,
                "lrc": lrc,
                "lines": parsed_lyrics,
            },
            LYRICS_OK,
        )

    def needs_lyrics_retry(self, track_data):
        """
        Indique si les paroles d'une piste déjà en cache
        doivent être recherchées à nouveau.

        - lyrics présent                      -> non
        - statut "not_found" confirmé         -> non
        - statut "error"                      -> oui
        - ancien JSON sans statut, lyrics None -> oui (probablement
          un ancien échec 429 enregistré comme "pas de paroles")
        """

        if track_data.get("lyrics") is not None:
            return False

        return track_data.get("lyrics_status") != LYRICS_NOT_FOUND

    def refresh_lyrics(self, track_data, mp3_path):
        """
        Relance uniquement la recherche de paroles d'une piste en cache.
        """

        lyrics, status = self.get_track_lyrics(mp3_path, track_data)

        track_data["lyrics"] = lyrics
        track_data["lyrics_status"] = status

        return track_data

    # ============================================================
    # METADATA
    # ============================================================

    def extract_metadata(self, mp3_path):
        """
        Extrait les métadonnées principales d'un MP3.
        """

        try:

            audio = MP3(
                mp3_path,
                ID3=ID3,
            )

            tags = audio.tags

            if tags is None:
                return {
                    "track": None,
                    "title": None,
                    "artist": None,
                    "album": None,
                }

            title = self.get_tag(tags, "TIT2")
            artist = self.get_tag(tags, "TPE1")
            album = self.get_tag(tags, "TALB")
            track = self.get_tag(tags, "TRCK")

            if track:

                try:
                    track = int(track.split("/")[0])

                except (
                    ValueError,
                    AttributeError,
                ):
                    track = None

            return {
                "track": track,
                "title": title,
                "artist": artist,
                "album": album,
            }

        except Exception:

            return {
                "track": None,
                "title": None,
                "artist": None,
                "album": None,
            }

    def get_tag(self, tags, key):
        """
        Récupère proprement une valeur ID3.
        """

        value = tags.get(key)

        if value is None:
            return None

        if hasattr(value, "text"):

            if value.text:
                return str(value.text[0])

            return None

        return str(value)

    # ============================================================
    # POCHETTES
    # ============================================================

    def find_cover(self, folder):
        """
        Cherche une pochette déjà présente dans le dossier.
        """

        image_extensions = {
            ".jpg",
            ".jpeg",
            ".png",
        }

        preferred_names = [
            "cover.jpg",
            "cover.jpeg",
            "cover.png",
            "folder.jpg",
            "folder.jpeg",
            "folder.png",
            "album.jpg",
            "album.jpeg",
            "album.png",
            "front.jpg",
            "front.jpeg",
            "front.png",
        ]

        for name in preferred_names:

            path = folder / name

            if path.is_file():
                return f"/{path}"

        for path in folder.iterdir():

            if (
                path.is_file()
                and path.suffix.lower() in image_extensions
                and not path.name.startswith("._")
            ):
                return f"/{path}"

        return None

    def extract_cover(self, mp3_path, folder):
        """
        Extrait la pochette embarquée dans le MP3.
        """

        try:
            tags = ID3(mp3_path)

        except Exception:
            return None

        covers = tags.getall("APIC")

        if not covers:
            return None

        cover = next(
            (c for c in covers if c.type == 3),
            covers[0],
        )

        mime = cover.mime.lower()

        if mime == "image/png":
            extension = ".png"
        else:
            extension = ".jpg"

        output = folder / f"cover{extension}"

        if output.exists():

            i = 1

            while True:

                output = folder / f"cover_{i}{extension}"

                if not output.exists():
                    break

                i += 1

        try:

            output.write_bytes(cover.data)

            return output

        except Exception:
            return None

    # ============================================================
    # OUTILS INCREMENTAUX
    # ============================================================

    def load_existing_library(self, output_path):
        """
        Charge library.json s'il existe.

        Retourne :
            {"albums": [...]}

        ou une bibliothèque vide.
        """

        if not output_path.is_file():
            return {"albums": []}

        try:

            with open(
                output_path,
                "r",
                encoding="utf-8",
            ) as f:

                library = json.load(f)

            if not isinstance(library, dict):
                return {"albums": []}

            if not isinstance(
                library.get("albums"),
                list,
            ):
                return {"albums": []}

            return library

        except (
            json.JSONDecodeError,
            OSError,
            TypeError,
        ):

            print("WARM : unable to read lib new full scan")

            return {"albums": []}

    def get_file_key(self, mp3_path):
        """
        Identifiant stable d'un fichier dans le JSON.

        On utilise le chemin absolu normalisé.
        """

        return str(mp3_path.resolve())

    def get_cached_tracks(self, album):
        """
        Transforme les tracks existants en dictionnaire :

            chemin -> track

        afin de pouvoir retrouver rapidement
        les anciens morceaux.
        """

        result = {}

        for track in album.get("tracks", []):

            path = track.get("path")

            if not path:
                continue

            try:
                key = self.get_file_key(Path(path))

            except Exception:
                continue

            result[key] = track

        return result

    def get_track_mtime(self, mp3_path):
        """
        Retourne la date de modification du fichier.
        """

        try:
            return mp3_path.stat().st_mtime_ns

        except OSError:
            return None

    def get_track_size(self, mp3_path):
        """
        Retourne la taille du fichier en octets.
        """

        try:
            return mp3_path.stat().st_size

        except OSError:
            return None

    def is_track_unchanged(self, old_track, mp3_path):
        """
        Garde-fou : décide si un MP3 déjà connu doit être réanalysé.

        La date de modification n'est PAS un critère fiable (copie,
        resynchronisation, disque externe/réseau, ancien JSON sans
        _mtime...). Un fichier n'est donc considéré comme modifié que
        si sa taille a changé par rapport à celle enregistrée.

        - ancien JSON sans _size -> considéré inchangé, empreinte complétée
        - taille identique       -> inchangé, même si la date a bougé

        Si le fichier est jugé inchangé, son empreinte (_mtime / _size)
        est mise à jour dans old_track.
        """

        current_size = self.get_track_size(mp3_path)
        old_size = old_track.get("_size")

        if (
            old_size is not None
            and current_size is not None
            and old_size != current_size
        ):
            return False

        old_track["_mtime"] = self.get_track_mtime(mp3_path)
        old_track["_size"] = current_size

        return True

    # ============================================================
    # CREATION / MISE A JOUR D'UNE TRACK
    # ============================================================

    def build_track(self, mp3_path):
        """
        Analyse complètement un nouveau fichier MP3.
        """

        metadata = self.extract_metadata(mp3_path)

        duration = None

        try:

            audio = MP3(mp3_path)
            duration = round(audio.info.length)

        except Exception:
            pass

        lyrics, lyrics_status = self.get_track_lyrics(
            mp3_path,
            metadata,
        )

        track_data = {
            "track": metadata["track"],
            "title": metadata["title"],
            "artist": metadata["artist"],
            "album": metadata["album"],
            "path": self.get_file_key(mp3_path),
            "duration": duration,
            "lyrics": lyrics,
            # ok / not_found / error : permet de retenter les erreurs
            "lyrics_status": lyrics_status,
            # Permet de savoir si le MP3 a changé
            "_mtime": self.get_track_mtime(mp3_path),
            "_size": self.get_track_size(mp3_path),
        }

        return track_data

    # ============================================================
    # SCANNER MUSICAL INCREMENTAL
    # ============================================================

    def scan_music_folder(self, root_folder):
        """
        Scanner incrémental.

        Pour chaque dossier contenant des MP3 :

        - les anciens morceaux existants sont conservés
        - les morceaux supprimés sont retirés
        - les nouveaux morceaux sont analysés
        - les morceaux modifiés sont réanalysés
        - les paroles déjà trouvées (ou confirmées absentes) ne sont
          PAS recherchées à nouveau
        - les paroles qui ont échoué (429, erreur réseau) sont retentées
        """

        root = Path(root_folder)

        if not root.is_dir():
            raise ValueError(f"{root} n'est pas un dossier")

        output_path = LIBRARY_PATH

        # --------------------------------------------------------
        # 1. Charger l'ancien JSON
        # --------------------------------------------------------

        old_library = self.load_existing_library(output_path)

        old_albums = {
            str(Path(album["folder"]).resolve()): album
            for album in old_library.get("albums", [])
            if album.get("folder")
        }

        albums = []

        stats = {
            "albums_new": 0,
            "tracks_new": 0,
            "tracks_updated": 0,
            "tracks_cached": 0,
            "tracks_removed": 0,
            "lyrics_retried": 0,
            "lyrics_errors": 0,
        }

        # --------------------------------------------------------
        # 2. Parcours des dossiers
        # --------------------------------------------------------

        folders = [
            root,
            *root.rglob("*"),
        ]

        for folder in track(
            folders,
            description="Scanning music folders...",
            transient=True,
        ):

            if not folder.is_dir():
                continue

            mp3_files = sorted(
                [
                    f
                    for f in folder.iterdir()
                    if (
                        f.is_file()
                        and f.suffix.lower() == ".mp3"
                        and not f.name.startswith("._")
                    )
                ]
            )

            if not mp3_files:
                continue

            print()
            print("=" * 60)
            print(f"folder : {folder}")
            print("=" * 60)

            folder_key = str(folder.resolve())

            old_album = old_albums.get(folder_key)

            # ----------------------------------------------------
            # 3. Pochette
            # ----------------------------------------------------

            cover = self.find_cover(folder)

            if cover is None:

                # On ne tente l'extraction que si nécessaire
                old_cover = old_album.get("cover") if old_album else None

                if old_cover:
                    cover = old_cover

                else:
                    cover = self.extract_cover(
                        mp3_files[0],
                        folder,
                    )

            # ----------------------------------------------------
            # 4. Anciennes tracks
            # ----------------------------------------------------

            old_tracks = {}

            if old_album:
                old_tracks = self.get_cached_tracks(old_album)

            tracks = []

            current_keys = set()

            # ----------------------------------------------------
            # 5. Traiter chaque MP3
            # ----------------------------------------------------

            for mp3_path in mp3_files:

                key = self.get_file_key(mp3_path)

                current_keys.add(key)

                old_track = old_tracks.get(key)

                unchanged = old_track is not None and self.is_track_unchanged(
                    old_track, mp3_path
                )

                # ----------------------------------------------
                # MORCEAU INCHANGE, PAROLES DEJA TRAITEES
                # ----------------------------------------------

                if unchanged and not self.needs_lyrics_retry(old_track):

                    print(f"  ✓ Kept : {mp3_path.name}")

                    track_data = old_track

                    stats["tracks_cached"] += 1

                # ----------------------------------------------
                # MORCEAU INCHANGE, MAIS PAROLES A RETENTER
                # (ancien échec 429 / erreur réseau)
                # ----------------------------------------------

                elif unchanged:

                    print(f"  ⟳ lyrics to try again : {mp3_path.name}")

                    track_data = self.refresh_lyrics(old_track, mp3_path)

                    stats["lyrics_retried"] += 1
                    stats["tracks_cached"] += 1

                # ----------------------------------------------
                # NOUVEAU OU MODIFIE
                # ----------------------------------------------

                else:

                    if old_track is None:

                        print(f"  + New : {mp3_path.name}")

                        stats["tracks_new"] += 1

                    else:

                        print(f"  ↻ Modified : {mp3_path.name}")

                        stats["tracks_updated"] += 1

                    track_data = self.build_track(mp3_path)

                if track_data.get("lyrics_status") == LYRICS_ERROR:
                    stats["lyrics_errors"] += 1

                tracks.append(track_data)

            # ----------------------------------------------------
            # 6. Détecter les morceaux supprimés
            # ----------------------------------------------------

            if old_album:

                removed = set(old_tracks.keys()) - current_keys

                if removed:

                    stats["tracks_removed"] += len(removed)

                    for removed_key in removed:

                        print(f"  - Deleted : {removed_key}")

            # ----------------------------------------------------
            # 7. Trier
            # ----------------------------------------------------

            tracks.sort(
                key=lambda x: (
                    x["track"] is None,
                    (x["track"] if x["track"] is not None else 0),
                )
            )

            # ----------------------------------------------------
            # 8. Nom de l'album
            # ----------------------------------------------------

            album_name = None

            for track_data in tracks:

                if track_data.get("album"):
                    album_name = track_data["album"]
                    break

            if not album_name:
                album_name = folder.name

            # ----------------------------------------------------
            # 9. Construire l'album
            # ----------------------------------------------------

            album_data = {
                "folder": folder,
                "name": album_name,
                "cover": cover,
                "tracks": tracks,
            }

            albums.append(album_data)

            if old_album is None:
                stats["albums_new"] += 1

        # --------------------------------------------------------
        # 10. Les albums qui ont complètement disparu
        # --------------------------------------------------------

        current_album_keys = {str(Path(album["folder"]).resolve()) for album in albums}

        for old_key, old_album in old_albums.items():

            if old_key not in current_album_keys:

                print()
                print(f"Deleted Album : {old_album.get('name', old_key)}")

        # --------------------------------------------------------
        # 11. Ecriture
        # --------------------------------------------------------
        # _mtime et lyrics_status restent dans le JSON : ils servent
        # à savoir quoi recalculer / retenter au prochain scan.

        library = {"albums": albums}

        database_counts = Database(
            DATABASE_PATH,
            migrate_from=LEGACY_DATABASE_PATH,
        ).sync_library(
            self.convert_paths(library)
        )
        self.write_json_library(
            library,
            output_path,
        )

        # --------------------------------------------------------
        # 12. Résumé
        # --------------------------------------------------------

        print()
        print("=" * 60)
        print("SCAN finished")
        print("=" * 60)

        print(f"New Albums      : {stats['albums_new']}")
        print(f"New Tracks     : {stats['tracks_new']}")
        print(f"Modified Tracks     : {stats['tracks_updated']}")
        print(f"Kept Tracks    : {stats['tracks_cached']}")
        print(f"Deleted Tracks    : {stats['tracks_removed']}")
        print(f"Lyrics tried    : {stats['lyrics_retried']}")
        print(f"Lyrics in error   : {stats['lyrics_errors']}")

        if stats["lyrics_errors"]:
            print("  -> restart scan : only those tracks will be tried again.")

        print()
        print(f"created library: {output_path}")
        print(
            f"updated SQLilte database : {DATABASE_PATH} "
            f"({database_counts['albums']} albums, {database_counts['tracks']} pistes)"
        )

        return library

    # ============================================================
    # JSON
    # ============================================================

    def convert_paths(self, obj):
        """
        Convertit les Path en str pour JSON.
        """

        if isinstance(obj, dict):

            return {k: self.convert_paths(v) for k, v in obj.items()}

        elif isinstance(obj, list):

            return [self.convert_paths(i) for i in obj]

        elif isinstance(obj, Path):

            return str(obj)

        else:
            return obj

    def write_json_library(
        self,
        library,
        output_path,
    ):
        """
        Écrit la bibliothèque musicale.
        """

        library_serializable = self.convert_paths(library)

        output_path = ensure_library_parent(output_path)

        with open(
            output_path,
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                library_serializable,
                f,
                ensure_ascii=False,
                indent=4,
            )

if __name__ == "__main__":

    scanner = Scanner()
    scanner.welcome_screen()
    print(LIBRARY_PATH.parent)
    print(LIBRARY_FILENAME)
    scanner.scan_music_folder(LIBRARY_PATH.parent)

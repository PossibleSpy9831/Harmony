import termios
import tty
import sys
import os
import select



class Keyboard():

    def __init__(self):

        self.fd = sys.stdin.fileno()

        self.old_settings = None

    def start(self):

        self.old_settings = termios.tcgetattr(self.fd)

        tty.setcbreak(self.fd)

    def stop(self):

        if self.old_settings is not None:

            termios.tcsetattr(
                self.fd,
                termios.TCSADRAIN,
                self.old_settings,
            )

            self.old_settings = None

    def read_key(self):

        readable, _, _ = select.select(
            [sys.stdin],
            [],
            [],
            0,
        )

        if not readable:
            return None

        first = os.read(
            self.fd,
            1,
        )

        if not first:
            return None

        if first == b"\x1b":

            # On attend brièvement les caractères
            # constituant la séquence flèche.
            readable, _, _ = select.select(
                [sys.stdin],
                [],
                [],
                0.02,
            )

            if not readable:
                return "ESC"

            second = os.read(
                self.fd,
                1,
            )

            if second != b"[":
                return "ESC"

            readable, _, _ = select.select(
                [sys.stdin],
                [],
                [],
                0.02,
            )

            if not readable:
                return "ESC"

            third = os.read(
                self.fd,
                1,
            )

            if third == b"A":
                return "UP"

            if third == b"B":
                return "DOWN"

            if third == b"C":
                return "RIGHT"

            if third == b"D":
                return "LEFT"

            return "ESC"

        try:
            return first.decode(
                "utf-8",
                errors="ignore",
            )
        except Exception:
            return None


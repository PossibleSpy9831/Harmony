import math
import threading

from rich.align import Align
from rich.box import ROUNDED
from rich.panel import Panel
from rich.text import Text


class Spectrum:
    def __init__(
        self,
        mpv,
        bars=48,
        height=16,
        sample_rate=44100,
        fft_size=2048,
    ):
        self.mpv = mpv
        self.bars = max(8, bars)
        self.height = max(4, height)
        self.sample_rate = sample_rate
        self.fft_size = fft_size

        self.levels = [0.0] * self.bars
        self.peaks = [0.0] * self.bars
        self.lock = threading.Lock()

        self._window = [
            0.5 - 0.5 * math.cos(2.0 * math.pi * i / (fft_size - 1))
            for i in range(fft_size)
        ]

        self._bands = self._make_bands()

    def _make_bands(self):
        nyquist = self.sample_rate / 2
        low = 35.0
        high = min(16000.0, nyquist)

        bands = []

        for i in range(self.bars):
            f1 = low * (high / low) ** (i / self.bars)
            f2 = low * (high / low) ** ((i + 1) / self.bars)

            start = max(
                1,
                int(f1 * self.fft_size / self.sample_rate),


            )
            end = max(
                start + 1,
                int(f2 * self.fft_size / self.sample_rate),
            )

            bands.append((start, end))

        return bands

    def _fft(self, samples):
        n = self.fft_size

        real = [samples[i] * self._window[i] for i in range(n)]
        imag = [0.0] * n

        j = 0

        for i in range(1, n):
            bit = n >> 1

            while j & bit:
                j ^= bit
                bit >>= 1

            j ^= bit

            if i < j:
                real[i], real[j] = real[j], real[i]
                imag[i], imag[j] = imag[j], imag[i]

        length = 2

        while length <= n:
            angle = -2.0 * math.pi / length
            wlen_real = math.cos(angle)
            wlen_imag = math.sin(angle)

            for start in range(0, n, length):
                wr = 1.0
                wi = 0.0
                half = length // 2

                for k in range(start, start + half):
                    even_r = real[k]
                    even_i = imag[k]

                    odd_r = real[k + half] * wr - imag[k + half] * wi
                    odd_i = real[k + half] * wi + imag[k + half] * wr

                    real[k] = even_r + odd_r
                    imag[k] = even_i + odd_i

                    real[k + half] = even_r - odd_r
                    imag[k + half] = even_i - odd_i

                    next_wr = wr * wlen_real - wi * wlen_imag
                    wi = wr * wlen_imag + wi * wlen_real
                    wr = next_wr

            length <<= 1

        return [math.hypot(real[i], imag[i]) / n for i in range(n // 2)]

    def update(self):
        reader = getattr(
            self.mpv,
            "read_audio_samples",
            None,
        )

        if reader is None:
            return False

        try:
            samples = reader(self.fft_size)
        except (OSError, ValueError):
            return False

        if samples is None or len(samples) < self.fft_size:
            return False

        spectrum = self._fft(samples[-self.fft_size :])
        new_levels = []

        for start, end in self._bands:
            end = min(end, len(spectrum))
            values = spectrum[start:end]

            magnitude = sum(values) / len(values) if values else 0.0

            magnitude = min(
                1.0,
                math.log1p(magnitude * 350.0) / math.log1p(350.0),
            )

            new_levels.append(magnitude)

        with self.lock:
            smoothed = []

            for new, old in zip(new_levels, self.levels):
                if new > old:
                    value = old * 0.30 + new * 0.70
                else:
                    value = old * 0.84 + new * 0.16

                smoothed.append(value)

            self.levels = smoothed
            self.peaks = [
                max(level, peak - 0.025)
                for level, peak in zip(
                    self.levels,
                    self.peaks,
                )
            ]

        return True

    def reset(self):
        with self.lock:
            self.levels = [0.0] * self.bars
            self.peaks = [0.0] * self.bars

    def render(self):
        with self.lock:
            levels = self.levels[:]
            peaks = self.peaks[:]

        lines = []

        for row in range(self.height, 0, -1):
            line = Text("  ")

            for i, level in enumerate(levels):
                bar_height = round(level * self.height)
                peak_height = round(peaks[i] * self.height)

                if bar_height >= row:
                    if row >= self.height * 0.75:
                        color = "#b6ffb6"
                    elif row >= self.height * 0.45:
                        color = "#39ff14"
                    else:
                        color = "#168a24"

                    line.append("█", style=f"bold {color}")
                elif peak_height == row:
                    line.append("▀", style="bold #65ff65")
                else:
                    line.append(" ", style="#102b16")

                line.append(" ")

            lines.append(line)

        body = Text("\n").join(lines)

        title = Text()
        title.append("[", style="#4d774d")
        title.append("HARMONY SPECTRUM", style="#65ff65")
        title.append("]", style="#4d774d")

        subtitle = Text()
        subtitle.append("[", style="#4d774d")
        subtitle.append("AUDIO STREAM", style="#65ff65")
        subtitle.append("]", style="#4d774d")
        subtitle.append("  ", style="default")
        subtitle.append("::", style="#245c2b")
        subtitle.append("  FFT ACTIVE  ", style="#39ff14")
        subtitle.append("::", style="#245c2b")
        subtitle.append("  ESC TO EXIT", style="#4d774d")

        return Panel(
            Align.center(body, vertical="middle"),
            title=title,
            subtitle=subtitle,
            border_style="#237a32",
            box=ROUNDED,
            padding=(1, 1),
        )

    def render_compact(self, bars=20, height=4):
        bars = max(1, min(int(bars), self.bars))
        height = max(1, int(height))
        with self.lock:
            levels = self.levels[:]
            peaks = self.peaks[:]

        compact_levels = []
        compact_peaks = []
        for index in range(bars):
            start = index * len(levels) // bars
            end = max(start + 1, (index + 1) * len(levels) // bars)
            compact_levels.append(max(levels[start:end]))
            compact_peaks.append(max(peaks[start:end]))

        lines = []
        for row in range(height, 0, -1):
            line = Text()
            for level, peak in zip(compact_levels, compact_peaks):
                bar_height = round(level * height)
                peak_height = round(peak * height)
                if bar_height >= row:
                    color = "#b6ffb6" if row >= height * 0.75 else "#39ff14"
                    line.append("█", style=f"bold {color}")
                elif peak_height == row:
                    line.append("▀", style="bold #65ff65")
                else:
                    line.append(" ", style="#102b16")
                line.append(" ")
            lines.append(line)

        return Panel(
            Align.center(Text("\n").join(lines), vertical="middle"),
            title="[bold #39ff14]LIVE SPECTRUM[/bold #39ff14]",
            border_style="#237a32",
            box=ROUNDED,
            padding=(0, 1),
        )

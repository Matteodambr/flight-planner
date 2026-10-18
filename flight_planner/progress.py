"""Single-line terminal progress bar; falls back to one line per step when not on a terminal."""

from __future__ import annotations

import shutil
import sys
import time


class Progress:
    def __init__(self, total: int, stream=None, enabled: bool | None = None):
        self.total = max(total, 1)
        self.stream = stream or sys.stderr
        self.tty = self.stream.isatty() if enabled is None else enabled
        self.done = self.cached = self.failed = 0
        self.fetched = 0
        self.start = time.monotonic()
        self.label = ""

    def step(self, label: str, *, cached: bool = False, failed: bool = False, detail: str = "") -> None:
        self.done += 1
        self.cached += cached
        self.failed += failed
        self.fetched += not cached
        self.label = label
        if self.tty:
            self._draw()
        else:
            print(f"[{self.done}/{self.total}] {label}: {detail}", file=self.stream, flush=True)

    def message(self, text: str) -> None:
        """Print a line above the bar (warnings, retries)."""
        if self.tty:
            self.stream.write("\r\033[K" + text + "\n")
            self._draw()
        else:
            print(text, file=self.stream, flush=True)

    def wait(self, seconds: float, reason: str, sleep=time.sleep) -> None:
        """Sleep with a live countdown on the bar line. The wait is excluded from the ETA."""
        end = time.monotonic() + seconds
        if not self.tty:
            print(f"  waiting {seconds / 60:g} min ({reason})", file=self.stream, flush=True)
            sleep(seconds)
        else:
            remaining = seconds
            while remaining > 0:
                m, sec = divmod(int(remaining), 60)
                self.stream.write(f"\r\033[K⏸ {self.done}/{self.total} · {reason} {m}:{sec:02d} (Ctrl+C to stop; progress is cached)")
                self.stream.flush()
                sleep(min(1.0, remaining))
                remaining = end - time.monotonic() if sleep is time.sleep else remaining - 1
            self._draw()
        self.start += seconds

    def close(self) -> None:
        if self.tty:
            self.stream.write("\n")
            self.stream.flush()

    def _eta(self) -> str:
        remaining = self.total - self.done
        if not remaining:
            return "done"
        if not self.fetched:
            return "ETA --"
        # Cached steps are instant, so estimate from fetched ones only.
        per = (time.monotonic() - self.start) / self.fetched
        secs = int(per * remaining)
        return f"ETA {secs // 60}m{secs % 60:02d}s"

    def _draw(self) -> None:
        pct = self.done / self.total
        stats = f" {self.done}/{self.total} {pct:4.0%} · {self._eta()}"
        if self.cached:
            stats += f" · {self.cached} cached"
        if self.failed:
            stats += f" · {self.failed} failed"
        stats += f" · {self.label}"
        width = shutil.get_terminal_size((80, 20)).columns
        bar_w = max(10, min(40, width - len(stats) - 3))
        filled = int(bar_w * pct)
        line = f"[{'█' * filled}{'░' * (bar_w - filled)}]{stats}"
        self.stream.write("\r\033[K" + line[: width - 1])
        self.stream.flush()

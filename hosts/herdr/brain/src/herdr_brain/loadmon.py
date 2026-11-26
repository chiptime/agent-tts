"""Machine load monitoring (POST-01, docs/paridad/13 §2).

A stdlib-only sampler — ``/proc/stat`` (CPU), ``/proc/meminfo`` (RAM),
``shutil.disk_usage`` (disk), ``/proc/net/dev`` (network) — polling every
minute, a rotating 24h JSONL history under the brain's state dir, and
operator thresholds in ``load-alerts.toml`` validated row by row (the
doc 06 ``operator_config`` pattern). Threshold crossings travel through
the existing ``AnnouncementHub`` (SSE + spoken text), exactly like the
daemon watcher's system announcements.

Non-Linux degrades honestly: ``platform_supported()`` is False, ``/load``
answers ``{"available": false, ...}`` and no sampler runs.

The history holds no secrets (percentages and byte counters only), but
every announcement text still passes through ``mask_text`` before it is
published — outbound masking is a brain-wide contract, not a per-module
judgment call.
"""

from __future__ import annotations

import json
import logging
import math
import os
import shutil
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .config import Settings
from .operator_config import (  # noqa: F401  (_stat_signature/_load_surface reused)
    Issue,
    _load_surface,
    _stat_signature,
    default_config_dir,
)
from .secrets_mask import mask_text
from .tts import new_audio_path
from .watcher import ANNOUNCEMENT_PREFIX, AnnouncementHub

logger = logging.getLogger(__name__)

#: One sample per minute (doc 13 §2: "muestreo por minuto").
SAMPLE_INTERVAL_S = 60.0

#: The retained window: 24 hours (doc 13 §2: "ventana de 24 h").
WINDOW_S = 24 * 3600.0

#: History rows at the minute cadence that fill the window exactly.
HISTORY_MAX_ROWS = 1440

#: Rotating history file, under the brain's state dir (doc 13 §2).
HISTORY_FILENAME = "load-history.jsonl"

#: Operator threshold file, under the brain's config dir (doc 13 §2).
FILE_ALERTS = "load-alerts.toml"
TABLE_ALERTS = "alerts"
ALERT_METRICS = ("cpu", "ram", "disk", "net_rx", "net_tx")
ALLOWED_ALERT_FIELDS = frozenset(
    {"metric", "threshold", "path", "iface", "reannounce_s", "enabled"}
)
DEFAULT_REANNOUNCE_S = 30 * 60.0

#: Loopback never counts as machine load.
EXCLUDED_IFACES = frozenset({"lo"})

#: A percent value must fall this far below the threshold before the
#: recovery is announced — anti-flap margin for metrics hovering at the
#: line (disk and RAM can oscillate around 90% for hours).
HYSTERESIS_POINTS = 5.0


def platform_supported() -> bool:
    """Load metrics read Linux ``/proc``; anything else says so."""
    return sys.platform.startswith("linux")


def host_name() -> str:
    import socket

    return socket.gethostname() or "localhost"


# ── /proc readers (pure, path-injectable for tests) ──────────────────────


def read_proc_stat(path: Path) -> Optional[Tuple[int, int]]:
    """``(idle, total)`` jiffies from the ``cpu`` summary line; ``None``
    when the file is missing or not a /proc/stat shape."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        if not line.startswith("cpu "):
            continue
        fields = line.split()
        values = []
        for raw in fields[1:]:
            try:
                values.append(int(raw))
            except ValueError:
                return None
        # the cpu summary needs user/nice/system/idle at minimum: a
        # truncated line is garbage, not a zero-load machine
        if len(values) < 4:
            return None
        idle = values[3] + (values[4] if len(values) > 4 else 0)  # idle + iowait
        return idle, sum(values)
    return None


def read_meminfo(path: Path) -> Optional[float]:
    """RAM load percent from ``MemTotal``/``MemAvailable``; ``None`` when
    the file lacks either row."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    total = available = None
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        key = key.strip()
        if key in ("MemTotal", "MemAvailable"):
            try:
                value = int(rest.strip().split()[0])
            except (ValueError, IndexError):
                return None
            if key == "MemTotal":
                total = value
            else:
                available = value
    if not total or available is None:
        return None
    return (1.0 - available / total) * 100.0


def read_net_dev(path: Path) -> Dict[str, Tuple[int, int]]:
    """Per-interface ``(rx_bytes, tx_bytes)``, loopback excluded; ``{}``
    when the file is missing."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    counters: Dict[str, Tuple[int, int]] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue  # the two header lines
        iface, _, rest = line.partition(":")
        iface = iface.strip()
        if iface in EXCLUDED_IFACES:
            continue
        fields = rest.split()
        if len(fields) < 9:
            continue
        try:
            rx, tx = int(fields[0]), int(fields[8])
        except ValueError:
            continue
        counters[iface] = (rx, tx)
    return counters


def disk_percent(path: str, usage_fn=shutil.disk_usage) -> Optional[float]:
    """Used percent of one filesystem; ``None`` when it cannot be probed."""
    try:
        usage = usage_fn(path)
        if not usage.total:
            return None
        return usage.used / usage.total * 100.0
    except OSError:
        return None


# ── sampling ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LoadSample:
    """One poll's snapshot. CPU and network rates need two polls (a
    delta), so the first sample carries ``None`` there — an honest gap,
    never a zero."""

    ts: float
    cpu_percent: Optional[float]
    ram_percent: Optional[float]
    disk_percent: Optional[float]
    net_rx_bps: Optional[float]
    net_tx_bps: Optional[float]
    extra_disk: Tuple[Tuple[str, float], ...] = ()
    extra_net: Tuple[Tuple[str, float, float], ...] = ()

    def to_history_row(self) -> dict:
        row = {
            "ts": self.ts,
            "cpu_percent": self.cpu_percent,
            "ram_percent": self.ram_percent,
            "disk_percent": self.disk_percent,
            "net_rx_bps": self.net_rx_bps,
            "net_tx_bps": self.net_tx_bps,
        }
        if self.extra_disk:
            row["extra_disk"] = {path: value for path, value in self.extra_disk}
        if self.extra_net:
            row["extra_net"] = [
                {"iface": iface, "rx_bps": rx, "tx_bps": tx}
                for iface, rx, tx in self.extra_net
            ]
        return row


class MetricSampler:
    """Turns raw ``/proc`` reads into rate-bearing samples. The deltas
    live here: CPU jiffies and interface byte counters between the
    previous poll and this one, over the observed wall-clock gap."""

    def __init__(
        self,
        *,
        proc_stat_path: Path = Path("/proc/stat"),
        meminfo_path: Path = Path("/proc/meminfo"),
        net_dev_path: Path = Path("/proc/net/dev"),
        disk_usage_fn=shutil.disk_usage,
        root_path: str = "/",
        extra_disk_paths: Tuple[str, ...] = (),
        clock: Callable[[], float] = time.time,
    ):
        self._proc_stat = proc_stat_path
        self._meminfo = meminfo_path
        self._net_dev = net_dev_path
        self._disk_usage = disk_usage_fn
        self._root = root_path
        self._extra_disk = tuple(extra_disk_paths)
        self._clock = clock
        self._prev_cpu: Optional[Tuple[int, int]] = None
        self._prev_net: Dict[str, Tuple[int, int]] = {}
        self._prev_ts: Optional[float] = None

    def sample(
        self, *, extra_disk_paths: Optional[Tuple[str, ...]] = None
    ) -> LoadSample:
        """One poll. ``extra_disk_paths`` overrides the constructor's set
        for this poll — the monitor re-reads the operator's alert rows
        every cycle, so a freshly added disk row starts sampling without
        a restart."""
        now = self._clock()
        extra_disk = self._extra_disk if extra_disk_paths is None else tuple(extra_disk_paths)

        cpu_percent: Optional[float] = None
        current_cpu = read_proc_stat(self._proc_stat)
        if current_cpu is not None and self._prev_cpu is not None:
            prev_idle, prev_total = self._prev_cpu
            idle, total = current_cpu
            d_total = total - prev_total
            d_idle = idle - prev_idle
            if d_total > 0 and d_idle >= 0:
                cpu_percent = (1.0 - d_idle / d_total) * 100.0
        if current_cpu is not None:
            self._prev_cpu = current_cpu

        ram_percent = read_meminfo(self._meminfo)

        net_rx_bps: Optional[float] = None
        net_tx_bps: Optional[float] = None
        extra_net: Tuple[Tuple[str, float, float], ...] = ()
        current_net = read_net_dev(self._net_dev)
        if self._prev_ts is not None:
            dt = now - self._prev_ts
            # a poll at the same instant has no honest rate: dividing by
            # zero is not a speed of light traffic spike
            if dt > 0:
                common = set(current_net) & set(self._prev_net)
                rates: List[Tuple[str, float, float]] = []
                for iface in sorted(common):
                    d_rx = current_net[iface][0] - self._prev_net[iface][0]
                    d_tx = current_net[iface][1] - self._prev_net[iface][1]
                    if d_rx < 0 or d_tx < 0:
                        continue  # a counter reset is not a negative rate
                    rates.append((iface, d_rx / dt, d_tx / dt))
                if rates:
                    net_rx_bps = sum(rate[1] for rate in rates)
                    net_tx_bps = sum(rate[2] for rate in rates)
                    extra_net = tuple(rates)
        if current_net:
            self._prev_net = current_net
        self._prev_ts = now

        root_disk = disk_percent(self._root, self._disk_usage)
        extra = tuple(
            (path, value)
            for path in extra_disk
            if (value := disk_percent(path, self._disk_usage)) is not None
        )
        return LoadSample(
            ts=now,
            cpu_percent=cpu_percent,
            ram_percent=ram_percent,
            disk_percent=root_disk,
            net_rx_bps=net_rx_bps,
            net_tx_bps=net_tx_bps,
            extra_disk=extra,
            extra_net=extra_net,
        )


# ── the rotating JSONL history ───────────────────────────────────────────


class LoadHistory:
    """Append-only JSONL with a hard row cap: rotation rewrites the file
    keeping the newest ``max_rows`` rows (doc 13 §2: "rotación
    obligatoria"). Reads tolerate corrupt or non-dict lines — a partial
    write from a crash never poisons the endpoint."""

    def __init__(
        self,
        path: Path,
        *,
        window_s: float = WINDOW_S,
        max_rows: int = HISTORY_MAX_ROWS,
        clock: Callable[[], float] = time.time,
    ):
        self._path = Path(path)
        self._window_s = window_s
        self._max_rows = max_rows
        self._clock = clock
        self._lock = threading.Lock()

    def append_row(self, row: dict) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row) + "\n")
            self._rotate_locked()

    def append(self, sample: LoadSample) -> None:
        self.append_row(sample.to_history_row())

    def read(self, *, window_s: Optional[float] = None) -> List[dict]:
        """Newest last; rows older than the window (default 24h) dropped.
        Lock-guarded so a concurrent rotation rewrite is never observed
        mid-replace."""
        window = self._window_s if window_s is None else window_s
        cutoff = self._clock() - window
        with self._lock:
            rows = self._read_all_rows()
        return [
            row
            for row in rows
            if self._usable_ts(row.get("ts")) and row["ts"] >= cutoff
        ]

    # -- internals ------------------------------------------------------

    @staticmethod
    def _usable_ts(ts: object) -> bool:
        # bool sneaks through isinstance(int) and NaN survives any
        # comparison: both are garbage, never a sample time
        return (
            isinstance(ts, (int, float))
            and not isinstance(ts, bool)
            and math.isfinite(ts)
        )

    def _read_all_rows(self) -> List[dict]:
        try:
            # errors="replace": a torn write or a stray byte must not
            # turn the history endpoint (and rotation) into a 500
            text = self._path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        rows: List[dict] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
        return rows

    def _rotate_locked(self) -> None:
        rows = self._read_all_rows()
        if len(rows) <= self._max_rows:
            return
        keep = rows[-self._max_rows :]
        # a unique temp name: two LoadHistory views of one file (or a
        # crash mid-rename) must never collide on the same temp path
        tmp = self._path.with_name(
            f"{self._path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        )
        with tmp.open("w", encoding="utf-8") as handle:
            for row in keep:
                handle.write(json.dumps(row) + "\n")
        os.replace(tmp, self._path)


# ── operator thresholds: load-alerts.toml (doc 06 pattern) ───────────────


@dataclass(frozen=True)
class AlertRow:
    """One ``[[alerts]]`` row. ``threshold`` is a percent for
    ``cpu``/``ram``/``disk`` and an absolute bytes-per-second for the
    ``net_*`` metrics."""

    metric: str
    threshold: float
    path: str = "/"
    iface: Optional[str] = None
    reannounce_s: float = DEFAULT_REANNOUNCE_S
    enabled: bool = True


def validate_alerts(
    doc: object, file_name: str = FILE_ALERTS
) -> Tuple[Tuple[AlertRow, ...], Tuple[Issue, ...]]:
    """Row-by-row validation: a malformed row is discarded with exactly
    one reason while its healthy siblings keep serving. Unknown fields
    discard the row (a typo must never silently disable a threshold)."""
    if not isinstance(doc, dict):
        return (), (Issue(file_name, 0, "document must be a table"),)
    raw = doc.get(TABLE_ALERTS)
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        return (), (
            Issue(file_name, 0, f"[[{TABLE_ALERTS}]] must be an array of tables"),
        )

    rows: List[AlertRow] = []
    issues: List[Issue] = []

    def _number_field(row: dict, name: str, default: float) -> Tuple[Optional[float], Optional[str]]:
        value = row.get(name)
        if value is None:
            return default, None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None, f"{name} must be a number"
        if not math.isfinite(value):
            return None, f"{name} must be a finite number"
        if value <= 0:
            return None, f"{name} must be positive"
        return float(value), None

    for ordinal, row in enumerate(raw, start=1):
        if not isinstance(row, dict):
            issues.append(Issue(file_name, ordinal, "row must be a table"))
            continue
        unknown = sorted(set(row) - ALLOWED_ALERT_FIELDS)
        if unknown:
            issues.append(Issue(file_name, ordinal, f"unknown field {unknown[0]!r}"))
            continue
        metric = row.get("metric")
        if metric not in ALERT_METRICS:
            issues.append(
                Issue(
                    file_name,
                    ordinal,
                    f"metric must be one of {', '.join(ALERT_METRICS)}",
                )
            )
            continue
        threshold, reason = _number_field(row, "threshold", 0.0)
        if reason or threshold is None or threshold <= 0:
            issues.append(Issue(file_name, ordinal, reason or "threshold must be a positive number"))
            continue
        path = "/"
        if row.get("path") is not None:
            if metric != "disk":
                issues.append(Issue(file_name, ordinal, "path only applies to disk rows"))
                continue
            value = row["path"]
            if not isinstance(value, str) or not value.strip():
                issues.append(Issue(file_name, ordinal, "path must be a non-empty string"))
                continue
            path = value.strip()
        iface = None
        if row.get("iface") is not None:
            if not metric.startswith("net_"):
                issues.append(Issue(file_name, ordinal, "iface only applies to net_rx/net_tx rows"))
                continue
            value = row["iface"]
            if not isinstance(value, str) or not value.strip():
                issues.append(Issue(file_name, ordinal, "iface must be a non-empty string"))
                continue
            iface = value.strip()
        reannounce_s, reason = _number_field(row, "reannounce_s", DEFAULT_REANNOUNCE_S)
        if reason:
            issues.append(Issue(file_name, ordinal, reason))
            continue
        enabled = row.get("enabled", True)
        if not isinstance(enabled, bool):
            issues.append(Issue(file_name, ordinal, "enabled must be a boolean"))
            continue
        rows.append(
            AlertRow(
                metric=metric,
                threshold=threshold,
                path=path,
                iface=iface,
                reannounce_s=reannounce_s,
                enabled=enabled,
            )
        )
    return tuple(rows), tuple(issues)


class AlertStore:
    """The mtime-cached ``load-alerts.toml`` view: one ``stat`` per poll,
    a changed signature reloads and re-validates under the lock, and a
    parse failure keeps the last valid rows (the dock-file discipline —
    an operator mid-edit must not lose their alerts)."""

    def __init__(
        self,
        config_dir: Optional[Path | str] = None,
        *,
        file_name: str = FILE_ALERTS,
        environ: Optional[Dict[str, str]] = None,
    ):
        if config_dir is not None:
            self._dir = Path(config_dir).expanduser()
        else:
            self._dir = default_config_dir(environ)
        self._file_name = file_name
        self._lock = threading.Lock()
        self._signature: object = None
        self._rows: Tuple[AlertRow, ...] = ()

    def rows(self) -> Tuple[AlertRow, ...]:
        path = self._dir / self._file_name
        with self._lock:
            signature = _stat_signature(path)
            if signature != self._signature:
                loaded, issues = _load_surface(path, self._file_name, validate_alerts)
                if loaded is None:
                    # parse failure: keep the last valid rows, report once
                    loaded = self._rows
                    for issue in issues:
                        logger.warning(
                            "load alerts: %s; keeping the last valid rows",
                            issue.describe(),
                        )
                else:
                    for issue in issues:
                        logger.warning("load alerts: %s", issue.describe())
                self._rows = loaded
                self._signature = signature
            return self._rows


# ── the alert engine ─────────────────────────────────────────────────────

_METRIC_SUBJECTS = {
    "cpu": "CPU",
    "ram": "RAM",
    "net_rx": "la red de entrada",
    "net_tx": "la red de salida",
}


def _fmt_rate(bps: float) -> str:
    if bps >= 1_000_000:
        return f"{bps / 1_000_000:.1f} MB/s"
    if bps >= 1_000:
        return f"{bps / 1_000:.1f} kB/s"
    return f"{bps:.0f} B/s"


class AlertEngine:
    """Threshold crossings with hysteresis and a re-announce window.

    A row announces when its value climbs to the threshold (including a
    hot boot: like the daemon watcher, the first poll already warns),
    re-announces at most once per ``reannounce_s`` while it stays above,
    and announces the recovery only after the value fell a full margin
    below the threshold — metrics hovering at the line must not flap.
    The dead zone between the threshold and its margin freezes the row's
    state: no events either way."""

    def __init__(
        self,
        hostname: str,
        *,
        clock: Callable[[], float] = time.monotonic,
        hysteresis: float = HYSTERESIS_POINTS,
    ):
        self._host = hostname
        self._clock = clock
        self._hysteresis = hysteresis
        self._state: Dict[tuple, Dict[str, Optional[object]]] = {}
        self._lock = threading.Lock()

    def evaluate(self, sample: LoadSample, alerts: Tuple[AlertRow, ...]) -> List[dict]:
        events: List[dict] = []
        now = self._clock()
        with self._lock:
            for alert in alerts:
                if not alert.enabled:
                    continue
                value = self._value(sample, alert)
                if value is None:
                    continue
                key = (alert.metric, alert.path, alert.iface, alert.threshold)
                state = self._state.setdefault(key, {"above": None, "warned": None})
                if value >= alert.threshold:
                    fresh_crossing = state["above"] is not True
                    warned = state["warned"]
                    due = warned is None or now - warned >= alert.reannounce_s
                    if fresh_crossing or due:
                        events.append(
                            {"text": self._warn_text(alert, value), "kind": "warning"}
                        )
                        state["warned"] = now
                    state["above"] = True
                elif value <= alert.threshold - self._margin_for(alert):
                    if state["above"] is True:
                        events.append(
                            {
                                "text": self._recovery_text(alert),
                                "kind": "info",
                            }
                        )
                        state["warned"] = None
                    state["above"] = False
                # dead zone: the row keeps its previous state, quietly
            # prune rules the operator removed, disabled or edited away:
            # stale keys would inflate /load's active count forever
            live = {
                (a.metric, a.path, a.iface, a.threshold)
                for a in alerts
                if a.enabled
            }
            for key in list(self._state):
                if key not in live:
                    del self._state[key]
        return events

    def active_count(self) -> int:
        with self._lock:
            return sum(
                1 for state in self._state.values() if state["above"] is True
            )

    def _margin_for(self, alert: AlertRow) -> float:
        # percent metrics get a fixed 5-point margin; byte rates would
        # barely notice a fixed 5 B/s — 5% of the threshold scales with
        # the rule (floor at 0 for tiny thresholds)
        if alert.metric.startswith("net_"):
            return max(0.0, alert.threshold * 0.05)
        return self._hysteresis

    # -- values and texts ------------------------------------------------

    def _value(self, sample: LoadSample, alert: AlertRow) -> Optional[float]:
        if alert.metric == "cpu":
            return sample.cpu_percent
        if alert.metric == "ram":
            return sample.ram_percent
        if alert.metric == "disk":
            if alert.path == "/":
                return sample.disk_percent
            return dict(sample.extra_disk).get(alert.path)
        rx_field, tx_field = ("net_rx_bps", "net_tx_bps")
        if alert.iface is None:
            return getattr(sample, rx_field if alert.metric == "net_rx" else tx_field)
        for iface, rx_bps, tx_bps in sample.extra_net:
            if iface == alert.iface:
                return rx_bps if alert.metric == "net_rx" else tx_bps
        return None  # the interface is absent from this poll

    def _warn_text(self, alert: AlertRow, value: float) -> str:
        if alert.metric == "cpu":
            return f"la máquina {self._host} está al {value:.0f} % de CPU"
        if alert.metric == "ram":
            return f"la máquina {self._host} está al {value:.0f} % de RAM"
        if alert.metric == "disk":
            return f"la máquina {self._host}: el disco {alert.path} está al {value:.0f} %"
        rate = _fmt_rate(value)
        verb = "recibe" if alert.metric == "net_rx" else "envía"
        return f"la máquina {self._host} {verb} {rate}"

    def _recovery_text(self, alert: AlertRow) -> str:
        if alert.metric == "disk":
            subject = f"el disco {alert.path}"
        else:
            subject = _METRIC_SUBJECTS[alert.metric]
        return f"la máquina {self._host} se normalizó: {subject} por debajo del umbral"


# ── the monitor (twin of DaemonWatcher, on its own minute beat) ──────────


class LoadMonitor:
    """Samples machine load every minute, appends the rotating history,
    and publishes threshold announcements through the shared hub — the
    SAME hub the agent watcher uses, so SSE and the spoken channel carry
    load alerts like any other system announcement. Its own timer is
    deliberate: agent-driven polls stop when every agent is dead, and
    exactly then a starving machine is worth announcing."""

    def __init__(
        self,
        settings: Settings,
        *,
        hub: Optional[AnnouncementHub] = None,
        tts_renderer: Optional[Callable] = None,
        sampler: Optional[MetricSampler] = None,
        history: Optional[LoadHistory] = None,
        alert_store: Optional[AlertStore] = None,
        engine: Optional[AlertEngine] = None,
        hostname: Optional[str] = None,
        poll_interval_s: float = SAMPLE_INTERVAL_S,
    ):
        self._settings = settings
        self._hub = hub or AnnouncementHub()
        self._tts = tts_renderer
        self._sampler = sampler or MetricSampler()
        self._history = history or LoadHistory(
            Path(settings.state_dir) / HISTORY_FILENAME
        )
        self._alerts = alert_store or AlertStore()
        # C1: the hostname reaches SSE labels, /load payloads and spoken
        # texts — mask it ONCE at the single source so every downstream
        # flow (announcement label, engine texts, snapshots, history
        # windows) carries the same masked name. Ordinary hostnames pass
        # through unchanged (the detectors demand a known secret shape).
        self.hostname = mask_text(hostname or host_name())
        self._engine = engine or AlertEngine(self.hostname)
        self._poll_interval = poll_interval_s
        self._last_sample: Optional[LoadSample] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        # Declared degradation (doc 13 §2 risk 5): off /proc hosts run no
        # sampler at all; /load says so honestly.
        if self._thread is not None or not platform_supported():
            return
        # Boot sample now, on the boot thread: /load is useful within
        # seconds (not after the first minute closes) and a hot machine
        # warns on the first beat. The thread then waits a full interval
        # before its next poll, so the boot sample is never doubled.
        try:
            self.poll_once()
        except Exception:  # noqa: BLE001 — never block boot on sampling
            pass
        self._thread = threading.Thread(target=self._run, name="herdr-loadmon", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self._poll_interval):
            try:
                self.poll_once()
            except Exception:  # noqa: BLE001 — the monitor must never die
                continue

    # -- one poll cycle ------------------------------------------------------

    def poll_once(self) -> List[dict]:
        if not platform_supported():
            return []
        alerts = self._alerts.rows()
        # feed the sampler every disk path the operator's rows reference:
        # a disk row targeting anything but '/' is dead without this
        extra_disk = tuple(
            dict.fromkeys(
                alert.path
                for alert in alerts
                if alert.metric == "disk" and alert.enabled and alert.path != "/"
            )
        )
        sample = self._sampler.sample(extra_disk_paths=extra_disk)
        self._last_sample = sample
        try:
            self._history.append(sample)
        except OSError as exc:
            # a full disk (ENOSPC) is exactly when the disk alert matters:
            # history persistence must never gate the announcement
            logger.warning("load history append failed: %s", exc)
        events = self._engine.evaluate(sample, alerts)
        return [self._announce_and_publish(event) for event in events]

    def _announce_and_publish(self, event: dict) -> dict:
        announcement = self._build_announcement(event["text"], event["kind"])
        self._hub.publish(announcement)
        return announcement

    def _build_announcement(self, text: str, kind: str) -> dict:
        # C1 (outbound masking): the text is masked BEFORE the length of
        # anything downstream and before TTS, so the SSE payload and the
        # spoken audio carry the identical masked string.
        masked = mask_text(text)
        audio_url: Optional[str] = None
        if self._tts is not None:
            try:
                out_path = new_audio_path(self._settings, prefix=ANNOUNCEMENT_PREFIX)
                self._tts(self._settings, masked, out_path)
                audio_url = f"/audio/{out_path.name}"
            except Exception:  # noqa: BLE001 — text must never block on TTS
                audio_url = None
        return {
            "type": "system",
            "kind": kind,
            "pane_id": None,
            "agent": None,
            "status": None,
            "label": self.hostname,
            "text": masked,
            "audio_url": audio_url,
        }

    # -- read views (the /load and /load/history payloads) -------------------

    def snapshot(self) -> dict:
        host = self.hostname
        if not platform_supported():
            return {
                "host": host,
                "available": False,
                "reason": (
                    f"load metrics read Linux /proc (unavailable on {sys.platform})"
                ),
                "alerts": {"configured": 0, "active": 0},
            }
        sample = self._last_sample
        return {
            "host": host,
            "available": True,
            "ts": sample.ts if sample else None,
            "cpu_percent": sample.cpu_percent if sample else None,
            "ram_percent": sample.ram_percent if sample else None,
            "disk_percent": sample.disk_percent if sample else None,
            "net_rx_bps": sample.net_rx_bps if sample else None,
            "net_tx_bps": sample.net_tx_bps if sample else None,
            "alerts": {
                "configured": len(self._alerts.rows()),
                "active": self._engine.active_count(),
            },
        }

    def history_view(self, host: Optional[str] = None, hours: float = 24.0) -> dict:
        # C1: the caller-supplied host name reaches the JSON payload —
        # mask it like every other outbound string (ordinary names pass
        # through unchanged)
        host = mask_text(host or self.hostname)
        hours = max(0.1, min(hours, 24.0))
        empty = {"host": host, "hours": hours, "samples": [], "count": 0}
        if not platform_supported():
            # S5: off /proc hosts never sampled — an honest empty window
            return empty
        if host != self.hostname:
            # v1 is single-machine (doc 13 §2 peer relay lands with doc 10):
            # an unknown host gets an honest empty window, never a guess.
            return empty
        samples = self._history.read(window_s=hours * 3600.0)
        return {"host": host, "hours": hours, "samples": samples, "count": len(samples)}

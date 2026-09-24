"""CI short-mode daemon smoke (RNF-AT-04-4).

Runs the automated smoke harness for a few seconds against a real
explicit-start daemon subprocess: RAM drift bound, ping responsiveness,
and a coherent idle status surface. The full 8 h run is the same harness
with --duration-sec 28800, executed manually or nightly (see the BLOQUE
1.2 notes; registering its result is part of the block's DoD).

Also records the resting RSS evidence for RNF-AT-04-2 (no local model
loaded): the daemon process must stay under 80 MB at rest.
"""

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from daemon_smoke import run_smoke  # noqa: E402

RESTING_RSS_LIMIT_MB = 80.0


def test_short_smoke_real_daemon_rest_is_stable():
    report = run_smoke(duration_sec=3.0, interval_sec=0.5)
    assert report["failures"] == [], report["failures"]
    assert len(report["samples"]) >= 3

    ram = report["ram"]
    assert "first_kb" in ram, ram.get("note")
    assert ram["growth_ok"], f"RAM grew {ram['growth_pct']}% in {report['duration_sec']}s"

    resting_mb = ram["resting_rss_mb"]
    assert resting_mb < RESTING_RSS_LIMIT_MB, (
        f"daemon resting RSS {resting_mb} MB exceeds the RNF-AT-04-2 bound "
        f"({RESTING_RSS_LIMIT_MB} MB without a local model loaded)"
    )

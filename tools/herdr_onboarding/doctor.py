"""One read-only installation doctor, shared by both host launchers.

Probe output is never reflected: credentials, subprocess stderr and HTTP
bodies are untrusted diagnostics. Repairs are printed, never executed.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import platform as host_platform
import shlex
import shutil
import stat
import subprocess
import sys

from herdr_onboarding import prompts, resolve, secrets
from herdr_onboarding.health import CheckResult, check_brain_health, default_fetch
from herdr_onboarding.wizard import MissingAnswer, StepAbort


@dataclass(frozen=True)
class Diagnosis:
    name: str
    result: CheckResult
    remediation: str


class Doctor:
    def __init__(self, role, env, *, runner=subprocess.run, fetch=default_fetch,
                 platform=None, python=None):
        self.role, self.env = role, dict(env)
        self.runner, self.fetch = runner, fetch
        self.platform = platform or host_platform.system()
        self.release = host_platform.release() if platform is None else ""
        self.python = python or sys.executable
        root = Path(__file__).resolve().parents[2]
        self.brain = shlex.join(["bash", str(root / "hosts/herdr/brain/bin/herdr-brain")])
        self.tts = shlex.join(["bash", str(root / "hosts/herdr/tts-plugin/bin/herdr-tts")])
        self.brain_src = str(root / "hosts/herdr/brain/src")
        self.install = shlex.join(["bash", str(root / "hosts/herdr/tts-plugin/scripts/install.sh")])

    def which(self, name):
        return shutil.which(name, path=self.env.get("PATH", ""))

    def probe(self, argv, *, env=None):
        try:
            result = self.runner(argv, env=self.env if env is None else env,
                                 capture_output=True, text=True, timeout=5)
            return result.returncode, result.stdout or ""
        except (OSError, subprocess.TimeoutExpired):
            return 1, ""

    def checks(self):
        # This table is the single public check/repair contract.
        return [self.path(), self.audio(), self.credentials(), self.daemon(),
                self.contract(), self.stt()]

    def path(self):
        local = Path(self.env["HOME"]) / ".local/bin"
        covered = any(Path(p).expanduser() == local for p in self.env.get("PATH", "").split(os.pathsep) if p)
        ok = bool(self.which("herdr-tts")) and covered
        repair = 'export PATH="$HOME/.local/bin:$PATH"'
        if not (local / "herdr-tts").is_file() and not self.which("herdr-tts"):
            repair = self.install
        return Diagnosis("path", CheckResult(ok, "herdr-tts reachable; ~/.local/bin covered" if ok
                          else "herdr-tts missing from PATH or ~/.local/bin not covered"), repair)

    def audio(self):
        if self.platform == "Darwin":
            ok = bool(self.which("afplay"))
            repair = "sudo launchctl kickstart -k system/com.apple.audio.coreaudiod; command -v afplay"
        elif self.env.get("WSL_DISTRO_NAME") or "microsoft" in self.release.lower():
            ok = bool(self.which("powershell.exe"))
            repair = 'export PATH="/mnt/c/Windows/System32/WindowsPowerShell/v1.0:$PATH"; command -v powershell.exe'
        else:
            ok = False
            for name, args in (("pactl", ["info"]), ("pw-cli", ["info", "0"]), ("aplay", ["-l"])):
                executable = self.which(name)
                if executable:
                    code, output = self.probe([executable, *args])
                    if code == 0 and output.strip():
                        ok = True
                        break
            repair = ("if command -v apt-get >/dev/null; then sudo apt-get install alsa-utils pulseaudio-utils; "
                      "elif command -v dnf >/dev/null; then sudo dnf install alsa-utils pulseaudio-utils; "
                      "else sudo pacman -S alsa-utils libpulse; fi")
        return Diagnosis("audio", CheckResult(ok, "platform audio backend available" if ok
                          else "no available platform audio backend"), repair)

    def credentials(self):
        repair = f"{self.brain} doctor --fix-credentials"
        if self.role == "plugin":
            return Diagnosis("credentials", CheckResult(True, "brain GLM key not required for plugin-only use"), repair)
        path = secrets.brain_env_path(self.env)
        ok = False
        try:
            mode = path.lstat().st_mode
            if stat.S_ISREG(mode) and stat.S_IMODE(mode) == 0o600:
                ok = bool((resolve._read_env_key(path, "GLM_API_KEY") or "").strip())
        except (OSError, UnicodeError):
            pass
        return Diagnosis("credentials", CheckResult(ok, "brain env is a mode-600 file with a nonempty GLM key" if ok
                          else "brain env missing, not a regular mode-600 file, or GLM key empty"), repair)

    @staticmethod
    def alive(path):
        try:
            pid = int(path.read_text().strip())
            if pid <= 0:
                return False
            os.kill(pid, 0)  # existence probe only; never sends a signal
            return True
        except (OSError, ValueError, OverflowError):
            return False

    def daemon(self):
        tts = Path(self.env.get("HERDR_TTS_DAEMON_PID_FILE", "/tmp/herdr-tts-daemon.pid"))
        repair = f"{self.tts} --no-first-run --restart-daemon"
        tts_alive = self.alive(tts)
        result = CheckResult(tts_alive, "TTS daemon pidfile is live" if tts_alive else "TTS daemon pidfile missing or dead")
        if self.role == "brain":
            state = Path(self.env.get("XDG_STATE_HOME", str(Path(self.env["HOME"]) / ".local/state")))
            brain_alive = self.alive(state / "herdr-brain/daemon.pid")
            repair += f"; {self.brain} --no-first-run restart"
            if result.ok and brain_alive:
                health = check_brain_health(self.env, fetch=self.fetch, attempts=1)
                # Do not reflect HTTP payload values or exception strings.
                result = CheckResult(health.ok, "live pidfiles and brain /health tts: ok" if health.ok
                                     else "live pidfiles but brain /health is not healthy")
            elif not brain_alive:
                result = CheckResult(False, "TTS or brain daemon pidfile missing or dead")
        return Diagnosis("daemon", result, repair)

    def contract(self):
        executable = self.which("herdr-tts")
        code, output = self.probe([executable, "--contract-version"]) if executable else (1, "")
        try:
            ok = code == 0 and int(output.strip()) >= 1
        except ValueError:
            ok = False
        return Diagnosis("contract", CheckResult(ok, "speech contract version >= 1" if ok
                          else "speech contract missing, invalid, or older than v1"), self.install)

    def stt(self):
        model_key = "HERDR_BRAIN_STT_MODEL" if self.role == "brain" else "AGENT_TTS_STT_MODEL"
        model = self.env.get(model_key)
        if not model and self.role == "brain":
            try:
                model = resolve._read_env_key(secrets.brain_env_path(self.env), model_key)
            except (OSError, UnicodeError):
                pass
        model = model or "small"
        module = "herdr_brain.stt" if self.role == "brain" else "agent_tts.stt.transcriber"
        # Import the runtime's exact cache-only API in its own interpreter.
        code = f"import sys; from {module} import model_is_cached; print('true' if model_is_cached(sys.argv[1]) else 'false')"
        offline = dict(self.env, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", PYTHONDONTWRITEBYTECODE="1")
        status, output = self.probe([self.python, "-c", code, model], env=offline)
        ok = status == 0 and output.strip() == "true"
        pull_module = "herdr_brain.stt" if self.role == "brain" else "agent_tts.stt.cli"
        assignments = [f"{model_key}={model}"]
        if self.role == "brain":
            data = self.env.get("XDG_DATA_HOME", str(Path(self.env["HOME"]) / ".local/share"))
            assignments += [f"AGENT_TTS_STT_MODEL={model}", f"PYTHONPATH={self.brain_src}",
                            f"HERDR_BRAIN_STT_PYTHON={Path(data) / 'herdr-tts/venv/bin/python'}"]
        repair = shlex.join(["env", *assignments, self.python, "-m", pull_module, "pull"])
        return Diagnosis("stt", CheckResult(ok, "STT model is locally cached" if ok
                          else "STT model unavailable locally or offline cache probe unavailable"), repair)


def report(rows, stdout, *, json_output=False):
    ok = all(row.result.ok for row in rows)
    if json_output:
        stdout.write(json.dumps({"status": "ok" if ok else "failed", "checks": [
            {"name": row.name, "ok": row.result.ok, "detail": row.result.detail,
             "remediation": None if row.result.ok else row.remediation} for row in rows]}) + "\n")
    else:
        for row in rows:
            stdout.write(f"{'PASS' if row.result.ok else 'FAIL'} {row.name}: {row.result.detail}"
                         + (f" — repair: {row.remediation}" if not row.result.ok else "") + "\n")
    return 0 if ok else 1


def capture_credentials(ctx):
    """Deliberate repair: capture only, never wizard preferences or marker.

    Unlike normal onboarding, an existing key does not suppress this explicit
    request. The same non-argv intake and atomic merge boundaries are reused.
    """
    value = prompts.read_secret("GLM API key", env=ctx.env, interactive=ctx.interactive)
    if not value:
        if ctx.interactive:
            raise StepAbort("no GLM key provided")
        raise MissingAnswer("GLM key via HERDR_ONBOARDING_SECRET_FD or HERDR_ONBOARDING_SECRET_FILE")
    ctx.register_secret(value)
    secrets.merge_env_file(secrets.brain_env_path(ctx.env), {"GLM_API_KEY": value})

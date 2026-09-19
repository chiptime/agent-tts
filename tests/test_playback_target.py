import pytest

from agent_tts import playback_target as pt


class TestResolveTarget:
    def test_flag_wins_over_env(self):
        assert pt.resolve_target("winhost", env={"AGENT_TTS_PLAYBACK": "local"}) == "winhost"

    def test_env_used_when_no_flag(self):
        assert pt.resolve_target(None, env={"AGENT_TTS_PLAYBACK": "wsl-ps"}) == "wsl-ps"
        assert pt.resolve_target("", env={"AGENT_TTS_PLAYBACK": "winhost"}) == "winhost"

    def test_default_is_local(self):
        assert pt.resolve_target(None, env={}) == "local"
        assert pt.resolve_target(None, env={"AGENT_TTS_PLAYBACK": ""}) == "local"

    def test_invalid_flag_raises(self):
        with pytest.raises(pt.InvalidPlaybackTarget):
            pt.resolve_target("bogus", env={})

    def test_invalid_env_raises(self):
        with pytest.raises(pt.InvalidPlaybackTarget):
            pt.resolve_target(None, env={"AGENT_TTS_PLAYBACK": "bogus"})

    def test_value_is_normalized(self):
        assert pt.resolve_target(" WinHost ", env={}) == "winhost"


class TestResolveTargetAuto:
    def _patch_wsl_with_powershell(self, monkeypatch):
        monkeypatch.setattr(
            pt.shutil,
            "which",
            lambda name: "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
        )
        monkeypatch.setattr(
            pt,
            "_proc_version_text",
            lambda: "Linux version 5.15.90.1-microsoft-standard-WSL2",
        )

    def test_auto_on_native_windows_is_local(self, monkeypatch):
        # Even with WSL markers and powershell.exe present, native Windows plays locally.
        monkeypatch.setattr(pt.sys, "platform", "win32")
        self._patch_wsl_with_powershell(monkeypatch)
        assert pt.resolve_target("auto", env={}) == "local"

    def test_auto_under_wsl_with_powershell_is_winhost(self, monkeypatch):
        monkeypatch.setattr(pt.sys, "platform", "linux")
        self._patch_wsl_with_powershell(monkeypatch)
        assert pt.resolve_target("auto", env={}) == "winhost"
        # WSL marker via env var instead of /proc/version.
        monkeypatch.setattr(pt, "_proc_version_text", lambda: "Linux version 5.15.0 (generic)")
        assert pt.resolve_target("auto", env={"WSL_DISTRO_NAME": "Ubuntu"}) == "winhost"

    def test_auto_under_wsl_without_powershell_is_local(self, monkeypatch):
        monkeypatch.setattr(pt.sys, "platform", "linux")
        monkeypatch.setattr(pt.shutil, "which", lambda name: None)
        monkeypatch.setattr(
            pt,
            "_proc_version_text",
            lambda: "Linux version 5.15.90.1-microsoft-standard-WSL2",
        )
        assert pt.resolve_target("auto", env={}) == "local"

    def test_auto_without_wsl_is_local(self, monkeypatch):
        monkeypatch.setattr(pt.sys, "platform", "linux")
        monkeypatch.setattr(
            pt.shutil,
            "which",
            lambda name: "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
        )
        monkeypatch.setattr(pt, "_proc_version_text", lambda: "Linux version 6.1.0-generic")
        assert pt.resolve_target("auto", env={}) == "local"

    def test_auto_env_value_resolves(self, monkeypatch):
        monkeypatch.setattr(pt.sys, "platform", "linux")
        self._patch_wsl_with_powershell(monkeypatch)
        assert pt.resolve_target(None, env={"AGENT_TTS_PLAYBACK": "auto"}) == "winhost"

    def test_auto_is_normalized(self, monkeypatch):
        monkeypatch.setattr(pt.sys, "platform", "linux")
        self._patch_wsl_with_powershell(monkeypatch)
        assert pt.resolve_target(" Auto ", env={}) == "winhost"

    def test_flag_still_wins_over_env(self, monkeypatch):
        monkeypatch.setattr(pt.sys, "platform", "linux")
        self._patch_wsl_with_powershell(monkeypatch)
        assert pt.resolve_target("local", env={"AGENT_TTS_PLAYBACK": "auto"}) == "local"

    def test_flag_auto_wins_over_env_target(self, monkeypatch):
        monkeypatch.setattr(pt.sys, "platform", "linux")
        self._patch_wsl_with_powershell(monkeypatch)
        # env says wsl-ps, but the "auto" flag takes precedence and resolves to winhost.
        assert pt.resolve_target("auto", env={"AGENT_TTS_PLAYBACK": "wsl-ps"}) == "winhost"

    def test_invalid_auto_variant_raises(self):
        with pytest.raises(pt.InvalidPlaybackTarget):
            pt.resolve_target("autos", env={})


class TestCandidateHosts:
    def test_explicit_env_host_wins(self, monkeypatch):
        monkeypatch.setattr(pt, "default_route_gateway", lambda: "10.9.8.7")
        assert pt.candidate_hosts(env={"AGENT_TTS_WINHOST_HOST": "192.168.1.50"}) == ["192.168.1.50"]

    def test_loopback_first_then_gateway(self, monkeypatch):
        monkeypatch.setattr(pt, "default_route_gateway", lambda: "172.20.144.1")
        assert pt.candidate_hosts(env={}) == ["127.0.0.1", "172.20.144.1"]

    def test_loopback_only_without_gateway(self, monkeypatch):
        monkeypatch.setattr(pt, "default_route_gateway", lambda: "")
        assert pt.candidate_hosts(env={}) == ["127.0.0.1"]


class TestGatewayParsing:
    def test_gateway_from_ip_route(self):
        text = "default via 172.20.144.1 dev eth0 proto kernel metric 20"
        assert pt.gateway_from_ip_route(text) == "172.20.144.1"
        assert pt.gateway_from_ip_route("") == ""
        assert pt.gateway_from_ip_route("10.0.0.0/8 dev eth0") == ""

    def test_gateway_from_proc_route(self):
        # Destination 00000000 marks the default route; the gateway is a
        # little-endian hex DWORD: 172.20.144.1 -> 01 90 14 AC.
        text = (
            "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
            "eth0\t00000000\t019014AC\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
            "eth0\t0009AC14\t00000000\t0001\t0\t0\t0\t000F0000\t0\t0\t0\n"
        )
        assert pt.gateway_from_proc_route(text) == "172.20.144.1"

    def test_default_route_gateway_falls_back_to_proc(self, monkeypatch):
        def boom(*args, **kwargs):
            raise FileNotFoundError("ip")

        monkeypatch.setattr(pt.subprocess, "run", boom)
        monkeypatch.setattr(pt, "_proc_route_gateway", lambda: "192.168.7.1")
        assert pt.default_route_gateway() == "192.168.7.1"


class TestWinhostSettings:
    def test_port_default_and_env_override(self):
        assert pt.winhost_port(env={}) == 7717
        assert pt.winhost_port(env={"AGENT_TTS_WINHOST_PORT": "9001"}) == 9001
        with pytest.raises(ValueError):
            pt.winhost_port(env={"AGENT_TTS_WINHOST_PORT": "abc"})

    def test_bind_default_and_env_override(self):
        assert pt.winhost_bind_host(env={}) == "0.0.0.0"
        assert pt.winhost_bind_host(env={"AGENT_TTS_WINHOST_BIND": "127.0.0.1"}) == "127.0.0.1"

    def test_connect_timeout_is_bounded(self):
        assert pt.CONNECT_TIMEOUT_SEC == 0.5

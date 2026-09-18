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

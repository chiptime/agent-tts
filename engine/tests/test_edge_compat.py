import edge_tts.communicate as communicate
import edge_tts.constants as constants
import edge_tts.drm as drm

from agent_tts import edge_compat


def test_apply_points_edge_tts_to_new_endpoint():
    edge_compat.apply()
    assert "api.msedgeservices.com" in constants.WSS_URL
    assert communicate.WSS_URL == constants.WSS_URL
    assert constants.TRUSTED_CLIENT_TOKEN in constants.WSS_URL
    assert edge_compat.apply() is False  # idempotent


def test_date_parser_is_locale_independent_and_accepts_numeric_zone():
    edge_compat.apply()
    ts = drm.DRM.parse_rfc2616_date("Mon, 12 Oct 2026 03:16:09 GMT")
    assert ts == 1791774969.0
    assert drm.DRM.parse_rfc2616_date("Mon, 12 Oct 2026 03:16:09 +0000") == ts
    assert drm.DRM.parse_rfc2616_date("not a date") is None
    assert drm.DRM.parse_rfc2616_date("") is None

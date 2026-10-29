"""Secret-mask module tests (S14).

Table tests per named detector: positives prove the credential never
survives and the hint placeholder appears; look-alike negatives pin the
ordinary text the mask must never touch (code, paths, git SHAs, UUIDs,
short values). Every string below is synthetic.
"""

from __future__ import annotations

import pytest

from herdr_brain.secrets_mask import (
    ENV_VAR,
    SPOKEN_PLACEHOLDER,
    mask_for_speech,
    mask_runs,
    mask_text,
    masking_enabled,
)

# Synthetic credentials (never real): long enough to clear every
# detector's minimum-length bar.
OPENAI_KEY = "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2"
OPENAI_PROJ_KEY = "sk-proj-" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6"
ANTHROPIC_KEY = "sk-ant-api03-" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6"
GITHUB_PAT = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
GITHUB_OAUTH = "gho_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
GITHUB_SERVER = "ghs_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
GITHUB_FINE = "github_pat_" + "a1B2c3D4e5F6g7H8i9J0k1L2_2233"
SLACK_BOT = "xoxb-" + "1234567890-AbCdEfGhIj"
SLACK_APP = "xoxa-" + "1234567890-AbCdEfGhIj"
SLACK_USER = "xoxp-" + "1234567890-AbCdEfGhIj"
AWS_KEY = "AKIA" + "ABCDEFGHIJKLMNOP"
GOOGLE_KEY = "AIza" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8s9"
STRIPE_KEY = "sk_live_" + "a1B2c3D4e5F6g7H8i9J0k1L2"
NPM_KEY = "npm_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8s9T0"
JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
    "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
)
PEM_BLOCK = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEpAIBAAKCAQEA7x9z8m1n2v3x4z5y6w7u8t9s0r1q\n"
    "-----END RSA PRIVATE KEY-----"
)
PASSWORD = "hunter2secret"


class TestEnvSwitch:
    def test_masking_is_on_by_default(self, monkeypatch):
        monkeypatch.delenv(ENV_VAR, raising=False)
        assert masking_enabled() is True

    @pytest.mark.parametrize("value", ["off", "0", "false", "no", "OFF", "Off", " false "])
    def test_explicit_off_values_disable(self, monkeypatch, value):
        monkeypatch.setenv(ENV_VAR, value)
        assert masking_enabled() is False

    @pytest.mark.parametrize("value", ["on", "1", "true", "yes", "whatever"])
    def test_anything_else_stays_on(self, monkeypatch, value):
        monkeypatch.setenv(ENV_VAR, value)
        assert masking_enabled() is True

    def test_off_leaves_text_verbatim(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "off")
        assert mask_text(f"token {ANTHROPIC_KEY}") == f"token {ANTHROPIC_KEY}"
        assert mask_for_speech(f"token {ANTHROPIC_KEY}") == f"token {ANTHROPIC_KEY}"


class TestProviderKeys:
    """Positives: every known provider prefix is replaced by a stable
    placeholder that keeps the prefix as a hint."""

    CASES = [
        (ANTHROPIC_KEY, "sk-ant-…[masked]"),
        (OPENAI_KEY, "sk-…[masked]"),
        (OPENAI_PROJ_KEY, "sk-proj-…[masked]"),
        (GITHUB_PAT, "ghp_…[masked]"),
        (GITHUB_OAUTH, "gho_…[masked]"),
        (GITHUB_SERVER, "ghs_…[masked]"),
        (GITHUB_FINE, "github_pat_…[masked]"),
        (SLACK_BOT, "xoxb-…[masked]"),
        (SLACK_APP, "xoxa-…[masked]"),
        (SLACK_USER, "xoxp-…[masked]"),
        (AWS_KEY, "AKIA…[masked]"),
        (GOOGLE_KEY, "AIza…[masked]"),
        (STRIPE_KEY, "sk_live_…[masked]"),
        (NPM_KEY, "npm_…[masked]"),
    ]

    @pytest.mark.parametrize("secret,placeholder", CASES)
    def test_secret_is_replaced_with_hint(self, secret, placeholder):
        out = mask_text(f"export KEY={secret} done")
        assert secret not in out
        assert placeholder in out
        assert out.startswith("export KEY=")
        assert out.endswith(" done")

    def test_key_inside_a_sentence_is_masked(self):
        out = mask_text(f"la clave es {ANTHROPIC_KEY}, cuidado")
        assert ANTHROPIC_KEY not in out
        assert f"la clave es sk-ant-…[masked], cuidado" == out

    def test_two_different_keys_share_one_stable_placeholder(self):
        other = "sk-ant-api03-" + "z9y8x7w6v5u4t3s2r1q0p9o8n7m6l5k4"
        assert mask_text(ANTHROPIC_KEY) == mask_text(other) == "sk-ant-…[masked]"

    def test_masking_is_idempotent(self):
        once = mask_text(f"token={ANTHROPIC_KEY} y {JWT}")
        assert mask_text(once) == once


class TestProviderKeyLookAlikes:
    """Negatives: ordinary text, paths, shas and short values survive."""

    @pytest.mark.parametrize(
        "text",
        [
            "task-management and risk-analysis planning",
            "sk-abc sk-12345",
            "AKIA12345678",
            "akiaABCDEFGHIJKLMNOP",
            "AIza-short-1",
            "npm_install installs dependencies",
            "ghp_short",
            "xoxb-123",
            "5c5c93fa11b2c3d4e5f60718293a4b5c6d7e8f90",
            "550e8400-e29b-41d4-a716-446655440000",
            "/home/bruno/.config/herdr-brain/env",
            "sk_live_short",
        ],
    )
    def test_look_alikes_are_unchanged(self, text):
        assert mask_text(text) == text


class TestJwtDetector:
    def test_jwt_is_masked(self):
        out = mask_text(f"bearer was {JWT} ok")
        assert JWT not in out
        assert "[masked:jwt]" in out

    def test_two_segments_are_not_a_jwt(self):
        partial = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0"
        assert mask_text(partial) == partial

    def test_lone_eyj_word_is_not_a_jwt(self):
        assert mask_text("eyJshort") == "eyJshort"


class TestPemDetector:
    def test_private_key_block_is_masked(self):
        out = mask_text(f"before\n{PEM_BLOCK}\nafter")
        assert "MIIEpA" not in out
        assert "[masked:private-key]" in out
        assert out.startswith("before\n") and out.endswith("\nafter")

    def test_certificate_block_is_public_and_stays(self):
        cert = "-----BEGIN CERTIFICATE-----\nMIIBszCCAVgA\n-----END CERTIFICATE-----"
        assert mask_text(cert) == cert

    def test_openssh_key_blocks_are_masked(self):
        block = (
            "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\n"
            "-----END OPENSSH PRIVATE KEY-----"
        )
        assert "b3BlbnNzaC1" not in mask_text(block)


class TestBearerDetector:
    def test_bearer_token_is_masked(self):
        out = mask_text("Authorization: Bearer abc12345def67890.ghi_jkl")
        assert "abc12345def67890" not in out
        assert out == "Authorization: Bearer [masked:bearer]"

    def test_lowercase_header_is_masked(self):
        out = mask_text("authorization: bearer abc12345def67890xyz")
        assert out == "authorization: bearer [masked:bearer]"

    def test_basic_scheme_is_not_touched(self):
        line = "Authorization: Basic dXNlcjpwYXNz"
        assert mask_text(line) == line


class TestUrlCredentialDetector:
    def test_user_password_is_masked(self):
        out = mask_text("postgres://admin:hunter2@db.local:5432/app")
        assert "admin:hunter2" not in out
        assert out == "postgres://[masked:url-credentials]@db.local:5432/app"

    def test_url_without_credentials_is_unchanged(self):
        assert mask_text("https://github.com/org/repo") == "https://github.com/org/repo"

    def test_host_port_is_not_a_credential(self):
        assert mask_text("redis://host:6379") == "redis://host:6379"


class TestSecretAssignments:
    CASES = [
        ("PASSWORD=hunter2secret", "PASSWORD=…[masked]"),
        ('"api_key": "z9d8e7f6g5h4"', '"api_key": "…[masked]"'),
        ("client_secret: 's3cr3tvalue'", "client_secret: '…[masked]'"),
        (
            "aws_secret_access_key=AbCdEf1234567890/ghIjKlMnOpQrStUv",
            "aws_secret_access_key=…[masked]",
        ),
        ("OPENAI_API_KEY=" + OPENAI_KEY, "OPENAI_API_KEY=…[masked]"),
        ("GITHUB_TOKEN=" + GITHUB_PAT, "GITHUB_TOKEN=…[masked]"),
        ("private_key = 'not-a-real-block-but-long'", "private_key = '…[masked]'"),
        ("token=abc123def456", "token=…[masked]"),
    ]

    @pytest.mark.parametrize("raw,masked", CASES)
    def test_secret_named_assignment_is_masked(self, raw, masked):
        assert mask_text(raw) == masked

    @pytest.mark.parametrize(
        "text",
        [
            "max_tokens=8192",
            "next_token=1699999999",
            "token=abc",
            'password: "abc"',
            'password = "getInput()"',
            "DB_PASSWORD=${DB_PASS}",
            'password: "********"',
            "the secret: nothing special here",
            'secret: "true"',
            "tokens=many",
            # Unrelated numeric assignments keep their exact bytes.
            "code=1234567890",
            "build_number=2026102801",
            "app_version=1.2.3",
        ],
    )
    def test_ordinary_assignments_are_unchanged(self, text):
        assert mask_text(text) == text

    @pytest.mark.parametrize(
        "raw,masked",
        [
            ("password=1234567890", "password=…[masked]"),
            ("PASSWORD=20261028", "PASSWORD=…[masked]"),
            ('"api_key": "1234567890"', '"api_key": "…[masked]"'),
            ("client_secret: 998877665544", "client_secret: …[masked]"),
        ],
    )
    def test_strong_keys_mask_numeric_pins(self, raw, masked):
        """A pin is digits too: strong secret names mask numeric values,
        while the bare ``token`` counter stays readable."""
        assert mask_text(raw) == masked

    def test_weak_token_key_keeps_numeric_values(self):
        assert mask_text("token=1234567890") == "token=1234567890"


class TestCombinedDetection:
    def test_all_detector_families_in_one_screen(self):
        screen = "\n".join(
            [
                f"export ANTHROPIC_KEY={ANTHROPIC_KEY}",
                f"auth: Authorization: Bearer {JWT}",
                f"db=postgres://admin:{PASSWORD}@db.local:5432/app",
                f"jwt {JWT}",
                PEM_BLOCK,
                "normal line 80% ok",
            ]
        )
        out = mask_text(screen)
        for secret in (ANTHROPIC_KEY, JWT, PASSWORD, PEM_BLOCK):
            assert secret not in out
        assert "normal line 80% ok" in out
        assert "sk-ant-…[masked]" in out
        assert "[masked:jwt]" in out
        assert "[masked:url-credentials]" in out
        assert "[masked:private-key]" in out


class TestSpokenMasking:
    def test_raw_secret_reads_naturally(self):
        assert mask_for_speech(f"la clave {ANTHROPIC_KEY} caducó") == (
            f"la clave {SPOKEN_PLACEHOLDER} caducó"
        )

    def test_display_placeholder_converts_to_spoken_form(self):
        # The replay path: an already-masked display string is spoken
        # as the natural phrase, never spelled out.
        assert mask_for_speech("clave sk-ant-…[masked] aquí") == (
            f"clave {SPOKEN_PLACEHOLDER} aquí"
        )
        assert mask_for_speech("[masked:jwt]") == SPOKEN_PLACEHOLDER

    def test_ordinary_text_is_unchanged(self):
        assert mask_for_speech("todo verde, 80% disco") == "todo verde, 80% disco"


class TestUnhintedMasking:
    """Derived hint text (pending excerpt): the kind survives, the raw
    provider prefix never appears (verifier B6)."""

    def test_provider_prefix_is_dropped(self):
        assert mask_text(f"allow {ANTHROPIC_KEY}", hinted=False) == "allow [masked]"

    def test_structured_placeholders_keep_their_kind(self):
        assert mask_text(f"jwt {JWT}", hinted=False) == "jwt [masked:jwt]"

    def test_assignments_keep_the_key_name(self):
        assert mask_text("token=abc123def456", hinted=False) == "token=…[masked]"

    def test_hinted_mode_still_carries_the_prefix(self):
        assert mask_text(f"allow {ANTHROPIC_KEY}") == f"allow sk-ant-…[masked]"


class TestMaskRuns:
    def test_secret_split_across_two_runs_is_masked(self):
        line = [
            {"t": "export KEY=", "fg": "#00cd00"},
            {"t": ANTHROPIC_KEY, "b": True},
        ]
        out = mask_runs([line])
        assert len(out) == 1
        joined = "".join(run["t"] for run in out[0])
        assert ANTHROPIC_KEY not in joined
        assert "export KEY=sk-ant-…[masked]" == joined
        # Styles survive: the prefix keeps its colour, the placeholder
        # carries the style of the run where the secret started.
        assert out[0][0].get("fg") == "#00cd00"
        assert out[0][1].get("b") is True

    def test_secret_spanning_three_runs(self):
        line = [
            {"t": "a sk-ant-api", "fg": "#ff0000"},
            {"t": "03-", "fg": "#ff0000"},
            {"t": ANTHROPIC_KEY[len("sk-ant-api03-"):] + " b", "fg": "#ff8700"},
        ]
        out = mask_runs([line])
        joined = "".join(run["t"] for run in out[0])
        assert "sk-ant-api" not in joined
        assert joined == "a sk-ant-…[masked] b"

    def test_line_without_secrets_is_returned_untouched(self):
        line = [{"t": "plain", "b": True}, {"t": " line"}]
        out = mask_runs([line])
        assert out[0] is line

    def test_multiline_payload_masks_each_line(self):
        runs = [
            [{"t": f"ok {OPENAI_KEY}"}],
            [{"t": "untouched"}],
        ]
        out = mask_runs(runs)
        assert "sk-…[masked]" in out[0][0]["t"]
        assert out[1] is runs[1]

    def test_multiline_pem_collapses_across_lines(self):
        """A PEM block spanning lines is masked as ONE placeholder even
        when colour splits its body (verifier B6)."""
        pem = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEpAIBAAKCAQEA7x9z8m1n2v3x4z5y6w7u8t9s0r1q\n"
            "-----END RSA PRIVATE KEY-----"
        )
        runs = [
            [{"t": "before"}],
            [{"t": "-----BEGIN RSA PRIVATE KEY-----"}],
            [{"t": "MIIEpA", "fg": "#ff0000"}, {"t": "IBAAKCAQEA7x9z", "fg": "#ff0000"}],
            [{"t": "-----END RSA PRIVATE KEY-----"}],
            [{"t": "after", "b": True}],
        ]
        out = mask_runs(runs)
        joined = "\n".join("".join(run["t"] for run in line) for line in out)
        assert "MIIEpA" not in joined
        assert "PRIVATE KEY" not in joined
        assert "[masked:private-key]" in joined
        # Untouched lines keep their content and styles exactly (the
        # crossing path rebuilds objects, so compare by value).
        assert out[0] == [{"t": "before"}]
        assert out[-1] == [{"t": "after", "b": True}]

    def test_off_switch_leaves_runs_identical(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "off")
        runs = [[{"t": f"key {ANTHROPIC_KEY}"}]]
        assert mask_runs(runs) is runs


class TestOrdinaryTextUnchanged:
    SCREEN = (
        "$ cd /repo && git log --oneline -3\n"
        "5c5c93f refactor: drop naming\n"
        "uuid 550e8400-e29b-41d4-a716-446655440000\n"
        "warn disk 80% free\n"
        "def get_password(self): return self._ask()\n"
        "max_tokens=8192\n"
    )

    def test_realistic_screen_is_unchanged(self):
        assert mask_text(self.SCREEN) == self.SCREEN

"""Tests for the secrets/credentials redactor (agent_tts.redact)."""

import pytest

from agent_tts.cleaner import clean_agent_text
from agent_tts.redact import redact_secrets
from agent_tts import redact_secrets as package_exported_redact_secrets

OPENAI_KEY = "sk-4f9a2c8e1b7d3f6a9c2e5b8d"
OPENAI_PROJ_KEY = "sk-proj-9f8e7d6c5b4a3f2e1d0c9b8a-_7x6z"
GITHUB_CLASSIC = "ghp_0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d"
GITHUB_PAT = "github_pat_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"
GITLAB_PAT = "glpat-1a2b3c4d5e6f7g8h9i0j2k3l4m"
AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
SLACK_BOT = "xoxb-fake-token-not-real-123456"
JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
    "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
)
SHA256_HASH = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
PEM_BLOCK = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEpAIBAAKCAQEA7ysK9x1mZQpT3vXwLd8JhR2NcUfYb5GaOe6IiS0Dk\n"
    "Pq7RtUvWxYz0123456789ABCDEFGHIJKLMNopqrstuvwxyz+/==\n"
    "-----END RSA PRIVATE KEY-----"
)


# ─── Token families ──────────────────────────────────────────

@pytest.mark.parametrize(
    "secret",
    [OPENAI_KEY, OPENAI_PROJ_KEY],
    ids=["legacy", "proj"],
)
def test_openai_keys_redacted(secret):
    redacted = redact_secrets(f"the key is {secret} ok")
    assert secret not in redacted
    assert "[clave de API omitida]" in redacted


@pytest.mark.parametrize(
    "prefix",
    ["ghp_", "gho_", "ghs_", "ghu_"],
)
def test_github_classic_tokens_redacted(prefix):
    secret = prefix + "0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d"
    redacted = redact_secrets(f"token {secret} leaked")
    assert secret not in redacted
    assert "[token redactado]" in redacted


def test_github_fine_grained_pat_redacted():
    redacted = redact_secrets(f"pat: {GITHUB_PAT}")
    assert GITHUB_PAT not in redacted
    assert "[token redactado]" in redacted


def test_gitlab_pat_redacted():
    redacted = redact_secrets(f"gitlab {GITLAB_PAT} expired")
    assert GITLAB_PAT not in redacted
    assert "[token redactado]" in redacted


def test_aws_access_key_redacted():
    redacted = redact_secrets(f"aws id {AWS_KEY} in env")
    assert AWS_KEY not in redacted
    assert "[clave de acceso omitida]" in redacted


@pytest.mark.parametrize(
    "prefix",
    ["xoxb-", "xoxa-", "xoxp-", "xoxr-", "xoxs-"],
)
def test_slack_tokens_redacted(prefix):
    secret = prefix + "1234567890-abcdefghijklmnop"
    redacted = redact_secrets(f"slack {secret} here")
    assert secret not in redacted
    assert "[token redactado]" in redacted


def test_jwt_redacted():
    redacted = redact_secrets(f"auth header {JWT} rejected")
    assert JWT not in redacted
    assert "[token redactado]" in redacted


def test_standalone_bearer_token_redacted():
    redacted = redact_secrets("curl -H 'Bearer abc123XYZ456' api")
    assert "abc123XYZ456" not in redacted
    assert "[token redactado]" in redacted


def test_authorization_header_collapses_to_single_placeholder():
    redacted = redact_secrets(f"request sent Authorization: Bearer {JWT} and failed")
    assert JWT not in redacted
    assert redacted.count("[token redactado]") == 1


@pytest.mark.parametrize(
    "line",
    [
        "api_key=abc123def456",
        "apikey: zz99xx88yy77ww66",
        "secret = p@ssw0rd!",
        'password="hunter2secret"',
        "passwd:'letmein9x'",
        "token: ghx_value1234",
    ],
)
def test_generic_assignments_redacted(line):
    redacted = redact_secrets(line)
    assert "[clave omitida]" in redacted
    # The key name and separator survive for context.
    assert "=" in redacted or ":" in redacted


@pytest.mark.parametrize(
    "line",
    ["API_KEY=abc123def456", "Password: hunter2secret"],
)
def test_generic_assignments_case_insensitive(line):
    assert "[clave omitida]" in redact_secrets(line)


def test_pem_private_key_block_redacted_multiline():
    text = f"config used:\n{PEM_BLOCK}\nthat is all"
    redacted = redact_secrets(text)
    assert "BEGIN RSA PRIVATE KEY" not in redacted
    assert "MIIEpAIBAAKCAQEA7ysK9" not in redacted
    assert "[clave privada omitida]" in redacted


def test_long_hex_hash_redacted():
    redacted = redact_secrets(f"digest {SHA256_HASH} computed")
    assert SHA256_HASH not in redacted
    assert "[hash redactado]" in redacted


# ─── Multi-secret text ───────────────────────────────────────

def test_multiple_secrets_in_single_text_all_redacted():
    text = (
        f"Deploy failed because {OPENAI_KEY} was revoked, "
        f"then {GITHUB_CLASSIC} hit the rate limit, "
        f"and AWS key {AWS_KEY} is missing; jwt: {JWT}."
    )
    redacted = redact_secrets(text)
    for secret in (OPENAI_KEY, GITHUB_CLASSIC, AWS_KEY, JWT):
        assert secret not in redacted


# ─── False-positive guards ───────────────────────────────────

@pytest.mark.parametrize(
    "sha",
    ["a1b2c3d", "a1b2c3d4e5f6", "da39a3ee5e6b4b0d3255bfef95601890afd80709"],
    ids=["short-sha-7", "short-sha-12", "full-sha1-40"],
)
def test_git_shas_are_never_redacted(sha):
    assert redact_secrets(f"commit {sha} fixes the crash") == f"commit {sha} fixes the crash"


def test_sketch_does_not_trigger_openai_rule():
    assert "sketch" in redact_secrets("the sketch shows the layout")


def test_tokenize_does_not_trigger_token_rule():
    text = "we tokenize the plan first"
    assert redact_secrets(text) == text


def test_bearer_in_prose_not_redacted():
    text = "Bearer authentication is standard"
    assert redact_secrets(text) == text


def test_prose_with_credential_words_not_redacted():
    text = "the secret ingredient is love and the token budget is 900"
    assert redact_secrets(text) == text


# ─── Package export ──────────────────────────────────────────

def test_redact_secrets_exported_from_package():
    assert package_exported_redact_secrets is redact_secrets
    assert package_exported_redact_secrets(f"key {OPENAI_KEY}").startswith("key [clave")


# ─── Cleaner pipeline integration ────────────────────────────

def test_clean_agent_text_redacts_every_credential_family():
    raw = (
        f"Deployment log: OPENAI_API_KEY={OPENAI_KEY} was accepted, "
        f"github token {GITHUB_CLASSIC} rotated, aws {AWS_KEY} still valid, "
        f"and this jwt leaked: {JWT}\n"
        f"Also the digest was {SHA256_HASH}."
    )
    cleaned = clean_agent_text(raw, pre_extracted=True)
    for secret in (OPENAI_KEY, GITHUB_CLASSIC, AWS_KEY, JWT, SHA256_HASH):
        assert secret not in cleaned
    assert "redactado" in cleaned or "omitida" in cleaned


def test_clean_agent_text_summarize_path_also_redacts():
    # The --tldr branch returns the summarizer output, which echoes input
    # sentences: redaction must run before it.
    raw = (
        f"The api_key=abc123def456 leaked into the log. "
        f"Deployment completed successfully with the {GITHUB_CLASSIC} token."
    )
    summary = clean_agent_text(raw, summarize=True, pre_extracted=True)
    assert "abc123def456" not in summary
    assert GITHUB_CLASSIC not in summary
    assert "omitida" in summary or "redactado" in summary

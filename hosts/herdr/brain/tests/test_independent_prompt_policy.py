"""S11 provider-envelope policy, not live-model adherence."""
from tests.test_llm import make_brain, text_response


def test_provider_envelope_keeps_honesty_and_full_choices(settings, make_stub):
    stub = make_stub()
    llm, _ = make_brain(settings, stub, [text_response("No verified result yet.")])
    llm.ask("Report progress without guessing")
    sent = llm._client.create_kwargs[0]["messages"][0]
    assert sent["role"] == "system"
    for required in (
        "At most 3 short sentences", "lead with problem impact",
        "tests passed or failed only when verified", "required decision",
        "Explicitly offer the recommended next action", "do not invent test results",
        "NEVER invent or guess transcript or screen content",
        "question and every option exactly as given",
        "required verbatim dialog keeps every option",
        "never narrate the send as done",
    ):
        assert required in sent["content"]
    assert "stretch the sentence limit" not in sent["content"]
    assert stub.prompt_calls == stub.key_calls == []


def test_rejected_conversation_preserves_data_bytes(tmp_path):
    from hashlib import sha256
    from tests.test_speech_cancel import FakeLLM, FakeTTS, make_client
    from herdr_brain.speech import SpeechRegistry
    llm, tts, registry = FakeLLM(), FakeTTS(), SpeechRegistry()
    client = make_client(llm, tts, registry, tmp_path / "audio")
    def hashes():
        return {str(p.relative_to(tmp_path)): sha256(p.read_bytes()).hexdigest()
                for p in tmp_path.rglob("*") if p.is_file()}
    before = hashes()
    result = client.post("/ask", json={"text": "Question", "conversation": True})
    assert result.status_code == 422
    assert result.content == b'{"detail":"conversation requires speech_request_id and speech_cancel_token"}'
    assert hashes() == before
    assert llm.calls == tts.calls == []
    assert len(registry) == 0

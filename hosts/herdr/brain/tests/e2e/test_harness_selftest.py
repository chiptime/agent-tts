"""VS0.6 harness self-test: prove the E2E setup reproduces real audio.

A passing run certifies the whole observable chain the voice-stack E2E suite
depends on: Chromium fetched a real MP3 over HTTP, decoded it through its real
media pipeline, played it with a genuine ``play()`` call under the
``--autoplay-policy=no-user-gesture-required`` launch flag, and the platform
itself fired the ``ended`` event when playback finished. Later voice-stack
tests can build on this harness without re-proving the pipeline.
"""

from typing import Any, Dict

from playwright.sync_api import Page


def test_real_audio_fixture_fires_ended(page: Page, audio_server: str) -> None:
    """Observable proved: the real media pipeline fires ``ended`` for the
    committed fixture.

    The test injects a minimal page whose ``<audio>`` element loads
    ``sample.mp3`` from the local HTTP fixture server. Decode is proven by
    awaiting ``canplay`` (readyState >= 3) before anything else. Playback is
    started with a real ``await audio.play()`` (never stubbed), and success is
    accepted only from the browser's own ``ended`` event. A hard 30-second
    JavaScript-side timeout rejects with ``ended-timeout`` so a broken pipeline
    fails the test instead of hanging the run forever.
    """
    page.set_content(
        f"""
        <!DOCTYPE html>
        <html>
            <head><title>VS0.6 harness self-test</title></head>
            <body>
                <audio id="a" src="{audio_server}/sample.mp3" preload="auto"></audio>
            </body>
        </html>
        """
    )

    result: Dict[str, Any] = page.evaluate(
        """
        async () => {
            const audio = document.getElementById('a');

            // Prove the fixture bytes were fetched and actually decoded by
            // the media pipeline before attempting playback.
            if (audio.readyState < 3) {
                await new Promise((resolve, reject) => {
                    audio.addEventListener('canplay', resolve, { once: true });
                    audio.addEventListener('error', () => reject(new Error(
                        'audio-load-error:' + (audio.error ? audio.error.code : 'unknown')
                    )), { once: true });
                });
            }

            // Real playback start; the autoplay launch flag makes this
            // resolve without any user gesture.
            await audio.play();

            // Only the platform's own `ended` event completes the test; the
            // hard timeout turns a dead pipeline into a fast failure.
            await new Promise((resolve, reject) => {
                const timer = setTimeout(
                    () => reject(new Error('ended-timeout')),
                    30000
                );
                audio.addEventListener('ended', () => {
                    clearTimeout(timer);
                    resolve();
                }, { once: true });
            });

            return {
                ended: true,
                currentTime: audio.currentTime,
                duration: audio.duration,
                readyState: audio.readyState,
            };
        }
        """
    )

    assert result["ended"] is True
    assert result["duration"] > 0.5, f"unexpected duration: {result['duration']}"
    assert abs(result["currentTime"] - result["duration"]) < 0.5, (
        f"playback did not reach the end: "
        f"currentTime={result['currentTime']} duration={result['duration']}"
    )
    assert result["readyState"] >= 3, (
        f"readyState dropped below 3 at end of playback: {result['readyState']}"
    )

/* Announcement playback policy — PURE logic, no DOM (PRD
 * announcements-without-call, delivery 1).
 *
 * The announcer owns the PRD §C decision table for transition
 * announcements arriving over SSE while the page is open, and the
 * ok ⇄ blocked audio state machine:
 *
 *   ok      --(play() rejected)-->                  blocked
 *   blocked --(any user gesture / "🔊 Activar voz")--> ok
 *
 * Decision order in handle(announcement):
 *   1. falsy announcement        → no-op.
 *   2. muted (FR-06)             → timed 🔇 toast only; the mute always
 *                                  dominates, even in blocked state.
 *   3. blocked + audio (FR-05)   → PERSISTENT 🔊 toast (no auto-hide);
 *                                  the announcement is dropped, never
 *                                  queued — unlock() must not replay it.
 *   4. in call (FR-08)           → delegates to the existing playback
 *                                  pipeline (deps.play); blocked state
 *                                  does not apply to calls.
 *   5. audio_url present (FR-03) → deps.play(announcement) at arrival;
 *                                  a rejected/throwing play enters
 *                                  blocked and re-shows the text (FR-04).
 *   6. text-only                  → timed 🔊 toast, nothing to play.
 *
 * Async-safety contract: deps.play may return a promise; its rejection
 * is handled HERE (catch attached synchronously, never unhandled) and
 * a synchronous throw is treated exactly like a rejection. The
 * announcement text is never lost on either path — FR-04.
 *
 * Dual environment: browser global (window.Announce) and CommonJS for
 * node --test. Injected deps: play, showToast(text, durationMs, kind,
 * html), isMuted, isInCall, onBlockedChange(blocked).
 */

(function (global) {
  "use strict";

  var MUTED_TOAST_MS = 6000;   // matches the historical muted toast
  var TEXT_TOAST_MS = 6000;    // matches the historical text-only toast

  function createAnnouncer(deps) {
    deps = deps || {};
    var play = deps.play || function () {};
    var showToast = deps.showToast || function () {};
    var isMuted = deps.isMuted || function () { return false; };
    var isInCall = deps.isInCall || function () { return false; };
    var onBlockedChange = deps.onBlockedChange || null;

    var blocked = false;

    function isBlocked() {
      return blocked;
    }

    /* Timed toasts replicate today's openEvents calls EXACTLY — plain
     * (text, durationMs), no kind, no html mount — so routing the
     * transition branch through this module changes nothing observable
     * on the muted and text-only paths. */
    function showTimed(announcement, prefix, durationMs) {
      showToast(prefix + announcement.label + ": " + announcement.text,
        durationMs);
    }

    /* The persistent blocked toast matches pumpAudio's announcement
     * toast shape (kind null + html through the safe toast mount):
     * FR-04 keeps the text visible with the same formatted rendering
     * the playback toast would have used. */
    function showPersistent(announcement) {
      showToast(
        "🔊 " + announcement.label + ": " + announcement.text,
        undefined, null, announcement.html
      );
    }

    /* Notifies the affordance owner ("🔊 Activar voz", T4) only on real
     * state transitions — repeated rejections while already blocked
     * must not re-fire the callback. */
    function enterBlocked(announcement) {
      var changed = !blocked;
      blocked = true;
      if (changed && onBlockedChange) onBlockedChange(true);
      if (announcement) showPersistent(announcement);
    }

    function unlock() {
      if (!blocked) return;
      blocked = false;
      if (onBlockedChange) onBlockedChange(false);
    }

    /* FR-04 host path: the audio pipeline (pumpAudio) reports the
     * browser's play() rejection for an announcement it was asked to
     * play. Without an announcement payload it only flips the state —
     * there is no text to keep visible. */
    function onPlayRejected(announcement) {
      enterBlocked(announcement || null);
    }

    /* FR-03 with the async-safety contract: the returned promise (if
     * any) gets its rejection handled here, and a synchronous throw is
     * treated as a rejection so nothing escapes handle(). */
    function attemptPlay(announcement) {
      var pending;
      try {
        pending = play(announcement);
      } catch (err) {
        enterBlocked(announcement);
        return;
      }
      if (pending && typeof pending.catch === "function") {
        pending.catch(function () { enterBlocked(announcement); });
      }
    }

    function handle(announcement) {
      if (!announcement) return;
      if (isMuted()) {
        showTimed(announcement, "🔇 ", MUTED_TOAST_MS);
        return;
      }
      if (!isInCall() && blocked && announcement.audio_url) {
        /* Persistent toast: the text survives (FR-04) but the audio is
         * dropped for good (FR-05) — unlock applies to the NEXT one. */
        showPersistent(announcement);
        return;
      }
      if (announcement.audio_url) {
        attemptPlay(announcement);
        return;
      }
      showTimed(announcement, "🔊 ", TEXT_TOAST_MS);
    }

    return {
      handle: handle,
      onPlayRejected: onPlayRejected,
      unlock: unlock,
      isBlocked: isBlocked
    };
  }

  var api = { createAnnouncer: createAnnouncer };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    global.Announce = api;
  }
})(typeof window !== "undefined" ? window : globalThis);

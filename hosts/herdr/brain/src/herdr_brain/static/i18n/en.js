/* English dictionary — S4, doc 09 (L3: es+en in v1).
 *
 * A full sibling of es.js: the node suite pins that both catalogs
 * expose exactly the same key set, so a missing translation can never
 * silently fall back mid-screen. The two language-name labels stay
 * NATIVE on purpose (each names itself), and product-own nouns
 * (brain, herdr) are not translated. */

(function (global) {
  "use strict";

  var dict = {
    /* ---- common ---- */
    "common.loading": "Loading…",
    "common.send": "Send",
    "common.cancel": "Cancel",
    "common.discard": "Discard",

    /* ---- header ---- */
    "header.pane_picker_title": "Switch conversation",
    "header.connecting": "connecting…",
    "header.settings_title": "Settings",
    "header.mute_title": "Mute announcements",
    "header.voice_unlock": "🔊 Enable voice",

    /* ---- call ---- */
    "call.start": "📞 Call",
    "call.end": "🔴 Hang up",
    "call.pause": "⏸ Pause",
    "call.resume": "▶ Resume",
    "call.stop_audio": "■ Stop audio",
    "call.stop": "■ Stop",
    "call.stop_armed": "Stop? Tap again",
    "call.stopped": "Turn interrupted",
    "call.title": "Call with brain",
    "call.minimize_title": "Minimize the call (it keeps running)",
    "call.cut": "Call was cut",
    "call.cut_recall": "Call was cut — call again?",
    "call.confirm_pill": "Confirm ▲",
    "call.confirm": "Confirm",
    "call.fallback_placeholder": "Type instead…",

    /* ---- interim strip / ghost ---- */
    "strip.listening": "● Listening",
    "strip.recording": "● Recording — speak",
    "strip.transcribing": "⏳ Understanding…",
    "strip.thinking": "⏳ Thinking…",
    "strip.speaking": "🔊 Speaking",
    "strip.paused": "⏸ Microphone paused",
    "strip.thinking_lower": "⏳ Thinking…",
    "strip.transcribing_lower": "⏳ transcribing…",
    "strip.paused_lower": "⏸ microphone paused",
    "strip.sending": "📤 Sending…",
    "strip.synthesizing": "⏳ Synthesizing…",
    "ghost.hint": "Say something — I'm listening",

    /* ---- status chip ---- */
    "status.nobody": "nobody",
    "status.working": "working",
    "status.blocked": "blocked",
    "status.done": "done",

    /* ---- reply composer ---- */
    "reply.placeholder": "Reply to the agent…",
    "reply.aria": "Reply to the agent",
    "reply.sent": "Reply sent to the agent",
    "reply.sending": "Sending the reply…",
    "reply.steering": "Injecting into the running turn…",
    "reply.destructive_confirm": "This send looks destructive: {match}.\n\n{text}\n\nSend anyway?",
    "reply.fail": "Could not send: {detail}",
    "reply.fail_offline": "Could not send: no connection to the brain",
    "reply.followup_fail": "Could not complete: {detail}",
    "reply.followup_fail_offline": "Could not complete: no connection to the brain",
    "receipt.submit": "Send now",
    "stop.fail": "Could not stop: {detail}",
    "stop.fail_offline": "Could not stop: no connection to the brain",

    /* ---- brain message (S13) ---- */
    "msg.button": "✉ Message",
    "msg.placeholder": "Message for the agent…",
    "msg.aria": "Operator message to the agent",
    "msg.delivered": "Message delivered to the agent",
    "msg.queued": "Message on its way to the agent",
    "msg.held": "The agent is busy — it will be delivered when free (max 60 min)",
    "msg.inbox": "The agent never went idle; the message stayed in its inbox",
    "msg.generic": "Message {state}",
    "msg.refused": "Not sent: {reason}",
    "msg.fail": "Could not send the message: {detail}",
    "msg.fail_offline": "Could not send the message: no connection to the brain",

    /* ---- keys / dialogs ---- */
    "keys.hint": "The agent is waiting — answer with keys:",
    "keys.sent": "Sent: {label} · waiting for the agent…",
    "keys.fail": "Could not send the key: {detail}",
    "keys.fail_offline": "Could not send the key: no connection to the brain",
    "keys.group_aria": "Keys to answer the dialog",
    "keys.dialog_aria": "Agent dialog",
    "keys.left": "Left",
    "keys.right": "Right",
    "keys.up": "Up",
    "keys.down": "Down",
    "keys.tab": "Tab",
    "keys.esc": "Escape",
    "keys.enter": "Enter",
    "keys.d1": "One",
    "keys.d2": "Two",
    "keys.d3": "Three",
    "keys.d4": "Four",
    "keys.d5": "Five",
    "keys.d6": "Six",
    "keys.d7": "Seven",
    "keys.d8": "Eight",
    "keys.d9": "Nine",
    "keys.d0": "Zero",
    "dialogs.group_aria": "Dialog options",
    "dialogs.unread_title": "I can't read this dialog",
    "dialogs.unread_body": "The agent {agent} is waiting for an answer I can't read with confidence. Use the keypad below — nothing is ever sent blind.",
    "dialogs.answered": "Answered: {label}",
    "dialogs.answered_plain": "Answered.",
    "dialogs.changed": "The dialog changed before answering; nothing was sent. Look at the screen and answer the new dialog, or use the keypad.",
    "dialogs.option_gone": "That option is no longer in the dialog; use the keypad.",
    "dialogs.fail": "Could not answer the dialog; use the keypad.",

    /* ---- permission dialog ---- */
    "permission.once": "Allow once",
    "permission.always": "Allow always",
    "permission.reject": "Deny",
    "permission.once_toast": "Permission granted once",
    "permission.always_toast": "Permission granted always",
    "permission.reject_toast": "Permission denied",
    "permission.sent_toast": "Permission sent",

    /* ---- cockpit tabs ---- */
    "tabs.aria": "Agent views",
    "tabs.agent": "Agent view",
    "tabs.screen": "Screen",
    "tabs.history": "History",
    "tabs.terminal": "Terminal",

    /* ---- glance ---- */
    "glance.empty": "No agent conversation yet",
    "glance.empty_no_session": "No agent conversation yet. Check the Screen tab.",
    "glance.label": "Agent view",
    "glance.replay": "🔊 Listen",
    "glance.replay_from": "Listen from sentence {n}",
    "glance.replay_piece": "Listen to fragment {n} of {total}",
    "glance.empty_no_identified": "The agent session has not been identified. Check the Screen tab.",
    "glance.more": "See more",
    "glance.more_aria": "Load earlier turns of the call",

    /* ---- panels ---- */
    "panel.screen": "screen",
    "panel.terminal": "live terminal",
    "panel.mirror_aria": "Mirror of the agent's terminal",
    "panel.terminal_empty": "No terminal to show yet",
    "history.more": "Load more",
    "history.empty": "No history for this agent yet",
    "history.aria": "Full agent history",
    "history.fail": "Could not load the history — reopen the tab to retry",

    /* ---- pending banner ---- */
    "pending.permission": "Asks permission",
    "pending.error": "Agent error",
    "pending.question": "The agent asks",
    "pending.attention": "Needs your attention",
    "pending.fallback_excerpt": "look at the screen",
    "pending.agent_fallback": "An agent",
    "pending.many": "{count} agents wait for your OK — tap to attend",
    "pending.one": "{name} waits for your OK — tap to see it",

    /* ---- herd / conversation sheet ---- */
    "herd.disappeared": "The selected agent vanished — falling back to the focused one.",
    "herd.recent": "Last 30 min",
    "herd.g_blocked": "Blocked",
    "herd.g_working": "Working",
    "herd.g_done": "Done",
    "herd.g_idle": "Idle",
    "herd.g_other": "Others",
    "herd.new_conversation": "＋ New conversation",
    "herd.new_started": "New conversation started",
    "herd.new_failed": "Could not restart the conversation on the server.",
    "conv.aria": "Conversations",
    "conv.title": "Conversations",
    "conv.close": "Close",

    /* ---- pending sheet ---- */
    "pendsheet.aria": "Pending",
    "pendsheet.title": "Pending",
    "pendsheet.close": "Close",
    "pendsheet.empty": "Nothing pending",
    "pendsheet.title_count": "📋 Pending ({count})",
    "pendsheet.title_open": "📋 Pending (",
    "pendsheet.title_close": ")",
    "pendsheet.listen_aria": "Listen to this notice again",
    "pendsheet.announced": "✓ Announced",
    "pendsheet.announced_aria": "Mark as announced",
    "pendsheet.discard": "✕ Discard",
    "pendsheet.discard_aria": "Discard this pending item",
    "pendsheet.save_failed": "Could not save the pending list on this device (private mode or no space?). The list is NOT persistent until reload.",
    "pendsheet.timeout": "⏱ Action cancelled by timeout",

    /* ---- settings sheet ---- */
    "settings.aria": "Settings",
    "settings.title": "Settings",
    "settings.close_title": "Close settings",
    "settings.voice_engine": "Voice engine",
    "settings.voice_server": "Voice: Server",
    "settings.voice_browser": "Voice: Browser",
    "settings.voice_server_title": "Server engine: your voice is transcribed on the server (works with BT headsets). Tap to switch to Browser.",
    "settings.voice_browser_title": "Browser engine: browser speech recognition (Web Speech). Tap to switch to Server.",
    "settings.theme": "Theme",
    "settings.theme_auto": "Theme: Auto",
    "settings.theme_light": "Theme: Light",
    "settings.theme_dark": "Theme: Dark",
    "settings.hint": "Server: transcribes in the back with whisper (robust). Browser: Chrome Web Speech (faster, less reliable on Android).",
    "settings.pending": "Pending",
    "settings.pending_open_aria": "See pending notices",
    "settings.pending_open": "🔔 Pending ({count})",
    "settings.language": "Language",
    "settings.language_es": "Idioma: Español",
    "settings.language_en": "Language: English",
    "settings.font": "Text size",
    "settings.font_small": "Text: Small",
    "settings.font_normal": "Text: Normal",
    "settings.font_large": "Text: Large",
    "settings.zen": "Zen mode",
    "settings.zen_on": "Zen mode: On",
    "settings.zen_off": "Zen mode: Off",
    "settings.zen_exit": "✕ Exit Zen",
    "settings.zen_exit_title": "Exit Zen mode",
    "settings.haptics": "Haptics",
    "settings.haptics_on": "Haptics: On",
    "settings.haptics_off": "Haptics: Off",

    /* ---- voice / mic ---- */
    "voice.blocked": "Microphone BLOCKED: grant the permission in the browser (⋮ menu → Site settings → Microphone) and call again.",
    "voice.keyboard_fallback": "Microphone blocked — use the keyboard or grant the permission in the browser.",
    "voice.engine_unavailable": "This browser does not support the Server engine — use the Browser engine.",
    "voice.browser_missing": "The Browser engine is not available in this browser.",
    "voice.server_missing": "The Server engine is not available in this browser.",
    "voice.record_failed": "Could not open the recording — try the Browser engine.",
    "voice.mic_failed": "Could not open the microphone ({name}) — try the Browser engine.",
    "voice.hearing": "I can't hear you",
    "voice.hearing_hint": "I can't hear you — check the mic",
    "voice.rec_fails": "Speech recognition keeps failing — switched to keyboard.",
    "voice.no_stt": "No speech recognition or microphone in this browser — keyboard mode.",
    "voice.transcribe_failed": "Could not transcribe (HTTP {status}): {detail}",
    "voice.audio_send_failed": "Could not send the audio to the server — {detail}.",
    "voice.network_error": "network error",
    "voice.not_understood": "I didn't understand the audio — try again.",

    /* ---- brain errors ---- */
    "brain.not_configured": "The brain is not configured: GLM_API_KEY is missing on the server. Set it in the service environment (for example a shell profile or an env file outside this repository) and restart the service.",
    "brain.ask_failed": "The question failed (HTTP {status}). Try again.",
    "brain.timeout": "The brain took too long — try again.",
    "brain.network": "Network error talking to the brain.",
    "brain.context_failed": "Could not confirm the call context — try again.",

    /* ---- tts ---- */
    "tts.synth_failed": "Could not synthesize the audio (HTTP {status}).",
    "tts.synth_failed_part": "Could not synthesize part of the audio (HTTP {status}).",
    "tts.synth_retry": "Could not synthesize the audio — check the connection.",
    "tts.synth_retry_part": "Could not synthesize part of the audio — check the connection.",
    "tts.piece_failed": "Could not play part {n} of the audio — try again.",
    "tts.segments_degraded": "Could not keep streaming the voice in segments; playback is degraded.",
    "tts.server_ended": "The server voice ended ({state}).",
    "tts.cancel_unconfirmed": "Could not confirm with the server that the voice was cancelled; local audio already stopped.",
    "tts.cancel_rejected": "The server refused the voice cancellation ({state}).",
    "tts.read_aloud": "Read aloud",
    "tts.read_this": "Read this answer aloud",

    /* ---- announcements ---- */
    "announce.who": "🔊 notice · {label}",
    "announce.label_default": "agent",
    "announce.agent": "agent",
    "announce.toast": "🔊 {label}: {text}",

    /* ---- approval gate card ---- */
    "approval.agent_row": "Agent",
    "approval.title_row": "Title",
    "approval.task_row": "Task",
    "approval.dict_new": "🎙 Dictate the new text — «yes» sends, «no» cancels",
    "approval.dict_task": "🎙 Dictate the new task — «yes» creates the panel, «no» cancels",
    "approval.save": "✓ Save",
    "approval.discard": "✕ Discard",
    "approval.send": "✓ Send",
    "approval.edit": "✏️ Edit",
    "approval.redict": "🎙 Re-dictate",
    "approval.cancel": "✕ Cancel",
    "approval.sending": "📤 Sending…",
    "approval.expired": "⏱ Time ran out",
    "approval.create": "🆕 Create panel",
    "approval.to": "📤 To: {target}",
    "approval.timeout_turn": "⏱ Action cancelled by timeout",

    /* ---- device pairing ---- */
    "pair.paired_status": "📱 Paired as «{name}»",
    "pair.expired_status": "🔒 Token expired: pair again",
    "pair.blocked_status": "🔒 Locked: device not paired",
    "pair.open_status": "Not paired (reads and writes stay open)",
    "pair.write_required": "This brain requires a paired device to write. Ask for a code with `herdr-brain pair` on the machine and pair here.",
    "pair.token_expired": "This device's token expired. Ask for a new code with `herdr-brain pair` and pair again (or ask the operator to extend the deadline with `devices set-expiry`).",
    "pair.code_placeholder": "Code (8 characters)",
    "pair.label_placeholder": "Device name",
    "pair.submit": "Pair",
    "pair.this_device": " (this device)",
    "pair.expires": " · expires {when}",
    "pair.revoke": "Revoke",
    "pair.unlink": "Unlink",
    "pair.no_pending": "No pairing is pending — ask for a new code.",
    "pair.code_expired": "The code expired (it lasts 10 minutes) — ask for another.",
    "pair.exhausted": "Too many failed attempts — ask for another code.",
    "pair.bad_code": "Wrong code — check the characters.",
    "pair.duplicate_label": "That name is already in use — choose another.",
    "pair.generic_fail": "Could not pair ({reason}).",
    "pair.paired_toast": "Device paired — writing is now enabled",

    /* ---- push card ---- */
    "push.title": "Push notices",
    "push.no_keys": "The server could not create its push keys — check `herdr-brain doctor`.",
    "push.blocked": "The browser blocks notifications; enable them in the site settings and try again.",
    "push.active": "🔔 Notices active on this device",
    "push.off": "No push notices on this device",
    "push.reenable": "Re-enable",
    "push.enable": "Enable",
    "push.test": "Test",
    "push.disable": "Disable",
    "push.enabled_toast": "Push notices enabled",
    "push.enable_failed": "Could not enable the notices: {error}",
    "push.disabled_toast": "Push notices disabled",
    "push.disable_failed": "Could not disable the notices — try again",
    "push.test_sent": "Test notification sent ({sent})",
    "push.test_none": "Nothing was sent — is another device subscribed?",
    "push.test_failed": "The test never reached the brain — no connection",

    /* ---- receipts (S12) ---- */
    "receipts.outcome_effective": "Sent — the agent took the turn",
    "receipts.outcome_sent": "Sent — no confirmation yet",
    "receipts.outcome_typed_not_submitted": "Left typed, not sent",
    "receipts.outcome_discarded": "Discarded",
    "receipts.outcome_refused": "The agent did not accept the text",
    "receipts.outcome_unknown": "I can't confirm whether it was sent",
    "receipts.speech_effective": "Sent. The agent took the turn.",
    "receipts.speech_sent": "Sent. No confirmation yet.",
    "receipts.speech_typed_not_submitted": "Left typed, not sent. You can send or discard it.",
    "receipts.speech_discarded": "Discarded.",
    "receipts.speech_refused": "The agent did not accept the text.",
    "receipts.speech_unknown": "I can't confirm whether it was sent.",

    /* ---- diagnostics ---- */
    "diag.title": "Voice diagnostics",
    "diag.close": "✕ Close",
    "diag.initial": "Last query: — · Last error: none",
    "diag.none": "none",
    "diag.status": "Last query: {when} · Last error: {error}",

    /* ---- mermaid viewer ---- */
    "mermaid.aria": "Mermaid diagram viewer",
    "mermaid.title": "Mermaid diagram",
    "mermaid.zoom_in_title": "Zoom in",
    "mermaid.zoom_out_title": "Zoom out",
    "mermaid.zoom_reset_title": "Reset zoom",
    "mermaid.copy": "Copy",
    "mermaid.copy_title": "Copy source code",
    "mermaid.close_title": "Close viewer",
    "mermaid.hint": "Drag to move · wheel or buttons to zoom",

    /* ---- offline shell / update notice (S4) ---- */
    "offline.banner": "Offline",
    "update.available": "New version available — Reload",
    "update.reload": "Reload",

    /* ---- operator dock (S3) ---- */
    "dock.replies": "Quick replies",
    "dock.commands": "Commands",
    "dock.presets": "Keys",
    "dock.launchers": "Launchers",
    "dock.confirm_title": "Tap again to confirm",
    "dock.cwd_missing": "Could not launch: working directory missing",
    "dock.launch_failed": "Could not launch — try again",
    "dock.preset_failed": "Could not send the preset — try again"
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = dict;
  } else if (global.BrainI18n && typeof global.BrainI18n.register === "function") {
    global.BrainI18n.register("en", dict);
  } else {
    global.__brainI18nEn = dict;
  }
})(typeof window !== "undefined" ? window : globalThis);

/* Spanish dictionary (SOURCE locale) — S4, doc 09.
 *
 * Every value is byte-copied from the pre-i18n UI (index.html and the
 * app modules): booting with this dictionary renders EXACTLY the
 * strings the PWA showed before locales existed. Do not "improve"
 * wording here without pinning the change in the browser suite —
 * several of these lines are pinned by tests that predate i18n. */

(function (global) {
  "use strict";

  var dict = {
    /* ---- common ---- */
    "common.loading": "Cargando…",
    "common.send": "Enviar",
    "common.cancel": "Cancelar",
    "common.discard": "Descartar",

    /* ---- header ---- */
    "header.pane_picker_title": "Cambiar de conversación",
    "header.connecting": "conectando…",
    "header.settings_title": "Ajustes",
    "header.mute_title": "Silenciar anuncios",
    "header.voice_unlock": "🔊 Activar voz",

    /* ---- call ---- */
    "call.start": "📞 Llamar",
    "call.end": "🔴 Colgar",
    "call.pause": "⏸ Pausa",
    "call.resume": "▶ Reanudar",
    "call.stop_audio": "■ Parar audio",
    "call.stop": "■ Detener",
    "call.stop_armed": "¿Detener? Toca otra vez",
    "call.stopped": "Turno interrumpido",
    "call.title": "Llamada con brain",
    "call.minimize_title": "Minimizar la llamada (sigue en marcha)",
    "call.cut": "Llamada cortada",
    "call.cut_recall": "Llamada cortada — ¿Volver a llamar?",
    "call.confirm_pill": "Confirmar ▲",
    "call.confirm": "Confirmar",
    "call.fallback_placeholder": "Escribe en su lugar…",

    /* ---- interim strip / ghost ---- */
    "strip.listening": "● Escuchando",
    "strip.recording": "● Grabando — habla",
    "strip.transcribing": "⏳ Entendiendo…",
    "strip.thinking": "⏳ Infiriendo…",
    "strip.speaking": "🔊 Hablando",
    "strip.paused": "⏸ Micrófono en pausa",
    "strip.thinking_lower": "⏳ Infiriendo…",
    "strip.transcribing_lower": "⏳ transcribiendo…",
    "strip.paused_lower": "⏸ micrófono en pausa",
    "strip.sending": "📤 Enviando…",
    "strip.synthesizing": "⏳ Sintetizando…",
    "ghost.hint": "Di algo — te escucho",

    /* ---- status chip ---- */
    "status.nobody": "nadie",
    "status.working": "trabajando",
    "status.blocked": "bloqueado",
    "status.done": "listo",

    /* ---- reply composer ---- */
    "reply.placeholder": "Responde al agente…",
    "reply.aria": "Respuesta al agente",
    "reply.sent": "Respuesta enviada al agente",
    "reply.sending": "Enviando la respuesta…",
    "reply.steering": "Inyectando en el turno en curso…",
    "reply.destructive_confirm": "Este envío parece destructivo: {match}.\n\n{text}\n\n¿Enviar igualmente?",
    "reply.fail": "No se pudo enviar: {detail}",
    "reply.fail_offline": "No se pudo enviar: sin conexión con el brain",
    "reply.followup_fail": "No se pudo completar: {detail}",
    "reply.followup_fail_offline": "No se pudo completar: sin conexión con el brain",
    "receipt.submit": "Enviar ahora",
    "stop.fail": "No se pudo detener: {detail}",
    "stop.fail_offline": "No se pudo detener: sin conexión con el brain",

    /* ---- brain message (S13) ---- */
    "msg.button": "✉ Mensaje",
    "msg.placeholder": "Mensaje para el agente…",
    "msg.aria": "Mensaje de operador al agente",
    "msg.delivered": "Mensaje entregado al agente",
    "msg.queued": "Mensaje en camino al agente",
    "msg.held": "El agente está ocupado — se entregará cuando esté libre (máx. 60 min)",
    "msg.inbox": "El agente no se puso libre; el mensaje quedó en su bandeja",
    "msg.generic": "Mensaje {state}",
    "msg.refused": "No se envió: {reason}",
    "msg.fail": "No se pudo enviar el mensaje: {detail}",
    "msg.fail_offline": "No se pudo enviar el mensaje: sin conexión con el brain",

    /* ---- keys / dialogs ---- */
    "keys.hint": "El agente espera — respóndele con teclas:",
    "keys.sent": "Enviado: {label} · esperando al agente…",
    "keys.fail": "No se pudo enviar la tecla: {detail}",
    "keys.fail_offline": "No se pudo enviar la tecla: sin conexión con el brain",
    "keys.group_aria": "Teclas para responder al diálogo",
    "keys.dialog_aria": "Diálogo del agente",
    "keys.left": "Izquierda",
    "keys.right": "Derecha",
    "keys.up": "Arriba",
    "keys.down": "Abajo",
    "keys.tab": "Tabulador",
    "keys.esc": "Escape",
    "keys.enter": "Intro",
    "keys.d1": "Uno",
    "keys.d2": "Dos",
    "keys.d3": "Tres",
    "keys.d4": "Cuatro",
    "keys.d5": "Cinco",
    "keys.d6": "Seis",
    "keys.d7": "Siete",
    "keys.d8": "Ocho",
    "keys.d9": "Nueve",
    "keys.d0": "Cero",
    "dialogs.group_aria": "Opciones del diálogo",
    "dialogs.unread_title": "No puedo leer este diálogo",
    "dialogs.unread_body": "El agente {agent} espera una respuesta que no sé leer con seguridad. Usa el teclado de abajo, nunca se envía nada a ciegas.",
    "dialogs.answered": "Respondido: {label}",
    "dialogs.answered_plain": "Respondido.",
    "dialogs.changed": "El diálogo cambió antes de responder; nada se envió. Mira la pantalla y responde al diálogo nuevo, o usa el teclado.",
    "dialogs.option_gone": "Esa opción ya no está en el diálogo; usa el teclado.",
    "dialogs.fail": "No se pudo responder el diálogo; usa el teclado.",

    /* ---- permission dialog ---- */
    "permission.once": "Permitir una vez",
    "permission.always": "Permitir siempre",
    "permission.reject": "Rechazar",
    "permission.once_toast": "Permiso concedido una vez",
    "permission.always_toast": "Permiso concedido siempre",
    "permission.reject_toast": "Permiso rechazado",
    "permission.sent_toast": "Permiso enviado",

    /* ---- cockpit tabs ---- */
    "tabs.aria": "Vistas del agente",
    "tabs.agent": "Vista del agente",
    "tabs.screen": "Pantalla",
    "tabs.history": "Historial",
    "tabs.terminal": "Terminal",

    /* ---- glance ---- */
    "glance.empty": "Sin conversación del agente todavía",
    "glance.empty_no_session": "Sin conversación del agente todavía. Consulta la pestaña Pantalla.",
    "glance.label": "Vista del agente",
    "glance.replay": "🔊 Escuchar",
    "glance.replay_from": "Escuchar desde la frase {n}",
    "glance.replay_piece": "Escuchar fragmento {n} de {total}",
    "glance.empty_no_identified": "No se ha identificado la sesión del agente. Consulta la pestaña Pantalla.",
    "glance.more": "Ver más",
    "glance.more_aria": "Cargar turnos anteriores de la llamada",

    /* ---- panels ---- */
    "panel.screen": "pantalla",
    "panel.terminal": "terminal en vivo",
    "panel.mirror_aria": "Espejo de la terminal del agente",
    "panel.terminal_empty": "Sin terminal que mostrar todavía",
    "history.more": "Cargar más",
    "history.empty": "Sin historial de este agente todavía",
    "history.aria": "Historial completo del agente",
    "history.fail": "No se pudo cargar el historial — vuelve a abrir la pestaña para reintentar",

    /* ---- pending banner ---- */
    "pending.permission": "Pide permiso",
    "pending.error": "Error del agente",
    "pending.question": "El agente pregunta",
    "pending.attention": "Necesita tu atención",
    "pending.fallback_excerpt": "mira la pantalla",
    "pending.agent_fallback": "Un agente",
    "pending.many": "{count} agentes esperan tu OK — toca para atender",
    "pending.one": "{name} espera tu OK — toca para verlo",

    /* ---- herd / conversation sheet ---- */
    "herd.disappeared": "El agente seleccionado desapareció — volviendo al enfocado.",
    "herd.recent": "Últimos 30 min",
    "herd.g_blocked": "Bloqueado",
    "herd.g_working": "Trabajando",
    "herd.g_done": "Listo",
    "herd.g_idle": "Inactivo",
    "herd.g_other": "Otros",
    "herd.new_conversation": "＋ Nueva conversación",
    "herd.new_started": "Conversación nueva iniciada",
    "herd.new_failed": "No se pudo reiniciar la conversación en el servidor.",
    "conv.aria": "Conversaciones",
    "conv.title": "Conversaciones",
    "conv.close": "Cerrar",

    /* ---- pending sheet ---- */
    "pendsheet.aria": "Pendientes",
    "pendsheet.title": "Pendientes",
    "pendsheet.close": "Cerrar",
    "pendsheet.empty": "Sin pendientes",
    "pendsheet.title_count": "📋 Pendientes ({count})",
    "pendsheet.title_open": "📋 Pendientes (",
    "pendsheet.title_close": ")",
    "pendsheet.listen_aria": "Volver a escuchar este aviso",
    "pendsheet.announced": "✓ Anunciado",
    "pendsheet.announced_aria": "Marcar como anunciado",
    "pendsheet.discard": "✕ Descartar",
    "pendsheet.discard_aria": "Descartar este pendiente",
    "pendsheet.save_failed": "No pude guardar los pendientes en este dispositivo (¿modo privado o sin espacio?). La lista NO es persistente hasta recargar.",
    "pendsheet.timeout": "⏱ Acción cancelada por tiempo",

    /* ---- settings sheet ---- */
    "settings.aria": "Ajustes",
    "settings.title": "Ajustes",
    "settings.close_title": "Cerrar ajustes",
    "settings.voice_engine": "Motor de voz",
    "settings.voice_server": "Voz: Servidor",
    "settings.voice_browser": "Voz: Navegador",
    "settings.voice_server_title": "Motor Servidor: tu voz se transcribe en el servidor (funciona con auriculares BT). Toca para cambiar al Navegador.",
    "settings.voice_browser_title": "Motor Navegador: reconocimiento del navegador (Web Speech). Toca para cambiar al Servidor.",
    "settings.theme": "Tema",
    "settings.theme_auto": "Tema: Auto",
    "settings.theme_light": "Tema: Claro",
    "settings.theme_dark": "Tema: Oscuro",
    "settings.hint": "Servidor: transcribe en el back con whisper (robusto). Navegador: Web Speech de Chrome (más rápido, menos fiable en Android).",
    "settings.pending": "Pendientes",
    "settings.pending_open_aria": "Ver los avisos pendientes",
    "settings.pending_open": "🔔 Pendientes ({count})",
    "settings.language": "Idioma",
    "settings.language_es": "Idioma: Español",
    "settings.language_en": "Language: English",
    "settings.font": "Tamaño del texto",
    "settings.font_small": "Texto: Pequeño",
    "settings.font_normal": "Texto: Normal",
    "settings.font_large": "Texto: Grande",
    "settings.zen": "Modo Zen",
    "settings.zen_on": "Modo Zen: Sí",
    "settings.zen_off": "Modo Zen: No",
    "settings.haptics": "Háptica",
    "settings.haptics_on": "Háptica: Sí",
    "settings.haptics_off": "Háptica: No",

    /* ---- voice / mic ---- */
    "voice.blocked": "Micrófono BLOQUEADO: concede el permiso en el navegador (menú ⋮ → Ajustes del sitio → Micrófono) y vuelve a llamar.",
    "voice.keyboard_fallback": "Micrófono bloqueado — usa el teclado o concede el permiso en el navegador.",
    "voice.engine_unavailable": "Este navegador no soporta el motor Servidor — usa el motor Navegador.",
    "voice.browser_missing": "El motor Navegador no está disponible en este navegador.",
    "voice.server_missing": "El motor Servidor no está disponible en este navegador.",
    "voice.record_failed": "No pude abrir la grabación — prueba el motor Navegador.",
    "voice.mic_failed": "No pude abrir el micrófono ({name}) — prueba el motor Navegador.",
    "voice.hearing": "No te oigo",
    "voice.hearing_hint": "No te oigo — comprueba el micro",
    "voice.rec_fails": "El reconocimiento de voz falla repetidamente — cambiado a teclado.",
    "voice.no_stt": "Ni reconocimiento de voz ni micrófono disponibles en este navegador — modo teclado.",
    "voice.transcribe_failed": "No pude transcribir (HTTP {status}): {detail}",
    "voice.audio_send_failed": "No pude enviar el audio al servidor — {detail}.",
    "voice.network_error": "error de red",
    "voice.not_understood": "No entendí el audio — inténtalo otra vez.",

    /* ---- brain errors ---- */
    "brain.not_configured": "El brain no está configurado: falta GLM_API_KEY en el servidor. Defínela en el entorno del servicio (por ejemplo, un perfil de shell o un archivo de entorno fuera de este repositorio) y reinicia el servicio.",
    "brain.ask_failed": "La pregunta falló (HTTP {status}). Prueba otra vez.",
    "brain.timeout": "El brain tardó demasiado — vuelve a intentarlo.",
    "brain.network": "Error de red hablando con el brain.",
    "brain.context_failed": "No pude confirmar el contexto de la llamada — vuelve a intentarlo.",

    /* ---- tts ---- */
    "tts.synth_failed": "No pude sintetizar el audio (HTTP {status}).",
    "tts.synth_failed_part": "No pude sintetizar una parte del audio (HTTP {status}).",
    "tts.synth_retry": "No pude sintetizar el audio — revisa la conexión.",
    "tts.synth_retry_part": "No pude sintetizar una parte del audio — revisa la conexión.",
    "tts.piece_failed": "No pude reproducir la parte {n} del audio — prueba otra vez.",
    "tts.segments_degraded": "No pude seguir bajando la voz por segmentos; la reproducción quedó degradada.",
    "tts.server_ended": "La voz del servidor terminó ({state}).",
    "tts.cancel_unconfirmed": "No pude confirmar con el servidor que se canceló la voz; el audio local ya se detuvo.",
    "tts.cancel_rejected": "El servidor rechazó la cancelación de la voz ({state}).",
    "tts.read_aloud": "Leer en voz alta",
    "tts.read_this": "Leer esta respuesta en voz alta",

    /* ---- announcements ---- */
    "announce.who": "🔊 anuncio · {label}",
    "announce.label_default": "agente",
    "announce.agent": "agente",
    "announce.toast": "🔊 {label}: {text}",

    /* ---- approval gate card ---- */
    "approval.agent_row": "Agente",
    "approval.title_row": "Título",
    "approval.task_row": "Tarea",
    "approval.dict_new": "🎙 Dicta el texto nuevo — «sí» envía, «no» cancela",
    "approval.dict_task": "🎙 Dicta la tarea nueva — «sí» crea el panel, «no» cancela",
    "approval.save": "✓ Guardar",
    "approval.discard": "✕ Descartar",
    "approval.send": "✓ Enviar",
    "approval.edit": "✏️ Editar",
    "approval.redict": "🎙 Re-dictar",
    "approval.cancel": "✕ Cancelar",
    "approval.sending": "📤 Enviando…",
    "approval.expired": "⏱ Tiempo agotado",
    "approval.create": "🆕 Crear panel",
    "approval.to": "📤 Para: {target}",
    "approval.timeout_turn": "⏱ Acción cancelada por tiempo",

    /* ---- device pairing ---- */
    "pair.paired_status": "📱 Emparejado como «{name}»",
    "pair.expired_status": "🔒 Token caducado: vuelve a emparejar",
    "pair.blocked_status": "🔒 Bloqueado: dispositivo no emparejado",
    "pair.open_status": "Sin emparejar (escritura y lectura siguen abiertas)",
    "pair.write_required": "Este brain exige un dispositivo emparejado para escribir. Pide un código con `herdr-brain pair` en la máquina y empareja aquí.",
    "pair.token_expired": "El token de este dispositivo caducó. Pide un código nuevo con `herdr-brain pair` y vuelve a emparejar (o pide al operador ampliar el plazo con `devices set-expiry`).",
    "pair.code_placeholder": "Código (8 caracteres)",
    "pair.label_placeholder": "Nombre del dispositivo",
    "pair.submit": "Emparejar",
    "pair.this_device": " (este dispositivo)",
    "pair.expires": " · caduca {when}",
    "pair.revoke": "Revocar",
    "pair.unlink": "Desvincular",
    "pair.no_pending": "No hay ningún emparejamiento pendiente — pide un código nuevo.",
    "pair.code_expired": "El código caducó (dura 10 minutos) — pide otro.",
    "pair.exhausted": "Demasiados intentos fallidos — pide otro código.",
    "pair.bad_code": "Código incorrecto — revisa los caracteres.",
    "pair.duplicate_label": "Ese nombre ya está en uso — elige otro.",
    "pair.generic_fail": "No se pudo emparejar ({reason}).",
    "pair.paired_toast": "Dispositivo emparejado — la escritura queda habilitada",

    /* ---- push card ---- */
    "push.title": "Avisos push",
    "push.no_keys": "Sin claves en el servidor — pide al operador importarlas (`herdr-brain push import`) o generar un par nuevo.",
    "push.blocked": "El navegador bloquea las notificaciones; actívalas en los ajustes del sitio y vuelve a intentarlo.",
    "push.active": "🔔 Avisos activos en este dispositivo",
    "push.off": "Sin avisos push en este dispositivo",
    "push.reenable": "Reactivar",
    "push.enable": "Activar",
    "push.test": "Probar",
    "push.disable": "Desactivar",
    "push.enabled_toast": "Avisos push activados",
    "push.enable_failed": "No se pudieron activar los avisos: {error}",
    "push.disabled_toast": "Avisos push desactivados",
    "push.disable_failed": "No se pudieron desactivar los avisos — prueba otra vez",
    "push.test_sent": "Notificación de prueba enviada ({sent})",
    "push.test_none": "No se envió nada — ¿hay otro dispositivo suscrito?",
    "push.test_failed": "La prueba no llegó al brain — sin conexión",

    /* ---- receipts (S12) ---- */
    "receipts.outcome_effective": "Enviado — el agente tomó el turno",
    "receipts.outcome_sent": "Enviado — sin confirmación aún",
    "receipts.outcome_typed_not_submitted": "Quedó escrito sin enviar",
    "receipts.outcome_discarded": "Descartado",
    "receipts.outcome_refused": "El agente no aceptó el texto",
    "receipts.outcome_unknown": "No puedo confirmar si se envió",
    "receipts.speech_effective": "Enviado. El agente tomó el turno.",
    "receipts.speech_sent": "Enviado. Sin confirmación todavía.",
    "receipts.speech_typed_not_submitted": "Quedó escrito sin enviar. Puedes enviarlo o descartarlo.",
    "receipts.speech_discarded": "Descartado.",
    "receipts.speech_refused": "El agente no aceptó el texto.",
    "receipts.speech_unknown": "No puedo confirmar si se envió.",

    /* ---- diagnostics ---- */
    "diag.title": "Diagnóstico de voz",
    "diag.close": "✕ Cerrar",
    "diag.initial": "Última consulta: — · Último error: ninguno",
    "diag.none": "ninguno",
    "diag.status": "Última consulta: {when} · Último error: {error}",

    /* ---- mermaid viewer ---- */
    "mermaid.aria": "Visor de diagrama Mermaid",
    "mermaid.title": "Diagrama Mermaid",
    "mermaid.zoom_in_title": "Acercar (Zoom +)",
    "mermaid.zoom_out_title": "Alejar (Zoom -)",
    "mermaid.zoom_reset_title": "Restablecer zoom",
    "mermaid.copy": "Copiar",
    "mermaid.copy_title": "Copiar código fuente",
    "mermaid.close_title": "Cerrar visor",
    "mermaid.hint": "Arrastra para mover · Rueda o botones para zoom",

    /* ---- offline shell / update notice (S4) ---- */
    "offline.banner": "Sin conexión",
    "update.available": "Nueva versión disponible — Recargar",
    "update.reload": "Recargar",

    /* ---- operator dock (S3) ---- */
    "dock.replies": "Respuestas rápidas",
    "dock.commands": "Comandos",
    "dock.presets": "Teclas",
    "dock.launchers": "Lanzadores",
    "dock.confirm_title": "Toca otra vez para confirmar",
    "dock.cwd_missing": "No se pudo lanzar: falta el directorio de trabajo",
    "dock.launch_failed": "No se pudo lanzar — prueba otra vez",
    "dock.preset_failed": "No se pudo enviar el preset — prueba otra vez"
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = dict;
  } else if (global.BrainI18n && typeof global.BrainI18n.register === "function") {
    global.BrainI18n.register("es", dict);
  } else {
    global.__brainI18nEs = dict;
  }
})(typeof window !== "undefined" ? window : globalThis);

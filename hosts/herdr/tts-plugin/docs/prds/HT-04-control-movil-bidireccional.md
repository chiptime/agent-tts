**ID**: PRD-HT-04 · **Proyecto**: herdr-tts
**Prioridad final (revisión 2026-09-22)**: P1 · **Estado**: Aprobada
**Dependencias**: Comparte inyección de texto con HT-01

# PRD-HT-04 — Control bidireccional desde el móvil

> **Nota de revisión (2026-09-22)**: Prerrequisito externo: validar con Herdr core el verbo de inyección de texto (`pane send-keys` o equivalente). El subconjunto de botones simple (Detener, que solo reutiliza `--stop`) puede aterrizar antes de la inyección.

**Prioridad**: Alta · **Esfuerzo**: L

## Resumen ejecutivo
Convertir el push ntfy (hoy de solo lectura) en control remoto: acciones de respuesta en la propia notificación (ntfy `Actions` con postback HTTP a un endpoint local del daemon, autenticado con token en el topic) y un listener de replies al topic. Lista blanca estricta (continuar/detener) más texto libre que se inyecta como prompt en el pane del chat notificado. Jamás ejecuta comandos arbitrarios.

## Problema y flujo actual
Si un agente pregunta algo y el usuario no está en el PC, el flujo se detiene. Hoy el push permite escuchar el MP3 y copiar `herdr agent focus <pane_id>` al portapapeles, pero responder exige volver al teclado: el loop atención→decisión→respuesta está roto en el paso final.

## Propuesta
Tres piezas: (a) endpoint HTTP en el daemon `POST /herdr-tts/reply` con token aleatorio y bind local/LAN configurable; (b) notificaciones con `Actions` http ("Continuar", "Detener") que postean al endpoint con el token y el `pane_id` de origen; (c) suscripción al topic (stream JSON de ntfy) para respuestas de texto libre: confirmaciones cortas inyectan la respuesta; "stop" detiene reproducción/evento; el texto libre se inyecta como prompt en el pane del chat notificado.

## Historias de usuario (US-HT-04-1, ...)
- **US-HT-04-1** — Como operador fuera del PC, quiero confirmar o detener a un agente desde la propia notificación, para no cortar mi desplazamiento.
- **US-HT-04-2** — Como operador en el móvil, quiero escribir una respuesta corta y que llegue como prompt al chat notificado, para desbloquear al agente en remoto.
- **US-HT-04-3** — Como operador preocupado por seguridad, quiero token obligatorio, rate-limit y lista blanca de acciones, para que el móvil nunca sea una puerta a comandos.
- **US-HT-04-4** — Como operador multitarea, quiero que cada acción remota quede registrada, para auditar qué se hizo en mi nombre.

## Requisitos funcionales (RF-HT-04-...)
- **RF-HT-04-1**: listener HTTP en el daemon con `TTS_REMOTE_CONTROL="off|on"` (default off), `TTS_REMOTE_BIND` (default `127.0.0.1`; LAN como opt-in explícito) y puerto documentado.
- **RF-HT-04-2**: token aleatorio generado en la primera activación (`TTS_REMOTE_TOKEN_FILE`), incrustado en las Actions y exigido en el listener; token inválido = 401 + línea en `daemon.log`.
- **RF-HT-04-3**: Actions en la notificación: "Continuar" y "Detener" (postback HTTP); "Detener" ejecuta la misma vía que `--stop` (audio local y evento en curso).
- **RF-HT-04-4**: los mensajes de texto al topic solo se procesan si referencian un `pane_id` de una notificación reciente (TTL configurable, default 10 min) o si existe exactamente un chat pendiente.
- **RF-HT-04-5**: semánticas de texto: "si"/"sí"/"continue"/"ok" = confirmación inyectada; "stop" = detener; texto libre = prompt inyectado en el pane de origen con la misma regla de Enter que HT-01.
- **RF-HT-04-6**: lista blanca: solo el catálogo anterior es ejecutable; el texto libre jamás se interpreta como comando de shell.
- **RF-HT-04-7**: rate-limit de 10 peticiones/minuto por origen; exceso = 429 y silencio temporal.
- **RF-HT-04-8**: auditoría en `daemon.log` por acción remota: timestamp, acción, `pane_id`, primeros 40 caracteres del texto; nunca audio.
- **RF-HT-04-9**: la función es aditiva: con `off`, el push se comporta exactamente como hoy.

## Requisitos no funcionales (RNF-HT-04-...)
- **RNF-HT-04-1**: el listener vive dentro del daemon existente: cero procesos nuevos.
- **RNF-HT-04-2**: latencia acción→pane < 1 s en LAN.
- **RNF-HT-04-3**: un listener caído nunca afecta al bucle del daemon (fail-open, igual que la purga de retención).
- **RNF-HT-04-4**: superficie mínima: sin ejecución de comandos, sin lectura arbitraria de estado, sin escritura de configuración.

## Encaje en la arquitectura actual
`send_ntfy_push()` ya construye las cabeceras del push: las Actions son una extensión del mismo builder. "Detener" reutiliza `--stop`; la inyección de texto comparte mecanismo con HT-01; el listener se integra al bucle del daemon con el patrón best-effort ya usado por la purga horaria del almacén de audio.

## Prior art y diferenciación
`herdr-announcer` expone IPC pero local; ningún comparado ofrece respuesta desde el móvil. Los puentes de llamada (`herdr-call` en la comparativa del README) cubren voz humana pero no acciones estructuradas. Diferenciación: cerrar el loop completo atención→respuesta sin exponer un shell remoto, sobre la infraestructura ntfy ya existente.

## Dependencias
- Comparte el mecanismo de inyección de texto con HT-01 (implementar tras o junto a él).
- Verificar soporte de `Actions` http en las apps ntfy de Android e iOS utilizadas.

## Riesgos y mitigaciones
- Topic público en ntfy.sh → token obligatorio + TTL corto de referencia + opción de servidor self-hosted (`NTFY_SERVER` ya existe).
- Host inalcanzable fuera de casa → alcance documentado: LAN o Tailscale; nunca exponer a internet abierto.
- Abuso del texto libre → solo inyección en pane (RF-HT-04-6), rate-limit (RF-HT-04-7) y auditoría (RF-HT-04-8).

## Métricas de éxito
1. > 3 respuestas remotas con éxito por semana tras un mes.
2. Cero acciones ejecutadas con token inválido.
3. Latencia p50 acción→pane < 1 s en LAN.

## Fuera de alcance
App nativa, ejecución de comandos arbitrarios, streaming de audio bidireccional, control desde fuera de LAN/Tailscale sin puerta de enlace explícita.

## Open questions
¿Las Actions http bastan en iOS o se necesitan view-actions? ¿Topic dedicado de control separado del topic de notificación? ¿TTL por tipo de evento (blocked vs done)?

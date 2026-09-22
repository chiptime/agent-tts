# BLOQUE 4 — Radio mode + verificación opencode web (HT-03 + AT-03 recortada)

**Alcance**: PRD-HT-03 (P2) + tarea de verificación de PRD-AT-03 · **Prioridad**: P2 · **Esfuerzo agregado**: M + 10 min
**Repositorios**: herdr-tts (radio) + agent-tts (verificación) · **Estado**: Aprobado (revisión 22/09/2026)

### Objetivo del bloque

Entregar radio mode como flujo diario de triaje por voz en herdr-tts — un boletín hablado y priorizado de todos los chats que piden atención — y cerrar la única tarea activa que queda de PRD-AT-03 en el repo hermano: verificar con una sesión web real de OpenCode que el conector actual resuelve el último mensaje. Radio entra aquí en su versión core (reproducción secuencial bajo el mutex actual del motor) más tres mejoras progresivas que aterrizan solas cuando sus dependencias (Bloque 1 y Bloque 2 de este desglose) existan.

### Por qué este bloque existe y por qué va aquí (rationale)

Radio mode es el consumidor puro del Bloque 1 (cola prioritaria AT-08 del motor) y del Bloque 2 (identidad vocal HT-02). Al llegar aquí, aterriza COMPLETO — cabeceras con nombre de agente hablado y encolado priorizado con los blocked en prioridad alta — en vez de en modo degradado. Ese es el motivo de su posición en la secuencia: no necesita esperar, pero espera bien.

Por eso es P2 y no está bloqueado: existe un fallback secuencial válido bajo el mutex actual, así que puede shippear antes si hace falta, mejorando solo cuando las dependencias aterricen. Ninguna de las dos mejoras cambia el contrato de la feature; ambas lo enriquecen (RF-HT-03-5 y RF-HT-03-8 en [../HT-03-radio-mode.md](../HT-03-radio-mode.md)).

La verificación de AT-03 (~10 min) es el calentamiento perfecto de la primera sesión del bloque: el schema de OpenCode ya fue verificado el 22/09/2026 (tabla única `session`, lookup por id sin filtro de workspace — probablemente ya cubierto). Ejecutarla primero valida el entorno del repo hermano con costo mínimo y descuenta el único resto vivo de una PRD recortada.

### Secuencia de ejecución: hitos con criterios de aceptación

**Hito 0 — Verificación opencode web (agent-tts, ~10 min)**

Ejecutar una sesión web real de OpenCode, confirmar que el conector actual resuelve el último mensaje con el lookup por id existente, y registrar el resultado dentro de la PRD AT-03 del repo hermano (`docs/prds/AT-03-conectores-autodeteccion.md` de agent-tts) marcando el RF aplicable (RF-AT-03-3) como verificado o documentando el hueco.

- Aceptación: resultado documentado con fecha y evidencia dentro de la PRD AT-03; el índice de PRDs de agent-tts refleja el registro.

**Hito 1 — Radio core (herdr-tts)**

Implementación completa de la feature según su fuente de verdad ([../HT-03-radio-mode.md](../HT-03-radio-mode.md)), sin duplicar aquí sus RF/RNF:

- Command id `radio` en el keymap declarativo, sin acorde por defecto (sugerido y validado con `keymap check`).
- Reuso del roster fusionado del dashboard v3: misma clave `pane_id`, mismo orden needs-attention-first (blocked primero, done por recencia).
- TL;DR heurístico offline por chat (1-2 frases vía el motor), título truncado a 40 chars, cierre con "N chats más silenciosos omitidos".
- `TTS_RADIO_MAX_CHATS` (default 6).
- Interrumpible con stop; re-pulsar descarta el boletín en curso y re-triaja con datos frescos.
- Debounce propio del command: 10 s.
- Estado "radio en curso" visible en dashboard.

- Aceptación: decidir a qué chat entrar en < 60 s con flota de 5+ chats; primer audio < 1.5 s; tasa de abandono del boletín < 30%.

**Hito 2 — Mejoras progresivas**

Cada una aterriza cuando su dependencia exista; ninguna bloquea al Hito 1.

- (a) Cabeceras con identidad hablada cuando HT-02 esté conmutada. Ya implementada en paralelo por el Bloque 2 — aquí solo queda verificar la integración (RF-HT-03-8).
- (b) Encolado por la cola prioritaria del motor cuando el paquete AT-04+AT-08 (Bloque 1) publique; los blocked entran en prioridad alta (RF-HT-03-5).
- (c) RF-HT-03-9: modo experimental `TTS_RADIO_LLM_SUMMARY=on` usando la cadena `--llm-summary` existente del motor (`claude -p` / `codex exec` / `ollama`), limitado a 1-2 proveedores, timeout corto, fallback heurístico. Nace experimental, opt-in, sin infraestructura LLM nueva.

- Aceptación: (a) cabecera hablada verificada con HT-02 activo; (b) boletín encolado con prioridad sin romper el fallback; (c) modo experimental documentado como opt-in con fallback al heurístico comprobado.

### Mejoras que aterrizan cuando sus dependencias existan

| Mejora | Desbloqueada por | Referencia |
|---|---|---|
| Cabeceras con nombre de agente hablado | HT-02 conmutada (Bloque 2; implementación ya en paralelo — queda verificar integración) | RF-HT-03-8 |
| Encolado priorizado (blocked en prioridad alta) | Publicación del paquete AT-04+AT-08 (Bloque 1) | RF-HT-03-5 |
| Resumen LLM experimental (`TTS_RADIO_LLM_SUMMARY=on`) | Ninguna bloqueante: la cadena `--llm-summary` ya existe en el motor; va tras el core por ser opt-in experimental | RF-HT-03-9 |

### Qué NO se incluye

- Boletín en texto/pantalla.
- Envío del boletín a ntfy (eso es HT-08, P4).
- Orden personalizado por el usuario.
- Clasificación LLM por defecto (solo experimental opt-in, Hito 2c).
- Conectores gemini-cli y goose y auto-detección por /proc de AT-03: fuera de alcance actual según la nota de revisión del 22/09/2026 de esa PRD; este bloque solo ejecuta su tarea de verificación.

### Superficies compartidas y zonas de conflicto

- **Fusión del roster del dashboard v3** (clave `pane_id`): radio la consume sin duplicarla; cualquier cambio en la fusión impacta dashboard y radio a la vez.
- **Keymap declarativo y superficie del plugin** (`bin/`, `README.md`, `herdr-plugin.toml`, `scripts/`): el command id `radio` toca las mismas superficies que el WIP sucio de otra sesión en curso — coordinar la propiedad de esos ficheros antes de editarlos.
- **Motor agent-tts**: radio consume la cola prioritaria (interfaz definida en el Bloque 1); el fallback secuencial comparte el mutex existente. El Hito 0 escribe solo el registro de verificación dentro de la PRD AT-03 del repo hermano.
- **Dashboard**: el estado "radio en curso" añade una superficie de UI compartida con el dashboard v3.

### Riesgos específicos del paquete

- **Boletines largos**: límites de chats (`TTS_RADIO_MAX_CHATS`, default 6) y de frases por ítem ya definidos en la fuente (RF-HT-03-2/3).
- **TL;DR heurístico mediocre en prosa conversacional**: mitigación con el modo experimental del Hito 2c (RF-HT-03-9), que conserva fallback heurístico garantizado.
- **Saturación por re-pulsación**: debounce propio de 10 s del command (RF-HT-03-7).
- **Coordinación con la sesión paralela** que posee el WIP de `bin/` y `scripts/`: riesgo de conflictos de edición; mitigación: no tocar esos ficheros sin coordinación previa.

### Definición de done del bloque

- Radio usable a diario con las métricas de éxito de HT-03 medidas sobre uso real (decisión < 60 s, primer audio < 1.5 s, abandono < 30%).
- Verificación de AT-03 documentada con fecha y evidencia dentro de la PRD del repo hermano.
- Índices de ambos repos actualizados: el README de PRDs de herdr-tts refleja el estado real de dependencias de HT-03; la PRD AT-03 de agent-tts registra la verificación.

### Referencias

- [PRD-HT-03 — Radio mode](../HT-03-radio-mode.md) — fuente de verdad de RF/RNF, métricas y fuera de alcance.
- [README de PRDs de herdr-tts](../README.md) — roadmap consolidado, orden de ataque y regla transversal opt-in.
- PRD-AT-03 — Conectores y auto-detección (repo hermano agent-tts): `../../../../agent-tts/docs/prds/AT-03-conectores-autodeteccion.md` — nota de revisión que define la tarea de verificación y su alcance recortado.

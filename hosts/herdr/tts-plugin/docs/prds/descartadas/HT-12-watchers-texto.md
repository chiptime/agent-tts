**ID**: PRD-HT-12 · **Proyecto**: herdr-tts
**Estado**: **DESCARTADA en revisión 2026-09-22** (decisión del maintainer)
**Dependencias**: Ninguna

# PRD-HT-12 — Watchers personalizados de texto

**Prioridad**: Media · **Esfuerzo**: M

## Resumen ejecutivo
`~/.config/herdr-tts/watchers.json`: lista de reglas `{pane_pattern, regex, acción: speak|push|both, cooldown}` evaluada en el sweep existente de ~4 s sobre el scrollback de los panes. Mismas puertas de gating (mute/snooze/debounce), limpieza al cerrar el pane. Cubre eventos que importan y que el watcher de estados no ve: build fallido en un pane de shell, un error concreto en un log, un patrón en un deploy.

## Problema y flujo actual
El watcher reacciona a estados de agente (working/done/blocked), pero el trabajo real también produce señales de texto: "build FAILED" en un pane de CI local, un test que revienta tras 20 minutos, un deploy que termina. Esos panes no son agentes (o el estado no cambia) y hoy son invisibles para la capa de voz.

## Propuesta
Fichero declarativo de watchers. El sweep del daemon ya visita los panes cada ~4 s: para los que matcheen `pane_pattern`, lee el scrollback (`herdr pane read`, ya usado en adquisición) y evalúa las regex sobre las líneas nuevas desde el último offset por pane. Un match dispara el pipeline estándar (síntesis + push según acción) pasando por todas las puertas del gating ledger, con cooldown por watcher.

## Historias de usuario (US-HT-12-1, ...)
- **US-HT-12-1** — Como operador con CI local, quiero oír cuando un build falla en un pane de shell, sin mirar la terminal.
- **US-HT-12-2** — Como operador con suites de test largas, quiero un aviso solo con el veredicto (pass/fail), no cada línea de progreso.
- **US-HT-12-3** — Como operador que lanza deploys, quiero push al móvil cuando aparece el patrón de éxito o error, para no vigilar el pane.
- **US-HT-12-4** — Como operador, quiero que estos avisos respeten mute/snooze/debounce como cualquier otro, para tener una sola maquinaria de ruido.

## Requisitos funcionales (RF-HT-12-...)
- **RF-HT-12-1**: fichero `watchers.json`: array de `{name, pane_pattern (regex sobre título o agent_type del pane), regex, action: "speak"|"push"|"both", cooldown_s (default 300), enabled}`; validado al cargar con error accionable por regla.
- **RF-HT-12-2**: evaluación incremental: offset de scrollback por pane; solo se evalúan las líneas nuevas (nunca se re-procesa lo ya leído).
- **RF-HT-12-3**: disparo: el texto que matchea (con una línea de contexto previa opcional) entra por el pipeline estándar: gating ledger completo (mute/snooze/debounce del pane), síntesis con la voz vigente y push según `action`; el settle window no aplica (no es transición de estado de agente).
- **RF-HT-12-4**: cooldown por watcher y pane: re-match dentro del cooldown se ignora.
- **RF-HT-12-5**: ciclo de vida: pane cerrado → offset y estado del watcher purgados (misma poda del daemon que glifos y cachés de título).
- **RF-HT-12-6**: comando de verificación: `herdr-tts --watchers-test "<texto>"` (dry-run que imprime qué reglas dispararían y con qué acción).
- **RF-HT-12-7**: límites: máximo 20 watchers activos; presupuesto de evaluación por sweep (p. ej. últimos 200 saltos de scrollback por pane).

## Requisitos no funcionales (RNF-HT-12-...)
- **RNF-HT-12-1**: sin watchers configurados, coste cero: el sweep no cambia.
- **RNF-HT-12-2**: la evaluación no añade spawns por sweep más allá de la lectura de scrollback que ya hace para panes relevantes; regex evaluadas en el intérprete ya usado por el daemon.
- **RNF-HT-12-3**: fallo de un watcher (regex inválida, pane ilegible) nunca rompe el sweep: fail-open por regla con línea en `daemon.log`.

## Encaje en la arquitectura actual
El sweep ya ejecuta un `herdr agent list` por barrido y ya lee scrollback para shells genéricos: el watcher reusa ambas vías. El gating y el fan-out son los existentes; la poda de panes cerrados ya existe para glifos y caché de títulos. El texto matcheado pasa por el sanitizador y el redactor de secretos del motor antes de sintetizar o pushear.

## Prior art y diferenciación
Los comparados no observan contenido de pane; las soluciones ad hoc existentes son wrappers de campana de terminal sin gating ni voz. Diferenciación: watchers declarativos sobre la misma maquinaria de gating/síntesis/push de la suite, sin procesos nuevos y con cooldown.

## Dependencias
Ninguna.

## Riesgos y mitigaciones
- Regex costosas sobre scrollbacks grandes → presupuesto por sweep y evaluación incremental (RF-HT-12-2/7).
- Falsos positivos por líneas repetidas (spinners, barras de progreso) → cooldown + exigencia de línea nueva.
- Privacidad: texto de pane viajando al push → redactor de secretos del motor ya en el pipeline (obligatorio en la ruta).

## Métricas de éxito
1. Latencia evento real → voz < 10 s (un sweep + síntesis).
2. Cero regresión de CPU del daemon en reposo con watchers activos (comparativa de ticks antes/después).
3. >= 2 casos de uso reales (CI local, deploy) operativos en la primera semana.

## Fuera de alcance
Watchers sobre ficheros de log externos (solo panes), condiciones multi-línea con estado, acciones arbitrarias (solo speak/push/both), watchers con captura de grupos interpolada en la voz.

## Open questions
¿`pane_pattern` sobre título del pane, agent_type, o ambos combinados? ¿Debería el watcher heredar la voz del chat si el pane resulta ser un agente (HT-02)? ¿Push con MP3 inline o solo texto para matches cortos?

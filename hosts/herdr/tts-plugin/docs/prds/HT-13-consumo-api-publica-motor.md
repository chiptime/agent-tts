**ID**: PRD-HT-13 · **Proyecto**: herdr-tts
**Prioridad final (revisión 2026-09-22)**: P1 · **Estado**: Implementada (2026-09-22, pendiente de commit/push)
**Dependencias**: agent-tts ≥ 0.3.0 con API pública estable (`audio_store`, catálogo de voces/proveedores, `--probe`, `main`)

# PRD-HT-13 — Consumo de la API pública del motor (de-duplicación del host)

> **Nota de revisión (2026-09-22)**: Nace de la auditoría de capas con test de migración Herdr→Orca. Valida cada duplicación host/motor: si la duplicación se justifica, se documenta; si no, se sustituye por consumo de la API pública del core. Veredicto global: **ninguna de las duplicaciones detectadas se justifica**.

**Prioridad**: Alta · **Esfuerzo**: M

## Resumen ejecutivo
El host (5.406 líneas de bash) reimplementa hoy semántica que pertenecen al motor: publicación de podcast, cálculo de duración de audio, precedencia de retención, catálogo de voces, lista de proveedores y parseo del status IPC. Con la API pública estabilizada en agent-tts 0.3.0, el host pasa a ser consumidor puro: cada duplicación se sustituye por una llamada a la API, el host se queda solo con la orquestación (qué pane, qué evento, cuándo). De paso corrige el bug de validación de `--provider` que omite `kokoro`.

## Problema y flujo actual
Auditoría de capas (2026-09-22) sobre `bin/herdr-tts`:
1. **Podcast reimplementado en bash+python inline** (`:794-801`, `:5256`): importa `PodcastFeed`/`run_podcast_server` del core y recrea la publicación, en vez de delegar en el CLI/API del motor.
2. **Duración de audio decodificada a mano** (`:1690-1695`): bucle `miniaudio.decode()` inline duplicando `audio_duration` del core.
3. **Semántica de retención duplicada** (`:1641-1680`): `config_get_retention_raw`/`audio_retention_days` con tiers `HERDR_TTS_/AGENT_TTS_/TTS_` y `audio_store_dir` duplican el parseo de `audio_store.retention_days()`/`audio_dir()`.
4. **Catálogo de voces hardcodeado** (`:1118-1119` rotaciones, `:3539` `SETTINGS_VOICES`): viola el propio RF-HT-11-1 ("la lista viene del motor/voice manager").
5. **Validación de `--provider` divergente** (`:5168`): acepta `edge|openai|elevenlabs|eleven|piper|local` y **omite `kokoro`**, que el core soporta (`cli.py`) y el propio menú de Ajustes del host muestra (`:3531`). Bug real.
6. **Status IPC parseado a mano en bash** (`:333-335`, `:1164-1192`, `:2499-2509`): el formato humano `status=… pos=… total=…` del core se re-parsea en 3 sitios.
7. **Acoplamiento a internos del core** (`lib/tts_engine.py:35-43`): importa `agent_tts.cli.main` y `agent_tts.constants` (este último **sin usar ningún símbolo**); usa `agent_tts.audio_store.prune_expired` por path de módulo interno.
8. Menor: `pkill -f "(paplay|afplay|mpv).*/tmp/.*\.wav"` (`:311`) asume reproductores externos que el core ya no usa.

## Validación de la duplicación (¿tiene sentido?)
| # | Duplicación | ¿Se justifica? | Motivo |
|---|---|---|---|
| 1 | Podcast | **No** | La semántica del feed (RSS, metadatos, serving) es conocimiento del motor; solo la orquestación ("publica el audio del pane actual") es del host. |
| 2 | Duración | **No** | Un decode miniaudio inline en bash replica lógica del motor y se desincroniza con ella. |
| 3 | Retención | **No** (matizable) | El tier `HERDR_TTS_` es config legítima del host; lo que no se justifica es duplicar la *semántica de precedencia*: el host exporta `AGENT_TTS_AUDIO_RETENTION_DAYS`/`AGENT_TTS_AUDIO_DIR` desde su `config.env` y el core parsea. |
| 4 | Catálogo de voces | **No** | El motor es la única fuente de verdad de qué voces existen por proveedor; hardcodearlo en el host rompe con cada release del motor. |
| 5 | Lista de proveedores | **No** (es bug) | Derivar del motor elimina la divergencia kokoro de raíz. |
| 6 | Parseo status IPC | **No a medio plazo** | El formato humano no es contrato. Mientras el core no exponga `--json`, concentrar el parseo en UNA función bash, no en tres sitios. |
| 8 | pkill de huérfanos | **No** | Contrato del core: sin reproductores externos. Eliminar o re-acotar. |

Lo único que sí se mantiene en el host: la orquestación de cada pieza (qué audio publicar, cuándo purgar, qué voz offering mostrar), y la config propia `HERDR_TTS_*`.

## Propuesta
1. **Podcast**: sustituir el python inline por llamadas al CLI del core (`agent-tts --podcast …`, `agent-tts --podcast-serve …`) o a la API exportada; el host solo decide qué fichero publicar y cuándo.
2. **Duración**: `agent-tts --probe FILE` (float por stdout) sustituye al decode inline.
3. **Retención**: el daemon escribe `AGENT_TTS_AUDIO_RETENTION_DAYS`/`AGENT_TTS_AUDIO_DIR` derivados de `config.env` (escritura gestionada) y el core aplica su precedencia única; eliminar `config_get_retention_raw`/`audio_retention_days`/`audio_store_dir` duplicados.
4. **Voces**: `SETTINGS_VOICES` y rotaciones se generan desde `agent-tts voice list --json` (catálogo del motor) con caché en el host; fallar con aviso accionable si el motor no responde.
5. **Proveedores**: la validación de `--provider` se deriva de la lista del catálogo (`providers` en el JSON), arreglando kokoro sin mantener listas paralelas.
6. **Status IPC**: mientras el core no ofrezca salida JSON del status, unificar el parseo en una única función bash; cuando el core exponga `--json` (propuesta futura al core), consumirla directamente.
7. **Acoplamiento**: eliminar el import de `agent_tts.constants` (no se usa); invocar el motor vía consola script `agent-tts` (o `from agent_tts import main` ya público) en lugar de `agent_tts.cli`.
8. **pkill huérfanos**: eliminar la línea o re-acotarla a los binarios que el core realmente pueda dejar vivos.

## Historias de usuario (US-HT-13-…)
- **US-HT-13-1** — Como mantenedor del host, quiero que podcast/duración/retención/voces/proveedores vengan de la API del motor, para que una migración de host (Orca) no arrastre reimplementaciones.
- **US-HT-13-2** — Como usuario de kokoro, quiero que `--provider kokoro` sea aceptado por el host, para usar el proveedor que el motor ya soporta.
- **US-HT-13-3** — Como mantenedor, quiero un único punto de parseo del status IPC, para que los cambios de formato del motor no rompan tres sitios a la vez.

## Requisitos funcionales (RF-HT-13-…)
- **RF-HT-13-1**: la publicación de podcast pasa por el CLI/API del core; prohibido importar `agent_tts.podcast` desde bash (regla de lint/documentada).
- **RF-HT-13-2**: la duración de audio se obtiene con `agent-tts --probe`; sin decodificación inline.
- **RF-HT-13-3**: la precedencia de retención es responsabilidad exclusiva del core; el host solo exporta envs `AGENT_TTS_AUDIO_*` desde su config gestionada.
- **RF-HT-13-4**: `SETTINGS_VOICES` y rotaciones se derivan de `agent-tts voice list --json`; ningún literal de voces en el host.
- **RF-HT-13-5**: la validación de `--provider` se deriva de la lista de proveedores del catálogo del motor (kokoro incluido).
- **RF-HT-13-6**: un único helper bash parsea el status IPC; documentado como punto único de acoplamiento al formato.
- **RF-HT-13-7**: `lib/tts_engine.py` no importa módulos internos (`agent_tts.constants` eliminado; `main` vía API pública o consola script).

## Requisitos no funcionales (RNF-HT-13-…)
- **RNF-HT-13-1**: cada sustitución añade como mucho una llamada a proceso al camino crítico de arranque del daemon (< 50 ms medido).
- **RNF-HT-13-2**: si el motor no está instalado o falla, los menús degradan con aviso accionable, nunca con crash del daemon.
- **RNF-HT-13-3**: el pin de `bootstrap.sh` al SHA del core solo se actualiza a una release con la API pública verificada (≥ 0.3.0).

## Encaje en la arquitectura actual
Es la contrapartida host de la purificación del core: el core ya no contiene rutas de herdr (`piper.py`, `cleaner.py` corregidos en 0.3.0) y expone `audio_store`, catálogo de voces/proveedores, `audio_duration`/`--probe` y `main` en su superficie pública. El host reduce su superficie de conocimiento del motor a: consola script + status IPC + config `HERDR_TTS_*`. Con esto, el test de migración Orca se reduce a reimplementar menús, hooks de sesión, daemon y notificaciones — cero lógica de motor.

## Dependencias
- agent-tts ≥ 0.3.0 (API pública: `audio_store`, `provider_names`/`provider_voices`, `--probe`, `main`). Bloqueante para RF-13-1…5 y RF-13-7.
- RF-HT-13-6 (status JSON) depende de una propuesta futura en el core.

## Riesgos y mitigaciones
- Cirugía en un bash de 5.406 líneas → cambios unitarios por pieza, smoke tests tras cada una (`scripts/smoke-tests.sh`).
- Llamadas a proceso extra en arranque → medir RNF-HT-13-1; cachear el catálogo de voces con TTL en disco.
- Divergencia de pin de bootstrap → actualizar el SHA del core solo junto a esta PR, nunca antes de validar la API.

## Métricas de éxito
1. Cero imports por ruta interna de módulo en el host (`agent_tts.podcast`, `agent_tts.cli`, `agent_tts.constants`, `agent_tts.audio_store`); solo API pública (`from agent_tts import …`).
2. `--provider kokoro` aceptado end-to-end (síntesis real).
3. Un único sitio en el host parsea el status IPC.
4. `grep -c "elvira alvaro ximena"` (literales de voces) = solo las tablas legacy de fallback documentadas (2).

## Fuera de alcance
Implementar `--json` del status IPC en el core (propuesta separada), adopción de `--pre-extracted` en herdr-tts (decidido: se conserva para herdr-brain; herdr-tts mantiene scrollback), cualquier feature nueva de menú/voces (HT-02/HT-11).

## Implementación (2026-09-22)
Landing en working tree (pendiente de commit), suite hermética **624 passed / 0 failed**:

- **RF-13-7** ✅ `lib/tts_engine.py`: eliminado el import de `agent_tts.constants` (todos sus símbolos sin usar) y `agent_tts.cli.main` → `main` vía API pública.
- **RF-13-2** ✅ `audio_duration_secs` llama a `agent-tts --probe`; eliminado el decode miniaudio inline.
- **RF-13-6** ✅ helper canónico `engine_status_field`; `seek_audio`, `show_status` y `dashboard_engine_field` (delegado) unificados. El test 24a ya no fija la forma del import, solo la intención (`prune_expired` en argv).
- **RF-13-5** ✅ `--provider` valida contra `voice list --json` del motor + alias canónicos (`eleven`→elevenlabs, `local`→piper); **kokoro aceptado**; fallback legacy si el motor no responde.
- **RF-13-4** ✅ ciclo de voz de Ajustes y rotación de auto-assign consumen el catálogo del motor (cacheado por proceso; paleta = 4 primeras voces del proveedor activo); tablas `VOICE_ROTATION_*`/`SETTINGS_VOICES` quedan como fallback documentado (candidatas a eliminación). El catálogo edge del core ahora expone ids cortos (`elvira`, no `es-ES-ElviraNeural`; dedupe alvaro/álvaro).
- **RF-13-3** ✅ `export_core_retention` (invocado en el dispatcher) exporta `AGENT_TTS_AUDIO_RETENTION_DAYS` resuelto del tier de config del host → la poda del core (`prune_expired` en cada arranque CLI y en `audio_store_prune`) usa el mismo knob. Corrige el fork real: el core podaba con su default 0 aunque el host tuviera retención activada.
- **RF-13-1** ✅ `publish_podcast_episode` y `--podcast-serve` usan la API pública (`from agent_tts import PodcastFeed/run_podcast_server`) y pasan argumentos por argv (elimina la interpolación `'''$title'''` inyectable y el pod_dir frágil). Desviación aceptada sobre el enunciado: publicar un fichero YA renderizado (ruta del watcher y manual) usa la API pública, no el CLI — el CLI `--podcast` solo publica en el momento de síntesis.
- **Extras core** (agent-tts): `AGENT_TTS_PODCAST_BASE_URL` (env) y feed_title neutro ("Agent Audio Feed", era "Herdr & Agent Audio Feed"); catálogo edge con ids cortos.
- **pkill huérfanos** ✅ eliminado (contrato del core: reproducción in-process).
- **Doc** ✅ `ARCHITECTURE.md` ya no afirma que el host entrega `--pre-extracted`; documentado el uso futuro por herdr-brain.

Pendiente fuera de esta PRD: commit/push de ambos repos, actualizar el pin SHA de `bootstrap.sh` a un main de agent-tts que contenga la API (decisión del maintainer tras pushear), propuesta core de `status --json` del IPC, eliminación de tablas legacy de fallback.

## Open questions
Ninguno abierto: `--pre-extracted` decidido (ver Fuera de alcance) y la caché del catálogo resuelta (por proceso, ver Implementación).

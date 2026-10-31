# Deuda técnica y pendientes — agent-tts (registro vivo)

> **Registro vivo** de deuda técnica, decisiones abiertas y residuales aceptados. Una sección por tema; cada ítem cita código, commit o evidencia. Las PRDs ejecutadas viven en [`docs/prds/archivadas/`](prds/archivadas/); las PRDs de trabajo planificado (TD-01/02/03) en [`docs/technical-debt-prd.md`](technical-debt-prd.md). Creado al cierre del paquete P1: **2026-09-25**. Mantenimiento: añadir/aquí, nunca en PRDs archivadas.

---

## 1. Contrato IPC y daemon (residuales aceptados y decisiones de contrato)

- **R3-01 — ventana espontánea veredicto→hook** (`src/agent_tts/queue_manager.py:566-579`, `src/agent_tts/daemon.py:983-1005,1041-1044`): un worker que termina espontáneamente entre el veredicto del watchdog y el hook libera al cliente con su outcome natural (veraz) mientras la snapshot cuenta `failed+wedged`. Miscount de contador en carrera de microsegundos; sin solape ni colgado. Gravedad disputada entre jueces JD (CRITICAL vs WARNING); **aceptado por el maintainer 2026-09-25**. Mejora futura: cierre de la ventana + test de fin espontáneo dentro de ella.
- **R2-03 — reply del enqueue preemptante en targets remotos**: worst case (espera de terminación ≤0,5 s + gateway subprocess ~2 s + connect timeouts) puede superar el timeout de cliente de 1 s; el daemon-side preempta y despacha igualmente. Aceptado en el JD.
- **Limitaciones congeladas del contrato** (detalle en `contracts/ipc-v2.md` §10): refusal transitorio en la ventana bind→listen; `send_ipc_command` devuelve `None` opaco en fallo de transporte; winhost v1 con preemption documentada; controles seek/frases en ERR para wsl-ps/winhost.
- **`stop` — semántica congelada (decisión del maintainer 2026-09-25)**: corta solo el anuncio activo; los ítems pendientes siguen. Adecuada para una cola de notificaciones; se congela así, sin `stop`-con-flush ni comando adicional.
- **A7 — vistas invisibles en el terminal invocante** (heredado de 1.2): `--highlight/--zen/--autoscroll/--bionic` renderizan dentro del daemon (stdout DEVNULL al auto-arrancar). Limitación documentada; rediseño UX (render en cliente vía status) pendiente, sin dependencia de contrato.
- **FUP-1 — Windows kill(0)** (heredado de 1.2): `os.kill(pid, 0)` en Windows = TerminateProcess; un PID reutilizado en PID_FILE puede hacer que `_kill_wedged_daemon` mate un proceso ajeno (sin `/proc` no hay veto de identidad). Follow-up Windows abierto.

## 2. Robustez de tests

- **Flakes bajo carga**: tests timing-adjacent de daemon lifecycle/queue fallan ~1 de cada 6-8 pasadas completas y pasan en solitario (`test_requests_reset_the_idle_clock`, `test_play_registering_during_shutdown_is_refused_not_orphaned`; el milestone 50-eventos fue blindado en T5.1). Invariantes de producto sin afectar según ambos jueces del JD.
- **Márgenes del idle-clock ratificados** (maintainer 2026-09-25; T5.1: ventana 1,0→2,0 s + retry acotado, solo test). El flake intermitente bajo carga queda como característica conocida del test, no del producto.
- **Idempotencia de `enqueue`** (mejora futura, T5.1): el cliente no reintenta un `enqueue` con reply `None` por ambigüedad de duplicado; claves de idempotencia lo cerrarían.

## 3. Mediciones y DoD pendientes

- **edge p95 — ACEPTADO (decisión del maintainer 2026-09-25)**: 584,1 ms p95 medido con daemon caliente y edge real (`metrics/edge/edge-p95-local-20260925T072309Z.json`). El presupuesto de 250 ms (RNF-AT-04-1) se interpreta como objetivo de **despacho local** (cumplido: ≤0,086 ms); con providers de red el dominante es el RTT de síntesis, no deuda del motor. Si el radio mode de herdr exigiera latencia menor, la vía sería streaming por primer-chunk como ítem propio (no abierto).
- **RAM kokoro caliente** (DoD 1.2): pendiente de instalar kokoro en el entorno y medir.
- **Smoke 8h** (RNF-AT-04-4): la corrida del 2026-09-25 no dejó resultado (log de 0 bytes). **Relanzado 2026-10-31 09:45 CEST** de forma desacoplada (`setsid nohup`, fin ~17:45 CEST) con salida en `engine/metrics/smoke/smoke-8h-20261031T0845Z.log`; prueba corta previa de 5 s OK (RSS 44,3 MB, crecimiento 0 %). Registrar aquí el JSON final.

## 4. Roadmap aplazado del paquete P1 (por el paraguas, no por defectos)

- **winhost v2**: ¿cola en el server Windows o encolado en cliente pre-send? (open question de AT-08).
- **Aging de prioridad**: mitigación de inanición de `working`, documentada como RFC posterior.
- **Ventana de coalescing adaptativa** por cadencia de la flota (la ventana es fija y configurable).

## 5. Infraestructura (TD PRD)

Estado de [`docs/technical-debt-prd.md`](technical-debt-prd.md) verificado el 2026-09-25:

- `.github/workflows/ci.yml` **existe** (TD-02 arrancado en algún punto posterior a la PRD).
- **TD-02 completado en local (2026-10-31)**: `engine/tests/conftest.py` con los markers `needs_audio`/`needs_powershell`/`windows_only`, `[tool.pytest.ini_options]` y `ruff` en `[dev]`, job `engine-lint` (solo `ruff check` con reglas de corrección E4/E7/E9/F; `ruff format` fuera por decisión del maintainer), macOS añadido a la matriz y badge de CI. Pendiente: primer run remoto; Windows y macOS siguen con `continue-on-error` hasta verse en verde. `providers/` queda exento de F401 por la congelación FR-06.
- **TD-01 ejecutado** (verificado 2026-10-31): `PiperTTSProvider.synthesize_stream()` con un proceso persistente (`supports_stream`/`stream_yields_group_chunks`), splitter compartido en `engine/src/agent_tts/text.py`, `--stream on` solo (nunca `auto`); criterios del PRD cubiertos en `engine/tests/test_stream_piper.py` (52 OK con `test_stream.py`). TD-03 (conector Orca): sigue en backlog.

## 6. Housekeeping

- ~~**Push de `main`**~~: resuelto. Verificado 2026-10-30: `main` = `origin/main` (`2c550cf`), 0 commits por delante.
- **Comunicar el contrato congelado a herdr-tts** (HT-03/HT-10): el artefacto es `contracts/ipc-v2.md`.
- ~~**Limpieza de bloque 1.3**~~: resuelto. Verificado 2026-10-30: ya no existen el worktree `<repo>-worktrees/bloque-1.3` ni la rama `feat/at-08-cola-y-cadena`.

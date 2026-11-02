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
- **Smoke 8h** (RNF-AT-04-4): ✅ **CUMPLIDO** (corrida 2026-10-31 09:45 → ~18:25 CEST, `engine/metrics/smoke/smoke-8h-20261031T0845Z.log`, local): `result: ok`, 0 fallos, todos los pings OK, RSS estable 45.540 kB del primer al último muestreo (crecimiento 0,0 % frente al límite del 5 %, reposo 44,5 MB < 80 MB de RNF-AT-04-2). La corrida del 2026-09-25 no dejó resultado (log vacío).

## 4. Roadmap aplazado del paquete P1 (por el paraguas, no por defectos)

- **winhost v2**: ¿cola en el server Windows o encolado en cliente pre-send? (open question de AT-08).
- **Aging de prioridad**: mitigación de inanición de `working`, documentada como RFC posterior.
- **Ventana de coalescing adaptativa** por cadencia de la flota (la ventana es fija y configurable).

## 5. Infraestructura (TD PRD)

Estado de [`docs/technical-debt-prd.md`](technical-debt-prd.md) verificado el 2026-09-25:

- `.github/workflows/ci.yml` **existe** (TD-02 arrancado en algún punto posterior a la PRD).
- **TD-02: configuración implementada; aceptación nativa pendiente.** Se añadieron markers de pytest, Ruff con reglas E4/E7/E9/F (sin `ruff format` por decisión del maintainer), matriz con macOS y badge. `providers/` sigue exento de F401 por FR-06.
  - **Historial macOS:** el run `41335155529` tuvo 146 fallos y 1298 pases. Las correcciones de ruta temporal corta, Bash 3.2, `/proc` y rutas resueltas eliminaron esos fallos deterministas. En `41356691284` y su repetición quedaron 2–4 fallos variables de cola/cadenas; el run posterior `41361628470` pasó realmente: **1444 pases, 12 omisiones**. Una ejecución correcta no prueba estabilidad; se mantiene `continue-on-error`.
  - **Windows, diagnóstico ya completo:** `cb10023` evita que el test de auto-SIGINT termine pytest. El run `41361628470` ejecutó por fin toda la suite: **115 fallos, 1328 pases, 13 omisiones**. Incluye tanto supuestos POSIX de tests como defectos de aislamiento/propiedad del canal TCP; no se clasifican todos como meros problemas de tests.
  - **Correcciones locales (2026-11-02, rama `fix/windows-engine-ci`, sin commit/publicación):** omisiones específicas para el worker STT POSIX/WSL2, conservando pruebas de rechazo tipado en Windows; fixtures IPC portables y marcador aislado mediante `AGENT_TTS_PORT_FILE`; protección frente a arranques/paradas de servidores perdedores; consulta Win32 de procesos sin `os.kill(pid, 0)`; URI de fichero, Unicode y rutas de Git Bash correctas. No se relajaron los permisos/propietario de snapshots STT. Detalles: [IPC](windows-ipc-testing.md), [STT](windows-stt-support.md).
  - **Evidencia local:** suite final **1469 pases, 11 omisiones**; Ruff y `git diff --check` correctos. El verificador independiente no reprodujo regresiones nuevas, pero su dictamen es **parcial**: faltan Windows nativo y sus bloqueos reales `msvcrt`. El test de reloj de inactividad falló tanto en la base como en el candidato durante las comprobaciones aisladas.
  - **Deuda previa confirmada:** si otro actor reemplaza manualmente el marcador de puerto de un servidor vivo, su `stop()` puede borrar ese reemplazo. Reproduce también en `cb10023`; el nuevo indicador de binding protege al perdedor y las paradas repetidas, no verifica la identidad de un marcador sustituido.
  - **CI nativo (runs `41444593612`, `41478879873`, `41485223219`):** Windows pasó de caída de pytest → 115 fallos → 17 → 3 → **0 fallos: 1403 pases, 82 omisiones** (run `41485223219`). Cada iteración descubrió defectos reales, no solo supuestos de test: bloqueo de fichero de Windows impidiendo borrar el marcador, `_pid_looks_like_agent_tts` devolviendo `True` para cualquier PID sin `/proc` (riesgo de matar un proceso ajeno), y la identidad POSIX rota en macOS al no existir `/proc`. Todos corregidos; macOS usa ahora `ps(1)` como respaldo y falla cerrado si no hay identidad.
  - **Omisiones en Windows (82) auditadas:** todas justificadas — 62 de STT POSIX/WSL2, 6 de variantes `AF_UNIX`, 2 de PowerShell/WSL reales, 9 de numpy opcional, `/proc`, `chmod` y auto-SIGINT. Ningún caso TCP del canal queda oculto.
  - **macOS sigue con 1–3 fallos variables** de temporización (`test_queue_milestone`, `test_chain_playback`) más ocasionales de recuperación de daemon; pasan al repetir y en Linux. `continue-on-error` se mantiene en macOS hasta estabilizarlos. En Windows ya hay una corrida plenamente verde; retirar su `continue-on-error` es decisión del maintainer (se recomienda exigir 2–3 corridas verdes consecutivas).
- **TD-01 ejecutado** (verificado 2026-10-31): `PiperTTSProvider.synthesize_stream()` con un proceso persistente (`supports_stream`/`stream_yields_group_chunks`), splitter compartido en `engine/src/agent_tts/text.py`, `--stream on` solo (nunca `auto`); criterios del PRD cubiertos en `engine/tests/test_stream_piper.py` (52 OK con `test_stream.py`). TD-03 (conector Orca): sigue en backlog.

## 6. Housekeeping

- ~~**Push de `main`**~~: resuelto. Verificado 2026-10-30: `main` = `origin/main` (`2c550cf`), 0 commits por delante.
- **Comunicar el contrato congelado a herdr-tts** (HT-03): el artefacto es `contracts/ipc-v2.md`.
- ~~**Limpieza de bloque 1.3**~~: resuelto. Verificado 2026-10-30: ya no existen el worktree `<repo>-worktrees/bloque-1.3` ni la rama `feat/at-08-cola-y-cadena`.

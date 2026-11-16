# Issue #1 — Retención de memoria del daemon (~1.5 GiB RSS)

**Estado**: EN CURSO · **Rama**: `fix/engine-memory-retention` (base `80fbca9` sobre `main`)
**Estrategia de entrega**: rama única, commits por work-unit (~400 líneas), sin push/PR sin autorización.
**Runner**: `cd engine && uv run pytest` · Lint: `uv run ruff check src tests` (si aplica).

## Objetivo

Que el daemon no retenga memoria después del pico: (1) el provider ONNX (kokoro)
expulsado del cache tras inactividad devuelve modelo+arena al OS; (2) la sesión ORT
acota su retención (arena OFF); (3) la cadena streamea por archivo en vez de
materializar PCM completo; (4) el replay de archivo suelta referencias transitorias.

## Specs (criterios del issue #1, verbatim)

- **S1**: "Edge-only daemon stays ≤ ~300 MiB RSS over 24 h with periodic playback."
  Mecanismo: expulsión por inactividad de providers con modelo residente en
  `ProviderCache` (kokoro; piper es subproceso, no retiene en-proceso).
- **S2**: "ORT-backed synthesis documents (or bounds) its retained-arena budget."
  Mecanismo: `SessionOptions.enable_cpu_mem_arena = False` en la fábrica por defecto
  + presupuesto documentado en el docstring del provider (fp32 ~325 MB / quantized
  ~92 MB de pesos residentes; sin arena, activaciones por-run se liberan).
- **S3**: "A 30-min chain digest does not transiently allocate full-PCM × N copies."
  Fix propuesto del issue: "Stream chains instead of materializing full PCM — feed
  per-file decoded segments through the session's boundary map". Contrato intacto
  (RF-AT-08-4): UNA sesión, gapless, boundary map combinado antes de play, stop corta
  y los archivos posteriores no suenan, ChainError tipado antes de reproducir.
- **S4** (raíz 2 del issue): "`_load_decoded_locked` keeps
  `raw_bytes = decoded.samples.tobytes()` ... long chains/digests push hundreds of
  MB of transient copies". Mecanismo: `_play_file` suelta los bytes codificados tras
  decodificar; `AudioSession.play` suelta el `decoded` cuando el buffer lo reemplaza.

## Decisiones registradas

- **D1**: streaming SÍNCRONO append-all antes de `play()` (sin hilo producer ni flag
  `producing` para cadenas): los archivos ya están decodificados y los appends son
  copias de memoria instantáneas; `producing` solo aporta cuando la producción es
  lenta (síntesis). Misma memoria pico, mucho menos riesgo de carrera.
- **D2**: TTL de expulsión = 600 s (constante `PROVIDER_MODEL_IDLE_EVICT_SEC` en
  `daemon.py`, inyectable en `ProviderCache` para tests). Contado desde el último
  uso; un request posterior al TTL recarga el modelo (latencia de carga aceptada).
- **D3**: arena OFF incondicional para kokoro: el modelo 82M con shapes variables
  (chunking de textos largos) hace que el arena retenga el pico de la shape máxima;
  la penalización de latencia por-run es marginal frente a ~300+ MB retenidos.
- **D4**: NO se cambia el tipo del buffer (`bytes` → `bytearray`) en las 3 clases de
  sesión: eliminaría la copia de concat del append (pico 2S→1S) pero toca código
  compartido de TODAS las rutas de playback; queda como follow-up documentado.
- **D5**: `plan_chain` comparte núcleo de validación/composición con `assemble_chain`
  (mismo orden y mensajes de error que ya fijan los tests); `assemble_chain` conserva
  su comportamiento público exacto.

## Tasks

- [x] **T1** (S3, D5): `chain.py` — `_validate_chain_sources` + `_compose_chain_map`
      extraídos (comportamiento idéntico, mensajes y orden preservados); nueva
      `plan_chain` + `ChainPlan` (mapa combinado SIN PCM) + `plan_gap_segment`
      (silencio 16-bit entre ítems). Tests: paridad plan/assemble (map+items para
      gap 0 y 250 ms), formato sin PCM, misma validación tipada. **15/15
      test_chain.py**.
- [x] **T2** (S3): `daemon.py` `_play_chain` streamea: decode → plan → boundaries →
      `prepare_pcm(first)` → appends (gap entre ítems) liberando cada segmento →
      watcher → `play(first)`. Harness de `test_chain_playback.py` actualizado al
      nuevo seam (graba `raw_bytes`, respeta preload). Test estructural nuevo:
      prepare=1, append=N-1, load=1 (nunca una segunda carga gigante). **10/10
      test_chain_playback.py** (asserts originales intactos: PCM exacto, stop,
      controles globales, trazas).
- [x] **T3** (S1): `ProviderCache` con `_last_used` + `_evict_idle_model_sessions`
      en cada `get()`; `PROVIDER_MODEL_IDLE_EVICT_SEC = 600.0`; TTL inyectable
      (`model_idle_evict_sec`). `TTSProvider.retains_model_session = False` (base),
      kokoro `True`. Tests: expulsión tras TTL (reload explícito), no-expulsión de
      providers sin modelo, refresh de last-use. **44/44 test_daemon.py**.
- [x] **T4** (S2): fábrica por defecto de kokoro con
      `SessionOptions(enable_cpu_mem_arena=False)`; presupuesto en docstring del
      módulo. Test con módulo ORT falso vía `sys.modules` (ORT no instalado en este
      env — el test corre real donde exista el extra kokoro). **14 passed/11
      skipped preexistentes por numpy ausente + 1 nuevo PASSED**.
- [x] **T5** (S4): `_play_file` → `_decode_audio_file` (los bytes codificados
      mueren con el frame del decode; `play()` recibe la expresión sin binding);
      `AudioSession.play` captura `sample_format` dentro del lock y suelta
      `decoded` (el buffer es el dueño del PCM). Test: weakref del decoded muerto
      tras el load + buffer consistente. **1482 passed, 13 skipped, 1 FAILED
      preexistente** (ver Known environmental failures).

## Known environmental failures (preexistentes, no causados por este diff)

- `tests/test_fallback_chain.py::test_providers_not_rewritten` — el guard T9 FR-06
  compara el working tree contra HEAD en `providers/`; el WIP PARALELO sin commit
  de `providers/kokoro.py` (chunking de textos largos, dueño: sesión previa) lo
  deja rojo desde antes de esta sesión. Se commitea el WIP → verde.
- `test_kokoro_provider.py`: 11 skips por numpy ausente en este env (extra kokoro
  no instalado); preexistente.

## Restricciones respetadas

- WIP paralelo en `providers/kokoro.py` + `test_kokoro_provider.py`: NUNCA
  commiteado. Los hunks de esta feature en esos archivos se separaron del WIP en
  el commit (el árbol conserva WIP + cambios de esta feature; HEAD solo los de
  esta feature).

## Presupuesto de memoria honesto (post-fix)

- Cadena N archivos: pico transitorio ≈ buffer acumulado + segmentos no appendeados
  + copia de concat del último append (≈ 2×PCM-full, mmap, se devuelve); sostenido
  en playback = 1× (solo el buffer). Antes: 2× sostenido durante todo el playback
  (assembled pineado por el watcher) + copia extra de load.
- Replay digest WAV: transitorio decode 2× (encoded+decoded), sostenido 1× (buffer).
  Antes: 3× sostenido.
- Kokoro tras run: pesos residentes (~325 MB fp32 / ~92 MB quantized), sin arena;
  expulsión a los 600 s de inactividad devuelve todo.
- Edge-only (sin fallback kokoro jamás ejecutado): base stack solamente.

## Log

- **L1** (2026-11-16, verbatim del issue #1): "engine: daemon retains ~1.5 GiB after
  ONNX inference and full-PCM chain assembly" — el daemon crece a ~1.5 GiB RSS y no
  devuelve memoria; plateau estable (no leak ilimitado); ~517 MiB cold-swapped a zram;
  arena de onnxruntime retiene el pico para siempre tras una síntesis kokoro/piper
  (vía fallback o request explícito); cadenas materializan PCM completo
  (`assemble_chain_files` + `raw_bytes = decoded.samples.tobytes()`); base stack
  ~300–400 MiB. Fixes propuestos: guard de carga ONNX + streaming de cadenas.
- **L2** (2026-11-16): exploración confirma: piper NO usa ORT en-proceso (subproceso);
  kokoro ya importa ORT lazy (solo en síntesis); `ProviderCache` jamás expulsa → una
  síntesis kokoro vía fallback retiene modelo+arena para siempre. WIP paralelo sin
  commit en `providers/kokoro.py` + `test_kokoro_provider.py` (chunking de textos
  largos): NO tocar ni commitear (restricción heredada de bloque 1.3).
- **L3** (2026-11-16): commits por work-unit en `fix/engine-memory-retention`
  (base `80fbca9`): `3e79f91` feat(chain) plan_chain (T1) · `3e89801` perf(daemon)
  streaming+eviction+releases (T2, T3, T5) · `e40b450` perf(kokoro) arena off +
  evictable (T4, split del WIP paralelo: candidata = HEAD+feature, árbol = WIP+feature,
  diferencia verificada = solo hunks del WIP). Verificación: suite completa
  1482 passed / 13 skipped / 1 failed preexistente (`test_providers_not_rewritten`,
  rojo por el WIP paralelo en providers/ — confirmado con stash antes de tocar nada);
  ruff limpio en todos los archivos tocados.
- **L4** (2026-11-16): merge a `main` autorizado por el usuario (fast-forward) y
  push a `origin/main`; rama local eliminada post-merge (git-prune-merged); el WIP
  paralelo de kokoro se restauró intacto sobre el checkout de main tras el merge.

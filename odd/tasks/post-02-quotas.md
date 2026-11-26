# POST-02 — Cuotas de proveedores (brain)

Objetivo: exponer cuotas de proveedores de IA en Herdr Brain vía el binario externo `ai-quotas`: servicio con caché (`quotas.py`), tool de voz `check_quotas` y endpoint `GET /quotas`. Rama `feat/post-02-quotas`. Entrega: ask-on-risk, commits work-unit en la rama (~350 líneas previstas). Runner: `cd hosts/herdr/brain && uv run --extra dev python -m pytest tests/test_quotas.py` (suite completa al cierre, separada de browser/e2e).

## Specs

S1 (fundación A — "fase 1 A"): el dato viene de `ai-quotas check --json` invocado como subprocess. Verificado por el operador: "'ai-quotas check --json' saca un array JSON por stdout (progreso por stderr) cubriendo gemini, gemini-argon, gemini-3p, claude, chatgpt, deepseek y zai (tarda ~5-15s → caché TTL 5m + refresco no bloqueante)". Adaptador `AiQuotasSource` detrás de una interfaz `QuotaSource` propia del repo; localización del binario tipo `_opencode_binary` (PATH → `~/.local/bin`).

S2 (caché): TTL 300s éxito / 15s fallo (clon de `OpencodeModelCatalog`), solo en memoria, stale-while-revalidate (nunca bloquea: si aún no hay dato responde no-disponible y refresca en background), prewarm al arranque de brain (decisión 2a). Timeout subprocess 30s. Fail-closed: binario ausente / exit != 0 / JSON inválido / vacío / timeout → payload con `reason`, nunca raise; `resets_at` época-1970 → null; `used_percent` null se conserva.

S3 (tool): `check_quotas` sin parámetros (decisión 3a), sync, en `TOOLS_SCHEMA` + dispatch; resultado string compacto por proveedor/ventana con % usado y reset (coste si viene); la voz sale del loop LLM normal.

S4 (endpoint): `GET /quotas` junto a `/state`, clasificación read automática de AccessMiddleware; respuesta `{available, reason, stale, fetched_at, providers:[...]}`.

S5 (seguridad/fail-closed — riesgo 2 del doc 03-cuotas.md): "solo lectura, nunca log ni persistencia de respuestas con datos de cuenta" — caché solo en memoria, stderr del binario nunca se loguea ni persiste; `mask_text()` aplicado en el seam de serialización de `/quotas` y en el string de la tool. Sin cambios en static/ (EXPECTED_DIGESTS intactos).

S6 (tests): `hosts/herdr/brain/tests/test_quotas.py` sin red ni binario real (runner/binario inyectables): normalizador (época-0, nulls, balance sin label, claude con detail), finder, TTL/backoff/SWR, tool (ok + fallo + dispatch), endpoint (ok + no-disponible + masking), schema presente.

Fuera de alcance (fase B futura — "fase b"): adaptadores HTTP nativos por proveedor dentro de brain; modo daemon `ai-quotas serve`; UI/static; ROADMAP.md y docs del checkout principal (los maneja el lead).

## Tasks

T1 [S1,S2,S5] quotas.py (QuotaSource, AiQuotasSource, QuotaService, normalizador, finder) + unit tests — inline — RED→GREEN (30 tests) — commit 23a81fb; corrección verifier: protocolo+adaptador reales, kick fail-soft, isfinite — commit d91aae2
T2 [S3,S5] tool check_quotas (schema + dispatch + prewarm) + tests — inline — RED→GREEN (41 tests) — commit 6ab6e1e; corrección verifier: mask_text en el retorno + detail visible — commit d91aae2
T3 [S4,S5] endpoint /quotas + masking + tests — inline — RED→GREEN (47 tests) — commit 75e3b55; corrección verifier: kind/resets_at enmascarados + test 403 pairing — commit d91aae2
T4 [S6] suite brain unit/API completa + docs tool-surface + odd-verifier — suite 4914 passed (post-corrección), README 6133c62; odd-verifier: veredicto NEEDS_FIX (5 bloqueantes) recibido vía lead → TODOS corregidos con TDD, re-runs verdes — commit d91aae2

**Risk:** item 3 (nuevo endpoint read + tool LLM que exponen datos de cuota — mitigado: device-gated read como /state, mask_text en el seam, campos whitelisteados, caché solo en memoria).

## Log

L1 (2026-11-26, verbatim): "[herdr-fleet parity-post -> quotas] Arranca la investigación de solo lectura del hito POST-02 (Cuotas de proveedores, rama feat/post-02-quotas en /home/bruno/Code/personal/agent-tts-worktrees/post-02-quotas). Referencia: docs/paridad/13-fases-posteriores.md (§3) y docs/paridad/fases-posteriores/03-cuotas.md. Hecho verificado: /home/bruno/.local/bin/ai-quotas existe y 'ai-quotas check --json' saca un array JSON por stdout (progreso por stderr) cubriendo gemini, gemini-argon, gemini-3p, claude, chatgpt, deepseek y zai (tarda ~5-15s → caché TTL 5m + refresco no bloqueante). Lee tools.py, server.py, pane_model.py y presenta tu plan detallado y decisiones abiertas al operador en esta pestaña (sin editar código aún, ni tocar ROADMAP.md). Al terminar la implementación tras aprobación, aplica ODD en odd/tasks/post-02-quotas.md + odd-verifier y reporta vía herdr-fleet report."

L2 (2026-11-26, verbatim): "Una cosa, el tema de ya hay cuotas es simplemente para que lo tengas un poco en mente, pero no forma parte del proyecto Agents TTS.  Tendríamos que generar algo concreto para Agents TTS, ya sea partiendo de Agents Cuotas o de otra cosa. ai-quotas" → reencuadre: entregable = código propio de agent-tts; ai-quotas es fuente externa opcional.

L3 (2026-11-26, verbatim): "fase 1 A y fase b" → aprobada fundación A (ai-quotas detrás de QuotaSource) como fase 1; "fase b" (adaptadores nativos) queda registrada como fase futura fuera de este hito. Defaults 2a (prewarm + SWR no bloqueante) y 3a (tool sin params) aceptados.

L4: investigación: docs de paridad viven solo en el checkout principal (no en este worktree); ROADMAP.md fila POST-02 ya anticipa "integración ai-quotas". Datos reales del binario: 15 records/7 providers; edges: resets_at época-1970 (gemini-3p), used_percent null (balances), state stale (gemini-argon), claude detail "disabled: out_of_credits". Seams mapeados: TOOLS_SCHEMA tools.py:4218, dispatch tools.py:3915, composición server.py:1441, patrón /health server.py:2133, blueprints pane_model.py:485/495, tests StubTools en composition root (test_workspace_routes.py:50).

L5 (2026-11-26, evidencia T4): corrida 1 de suite: 3 fallas = pines de superficie de tools (test_tools:748, test_llm:224, test_consult:1182) + 9 errors test_mux_zellij_live + 1 fallo test_first_run TTY. Causa raíz propia encontrada y corregida: el prewarm de create_app localizaba el binario REAL (~/.local/bin/ai-quotas) en cada boot de test y corría el subprocess de verdad (~8s + red) → contención → flakes; el hook hermético de conftest ahora stubbea quotas.ai_quotas_binary (ecfc172). Diferencial vs base 975c1ac en scratch mktemp: zellij live 10 passed, TTY 6 passed (ambiental/flake, no regresión). Corrida 2: 4903 passed, 0 failed, 156s. Los 3 pines actualizados con check_quotas. odd-verifier independiente: primer intento fallido 2x por provider usage limit; el lead parity-post obtuvo el veredicto y lo retransmitió.

L6 (2026-11-26, ronda de corrección odd-verifier): veredicto NEEDS_FIX (S4 PASS; S1/S2/S5 FAIL; S3/S6 PARTIAL) sobre 2749b81. Los 5 bloqueantes verificados contra el código (todos ciertos) y corregidos con TDD en un commit: (1) CRITICAL — check_quotas() ahora retorna mask_text(...) (test: label sk-… sale [masked]); (2) HIGH — /quotas enmascara también kind y resets_at (normal ISO sobrevive intacto, testeado); (3) HIGH — QuotaSource (Protocol) + AiQuotasSource definidos; QuotaService acepta source= inyectable (tests: FakeSource cacheado sin re-consulta, source que raise → "error", default wiring es AiQuotasSource); (4) HIGH — _kick(): flag bajo lock, spawn fuera del lock con try/except que resetea el flag (tests: spawner que falla no atasca el servicio y reintenta luego; spawner inline no deadlea); (5) MEDIUM — _clean_number exige math.isfinite (NaN/±Inf → None, sin 500 de Starlette). Limpiezas: unused calls eliminado + assert stale False en failure-TTL; test_missing_binary restaura el finder real antes del monkeypatch (el hook de conftest lo cortocircuitaba); test 403 con pairing activo sin bearer (receta PairingStore de test_msg_routes); check_quotas muestra detail enmascarado cuando existe; format_reset captura (ValueError, OverflowError, OSError). Re-runs: test_quotas 59 passed; test_tools+test_consult+test_llm 217 passed; suite completa 4914 passed; ruff --select F,E9 clean (vía uvx, el env de brain no trae ruff).

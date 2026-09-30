# PRD 04 — Fallback entre proveedores

> **Estado: DRAFT.** Trazado a D1 (hito 4), D3, D5, D6, D7, D8, D9. Requiere Hitos 1–3.
> Diseño técnico preciso: **LOCKED en `../TECHNICAL-PLAN.md` T9** (config, clasificación
> de reintentos, presupuesto único por job, reglas de parcial audible); ejecución:
> `../TASKS.md` VS4.*. Cobertura/gates: README D4 (Python/JS), tooling T12, evidencias
> `../EXECUTION.md` §4–§6.
> **Fuentes (D8):** motor `engine/src/agent_tts`; host `hosts/herdr/tts-plugin`. Líneas
> re-verificadas en HEAD `384dd4f` (2ª revisión 2026-09-30); re-verificar vigente al lanzamiento.

## 1. Contexto y problema

Hoy un fallo de proveedor degrada DENTRO del mismo proveedor:

- Streaming roto → `_sync_streamed` devuelve `None` → reintento con respuesta COMPLETA del MISMO
  proveedor; cancelación de usuario → `b""` (audio vacío, sin reintento). Evidencia:
  `agent-tts/engine/src/agent_tts/providers/elevenlabs.py:141-168` y `providers/openai.py:131-158`
  (patrón idéntico); contrato `providers/base.py:24-63` con `stop_checker` cooperativo.
- **No existe fallback entre proveedores** en ninguna parte inspeccionada (motor, hosts, herdr-tts).
  Si el proveedor configurado falla por completo, la voz se pierde para esa solicitud.

El usuario quiere degradación cruzada EXPLÍCITA y segura (D3): OFF por defecto, solo con
proveedores/voces/privacidad-costo configurados por el operador; la cancelación nunca dispara
fallback; sin audio ni coste duplicados tras parciales ya audibles.

## 2A. Objetivos y no-objetivos

**Objetivos**

1. Cadena de fallback configurable por operador: proveedor primario → [secundarios…] con voz y
   notas de privacidad/costo por eslabón **[PROPUESTA: configuración suministrada por el operador]**.
2. OFF por defecto: sin configuración explícita, el comportamiento es EXACTAMENTE el actual.
3. El trigger de fallback es SOLO fallo técnico no-cancelación (red/HTTP/error de proveedor).
4. Tras un parcial ya audible, el fallback MUST NO reiniciar el texto completo ni duplicar voz ya
   oída (continúa, resume o se abstiene honestamente — decisión de diseño documentada con
   evidencia). El coste adicional que el proveedor realmente facture por el audio restante PUEDE
   ser inevitable: no se garantiza coste cero; se acota con intentos configurados por eslabón, tope
   de reintentos y ausencia de submissions duplicadas accidentales.

**No-objetivos (D3/D7)**

- Catálogo nuevo de proveedores ni sustituir PortAudio (D7); los eslabones son proveedores ya
  soportados por el motor (`edge`, `openai`, `elevenlabs`, `piper`, opcional `kokoro` —
  `providers/__init__.py` y extras de `engine/pyproject.toml`).
- Auto-selección "inteligente" de proveedor sin configuración del operador.
- Mover la configuración física de proveedores: claves/endpoints las aporta el operador; los mocks
  de CI NUNCA autorizan llamadas cloud reales (D3).

## 2B. Arquitectura y límites de confianza

- **Punto de extensión:** la envoltura de decisión vive en la ORQUESTACIÓN del motor (donde ya se
  decide streaming vs batch y ya se contabiliza el parcial audible — `cli.py:292-324,462-497` y el
  builder de proveedor del daemon `daemon.py` `_build_engine` con payload de proveedor por
  solicitud). Los `TTSProvider` concretos NO se reescriben; se reutiliza su contrato
  (`base.py:24-63`) y su semántica de error verificada.
- **Reserva de equivalencia de voz:** mapa operador `{proveedor → voz}` **[PROPUESTA —
  LOCKED en T9: config genérico del motor, env `AGENT_TTS_FALLBACK_CONFIG` con default
  `~/.config/agent-tts/fallback.json`, schema estricto con `notes`
  obligatorio por eslabón]**; sin voz mapeada, el eslabón se salta honestamente
  (log/estado), sin adivinar.
- **Límites de confianza:** la configuración de fallback es local del operador (su máquina/servicio);
  jamás se deduce de respuestas de proveedores; nada de claves en artefactos commiteables
  (convención vigente: secretos fuera del repo).
- **Cancelación (Hito 1):** `stop_checker` activo durante TODOS los eslabones; un stop mientras cae
  al secundario aborta también ahí (`stop_checker` ya existe en `synthesize_stream`,
  `base.py:54-56`).

## 2C. Esquema de datos y FSM

Configuración **[PROPUESTA — schema y semántica LOCKED en T9: config genérico del MOTOR
(env `AGENT_TTS_FALLBACK_CONFIG`, default `~/.config/agent-tts/fallback.json`, patrón
vigente `cleaner.py:206-210` — sin dependencia de host), `notes` obligatorio, validación
estricta, ausencia de fichero = OFF = comportamiento actual idéntico]**:

```json
{
  "fallback_enabled": false,
  "chain": [
    {"provider": "openai", "voice": "nova", "notes": "cost: paid; data: cloud"},
    {"provider": "edge", "voice": "es-ES-ElviraNeural", "notes": "cost: free; data: cloud"}
  ]
}
```

FSM de un intento de síntesis **[PROPUESTA — LOCKED en T9]**:

```
idle ──solicitud──▶ primary_stream
primary_stream ──fallo técnico──▶ primary_batch            (EXISTENTE, no reimplementar)
primary_batch ──fallo técnico Y fallback_enabled Y quedan eslabones Y presupuesto──▶ next_provider
next_provider ──fallo──▶ … (hasta agotar eslabones O presupuesto 4/2) ──▶ failed_visible
cualquier estado ──stop_checker/cancel──▶ cancelled (NUNCA dispara fallback — D3)
primary_stream|batch|next ──éxito──▶ succeeded(provider=<nombre>) para evidencia
```

Regla anti-duplicado **[LOCKED en T9]**: el fallback automático re-sintetiza SÓLO bytes
que NUNCA se reprodujeron; si algo YA fue audible, se detiene con estado
`partial/uncertain` visible y la salida es REPLAY DELIBERADO (usuario) del texto pendiente
con POSIBLES DUPLICADOS explícitamente reconocidos — NO existe ni se afirma una
"alineación de texto restante exacta mid-group" entre proveedores. La contabilidad
reutiliza la disciplina de `rendered_chunks`/persistencia única
(`cli.py:320-328,462-497`). Casos fijados: modo frames ⇒ sin cross-fallback automático
tras parcial audible (abstención); modo fichero/sin-play (nada audible) ⇒ restart
completo en el siguiente eslabón PERMITIDO. Presupuesto único por job:
`FALLBACK_TOTAL_ATTEMPTS=4` cubre juntos stream→batch y cross-provider; 2 por eslabón;
chequeo de clave ANTES de cualquier submit; `stop_checker` antes de cada intento. Coste:
acotado por los intentos, posible y visible en evidencia — nunca se afirma coste cero.

## 2D. Seguridad y modelo de amenazas

- `fallback_enabled=false` por defecto: ausencia de fichero = comportamiento actual bit a bit.
- Notas de privacidad/costo por eslabón: la configuración las EXIGE (campo obligatorio) para que el
  operador decida a ciegas nunca (D3).
- Los tests/E2E usan proveedores fake deterministas; llamadas reales solo con autorización expresa
  del operador y fuera del gate por defecto (D5).
- Un eslabón que requiera clave ausente se considera no-configurado: se salta con estado honesto,
  sin pedir claves por artefactos ni logs.

## 2E. Métricas y criterios de aceptación

- **Aceptación (D3/D6):** con fallback configurado y primario simulado fallando técnicamente, la
  voz se entrega por el secundario SIN reiniciar el texto ni duplicar voz ya oída (evidencia por
  contadores del simulador: cero bytes re-sintetizados de lo ya audible, cero submissions
  duplicadas accidentales); el coste de facturación adicional que el flujo realmente genere es
  posible, visible y acotado por los intentos configurados; con cancelación concurrente, NINGÚN
  intento posterior al stop; sin configuración, deltas de comportamiento = ninguno.
- **No-regresión:** la cobertura del motor se mantiene según README D4 + TECHNICAL-PLAN T12
  + TASKS (tabla de gates), evidencias en `EXECUTION.md` §4; los tests de
  proveedores existentes (`engine/tests/test_providers.py`, `test_stream*.py`) siguen en verde.

## 3. Requisitos (RFC 2119) con trazabilidad y evidencia

| FR | Requisito | Traza | Evidencia / supuesto |
|----|-----------|-------|----------------------|
| FR-01 | El fallback entre proveedores MUST estar OFF por defecto; sin configuración explícita el comportamiento MUST ser idéntico al actual. | D3 | Base actual sin fallback: `elevenlabs.py:141-168`, `openai.py:131-158` |
| FR-02 | La cadena de fallback MUST definirla el operador: proveedores, voces y notas de privacidad/costo obligatorias por eslabón. | D3 | [PROPUESTA] de esquema |
| FR-03 | La cancelación de usuario MUST NOT disparar fallback; ningún eslabón se intenta tras un stop. | D3 | `stop_checker` existe: `base.py:54-56`; semántica `b""` verificada |
| FR-04 | El retry stream→batch del MISMO proveedor MUST reutilizarse tal cual, sin duplicarlo en la nueva capa. | D3 | EXISTENTE: `_sync_streamed`→`_sync_request` |
| FR-05 | Tras parcial audible, el fallback MUST NOT reiniciar el texto completo ni duplicar voz ya oída; el coste adicional de facturación real MAY ocurrir y ser visible — nunca se garantiza coste cero — con intentos configurados por eslabón, tope de reintentos y sin submissions duplicadas accidentales. | D3 | Contabilidad de parcial: `cli.py:320-328,462-497`; corrección 2026-09-30 |
| FR-06 | La decisión de fallback MUST vivir en la orquestación del motor reutilizando el contrato `TTSProvider`, sin reescribir proveedores. | D1 | `base.py:24-63`; `_build_engine` del daemon |
| FR-07 | Cada intento MUST registrarse con resultado y proveedor para evidencia (éxito/fallo/cancel/skip). | D5, D6 | [PROPUESTA] |
| FR-08 | Eslabones sin clave/voz mapeada MUST saltarse con estado honesto, nunca con intento a ciegas. | D3, D5 | — |
| FR-09 | Las pruebas automatizadas MUST usar proveedores simulados; llamadas reales solo con autorización expresa del operador. | D3, D5 | Restricción de verificación |

## 4. Escenarios de prueba y resultado observable esperado

- **Unit (motor):** con fakes que fallan técnicamente: cadena primario→secundario entrega audio;
  stop a mitad del primario → cero llamadas al secundario (contador del fake); parcial audible +
  fallo → stop visible SIN re-sintetizar automáticamente lo ya oído (replay deliberado con
  duplicados posibles reconocidos en la evidencia); los intentos
  están capados por configuración (aserción sobre bytes/contadores simulados); clave ausente →
  skip honesto; sin configuración → comportamiento exactamente el actual (tests actuales sin cambios).
- **Contract:** schema de configuración validado (eslabones, notas obligatorias, off por defecto);
  errores tipados por eslabón.
- **Integración:** daemon + host con fakes: anuncio/respuesta con primario caído suena por
  secundario; combinado con Hito 3, un anuncio diferido degradado queda registrado visible si
  TODOS los eslabones fallan.
- **E2E navegador (D6):** respuesta larga con fallo del proveedor simulado a mitad, con fixtures
  decodificados reales (no sólo eventos simulados): el teléfono oye la respuesta completa
  (secundario) sin repetir el comienzo; cancelación durante el fallback → silencio inmediato y sin
  intentos extra (evidencia de contadores).
- **Manual físico:** proveedores externos reales configurados por el operador, si el usuario
  decide probarlos con sus claves (`EXECUTION.md` §7 y §8).

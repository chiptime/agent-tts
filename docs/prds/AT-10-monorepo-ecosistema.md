**ID**: PRD-AT-10 · **Proyecto**: ecosistema agent-tts (motor + packs de host)
**Prioridad**: P2 · **Estado**: Aprobada por el maintainer (24/09/2026)
**Dependencias**: Ninguna bloqueante; se ejecuta tras el cierre del BLOQUE 1 (AT-09 + AT-04 + AT-08)

# PRD-AT-10 — Monorepo del ecosistema: engine + contracts + hosts

**Prioridad**: Media · **Esfuerzo**: L

### Resumen ejecutivo

Hoy el ecosistema vive repartido en tres repos separados — `agent-tts` (motor), `herdr-tts` (plugin de host) y `herdr-brain` (cerebro conversacional) — unidos por pins de commit hash y contratos replicados a mano. Cada feature del motor obliga a una danza de tres repos: commit en `agent-tts` → actualizar el pin hash en `herdr-tts` → ajustar `herdr-brain`. Con un solo host eso ya genera fricción real; con los packs proyectados (opencode, orca) se multiplica por N. Esta PRD propone consolidar el ecosistema en **un monorepo con `agent-tts` como repo raíz**, organizado por componentes con contratos versionados como única fuente de verdad, preservando la distribución por artefactos (uv/pip, npm, homebrew) y sin pérdida de historial Git.

### Problema y flujo actual

El acoplamiento entre repos ya es contractual, no casual:

1. **Pin por hash de commit**: `herdr-tts/scripts/install.sh` instala el motor vía `git+https://github.com/chiptime/agent-tts.git@<40-hex>`, y `scripts/smoke-tests.sh` valida el formato del pin por regex (`PIN_RE`, `PIN_PY_RE`).
2. **La danza de pins es recurrente**: el último commit de `herdr-tts` es literalmente `build(bootstrap) pin agent-tts 846915c: IPC karaoke en playback remoto` — un bump de pin por una feature del motor.
3. **Contrato replicado**: `herdr-brain` consume la superficie CLI de `herdr-tts` (contrato v1) definida fuera de su propio repo.

**Coste en el flujo maintainer** (criterio flow-first): el flujo diario es dirigir flotas de agentes por voz en Herdr bajo WSL2. Cada mejora del canal de control (AT-09), del daemon (AT-04) o de la cola (AT-08) llega a las flotas a través del plugin: la danza de tres repos añade latencia a *cada* entrega del paquete P1, multiplica los puntos de fallo (hash desactualizado, regex rota, brain desalineado) y hace que agregar el siguiente host exija montar infraestructura de repos y CI desde cero.

### Propuesta

Un monorepo con `agent-tts` como repo raíz, donde **la raíz del repo no implica dependencia padre**: el motor no contiene a los hosts; los hosts dependen del motor exclusivamente vía `contracts/`.

```
agent-tts/                      (repo raíz, nombre sin cambios)
├── engine/                     ← motor actual tal cual (src/, tests/, pyproject.toml, uv.lock…)
├── contracts/                  ← IPC contract (engine↔plugin) y tts↔brain contract v1
├── hosts/
│   ├── herdr/
│   │   ├── tts-plugin/         ← herdr-tts (bin/, lib/, scripts/, packaging/, herdr-plugin.toml, openspec/)
│   │   └── brain/              ← herdr-brain (src/, tests/, deploy/, scripts/)
│   ├── opencode/               ← pack futuro (patrón idéntico a herdr/)
│   └── orca/                   ← pack futuro (patrón idéntico a herdr/)
└── docs/
```

Decisiones de diseño:

| Área | Decisión |
|---|---|
| Distribución | Por artefactos, nunca clonando el repo: uv/pip (engine), npm y homebrew (packs de host, desde `packaging/`) |
| Pin del motor | Sobrevive intacto: `git+https://github.com/chiptime/agent-tts.git@<hash>#subdirectory=engine` — cambio de una línea en `install.sh` y ajuste de `PIN_RE` |
| Versionado | Por componente, con tags anotados prefijados: `engine/vX.Y.Z`, `herdr-tts/vX.Y.Z`. Nunca versión repo-wide |
| Regla de dependencia | `hosts/*` → `engine` vía `contracts/`; **jamás** `engine` → `hosts` (verificable por test de frontera) |
| CI | Filtrado por path: tests del engine si se toca `engine/`, del plugin si se toca `hosts/herdr/tts-plugin/`, de contrato cruzado si se tocan ambos |
| Standalone | `engine/` sigue instalable aislado (`pip install agent-tts` / `uv`) para usuarios sin Herdr; nada cambia para ellos |
| Historial | Preservación completa: movida pura del motor a `engine/` (navegable con `git log --follow`) y absorción de los otros dos repos con `git filter-repo --to-subdirectory-filter` + `merge --allow-unrelated-histories` |
| Remotes antiguos | Tras la migración, `herdr-tts` y `herdr-brain` se archivan read-only: los pins viejos de instalaciones existentes siguen resolviendo |

### Historias de usuario (US-AT-10-…)

- US-AT-10-1: Como maintainer que dirige flotas por voz, quiero que un cambio de contrato IPC aterrice en un solo commit y PR, para entregar features del paquete P1 sin danza de pins entre tres repos.
- US-AT-10-2: Como maintainer, quiero agregar un pack de host nuevo (orca, opencode) replicando el patrón de `hosts/herdr/`, sin montar repos, CI ni packaging nuevos.
- US-AT-10-3: Como usuario de agent-tts sin Herdr, quiero seguir instalando el motor standalone exactamente igual que hoy.
- US-AT-10-4: Como maintainer, quiero que `contracts/` sea la única fuente de verdad del IPC y del contrato v1, versionada y verificada por test.

### Requisitos funcionales (RF-AT-10-…)

- RF-AT-10-1: `git log --follow engine/…` resuelve la historia pre-migración del motor sin cortes.
- RF-AT-10-2: `hosts/herdr/tts-plugin/scripts/install.sh` instala el motor desde el monorepo con pin `@<hash>#subdirectory=engine`; los smoke tests adaptados fallan ante cualquier referencia residual a los repos antiguos.
- RF-AT-10-3: Cada componente es versionable y releasable de forma independiente mediante tags prefijados, sin tocar los demás.
- RF-AT-10-4: La CI ejecuta únicamente los tests del área tocada, más los de contrato cruzado cuando `contracts/` o dos áreas acopladas cambian.
- RF-AT-10-5: Un test de frontera falla si cualquier módulo de `engine/` importa o invoca código de `hosts/`.
- RF-AT-10-6: `pip install agent-tts` (o vía uv) desde PyPI/git funciona standalone sin clonar el monorepo.
- RF-AT-10-7: Los builds npm y homebrew se generan desde `hosts/herdr/tts-plugin/packaging/` sin requerir el árbol completo en el artefacto final.

### Requisitos no funcionales (RNF-AT-10-…)

- RNF-AT-10-1: Migración sin pérdida de historial: los tres historiales completos quedan navegables en el monorepo (autor, fecha, mensaje).
- RNF-AT-10-2: Reversibilidad hasta la Fase 5: tags de backup creados antes de cada fase destructiva; el archivado de remotes antiguos es el punto de no retorno.
- RNF-AT-10-3: Cero ruptura para instalaciones existentes: cualquier pin/instalación vigente al día de la migración sigue funcionando sin actualización.

### Plan por fases

1. **Fase 0 — Acuerdos**: ~~esta PRD aprobada y las preguntas abiertas resueltas~~ ✅ Decisiones 1–4 resueltas (24/09/2026).
2. **Fase 1 — `engine/`**: mover el contenido raíz a `engine/` en un único commit de movida pura (`git mv`, sin cambios de contenido). Historial preservado vía `--follow`.
3. **Fase 2 — Absorción de hosts**: para cada repo (herdr-tts, herdr-brain): clon fresco → `git filter-repo --to-subdirectory-filter hosts/herdr/<comp>` → `remote add` + `fetch` + `merge --allow-unrelated-histories` al monorepo. Tags de backup antes de tocar.
4. **Fase 3 — `contracts/` y pins**: extraer el IPC contract y el contrato v1 a `contracts/`; actualizar `install.sh` a `#subdirectory=engine`; adaptar `PIN_RE`/`PIN_PY_RE` en smoke tests.
5. **Fase 4 — CI y releases**: workflow con filtros de path; releases por componente con tags prefijados; packaging npm/homebrew apuntando a subdirectorios; test de frontera engine→hosts.
6. **Fase 5 — Cierre**: verificación end-to-end (uv, npm, brew), archivado read-only de los remotes antiguos, actualización de READMEs.

### Garantías de compatibilidad verificadas por test

Criterio transversal del roadmap: cada fase aterriza con su garantía verificada por test. Los smoke tests existentes del plugin (que hoy validan el formato del pin) se adaptan en la Fase 3 y pasan contra el monorepo antes de la Fase 4; el bootstrap end-to-end (venv + uv + motor pineado) se verifica por test en cada fase posterior a la movida. Donde una fase conserva un camino antiguo (pins a repos archivados), ese camino se verifica por test; donde lo elimina (referencias al repo viejo en `install.sh`), la garantía equivalente es el test que falla ante su presencia (RF-AT-10-2).

### Decisiones del maintainer (24/09/2026)

Resueltas en sesión de grill, una por una, con contexto de tradeoffs:

1. **Nombre del repo**: mantener `agent-tts`. Renombrar más adelante es casi gratis (redirects automáticos de GitHub); los nombres de paquetes PyPI/npm no cambian en ningún caso. Decisión de re-evaluación: solo si el ecosistema público lo pide cuando existan 2+ packs.
2. **Remotes antiguos**: tombstone + archive. Commit final en cada repo viejo con README apuntando al monorepo (`hosts/herdr/…`), luego GitHub Archive read-only. Se ejecuta en la Fase 5. Verificado en sesión: cero issues y cero PRs abiertas en ambos repos al 24/09/2026, archivar no congela nada en vuelo.
3. **Semilla de `contracts/`**: extraer de tests + docs. `ipc-v1.md` y `tts-brain-v1.md` se destilan del código y de lo que los smoke tests fuerzan (fuente normativa), con puntero *verified by* a cada test que los impone. Las PRDs quedan como historial de diseño; `contracts/` es normativo. Regla de oro: se documenta lo que los tests fuerzan, no lo que las PRDs prometieron.
4. **Orden frente al BLOQUE 1**: BLOQUE 1 primero. AT-04 y AT-08 se cierran en la estructura actual; la migración arranca al cierre del paquete. Coste aceptado por el maintainer: dos bumps de pin más (daemon y cola cambian semántica consumida por herdr-tts) y re-extracción del contrato IPC tras los cambios de semántica del daemon. Restricción dura registrada: el trabajo IPC en vuelo (árbol sucio en main al 24/09/2026) aterriza antes de cualquier fase de migración — la Fase 1 exige commit de movida pura sobre árbol limpio.

Supuestos de partida aceptados sin objeción: la estructura `engine/` + `contracts/` + `hosts/` es la base correcta; preservar historial Git es requisito; la distribución sigue siendo por artefactos.

## Checklist

- [x] PRD-AT-10 leída y aprobada por el maintainer — veredicto 24/09/2026: «tiene buena pinta»
- [x] Preguntas abiertas 1–4 resueltas (24/09/2026)
- [x] Fila añadida al índice de `docs/prds/README.md`
- [x] Trabajo IPC en vuelo aterrizado (precondición dura de la Fase 1)
- [x] BLOQUE 1 cerrado (AT-09 + AT-04 + AT-08)
- [x] Fases 1–5 ejecutadas con sus garantías de test
- [ ] Remotes antiguos con tombstone y archivados; verificación end-to-end completa

## Next step

Lectura completa y aprobación de esta PRD por el maintainer; luego aterrizar el trabajo IPC en vuelo y cerrar el BLOQUE 1 (AT-04, AT-08). Al cierre del paquete, ejecutar la Fase 1 (movida pura a `engine/`) en una rama dedicada.

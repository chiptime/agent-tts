# BLOQUE 2 — Identidad vocal completa (HT-02 + HT-11)
**Alcance**: PRD-HT-02 + PRD-HT-11 · **Prioridad**: P1 · **Esfuerzo agregado**: S-M (HT-02 ya implementada en paralelo)
**Repositorio**: herdr-tts · **Estado**: Aprobado (revisión 22/09/2026)

### Objetivo del bloque

Entregar la identidad vocal como experiencia completa de extremo a extremo: escuchar una voz antes de asignarla, asignarla al chat seleccionado o de forma global desde la misma superficie, y oír los eventos de ese agente con esa voz, sin editar ficheros a mano ni ritual de reinicio. HT-02 aporta la identidad persistente y audible (resolución de voz por llamada en el watcher); HT-11 aporta la audición de voces en la paleta. El motor agent-tts no cambia nada: `--voice` por llamada ya existe.

Esta PRD es la capa de orquestación del paquete: los RF, RNF, métricas y fuera de alcance detallados viven en las PRD fuente y se referencian por ruta relativa. El valor de este documento es el merge, el porqué, la secuencia y la definición de done.

### Por qué un solo bloque (rationale del merge)

- HT-11 es la UX que completa a HT-02: la nota de revisión del 22/09/2026 en `../HT-11-audicion-voces.md` ya las declara compañeras de la misma fase. Ambas viven en las mismas superficies: la paleta fzf, el roster del dashboard, `voices.json` y la escritura gestionada de config.
- Aterrizar HT-02 sin HT-11 reproduce exactamente la fricción que motivó el bloque: asignar voces a ciegas editando config. Elegir una voz leyendo nombres (`--voice alvaro`), editar, reiniciar el daemon y esperar un evento real para saber cómo suena desincentiva explorar y ancla al usuario a la voz default.
- Juntas convierten la identidad vocal en experiencia completa desde el día uno: asignación de oído, comparación de voces en segundos, y cambio de voz sin reinicio, todo sin salir de la paleta.
- El coste del merge es marginal porque las piezas ya existen: el motor ya acepta `--voice` por llamada, la paleta ya dispara reproducciones por el camino del mutex (`ctrl-r`), y Ajustes ya persiste config con escritura gestionada atómica (tmp + mv).

### Estado de partida REAL (importante)

Este bloque NO empieza desde cero. Estado verificado del repo a 22/09/2026:

- HT-02 ya está IMPLEMENTADA por una sesión paralela: 520 tests verdes y WIP sin commitear en `bin/herdr-tts`, `README.md`, `herdr-plugin.toml` y `scripts/smoke-tests.sh`.
- El WIP incluye: `voices.json` con el esquema de su PRD, precedencia pane > agent > global con fail-open a voz global, `--voice-for`, prefijo hablado opcional, auto-asignación opt-in, y glifo de voz en dashboard y paleta.
- Popups/Ajustes al 55% según el tracker: trabajo en curso de esa misma sesión paralela, fuera del alcance de este bloque salvo por lo que HT-11 necesite de él.
- HT-11 no está empezada.

Consecuencia operativa: el bloque empieza por cerrar y commitear ese WIP (Hito 0), con un solo writer por sesión y en commits separados de cualquier otro WIP del árbol. Nada de HT-11 se abre sobre `bin/herdr-tts` mientras el WIP de HT-02 siga sin commitear.

### Secuencia de ejecución: hitos con criterios de aceptación

**Hito 0 — Cerrar el WIP de HT-02**
Revisar el WIP sin commitear de la sesión paralela, commitearlo en work units limpios (el trabajo de voces separado de cualquier otro WIP, p. ej. Ajustes) y verificar la batería de smoke tests existente.
- Criterios de aceptación: worktree limpio de ficheros con cambios HT-02 (nada de voces pendiente en `bin/herdr-tts`, `README.md`, `herdr-plugin.toml`, `scripts/smoke-tests.sh`); batería de smoke tests existente en verde.

**Hito 1 — HT-11: audición de voces en la paleta**
Implementar HT-11 según sus RF en `../HT-11-audicion-voces.md`. Contenido del hito:
- Vista de voces en la paleta (tecla `v`): listado de las voces del proveedor activo obtenido del voice manager del motor (el host no mantiene su propia lista).
- Preview de frase fija bilingüe (español e inglés) sintetizada con la voz bajo el cursor, por el mismo camino de reproducción y mutex que el daemon.
- Auto-stop en < 0.3 s al navegar el cursor o salir de la vista.
- Caché de samples por (proveedor, voz) con purga LRU de 20 MB; la caché nunca sustituye la voz real de los eventos.
- `enter` = voz global (persiste `TTS_VOICE` con escritura gestionada); tecla dedicada = voz del chat seleccionado vía `voices.json` de HT-02.
- Criterios de aceptación: preview con caché inicia en < 0.3 s; cero solapes de audio con el daemon (mismo camino mutex); proveedor sin claves/modelo instalado: aviso claro y cero llamadas de red.

**Hito 2 — Cierre e integración**
- Escenarios nuevos en `scripts/smoke-tests.sh` para el flujo de voces: asignar por chat, preview y caché.
- Actualización de `README.md` (flujo de voces) y del índice `../README.md` (estado de HT-02/HT-11).
- Criterios de aceptación: batería ampliada en verde; las métricas de éxito de HT-02 definidas en su PRD quedan instrumentadas para el autoinforme a 2 semanas.

### Qué NO se incluye

- Cambios en el motor agent-tts: el bloque no toca el repo hermano; `--voice` por llamada ya existe y no necesita extensiones.
- Voces por proyecto/workspace, clonación de voz y mezcla de proveedores por agente (fuera de alcance declarado en `../HT-02-voces-por-agente.md`).
- Parametrizar la frase de preview desde la UI, fine-tuning o mezcla de voces, y preview de rate/velocidad (fuera de alcance declarado en `../HT-11-audicion-voces.md`).
- Terminar los popups/Ajustes al 55%: pertenecen a la sesión paralela, no a este bloque.

### Dependencias y prerrequisitos

- Ninguna de motor: `--voice` por llamada existe hoy (declarado en `../HT-02-voces-por-agente.md`).
- HT-11 depende de HT-02 para la asignación por chat (RF-HT-11-5); la asignación global funciona de forma autónoma. Por esto el Hito 1 va después del Hito 0, nunca en paralelo.
- Superficies ya existentes que el bloque reutiliza sin modificar: paleta fzf con reproducción por el camino del mutex (`ctrl-r`), escritura gestionada atómica (tmp + mv) usada por Ajustes, roster del dashboard que ya fusiona por `pane_id`.
- Prerrequisito operativo: disciplina de un solo writer sobre `bin/herdr-tts` mientras conviva WIP de otras sesiones en el árbol.

### Superficies compartidas y zonas de conflicto

- `bin/herdr-tts` (host monolítico): lo tocan el WIP de HT-02, HT-11 y bloques futuros (HT-04, HT-05). Zona de conflicto principal del roadmap.
- Paleta fzf: la vista de voces convive con las vistas existentes y con las que añadan bloques posteriores; mismas teclas y mismo preview.
- `voices.json`: tras el merge tiene dos entradas de escritura — el CLI (`--voice-for`) y la tecla dedicada de la paleta (HT-11). Ambas deben compartir validación al cargar y escritura gestionada para no divergir en el esquema.
- `config.env`: `TTS_VOICE` global se escribe desde HT-11 (`enter`) por la escritura gestionada existente.
- `README.md`, `herdr-plugin.toml`, `scripts/smoke-tests.sh`: tocados por el WIP de HT-02 y por el Hito 2; son los mismos ficheros que necesitarán otros bloques.
- Directorio de caché del plugin: namespace nuevo para los samples de preview (LRU 20 MB), sin colisión con cachés existentes.

### Riesgos específicos del paquete

- El WIP sucio toca los mismos ficheros que bloques futuros (`bin/`, `README.md`, `herdr-plugin.toml`, `scripts/`) → un solo writer por sesión; el Hito 0 commitea el WIP de HT-02 separado de cualquier otro WIP antes de abrir HT-11.
- Preview en proveedores de pago quema cuota → caché obligatoria por (proveedor, voz), frase corta fija, síntesis solo bajo demanda.
- fzf + audio asíncrono → auto-stop robusto con identificador de job: mover el cursor o salir de la vista corta el preview en < 0.3 s (RF-HT-11-3).
- Dos entradas de escritura sobre `voices.json` (CLI y paleta) → misma validación y misma escritura gestionada en ambas; el fail-open a voz global de HT-02 ya cubre la deriva del fichero.

### Definición de done del bloque

- HT-02 conmutable a "Aprobada-completa" en el índice `../README.md`: implementada, commiteada y con batería en verde.
- HT-11 implementada según sus RF en `../HT-11-audicion-voces.md`.
- Flujo completo "escuchar → asignar → escuchar hablado con esa voz" verificable end-to-end desde la paleta, sin editar ficheros ni reiniciar el daemon.
- Batería de smoke tests ampliada en verde y worktree limpio de WIP de este bloque.

### Referencias

- `../HT-02-voces-por-agente.md` — PRD fuente HT-02 (RF, RNF, métricas, fuera de alcance).
- `../HT-11-audicion-voces.md` — PRD fuente HT-11 (RF, RNF, métricas, fuera de alcance).
- `../README.md` — índice del roadmap 2026 y orden de ataque (Fase 1: HT-02 + HT-11 como identidad vocal completa).

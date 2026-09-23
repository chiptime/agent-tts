**ID**: PRD-HT-14 · **Proyecto**: herdr-tts
**Prioridad final (revisión 2026-09-23)**: P2 (propuesta, pendiente de revisión del maintainer) · **Estado**: Borrador
**Dependencias**: `settings-category-submenus` (navegación de Ajustes por categorías, ya en el árbol de trabajo)

# PRD-HT-14 — Tema claro configurable

**Prioridad**: Media · **Esfuerzo**: S

## Resumen ejecutivo

Los paneles que herdr-tts renderiza (dashboard, voice-palette, voice-menu, voice-settings, `--status` y el roster incrustado en la salida del agente) usan colores SGR básicos fijados en el código, elegidos para terminales de fondo oscuro. En terminales de fondo claro, el gris `90` resulta casi ilegible y el cian `36` pierde contraste. Esta PRD introduce un tema claro opt-in, seleccionable desde Ajustes, sin alterar un solo byte de la salida actual mientras el tema por defecto (`dark`) esté activo.

## Problema y flujo actual

- Todos los colores están hardcodeados inline sin paleta central: estado del motor en el dashboard (`bin/herdr-tts` L3852–3855), estados de agente del roster (L3919–3922) y títulos con `\e[1m` embebido en las cadenas i18n `TT_EN`/`TT_ES` (L433–437 EN, L910–914 ES; títulos de menú L393/L870).
- No existe ningún mecanismo de tema: ni variable de entorno, ni clave de config, ni centralización. (`PALETTE_*` en L4159–4161 son límites de ancho de la paleta, no colores.)
- Un operador con esquema claro ve el roster y los popups con texto atenuado ilegible; hoy su única opción es cambiar el esquema del terminal, lo que a su vez degrada todo lo demás.

## Propuesta

1. **Clave gestionada nueva `TTS_THEME`** (`dark` | `light`, default `dark`): añadida a la whitelist de `config_set` (L1198) y al bloque de defaults (`TTS_THEME="${TTS_THEME:-dark}"`), con la escritura atómica + `.bak` existente (L1195–1270).
2. **Resolución semántica de color**: función `theme_color <token>` que resuelve tokens semánticos (`ok`, `warn`, `error`, `accent`, `muted`, `title`) según `TTS_THEME`, con dos mapas literales dentro del script:

   | Token | Uso actual | dark (default) | light |
   |---|---|---|---|
   | `ok` | verde | `32` | `32` |
   | `warn` | amarillo | `33` | `1;33` (bold+33) |
   | `error` | rojo | `31` | `31` |
   | `accent` | cian | `36` | `34` |
   | `muted` | gris atenuado | `90` | `30` |
   | `title` | negrita | `1` | `1` |

   Con `dark` la salida es idéntica bit a bit a la actual. Los valores `light` son el punto de partida; decisión 2026-09-23: `warn` en claro usa bold+`33`, y el resto se ajusta en la pasada visual con un esquema claro de prueba (ver Métricas).
3. **Sustitución progresiva**: los puntos de color citados pasan a llamar `theme_color`; títulos i18n mantienen el bold literal (seguro en ambos temas) y delegan el resto al resolver. Superficies cubiertas: dashboard, voice-palette, voice-menu, voice-settings, `--status` y roster.
4. **Ajustes**: nueva categoría **Apariencia** (decisión del maintainer, 2026-09-23) con el knob `t` (ciclo `dark ↔ light`), siguiendo el contrato de extensión de `settings-category-submenus` (design.md L55): brazo `case` en el dispatch de la vista + fila `_dash_add` en el render de esa vista + hint `*)` de la vista en la misma edición; assertions del escenario RED primero. El propio popup re-renderiza al ciclar, así que el feedback visual es inmediato antes de persistir.
5. **i18n bilingüe**: todas las cadenas nuevas (categoría, knob, valores, notas de error) en `TT_EN` y `TT_ES`.

## Historias de usuario

- **US-HT-14-1**: Como operador con terminal de fondo claro, quiero activar el tema claro desde Ajustes → Apariencia, para leer dashboard, roster y popups sin esfuerzo visual.
- **US-HT-14-2**: Como operador, quiero ver el efecto del tema al instante en el propio popup y que la elección persista en `config.env` tras reiniciar el daemon.
- **US-HT-14-3**: Como operador con fondo oscuro, quiero que nada cambie por defecto: con `TTS_THEME=dark` la salida debe ser idéntica a la actual.

## Requisitos funcionales

- **RF-HT-14-1**: `TTS_THEME` es clave gestionada: `config_set` acepta `dark`/`light` y rechaza cualquier otro valor con rc 1 y stderr descriptivo (patrón de whitelist actual).
- **RF-HT-14-2**: existe `theme_color <token>` como único punto del script que emite códigos SGR de color; con `dark` reproduce los códigos actuales exactos (compatibilidad de salida).
- **RF-HT-14-3**: el mapa `light` sustituye como mínimo `muted 90→30` y `accent 36→34`; ningún token puede resolverse en claro a un código de contraste insuficiente sobre fondo blanco (criterio: legible en un esquema claro estándar).
- **RF-HT-14-4**: Ajustes gana la categoría Apariencia con el knob `t` que cicla `dark ↔ light`, actualiza la variable viva, persiste vía `config_set` y usa `SETTINGS_NOTE`/`SETTINGS_WARN` según el resultado (patrón del knob `p` de proveedor, L4788–4795).
- **RF-HT-14-5**: el cambio aplica a todas las superficies renderizadas por el plugin listadas en la Propuesta; el texto sin color y los resets (`\033[0m`) no se alteran.
- **RF-HT-14-6**: cadenas nuevas bilingües EN/ES en las tablas i18n.
- **RF-HT-14-7**: smoke tests RED-first: escenario de settings para el knob nuevo (contrato `settings-category-submenus`), snapshot de salida con `dark` idéntico al pre-cambio, whitelist de `config_set` acepta/rechaza, y persistencia del ciclo en `config.env`.
- **RF-HT-14-8**: sólo SGR básico; prohibido introducir truecolor/256 ni dependencias nuevas (restraint de plataforma Linux/WSL2/macOS y <2.5 MB RAM).

## Requisitos no funcionales

- **RNF-HT-14-1**: coste cero en memoria y arranque: una función + dos mapas literales; sin subprocesos ni lecturas extra.
- **RNF-HT-14-2**: regla transversal del roadmap: la feature nace apagada — default `dark`, y desactivar equivale a volver a `dark` sin dejar el flujo roto.
- **RNF-HT-14-3**: auditabilidad: todo código SGR de color vive en `theme_color`; queda prohibido el SGR literal disperso (gate por grep en smoke tests).
- **RNF-HT-14-4**: la suite de humo existente (631/631) sigue verde con `dark`.

## Encaje en la arquitectura actual

- Contrato de extensión de Ajustes definido por `settings-category-submenus`: un knob nuevo se engancha a exactamente una categoría (aquí, la nueva Apariencia), con dispatch + render + hint en una sola edición. No se añaden filas al índice.
- Escritura de config por el cauce existente: whitelist de `config_set` (L1197–1203), bloque gestionado con marcadores (L1191–1192), `mv -f` atómico (L1264) y `.bak` por proceso (L1218–1221).
- Render single-frame de popups ya existente: el re-render tras ciclar el tema no requiere cambios de infraestructura.
- Resolución de tokens una vez por frame a variables locales (`C_OK`, `C_MUTED`, ...): el resolver es fuente única auditable por grep sin convertir cada celda en un fork de subshell.
- El motor (`lib/tts_engine.py`) no participa: cero color/ANSI ahí (bridge puro).

## Prior art y diferenciación

Los TUIs de referencia (lazygit, yazi, zellij) exponen temas porque el color heredado del terminal no basta cuando se fijan códigos propios. herdr-tts hoy fija códigos pensados para fondo oscuro; el tema claro cierra esa asimetría con coste ínfimo. No es diferenciación de mercado — es deuda de accesibilidad visual del propio render — y por eso es P2: no compite con el núcleo de voz, lo rodea.

## Dependencias

- `settings-category-submenus` (navegación por categorías de Ajustes): código ya en el árbol de trabajo, cambio openspec en cierre (17/18 tareas; pendiente sólo la decisión de chain strategy). Sin esta base, el knob sería una fila plana más del índice.
- Ninguna dependencia externa, binaria ni de red.

## Riesgos y mitigaciones

- **Elección fina de códigos `light` subjetiva** → los valores de la tabla son punto de partida (`warn` ya decidido: bold+`33`); revisión visual con un esquema claro de prueba antes de marcar Implementada — el fondo diario del maintainer es oscuro y la feature apunta a usuarios con fondo claro.
- **Colores embebidos en cadenas i18n** → refactor mínimo: el bold literal de títulos es seguro en ambos temas; sólo los tokens semánticos pasan por `theme_color`.
- **Deriva futura (nuevos colores hardcodeados)** → RNF-HT-14-3 con gate por grep en smoke tests desde el primer commit.
- **Roster en terminales ajenos**: las líneas del roster aparecen en terminales cuyo esquema puede diferir del del operador; `TTS_THEME` rige lo renderizado por el plugin, no el terminal del consumidor. Decisión 2026-09-23: el roster adopta el tema; el matiz se documenta en la fila de Ajustes.

## Métricas de éxito

- Medición sobre uso real del maintainer (regla transversal del roadmap): una semana con esquema claro activado a propósito y `TTS_THEME=light` — el fondo diario del maintainer es oscuro, así que la validación es deliberada, no heredada.
- 0 regresiones con `dark`: snapshot de salida idéntico y suite de humo verde (≥631 tests).
- El ciclo `dark ↔ light` desde Ajustes persiste tras reinicio del daemon en ≥10 intentos.

## Fuera de alcance

- Auto-detección del fondo del terminal (OSC 11 / `COLORFGBG`).
- Temas adicionales beyond dark/light, truecolor o paletas 256.
- Tema de superficies remotas (dashboard web, ntfy, podcast RSS).
- Temas por superfície (un solo tema global para todo el plugin).

## Open questions

1. Prioridad final y fase en el "Orden de ataque": propone el maintainer en la próxima revisión del roadmap.

> Resueltas (2026-09-23): categoría nueva "Apariencia" confirmada frente a knob dentro de una categoría existente; `warn` en claro será bold+`33`, y el resto de valores light se ajusta en la pasada visual con esquema claro de prueba; el roster incrustado adopta el tema (incluido), documentando en la fila de Ajustes que `TTS_THEME` rige lo renderizado por el plugin aunque el pane del agente use otro esquema.

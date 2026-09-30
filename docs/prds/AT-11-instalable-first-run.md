**ID**: PRD-AT-11 · **Proyecto**: agent-tts
**Prioridad final (revisión 2026-09-30)**: P1 · **Estado**: **Aprobada** (2026-09-30)
**Dependencias**: AT-10 (monorepo, ejecutada) · **Evidencia base**: [AT-11-auditoria-instalacion.md](AT-11-auditoria-instalacion.md)

> **Nota de revisión (2026-09-30, 3ª)**: Aprobada por el maintainer con las decisiones de diseño resueltas (ver sección correspondiente). El desarrollo NO arranca todavía: el hand-off a SDD queda a la espera de orden expresa.

# PRD-AT-11 — Producto instalable independiente con first-run onboarding

### Resumen ejecutivo

Hoy el ecosistema solo es instalable por su maintainer: los instaladores apuntan a repos archivados, los binarios a rutas personales y las claves viven en los dotfiles de Bruno. Esta PRD convierte el stack (plugin TTS + brain) en un **producto instalable independiente**: un usuario nuevo clona/instala, ejecuta un asistente de primera ejecución que captura claves y preferencias, y acaba con voz funcionando sin tocar un solo fichero a mano. La configuración manual existente se absorbe en la instalación y/o en el primer lanzamiento.

### Problema y flujo actual

Un usuario genérico que sigue la documentación se encuentra: comandos de instalación contra repos archivados (auditoría B1-B4), ausencia de manifest instalable en la raíz (A4) o sin publicar (A3), un launcher que exportación rutas muertas (A1), instaladores que exigen los dotfiles personales del maintainer (C1), paths de homebrew y tailnet hardcodeados (C2, C4) y cinco pasos puramente manuales sin red de seguridad (D1-D4). El flujo diario del maintainer funciona por conocimiento tribal; el de cualquier otra persona, no.

### Decisión de producto confirmada (entry gate)

- El ecosistema **quiere ser un producto instalable independiente**: la instalación no depende de repos personales ni de la topología de directorios del maintainer.
- **Toda la configuración manual actual se aborda en la instalación y/o en el primer lanzamiento**: claves, modelo STT, preferencias de voz y PATH quedan cubiertos por el instalador o por un asistente first-run interactivo.

### Propuesta

Tres frentes que comparten un mismo principio — *el instalador y el first-run son la única interfaz de configuración*:

1. **Higiene de migración (A+B)**: manifests publicados y en raíz/subdirectorio correctamente documentados, cero referencias a repos legacy en instaladores y docs, dev mode del bootstrap apuntando a `engine/`.
2. **Desacoplamiento de máquina (C)**: instaladores que derivan todo de su propia ubicación y del entorno (`HERDR_BIN` descubierto de PATH/brew multi-prefijo, puerto configurable, ruta de claves estándar `~/.config/herdr-brain/env` generada por el propio instalador, sin tocar dotfiles). La unidad systemd pasa a ser **generada** desde una plantilla, no commiteada con rutas absolutas.
3. **First-run onboarding (D)**: asistente interactivo (TUI de texto, sin dependencias nuevas) tanto para `herdr-tts` como para el brain: detecta lo que puede (audio, PATH, claves ya presentes), pregunta solo lo imprescindible, descarga el modelo STT con consentimiento explícito, instala los keybindings con `keymap adopt/apply` + reload automático, y deja `/health` verde como criterio de salida. Complemento: `doctor` que diagnostica una instalación existente.

### Historias de usuario (US-AT-11-*)

- US-AT-11-1: Como usuario nuevo, quiero `herdr plugin install` + lanzar el asistente y tener voz TTS funcionando, sin editar ficheros de configuración a mano.
- US-AT-11-2: Como usuario nuevo del brain, quiero que el primer arranque me pida mi `GLM_API_KEY`, la guarde fuera de cualquier repo con permisos 600 y arranque el servicio ya configurado.
- US-AT-11-3: Como usuario preocupado por red/disco, quiero decidir explícitamente si descargo el modelo Whisper y con qué tamaño.
- US-AT-11-4: Como usuario con una instalación ya hecha, quiero un `doctor` que me diga qué está roto (PATH, claves, audio, daemon, contrato v1) y cómo arreglarlo.
- US-AT-11-5: Como maintainer, quiero que mi propia máquina deje de ser un caso especial: mis dotfiles consumen el mismo instalador genérico que cualquiera.

### Requisitos funcionales (RF-AT-11-*)

- RF-AT-11-1: `herdr plugin install chiptime/agent-tts/hosts/herdr/tts-plugin` (y la vía de repo local) **debe** funcionar desde un clon fresco; la documentación **debe** mostrar ese comando y ningún otro legacy (traza A3, A4, B1-B4).
- RF-AT-11-2: El instalador del plugin **debe** exponer `bin/herdr-tts` en `~/.local/bin` (creándolo si falta) y avisar si el PATH no lo cubre; **no debe** depender de symlinks manuales (traza D1, C6).
- RF-AT-11-3: El primer lanzamiento de `herdr-tts` y del brain **debe** ejecutar el asistente first-run si no existe marca de configuración (state file en `~/.config/`); el asistente **debe** poder saltarse con flag `--no-first-run` para entornos no interactivos (traza D3).
- RF-AT-11-4: El asistente **debe** capturar y persistir: clave GLM (obligatoria solo si se instala el brain), claves de providers opcionales, proveedor de voz y preferencias de keymap, escribiendo en las rutas estándar fuera de repo (`~/.config/herdr-brain/env`, modo 600) (traza D3, C1).
- RF-AT-11-5: El asistente del brain **debe** ofrecer la descarga del modelo STT con consentimiento explícito y selección de tamaño (tiny/base/small), y verificar el contrato tras descargar (traza D2).
- RF-AT-11-6: Los instaladores y units **no deben** contener rutas absolutas de máquina: `HERDR_BIN` se descubre (PATH, luego prefijos brew conocidos), el puerto es configurable con default, y toda ruta de repo se deriva de la ubicación del propio script (traza C2, C3, C5).
- RF-AT-11-7: La unidad systemd **debe** generarse desde una plantilla versionada con sustitución de variables en tiempo de instalación; el fichero generado vive en `~/.config/systemd/user/` y nunca se commitea (traza C3).
- RF-AT-11-8: `bin/herdr-brain` **no debe** exportar `HERDR_TTS_HOME` con rutas legacy: la resuelve relativa a su propia ubicación (`../tts-plugin`) o del entorno, en ese orden (traza A1).
- RF-AT-11-9: El dominio de exposición remota (tailscale) **debe** ser opcional y configurado por el asistente o env; ningún dominio personal puede quedar en código (traza C4).
- RF-AT-11-10: Debe existir `herdr-tts doctor` (o equivalente en el brain) que verifique: CLI en PATH, audio backend disponible, claves presentes, daemon vivo, contrato v1 y modelo STT; salida accionable con el comando de arreglo (traza D1-D4).
- RF-AT-11-11: La instalación **debe** ser idempotente y re-ejecutable; re-instalar sobre una instalación existente conserva claves y preferencias (extensión del contrato actual de `deploy/install.sh`).
- RF-AT-11-12: Tras ejecutar el asistente, `/health` del brain **debe** reportar `tts: ok` y `stt: ready|degraded` coherente con lo elegido, y `herdr plugin list` **no debe** emitir warnings de manifest (traza A2, D2).

### Requisitos no funcionales (RNF-AT-11-*)

- RNF-AT-11-1: El asistente first-run no añade dependencias nuevas al runtime (texto plano por stdin/stdout).
- RNF-AT-11-2: Ninguna clave se imprime, loguea o persiste fuera de las rutas estándar modo 600; el asistente la lee con `getpass` y jamás la re-echo.
- RNF-AT-11-3: Tiempo objetivo de instalación completa (host ya presente): < 10 minutos incluyendo modelo STT base en banda ancha doméstica.
- RNF-AT-11-4: Cada corrección de la auditoría llega con su garantía de compatibilidad verificada por test, según el criterio transversal del roadmap (p. ej. paridad observable de `bin/herdr-brain` antes/después del fix de `HERDR_TTS_HOME`).

### Verificación y criterios de aceptación

> **Nota de revisión (2026-09-30, 2ª)**: a petición del maintainer, esta PRD incorpora estrategia de verificación explícita. Un requisito de instalación que no se puede comprobar como lo sufriría un usuario real no está hecho.

### Principio de verificación

Todo requisito de esta PRD se comprueba desde la **perspectiva de un usuario real**: instalación limpia, sin conocimiento tribal del maintainer, siguiendo únicamente la documentación publicada. Nada se da por verificado porque "en mi máquina funciona".

### Niveles de verificación y cuándo se disparan

| Nivel | Qué es | Cuándo se ejecuta | Gate |
|---|---|---|---|
| **V1 — Tests automáticos** | Suite de tests de instaladores: derivación de rutas, generación de unit desde plantilla, flags no interactivos del asistente, tests stub del contrato v1 | **Cada cambio** que toque ficheros de la superficie de instalación (`deploy/`, `scripts/`, `bin/`, first-run, docs de instalación) | CI/local pre-commit |
| **V2 — Smoke de instalación limpia (automatizado)** | Harness `scripts/acceptance/clean-install.sh`: sandbox con `HOME` temporal vacío (sin `~/.dotfiles`, sin brew, sin tailscale, sin claves), ejecuta **literalmente** los pasos documentados del README y aserta el resultado | **Cada cambio** que toque la superficie de instalación (mismo disparador que V1) | Obligatorio para aprobar el cambio |
| **V3 — UAT manual como usuario real** | Un humano ejecuta el checklist de aceptación en máquina limpia (WSL distro fresca o contenedor) siguiendo solo la doc, cronometrando y anotando fricción | **Por release** y siempre que una PR cambie el flujo documentado de instalación/first-run | Checklist firmado antes de archivar la PRD |

El entorno clean-room de V2 es la definición operativa de "usuario real simulado": `HOME` nuevo, PATH mínimo, sin secretos previos, red permitida solo hacia los orígenes que la doc cita. El harness vive versionado en el monorepo para que el muestreo sea reproducible y auditable, no artesanal.

### Escenarios de aceptación (Gherkin, trazados a RF/US)

```gherkin
Escenario: Instalación del plugin desde clon fresco (RF-11-1, US-11-1) [V2]
  Dado un HOME limpio sin rastros del ecosistema herdr
  Y el monorepo clonado en una ruta arbitraria no-estándar
  Cuando ejecuto los comandos de instalación documentados para el plugin TTS
  Entonces `herdr plugin list` muestra herdr.tts sin warnings
  Y `herdr-tts --contract-version` imprime >= 1 desde ~/.local/bin
  Y ningún paso exigió editar ficheros a mano

Escenario: Primera ejecución con claves (RF-11-3, RF-11-4, US-11-2) [V2]
  Dado una instalación limpia del brain sin GLM_API_KEY configurada
  Cuando lanzo el asistente first-run en modo no interactivo con las respuestas por flag/env
  Entonces la clave queda en ~/.config/herdr-brain/env con modo 600 y fuera de cualquier repo
  Y la clave no aparece en logs, argv visibles ni salida del asistente
  Y existe la marca de configuración que suprime futuros first-run

Escenario: Descarga de modelo STT con consentimiento (RF-11-5, US-11-3) [V2]
  Dado un entorno clean-room sin modelos precargados
  Cuando el asistente ofrece el modelo STT y acepto "base"
  Entonces el modelo se descarga al store estándar y la verificación de contrato pasa
  Y /health reporta stt coherente con la elección

Escenario: Rechazo de la descarga degrada sin romper (RF-11-5) [V2]
  Cuando rechazo la descarga del modelo STT en el asistente
  Entonces la instalación termina con éxito
  Y /health reporta stt: unavailable y /ask funciona con audio_url funcional o null documentado

Escenario: Cero rutas de máquina (RF-11-6, RF-11-7, RF-11-8, RF-11-9) [V1+V2]
  Dado el árbol versionado del monorepo
  Cuando busco patrones de acoplamiento en instaladores, units y plantillas
  Entonces no existen ~/.dotfiles, /home/linuxbrew, dominios personales
    ni rutas de clon absolutas del maintainer
  Y bin/herdr-brain sin HERDR_TTS_HOME en el entorno resuelve el tts-plugin
    relativo a su propia ubicación

Escenario: Reinstalación idempotente conserva estado (RF-11-11) [V2]
  Dado una instalación previa completada con claves y preferencias
  Cuando re-ejecuto el instalador completo
  Entonces las claves y preferencias se conservan
  Y los servicios quedan en el mismo estado funcional que antes

Escenario: Doctor diagnostica rotura simulada (RF-11-10, US-11-4) [V2]
  Dado una instalación sana a la que se le rompe deliberadamente el PATH de herdr-tts
  Cuando ejecuto el comando doctor
  Entonces reporta el problema concreto con el comando exacto de arreglo

Escenario: Salud final post-asistente (RF-11-12) [V2+V3]
  Cuando el asistente first-run termina con éxito
  Entonces /health del brain reporta tts: ok
  Y herdr plugin list no emite warnings de manifest

Escenario: UAT cronometrado como usuario real (RNF-11-3, US-11-1) [V3]
  Dado una máquina limpia y solo la documentación publicada
  Cuando un humano instala host + plugin + brain sin ayuda externa
  Entonces la voz TTS y el brain quedan funcionando en menos de 10 minutos
  Y cada fricción encontrada queda registrada como issue o doc-fix
```

### Matriz de trazado RF → verificación

| RF | V1 | V2 | V3 | Escenario |
|---|---|---|---|---|
| RF-AT-11-1 | ✔ docs | ✔ | ✔ | Instalación desde clon fresco |
| RF-AT-11-2 | ✔ | ✔ | ✔ | Instalación desde clon fresco |
| RF-AT-11-3 | ✔ | ✔ | | Primera ejecución con claves |
| RF-AT-11-4 | ✔ | ✔ | | Primera ejecución con claves |
| RF-AT-11-5 | ✔ | ✔ | | Descarga STT + Rechazo degradado |
| RF-AT-11-6 | ✔ | ✔ | | Cero rutas de máquina |
| RF-AT-11-7 | ✔ | ✔ | | Cero rutas de máquina |
| RF-AT-11-8 | ✔ | ✔ | | Cero rutas de máquina |
| RF-AT-11-9 | ✔ | ✔ | | Cero rutas de máquina |
| RF-AT-11-10 | ✔ | ✔ | | Doctor diagnostica rotura |
| RF-AT-11-11 | ✔ | ✔ | | Reinstalación idempotente |
| RF-AT-11-12 | | ✔ | ✔ | Salud final post-asistente |

### Encaje en la arquitectura actual

- `hosts/herdr/tts-plugin/scripts/install.sh` absorbe el alta en PATH y la invocación del asistente; `bootstrap.sh` corrige su modo dev a `engine/`.
- `hosts/herdr/brain/deploy/` se divide en: plantilla de unit (`herdr-brain.service.tmpl`), instalador genérico y lógica de first-run compartida con `bin/herdr-brain`.
- El asistente vive en un módulo pequeño compartido (o duplicado deliberadamente si el acoplamiento entre plugins resulta peor); decisión de diseño en fase `sdd-design`.
- El contrato de superficie v1 (speech backend) no cambia: el asistente valida, no reemplaza.

### Non-goals (fuera de alcance)

- No se redefine el motor TTS ni su contrato IPC v1.
- No se publica a ningún registry central de plugins en v1: la vía es install desde GitHub/local.
- No se soporta Windows nativo; WSL2, Linux y macOS sí (audio backend según plataforma).
- No se migran los dotfiles del maintainer a nuevo formato: su setup actual debe seguir funcionando vía los defaults genéricos.

### Decisiones de diseño (resueltas por el maintainer, 2026-09-30)

1. **Vía primaria de despliegue del brain**: plugin herdr con `[[startup]]` es el camino recomendado; **systemd queda permitido** como opción soportada, con la unidad generada desde plantilla (RF-AT-11-7).
2. **Asistente first-run**: módulo **compartido** en el monorepo, no duplicación entre plugins.
3. **Exposición remota (tailscale)**: **documentación para usuarios avanzados**; la automatización en el asistente queda como posibilidad futura, no como requisito de esta PRD.
4. **Naming**: status quo — repo/paquete `agent-tts`, plugin ids `herdr.*`.

### Métricas de éxito y aceptación

La aceptación formal de esta PRD se rige por la sección **Verificación y criterios de aceptación** (niveles V1-V3, escenarios Gherkin y matriz de trazado). Las métricas de resultado del producto son:

- Instalación desde cero en máquina limpia (WSL2) siguiendo solo la documentación nueva, sin tocar ficheros a mano: voz TTS + brain con `/health` verde (escenario UAT cronometrado, V3).
- `grep -r "chiptime/herdr-tts" hosts/ docs/` sin resultados activos (solo histórico/archivado).
- `herdr plugin list` sin warnings en la máquina del maintainer tras migrar sus registros locales.
- Cero apariciones de `~/.dotfiles`, `/home/linuxbrew` o dominios personales en instaladores y units versionados (escenario Cero rutas de máquina).

### Riesgos y mitigaciones

- Riesgo: el asistente convierte la instalación en un paso frágil en entornos no interactivos (CI, dotfiles scripts). Mitigación: `--no-first-run` + flags de configuración no interactiva desde el día uno (RF-AT-11-3).
- Riesgo: doble vía de despliegue (plugin vs systemd) derive en dos fuentes de verdad de configuración. Mitigación: resolver OQ-1 antes de `sdd-design`; una sola ruta de lectura de claves/preferencias.
- Riesgo: romper el setup del maintainer al desacoplar. Mitigación: RNF-AT-11-4 (paridad verificada por test) + migración de sus registros locales como tarea explícita.

### Siguiente paso

PRD aprobada (2026-09-30). El hand-off a `sdd-propose` (con esta PRD y la auditoría como entrada) queda **pendiente de orden expresa del maintainer**; el desarrollo no arranca por decisión propia. La PRD documenta intención; no autoriza implementación por sí sola.

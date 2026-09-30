# AT-11 — Auditoría de instalación (post-migración al monorepo)

**Fecha**: 2026-09-30 · **Alcance**: superficie de instalación del ecosistema herdr (host + plugin TTS + brain) tras AT-10
**Método**: inspección directa de ficheros, instaladores y estado runtime (`herdr plugin list`, systemd, `~/.config/herdr/`). Cada hallazgo cita evidencia `fichero:línea`.
**Decisión de producto que motiva esta auditoría**: el ecosistema debe ser un **producto instalable independiente** (ver [PRD-AT-11](AT-11-instalable-first-run.md)).

## Resumen ejecutivo

| Categoría | Hallazgos | Estado |
|---|---|---|
| A. Bugs funcionales post-migración | 4 | 🔴 Rompen hoy |
| B. Referencias legacy en instaladores y docs | 5 | 🟡 Instalación documentada rota |
| C. Acoplamiento a la máquina del maintainer | 6 | 🟡 Bloquean usuario genérico |
| D. Gaps de onboarding (producto) | 5 | 🟡 Fricción de primera experiencia |

---

## A. Bugs funcionales post-migración

### A1. `bin/herdr-brain` exporta ruta TTS muerta 🔴

- **Evidencia**: `hosts/herdr/brain/bin/herdr-brain:21` — `export HERDR_TTS_HOME="${HERDR_TTS_HOME:-$HOME/Code/personal/herdr-tts}"`.
- **Efecto**: cualquier lanzamiento vía script del plugin pisa el default corregido de `src/herdr_brain/config.py:23` (`~/Code/personal/agent-tts/hosts/herdr/tts-plugin`, fix `384dd4f`) con la ruta del repo archivado → `/health` reporta `tts: missing`.
- **No afectado**: la vía systemd (`ExecStart=python -m herdr_brain.server`) no pasa por `bin/herdr-brain`; por eso el servicio local está sano.

### A2. Registro local de plugins apuntando a repos borrados 🔴

- **Evidencia**: `~/.config/herdr/plugins.json:91-92,352-353` — `manifest_path`/`plugin_root` de `herdr.brain` y `herdr.tts` en `~/Code/personal/herdr-{brain,tts}` (eliminados).
- **Efecto**: `herdr plugin list` emite `warning: manifest unavailable: No such file or directory (os error 2)`.

### A3. Manifest del brain sin commitear 🔴

- **Evidencia**: `hosts/herdr/brain/herdr-plugin.toml` existe en disco pero **no** está en el índice de git (`git ls-files | grep herdr-plugin` solo lista el de tts-plugin).
- **Efecto**: un clon fresco del monorepo no puede instalar el brain como plugin (`[[startup]]` incluido).

### A4. Sin manifest en la raíz del monorepo 🔴

- **Evidencia**: `~/Code/personal/agent-tts/herdr-plugin.toml` no existe; el manifest de tts-plugin vive en `hosts/herdr/tts-plugin/herdr-plugin.toml`.
- **Efecto**: `herdr plugin install chiptime/agent-tts` falla (manifest ausente en raíz); hay que pasar el subdirectorio explícito.

## B. Referencias legacy en instaladores y docs

### B1. Comando de instalación con repo viejo 🟡

- `hosts/herdr/tts-plugin/README.md:186` — `herdr plugin install chiptime/herdr-tts`.

### B2. Curl installer apuntando al repo archivado 🟡

- `hosts/herdr/tts-plugin/README.md:196,207` — `https://raw.githubusercontent.com/chiptime/herdr-tts/v0.16.0/scripts/install.sh`.

### B3. `CANONICAL_URL` legacy 🟡

- `hosts/herdr/tts-plugin/scripts/install.sh:19` — `CANONICAL_URL="https://github.com/chiptime/herdr-tts.git"`; las comprobaciones de origin fallarán contra un clon del monorepo.

### B4. Docs de desarrollo con clone del repo viejo 🟡

- `hosts/herdr/tts-plugin/README.md:222-223` — `git clone https://github.com/chiptime/herdr-tts.git ~/Code/personal/herdr-tts`.

### B5. Dev mode del bootstrap roto 🟡

- `hosts/herdr/tts-plugin/scripts/bootstrap.sh:72,78` — `LOCAL_AGENT_TTS="${HOME}/Code/personal/agent-tts"` + `pip install -e "$LOCAL_AGENT_TTS"`; el paquete Python vive en `engine/`, no en la raíz → `HERDR_TTS_DEV=1` falla.

## C. Acoplamiento a la máquina del maintainer

### C1. Dependencia dura de `~/.dotfiles` para la clave API 🟡

- `hosts/herdr/brain/deploy/install.sh:20,46-48` — `PRIVATE_ENV="$HOME/.dotfiles/shell/private-env.sh"`; aborta si no existen los dotfiles personales.
- `hosts/herdr/brain/bin/herdr-brain:48-60` — scrapea `GLM_API_KEY` del mismo fichero en runtime.

### C2. `/home/linuxbrew` hardcodeado 🟡

- `hosts/herdr/brain/deploy/herdr-brain.service:23` — `Environment=HERDR_BIN=/home/linuxbrew/.linuxbrew/bin/herdr`.
- `hosts/herdr/brain/bin/herdr-brain:27-28` — fallback al mismo path absoluto.

### C3. Ruta de clon asumida 🟡

- `hosts/herdr/brain/deploy/herdr-brain.service:17-18` — `WorkingDirectory`/`ExecStart` en `%h/Code/personal/agent-tts/hosts/herdr/brain` estático (no generado).

### C4. Tailnet y dominio personales 🟡

- `hosts/herdr/brain/bin/herdr-brain:206,211-214` — `$HOME/bin/tailscale` y URL con `*.tail2640fd.ts.net:8443` (dominio personal).

### C5. Puerto hardcodeado con política agresiva 🟡

- `hosts/herdr/brain/deploy/install.sh:21,88-100` — puerto `8741` fijo; mata cualquier proceso ajeno que escuche en él.

### C6. Symlinks manuales como mecanismo de PATH 🟡

- `~/.dotfiles/herdr/herdr-tts -> <repo>/bin/herdr-tts` — manual, específico de máquina, roto tras cada reubicación (se rompió con la migración).

## D. Gaps de onboarding (producto)

### D1. Ningún instalador pone `herdr-tts` en PATH 🟡

- Los keybindings de `~/.config/herdr/config.toml` (23 entradas `[[keys.command]]`) invocan `herdr-tts` como comando shell; ni `herdr plugin install`, ni `bootstrap.sh`, ni `install.sh` lo enlazan a `~/.local/bin`.

### D2. Modelo STT nunca se descarga solo 🟡

- `hosts/herdr/brain/src/herdr_brain/stt.py:6-11` — descarga exclusivamente manual (`python -m herdr_brain.stt pull`); sin ella `stt: unavailable` y `/transcribe` → 503. Decisión zero-surprise correcta, pero sin primera-experiencia que la acompañe.

### D3. Claves sin flujo de captura 🟡

- `GLM_API_KEY` (obligatoria para el brain) y `OPENAI_API_KEY`/`ELEVENLABS_API_KEY` (providers opcionales, `hosts/herdr/tts-plugin/README.md:507,516`) solo configurables editando ficheros a mano.

### D4. Config de keybindings requiere acción manual parcial 🟡

- `keymap adopt/apply` automatizado (`hosts/herdr/tts-plugin/scripts/install.sh:93-94`) pero el reload del server es manual (`herdr server reload-config`, `README.md:405`).

### D5. Identidades/nombres sin definir 🟡

- Plugin ids `herdr.tts` / `herdr.brain` (`hosts/herdr/*/herdr-plugin.toml:1`), paquete Python `agent-tts` (`engine/pyproject.toml:6`), repos `chiptime/agent-tts` — sin naming de producto único para instalar y documentar.

---

## Relación con la PRD

Esta auditoría es la **base de evidencia** de [PRD-AT-11 — Producto instalable independiente con first-run onboarding](AT-11-instalable-first-run.md). Cada requisito RF-AT-11-* traza a los hallazgos A/B/C/D de este documento.

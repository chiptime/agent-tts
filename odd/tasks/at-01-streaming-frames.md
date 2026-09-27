# AT-01 — Streaming incremental por frames de MP3 (byte-level)

**Estado**: En curso · **Rama**: `feat/at-01-streaming-frames` (worktree `~/Code/personal/agent-tts-worktrees/at-01-streaming-frames`, base `main` = `1541001`)

## Objetivo

Implementar streaming frame-accurate de MP3 para proveedores compatibles (Edge, OpenAI, ElevenLabs), decodificando audio a medida que los bytes llegan por la red y alimentando el buffer PCM de reproducción sin esperar a que finalice la síntesis de un grupo de oraciones completo (~250-400 chars).
Reducir el Time To First Audio (TTFA) por debajo de 300 ms en textos largos, manteniendo el resaltado karaoke (--highlight), la tolerancia a errores de red y la arquitectura monorepo intacta.

## Descubrimientos técnicos clave

1. **Bit Reservoir en MP3**: La decodificación aislada frame a frame con `miniaudio.decode(single_frame)` falla en frames intermedios porque `main_data_begin` requiere bytes del bit reservoir de frames anteriores.
2. **Soporte nativo de streaming en miniaudio**: `miniaudio.stream_any` con una subclase de `StreamableSource` (`ma_decoder_init` con callbacks de lectura CFFI) permite alimentar bytes incrementalmente y decodificar PCM de forma continua y limpia, sin pérdida de frames ni glitches audibles.
3. **Edge TTS emite chunks de 720 bytes (5 frames = 120 ms de audio)** junto con eventos `WordBoundary` en tiempo real a través del WebSocket.

## Plan de trabajo (TDD)

- [ ] **T1: Mp3FrameParser (`engine/src/agent_tts/stream/mp3_parser.py`)**
  - Parser de sync words (0xFFE/0xFFF), cálculo de longitud de frame MPEG-1/2/2.5 Layer III, bitrate y sample rate.
  - Salto de cabeceras ID3v2 (10 bytes + syncsafe integer).
  - Manejo de frames Xing/LAME.
  - Tests unitarios en `engine/tests/test_mp3_parser.py` (fixtures de frames reales, tags ID3, bytes corruptos).

- [ ] **T2: Decodificador continuo y feeder (`engine/src/agent_tts/stream/decoder.py`)**
  - Implementar `StreamBufferSource(miniaudio.StreamableSource)` con cola concurrente.
  - Generador de PCM continuo con colchón inicial configurable (100 ms) para prevenir underrun.
  - Tests unitarios en `engine/tests/test_stream_decoder.py`.

- [ ] **T3: Adaptación de proveedores para stream en vivo**
  - `edge.py`: generador asíncrono que emite chunks de audio y eventos `WordBoundary` sin esperar a completar la locución.
  - `openai.py` y `elevenlabs.py`: verificación del generador de bytes HTTP.

- [ ] **T4: Integración en CLI (`engine/src/agent_tts/cli.py`)**
  - Soporte de flag `--stream {auto,frames,groups,off}` (auto selecciona frames para edge/openai/elevenlabs).
  - Integración en `_speak_pipelined` conectando el feeder de frames con `session.prepare_pcm` y `session.append_pcm`.
  - Fallback a groups ante errores de parseo con advertencia en stderr.
  - Persistencia final con `merge_chunks_to_audio`.

- [ ] **T5: Karaoke y sincronización de palabras**
  - Asignación de timestamps reales de PCM a las palabras.
  - Para Edge: consumo de eventos `WordBoundary` durante el stream.
  - Para OpenAI/ElevenLabs: proyección sobre duración acumulada de frames.

- [ ] **T6: Pruebas de integración, regresión y DoD**
  - Medición de TTFA < 300 ms en texto de 800+ caracteres.
  - Suite completa de engine (778+ tests) y boundary tests pasando al 100%.

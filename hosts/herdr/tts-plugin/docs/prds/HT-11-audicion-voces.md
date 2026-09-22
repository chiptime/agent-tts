**ID**: PRD-HT-11 · **Proyecto**: herdr-tts
**Prioridad final (revisión 2026-09-22)**: P1 · **Estado**: Aprobada
**Dependencias**: HT-02 para asignación por chat

# PRD-HT-11 — Audición de voces en la paleta

> **Nota de revisión (2026-09-22)**: Compañera UX de HT-02, misma fase: juntas convierten la identidad vocal en experiencia completa desde el día uno (asignación de voces de oído, sin editar config).

**Prioridad**: Baja · **Esfuerzo**: S

## Resumen ejecutivo
Tecla de preview en la paleta fzf que sintetiza una frase fija corta multilingüe con la voz seleccionada, usando el motor y respetando el mutex; auto-stop al navegar. Acción para asignar la voz como global o del chat (HT-02). Caché del sample por (proveedor, voz) para re-preview instantáneo y sin gasto de cuota.

## Problema y flujo actual
Elegir voz a ciegas leyendo nombres (`--voice alvaro`) es una experiencia pobre: el coste de probar una voz (editar config, reiniciar daemon, escuchar un evento real) desincentiva explorar y deja al usuario anclado a la voz default.

## Propuesta
Vista de voces dentro de la paleta (que ya lista chats y audios): lista las voces del proveedor activo, una tecla de preview sintetiza la frase fija con la voz bajo el cursor, mover el cursor corta el preview, y dos acciones de asignación: voz global o voz del chat seleccionado (con HT-02). La caché por (proveedor, voz) hace que repetir un preview sea instantáneo.

## Historias de usuario (US-HT-11-1, ...)
- **US-HT-11-1** — Como usuario que elige voz, quiero escucharla antes de asignarla, para decidir con criterio y sin ritual de reinicio.
- **US-HT-11-2** — Como usuario que compara voces, quiero navegar la lista escuchando ráfagas cortas, para comparar de oído en segundos.
- **US-HT-11-3** — Como usuario con HT-02, quiero asignar la voz escuchada al chat seleccionado o como global, sin salir de la paleta.
- **US-HT-11-4** — Como usuario de proveedores de pago, quiero que repetir un preview no vuelva a sintetizar, para no quemar cuota.

## Requisitos funcionales (RF-HT-11-...)
- **RF-HT-11-1**: entrada a la vista de voces desde la paleta (tecla `v`); la vista lista las voces del proveedor activo (edge/openai/elevenlabs/piper/kokoro) vía el listado del motor/voice manager.
- **RF-HT-11-2**: preview: frase fija corta (dos frases: español e inglés) sintetizada on-demand con la voz bajo el cursor; pasa por el mismo camino de reproducción y mutex que el daemon (nunca un segundo reproductor).
- **RF-HT-11-3**: auto-stop: mover el cursor corta el preview en < 0.3 s; salir de la vista también.
- **RF-HT-11-4**: caché: un sample por (proveedor, voz) en el directorio de caché del plugin; purga LRU por tamaño (umbral 20 MB); la caché nunca sustituye la voz real de los eventos.
- **RF-HT-11-5**: asignación: `enter` = voz global (persiste `TTS_VOICE` con escritura gestionada); tecla dedicada = voz del chat seleccionado (persiste en `voices.json` de HT-02; sin HT-02, aviso accionable).
- **RF-HT-11-6**: preview de voz de un proveedor sin claves/modelo instalado: aviso claro y ninguna llamada de red.

## Requisitos no funcionales (RNF-HT-11-...)
- **RNF-HT-11-1**: preview con caché: reproducción inicia en < 0.3 s.
- **RNF-HT-11-2**: preview sin caché: una síntesis normal del motor (presupuesto 3-6 s, indicado en pantalla).
- **RNF-HT-11-3**: cero dependencias nuevas: fzf ya es requisito de la paleta.

## Encaje en la arquitectura actual
La paleta ya es la superficie fzf del plugin y ya dispara reproducciones (`ctrl-r`) por el camino del mutex; el preview es una variante más de ese camino. La asignación global persiste en `config.env` con la escritura gestionada atómica (tmp + mv) ya usada por Ajustes; la asignación por chat delega en `voices.json` (HT-02).

## Prior art y diferenciación
Ningún comparado ofrece audición previa de voces; los selectores de voz del sistema operativo no aplican a un contexto de flota. Diferenciación: pequeña en alcance pero elimina la mayor fricción de adopción de HT-02 y del cambio de voz en general.

## Dependencias
- HT-02 para la asignación por chat; la asignación global funciona de forma autónoma.

## Riesgos y mitigaciones
- Síntesis de preview en proveedores de pago gasta cuota → caché obligatoria, frase corta fija, solo bajo demanda.
- fzf y audio asíncrono → auto-stop robusto con identificador de job (RF-HT-11-3).
- Inconsistencia de listado entre proveedores → la lista viene del propio motor (voice manager), no se mantiene en el host.

## Métricas de éxito
1. > 80% de los cambios de voz van precedidos de audición previa.
2. > 70% de previews servidos desde caché tras la primera semana.
3. Cero solapamientos de audio entre preview y daemon (mutex intacto).

## Fuera de alcance
Parametrizar la frase de preview desde la UI (basta config por fichero), fine-tuning o mezcla de voces, preview de rate/velocidad.

## Open questions
¿Frase de preview fija por idioma o configurable en `config.env`? ¿Preview también desde el menú de voz de una tecla o solo desde la paleta?

**ID**: PRD-HT-15 · **Proyecto**: herdr-tts
**Prioridad final (2026-09-23)**: Pendiente de priorización · **Estado**: Implementada (2026-09-24, HT-15)
**Dependencias**: API existente de `agent-tts`; separación host/motor de HT-13

# PRD-HT-15 — Transformación Markdown/HTML para lectura sincronizada

**Prioridad**: Pendiente · **Esfuerzo estimado**: M

## Resumen ejecutivo

Preparar el contenido que consume el flujo de lectura de herdr-tts: texto plano → Markdown → HTML estructurado con anclas de frase y párrafo. Esta base alimentará el resaltado tipo karaoke y el futuro modal de lectura HT-16. HT-15 entrega transformación, sincronización y acceso mínimo desde el host; no entrega una interfaz modal ni un conversor independiente como producto.

## Problema y flujo actual

- La lectura visual actual muestra resaltado ANSI y desplazamiento en terminal; no genera HTML.
- La limpieza orientada a voz omite bloques de código, modifica tablas y enlaces y expande siglas. Reutilizarla directamente para presentación elimina estructura útil.
- Los índices de frase y párrafo del motor deben corresponder al contenido mostrado: separar frases con la misma expresión regular no basta si las representaciones divergen.

## Propuesta

1. Crear `lib/reader_pipeline.py` en el host, sin nuevas dependencias: biblioteca estándar y utilidades ya disponibles en `agent_tts`.
2. Componer `strip_ansi` y `redact_secrets` antes de transformar; filtrar ruido de terminal sin destruir marcadores Markdown, código ni tablas.
3. Inferir Markdown mediante heurísticas deterministas de títulos, viñetas, numeración, bloques de código y párrafos.
4. Renderizar un subconjunto GFM: títulos h1–h4, párrafos, código delimitado, citas, tablas, listas, negrita, cursiva, código en línea y enlaces seguros.
5. Emitir `data-sent-idx`, `data-para-idx` y un mapa de correspondencia compatible con `agent_tts.boundaries`.
6. Conectar el módulo mediante `lib/tts_engine.py` y una superficie explícita como `--render-html`; concretar su sintaxis en especificación/diseño. No activar generación HTML por defecto ni cambiar la lectura actual.

## Historias de usuario

- **US-HT-15-1**: Como operador que sigue respuestas habladas, quiero conservar títulos, tablas y código para comprender el contenido sin perder su estructura.
- **US-HT-15-2**: Como consumidor del flujo de lectura, quiero identificar la frase y el párrafo correspondientes al motor para poder resaltarlos sin desfase.
- **US-HT-15-3**: Como operador, quiero que el contenido renderizado no exponga credenciales ni ejecute HTML procedente del agente.

## Requisitos funcionales

- **RF-HT-15-1**: La limpieza elimina ANSI y ruido de terminal, oculta secretos antes de convertir y conserva estructura visual.
- **RF-HT-15-2**: La conversión de texto plano es determinista y admite entradas vacías y caracteres Unicode.
- **RF-HT-15-3**: El subconjunto Markdown indicado produce HTML válido; la sintaxis no admitida o incompleta se presenta como texto escapado sin ejecutar HTML original.
- **RF-HT-15-4**: Las anclas y el mapa mantienen paridad de índices de frase y párrafo con el motor. Las pruebas incluyen código omitido al hablar, tablas, enlaces, siglas y frases con formato en línea; el diseño debe explicitar su correspondencia, no asumirla por compartir una expresión regular.
- **RF-HT-15-5**: Todo texto y atributo se escapa con `html.escape`; los destinos de enlace se restringen a esquemas seguros. Escapar por sí solo no neutraliza un enlace `javascript:`.
- **RF-HT-15-6**: El puente y la CLI ofrecen acceso mínimo para pruebas y consumidores de lectura, con errores claros y cadenas en `TT_EN`/`TT_ES`, respetando el idioma seleccionado.
- **RF-HT-15-7**: Los escenarios herméticos comprueban conversión, seguridad, paridad, acceso CLI y compatibilidad, con pares RED-GREEN-REFACTOR por escenario.

## Requisitos no funcionales

- **RNF-HT-15-1**: Sin nuevas dependencias de paquetes, cambios de bootstrap ni modificaciones del motor hermano.
- **RNF-HT-15-2**: Ejecución bajo demanda; sin proceso residente adicional ni alteración del presupuesto de memoria del daemon.
- **RNF-HT-15-3**: Surface Contract permanece en versión `1`; síntesis, reproducción e IPC existentes no cambian.
- **RNF-HT-15-4**: La suite completa `bash scripts/smoke-tests.sh` debe seguir pasando. Esta PRD no afirma resultados de ejecución.

## Encaje en la arquitectura actual

El host es responsable de presentación y orquestación; `agent-tts` sigue siendo el motor de voz. El nuevo módulo compone sus utilidades existentes sin introducir renderizado HTML en el motor. `lib/tts_engine.py` sirve de puente y `bin/herdr-tts` expone el acceso explícito. HT-16 consumirá el resultado y resolverá la interacción visual.

## Prior art y diferenciación

La referencia interna es el seguimiento ANSI ya disponible y los índices de frase/párrafo del motor. Se amplía esa base a contenido estructurado reutilizable: el valor es mejorar la lectura de respuestas reales, no competir con bibliotecas Markdown generales.

## Dependencias

- Utilidades de limpieza, ocultación de secretos y límites del `agent-tts` fijado por el proyecto.
- HT-13 como criterio de separación de responsabilidades; no se requiere una nueva publicación del motor.
- HT-16 es consumidor posterior, no prerrequisito de HT-15.

## Riesgos y mitigaciones

| Riesgo | Severidad | Mitigación |
|---|---|---|
| Desfase entre contenido visual y hablado | Alta | Comparación con límites del motor y casos de normalización; bloquear aceptación si divergen |
| Inyección HTML/XSS | Alta | Escape de texto/atributos, esquemas de enlace permitidos y pruebas adversarias |
| Credenciales en HTML o mapa | Alta | Ocultación previa a toda transformación y comprobación de todas las salidas |
| Markdown mal formado | Media | Estados acotados y degradación a texto escapado |
| Crecimiento hacia el modal | Media | Mantener interacción y bucle visual en HT-16 |

## Métricas de éxito

- Cero discrepancias de índices en los casos de paridad; cero credenciales de prueba ni contenido ejecutable en las salidas adversarias.
- Suite de humo completa sin regresiones; comportamiento habitual idéntico sin invocar la nueva superficie.
- Revisar muestras de respuestas del uso real del maintainer para confirmar conservación de estructura. La validación de usabilidad del modal corresponde a HT-16.

## Fuera de alcance

- Modal interactivo, navegación por teclado y actualización visual en tiempo real: HT-16.
- Notas HTML de ntfy/RSS, cambios de síntesis/IPC del motor o incremento de contrato v1.
- Compatibilidad completa CommonMark/GFM y nuevas dependencias externas.

## Reversión y revisión

Revertir únicamente los cambios HT-15 en módulo, puente, CLI, pruebas y documentación; conservar trabajo paralelo y configuración. No hay migración de datos ni modificación del motor que deshacer.

Previsión: unas **800 líneas cambiadas** (rango 660–940), incluidas 250–350 del módulo nuevo y sus pruebas/documentación. Supera el presupuesto de 400: el orquestador debe resolver división en unidades revisables o excepción explícita antes de implementar, manteniendo pruebas y documentación con cada unidad.

## Preguntas abiertas

- No quedan decisiones de producto bloqueantes sobre el alcance confirmado. La prioridad del roadmap queda pendiente de asignación.
- Especificación/diseño concretarán el formato del mapa, la sintaxis CLI y la correspondencia visual/hablada; no podrán rebajar la paridad exigida ni ampliar el alcance a HT-16.

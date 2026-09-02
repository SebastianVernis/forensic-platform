# AGENTS.md — Motor de Análisis Forense

## Resumen del Proyecto

Motor de análisis forense de propósito general para expedientes legales. Ingiere archivos `.txt`, `.pdf` e imágenes (`.png`, `.jpg`, `.tiff`, etc.), seudonimiza nombres/vehículos, fragmenta texto para procesamiento por LLM, ejecuta análisis forense multidimensional (inconsistencias, incongruencias, trazabilidad, declaración vs. evidencia), y produce reportes JSON y Markdown con nombres reales restaurados. Incluye una UI estática para navegar resultados.

## Arquitectura y Flujo de Datos

```
INPUT_DIR (.txt, .pdf, imágenes)
  → runner.py (orquestador, punto de entrada CLI)
    → loader.py (ingesta: extracción de texto, parseo PDF, OCR)
    → adapter.py (LLM perfila el caso → genera prompts adaptados + blocklist del pseudonymizer)
    → pseudonymizer.py (extrae entidades → construye tabla de alias → reemplaza nombres, blocklist aumentado por adapter)
    → chunker.py (divide en chunks de ~100K chars con overlap)
    → analyzers/analizador_unificado.py (una llamada LLM por chunk con prompt adaptado)
    → output/resultados_unificados.json
  → report.py (consolida + desudonimiza → reporte_forense.md + .json)
  → output/ui/ (visor HTML estático)
```

**Principio clave**: Todo el análisis por LLM se hace sobre texto seudonimizado (PERSONA_001, VEHICULO_002, etc.). Los nombres reales solo se restauran en el reporte final mediante la tabla de alias. Esto protege PII durante el procesamiento.

## Ejecución del Pipeline

```bash
# Variables de entorno requeridas
export OLLAMA_API_KEY="tu-api-key"
export INPUT_DIR="/ruta/a/archivos/del/caso"

# Pipeline completo (auto-perfila el caso, adapta prompts)
python runner.py

# Con argumentos CLI (sobreescribe variables de entorno)
python runner.py --input-dir /ruta/a/archivos --output-dir /ruta/salida --tomos T30,T45

# Reutilizar tabla de alias y perfil existentes (salta ambas llamadas LLM)
python runner.py --reuse-alias --reuse-profile

# Omitir perfilado del caso (usar prompts genéricos)
python runner.py --skip-profiling

# Solo tomos específicos (match por substring en nombre de archivo)
python runner.py --tomos T30

# Generar reporte a partir de resultados existentes (paso separado)
python report.py

# Probar el pseudonymizer individualmente
python pseudonymizer.py
```

## Entorno y Dependencias

- **Paquetes Python** (sin `requirements.txt` aún):
  - `requests` — llamadas a API del LLM
  - `pymupdf` (`fitz`) — extracción de texto PDF y renderizado de páginas
  - `pytesseract` — OCR vía Tesseract
  - `pdf2image` — conversión de PDF a imagen
  - `Pillow` — manejo de imágenes
- **Herramientas de sistema requeridas**:
  - `tesseract` (v5+) — motor OCR con paquete de idioma `spa`
  - `pdftotext` (poppler-utils) — extracción de texto PDF alternativa
- **Credenciales API** (variables de entorno, nunca hardcodear):
  - `OLLAMA_API_KEY` — requerida, el pipeline falla sin ella
  - `OLLAMA_BASE_URL` — default: `https://ollama.com/v1`

## Configuración (`config.py`)

Todos los parámetros son configurables vía variables de entorno. `config.py` las lee al importar:

| Variable | Env Var | Default | Propósito |
|---|---|---|---|
| `INPUT_DIR` | `INPUT_DIR` | `""` (requerido) | Directorio con documentos fuente |
| `OUTPUT_DIR` | `OUTPUT_DIR` | `./output` | Directorio para archivos generados |
| `CHUNK_SIZE` | `CHUNK_SIZE` | 100000 chars | ~25K tokens en español, cabe en modelos de 200K contexto |
| `CHUNK_OVERLAP` | `CHUNK_OVERLAP` | 5000 chars | Evita cortar declaraciones en los límites de chunks |
| `MODELO_RAPIDO` | `MODELO_RAPIDO` | `minimax-m3:cloud` | LLM para todo el análisis |
| `MODELO_PROFUNDO` | `MODELO_PROFUNDO` | `kimi-k2.7-code:cloud` | Reservado para análisis de contexto largo (sin uso actual) |
| `TOMOS_INCLUIDOS` | `TOMOS_INCLUIDOS` | `None` (todos) | Filtro de tomos separados por coma |
| `OCR_DPI` | `OCR_DPI` | 300 | Resolución para renderizado de páginas PDF antes de OCR |
| `OCR_LANG` | `OCR_LANG` | `spa` | Idioma de Tesseract |
| `ENTIDADES_A_SEUDONIMIZAR` | `ENTIDADES_A_SEUDONIMIZAR` | `PERSONA,VEHICULO` | Tipos de entidad a seudonimizar |

Los args CLI (`--input-dir`, `--output-dir`, `--tomos`, `--reuse-alias`, `--reuse-profile`, `--skip-profiling`) sobreescriben las env vars en runtime.

## Cargador de Documentos (`loader.py`)

Maneja ingesta multi-formato:

- **`.txt`** — lectura directa (UTF-8 con reemplazo de errores)
- **`.pdf`** — PyMuPDF extrae texto nativo por página; si una página tiene <30 chars de texto (escaneada), renderiza la página a `OCR_DPI` y ejecuta Tesseract OCR
- **Imágenes** (`.png`, `.jpg`, `.jpeg`, `.tiff`, `.tif`, `.bmp`, `.webp`) — Tesseract OCR directo

Extensiones soportadas definidas en `EXTENSIONES_SOPORTADAS`. `cargar_documentos(input_dir)` retorna `{nombre_archivo: texto_extraído}`.

## Adaptador por Caso (`adapter.py`)

**Adaptación automática por caso**: antes del análisis principal, el adaptador envía una muestra del corpus al LLM para generar un **perfil del caso** — jurisdicción, tipo de caso, términos legales relevantes, tipos de entidad, roles jurídicos, alertas de calibración. Este perfil impulsa tres adaptaciones:

1. **Generación de prompts**: `generar_prompt_unificado(perfil)` produce un system prompt que inyecta la jurisdicción, tipo, terminología legal, roles jurídicos y alertas de calibración del caso. El mismo perfil puede generar prompts adaptados para cada analyzer individual (`generar_prompt_inconsistencias`, `generar_prompt_congruencia`, etc.).

2. **Aumentación del blocklist del pseudonymizer**: `generar_blocklist_perfil(perfil)` extrae nombres de lugares, instituciones y términos legales del perfil y los agrega a `NOMBRES_COMUNES_FALSO_POSITIVO`, evitando que sean tratados como nombres de persona. Esto reemplaza el paso manual de "agregar nombres de región".

3. **Tipos de entidad adicionales**: si el perfil detecta tipos de entidad adicionales más allá de personas y vehículos (ej: armas, organizaciones, cuentas bancarias), el prompt adaptado incluye convenciones de alias para esos tipos (ej: `ARMAS_001`, `ORGANIZACIONES_001`).

**Esquema del perfil** (`case_profile.json` en directorio de salida):

| Campo | Propósito |
|---|---|
| `jurisdiccion` | País/estado para contexto del prompt |
| `tipo_caso` / `subtipo_caso` | Clasificación del caso (penal/homicidio, civil/fraude, etc.) |
| `sistema_legal` | Sistema legal (civil_law/common_law/mixto) |
| `idioma_principal` | Código ISO de idioma para OCR y prompt |
| `lugares_mencionados` | Nombres de lugares → blocklist del pseudonymizer |
| `instituciones_mencionadas` | Instituciones → blocklist del pseudonymizer |
| `terminos_legales_relevantes` | Inyectados en prompts de análisis |
| `tipos_entidad_adicionales` | Tipos de entidad extra a aliasear (ARMAS, ORGANIZACIONES, etc.) |
| `roles_juridicos` | Roles para la sección de trazabilidad (reemplaza lista hardcodeada) |
| `temas_recurrentes` | Temas recurrentes inyectados en prompts |
| `alertas_calibracion` | Instrucciones específicas de análisis (ej: "verificar cadena de custodia de armas") |

**Flags del pipeline**:
- `--skip-profiling`: Omitir perfilado, usar prompts genéricos (ahorra 1 llamada LLM, análisis menos adaptado)
- `--reuse-profile`: Reutilizar `case_profile.json` existente del directorio de salida (ahorra la llamada LLM de perfilado en re-ejecuciones)

## Sistema de Analizadores

Todos los analyzers aceptan un parámetro opcional `prompt`. Cuando es `None`, usan su prompt genérico hardcodeado como fallback.

| Archivo | Función | Generador de Prompt Dinámico |
|---|---|---|
| `inconsistencias.py` | `analizar_inconsistencias(texto, meta, prompt=)` | `adapter.generar_prompt_inconsistencias(perfil)` |
| `congruencia.py` | `analizar_congruencia(texto, meta, prompt=)` | `adapter.generar_prompt_congruencia(perfil)` |
| `trazabilidad.py` | `analizar_trazabilidad(texto, meta, prompt=)` | `adapter.generar_prompt_trazabilidad(perfil)` |
| `declaracion_vs_evidencia.py` | `analizar_declaracion_evidencia(texto, meta, prompt=)` | `adapter.generar_prompt_declaracion_evidencia(perfil)` |
| `analizador_unificado.py` | `analizar_fragmento(texto, meta, prompt=)` | `adapter.generar_prompt_unificado(perfil)` |

**Actualmente activo**: El analyzer unificado con `prompt=` del adapter. Los analyzers individuales están disponibles pero no conectados en `runner.py`.

## Cliente LLM (`llm_client.py`)

- `call_llm()` — respuesta de texto crudo, lógica de retry (backoff exponencial en 429/5xx, 5s de espera en timeout)
- `call_llm_json()` — parsea respuesta como JSON, con reparación agresiva:
  1. Elimina code fences de markdown
  2. Agrega llaves/corchetes de cierre faltantes basándose en conteo de balance
  3. Extrae el primer objeto JSON válido vía parser con tracking de profundidad
- Prompting JSON: agrega `"Responde EXCLUSIVAMENTE con JSON válido"` al último mensaje de usuario

## Pseudonimizador (`pseudonymizer.py`)

- **Sin spaCy** — regex custom para velocidad y portabilidad
- Patrones de extracción de entidades: `CARGO_NOMBRE_PATTERN` (alta confianza), `NOMBRE_PATTERN` (Title case + ALL CAPS), `INICIALES_PATTERN`, `VEHICULO_PATTERNS` (placas, marcas)
- `NOMBRES_COMUNES_FALSO_POSITIVO` — blocklist de términos legales en español que parecen nombres propios pero no lo son (ej: "Fiscalía", "Juzgado", meses). **Aumentada automáticamente por `adapter.py`** en runtime con nombres de lugares, instituciones y términos legales detectados del perfil del caso. No requiere actualización manual.
- **Deduplicación**: si "Juan Pérez" es substring de "Juan Pérez Mendoza", el más corto se descarta (se mantiene si tiene mayor frecuencia)
- **El orden de reemplazo importa**: vehículos primero (regex más específico), luego personas ordenadas por longitud de nombre descendente (más largo primero evita reemplazos parciales)
- Reemplazo de personas usa límites de palabra `\b`; reemplazo de vehículos no los usa (placas/marcas pueden estar adyacentes a puntuación)

## Fragmentador (`chunker.py`)

- `chunk_texto()` — chunks de tamaño fijo con snapping a límite de párrafo: si un corte cae dentro de los últimos 5K chars, retrocede hasta el último `\n\n`
- `chunk_por_secciones()` — división por párrafos con techo de 100K (estrategia alternativa, sin uso actual en el pipeline)

## Generador de Reportes (`report.py`)

- `consolidar_hallazgos()` — fusiona resultados de todos los chunks, deduplicando entradas de trazabilidad y acumulando acciones/relaciones
- `generar_reporte()` — produce Markdown con nombres reales restaurados vía `desudonimizar()`
- Salida: `reporte_forense.json` (programático, aún tiene referencias de alias) + `reporte_forense.md` (legible, nombres reales)
- Secciones: 1. Inconsistencias, 2. Incongruencias, 3. Trazabilidad, 4. Declaración vs. Evidencia, + Apéndice de alias

## Directorio de Salida (`output/`)

**Gitignored** — contiene datos específicos del caso y PII. Se genera en runtime.

| Archivo | Contenido |
|---|---|
| `alias_map.json` | Mapeo seudónimo → nombre real (sensible) |
| `case_profile.json` | Perfil del caso auto-generado (jurisdicción, términos, entidades) |
| `resultados_unificados.json` | Resultados crudos del análisis LLM por chunk |
| `reporte_forense.json` | Análisis consolidado (programático) |
| `reporte_forense.md` | Reporte final legible |
| `ui/` | Visor HTML estático (SPA tema oscuro) |

## Gotchas y Patrones No Obvios

1. **`ANALYZERS` dict sobrescrito**: Si agregas analyzers individuales al dict en `runner.py`, asegúrate de que el analyzer unificado no lo sobreescriba después.

2. **Patrón `sys.path.insert(0, ...)`**: Múltiples archivos insertan manualmente la raíz del proyecto en `sys.path` para imports entre módulos. Necesario porque no hay instalación de paquete.

3. **Chunk size en caracteres, no tokens**: 100K chars ≈ 25K tokens en texto en español. Calibrado para modelos con ventanas de contexto de 200K tokens.

4. **`call_llm_json()` reparación frágil**: Las estrategias de reparación JSON (balance de llaves, extracción por tracking de profundidad) funcionan para errores comunes del LLM pero pueden producir resultados malformados para estructuras anidadas complejas. Si la calidad del análisis degrada, revisar la salida cruda del LLM por truncamiento.

5. **Pseudonymizer usa `re.IGNORECASE`**: Los nombres reales se reemplazan case-insensitivamente, así que "JUAN PÉREZ" y "Juan Pérez" ambos se convierten en el mismo alias. Sin embargo, `desudonimizar()` usa `str.replace()` simple que es case-sensitive — solo restaura la forma canónica de la tabla de alias.

6. **Filtro `TOMOS_INCLUIDOS` basado en substring**: Verifica `any(t in k for t in tomos_incluidos)` contra nombres de archivo, así que `["T30"]` matchea tanto `T30.txt` como `T301.txt`.

7. **Reporte JSON vs Markdown difieren en seudonimización**: `reporte_forense.json` contiene referencias de alias que requieren desudonimización manual, mientras `reporte_forense.md` ya tiene nombres reales restaurados.

8. **Umbral de fallback OCR en PDF**: Páginas con <30 chars de texto nativo se tratan como escaneadas y se envían a OCR. Ajustar este umbral en `loader.py:_load_pdf()` si tus PDFs tienen páginas cortas pero con texto nativo válido.

9. **Blocklist del pseudonymizer se auto-aumenta**: El adapter agrega nombres de lugares y términos de instituciones a `NOMBRES_COMUNES_FALSO_POSITIVO` en runtime. Esto muta el set global en `pseudonymizer.py` — si importas pseudonymizer antes de que el adapter corra, el blocklist estará incompleto. El runner maneja el orden correctamente.

10. **`output/` está gitignored**: Toda la salida del pipeline contiene PII específica del caso. El directorio se crea en runtime.

11. **Perfilado del caso cuesta 1 llamada LLM extra**: El adapter envía ~30K chars de texto muestra para generar el perfil. Usar `--reuse-profile` en re-ejecuciones para saltarlo, o `--skip-profiling` para usar prompts genéricos enteramente.

12. **Generadores de prompts del adapter usan f-strings con llaves dobles**: Las funciones `generar_prompt_*` usan f-strings de Python con `{{`/`}}` para ejemplos JSON en el prompt. Al modificar, recordar que `{{` en un f-string produce un `{` literal en la salida.

## Estilo de Código

- Lenguaje: Python 3, comentarios y nombres de variables en español (coincide con el dominio legal)
- Type hints: usados en firmas de funciones pero no enforceados
- Docstrings: en español, presentes en todas las funciones públicas
- Imports: hack de `sys.path.insert` en cada archivo para imports entre módulos
- Sin linting, formateo, o type-checking configurado
- Sin framework de tests — `pseudonymizer.py` tiene un test inline `__main__` únicamente

## Agregar un Nuevo Analyzer

1. Crear `analyzers/nuevo_analisis.py` con una constante `PROMPT_*_GENERICO` y una función `analizar_*(texto_chunk, chunk_meta, prompt=None)`
2. Seguir el patrón: `(texto_chunk, chunk_meta, prompt=) -> dict` usando `call_llm_json(messages, model=MODELO_RAPIDO)`
3. Agregar una función `generar_prompt_nuevo_analisis(perfil)` en `adapter.py`
4. Importar en `runner.py` y conectar al loop de análisis
5. Agregar lógica de consolidación en `report.py:consolidar_hallazgos()` y renderizado en `generar_reporte()`
6. Actualizar la numeración de secciones del reporte

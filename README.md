# Motor de Análisis Forense

Motor de análisis forense de propósito general para expedientes legales. Ingiere documentos de cualquier caso (`.txt`, `.pdf`, imágenes), seudonimiza entidades, analiza con LLM, y genera reportes con hallazgos estructurados.

## Qué Hace

1. **Ingesta multi-formato**: Lee `.txt`, `.pdf` (texto nativo + OCR para páginas escaneadas), e imágenes (OCR vía Tesseract)
2. **Seudonimización automática**: Detecta personas y vehículos con regex forense, genera alias canónicos (PERSONA_001, VEHICULO_001), y reemplaza todas las menciones en el texto
3. **Adaptación por caso**: Perfila automáticamente cada expediente (jurisdicción, tipo de caso, términos legales, roles) y adapta los prompts de análisis en consecuencia
4. **Análisis forense multidimensional** (4 dimensiones en una sola llamada LLM):
   - **Inconsistencias** — contradicciones entre declaraciones de diferentes personas
   - **Incongruencias** — problemas internos de lógica, fechas, lugares, cantidades, secuencias
   - **Trazabilidad** — mapa de entidades: roles, acciones, relaciones, menciones
   - **Declaración vs. Evidencia** — discrepancias entre lo que dice una persona y la evidencia objetiva
5. **Reportes**: Genera reporte JSON (programático) y Markdown (legible con nombres reales restaurados)

## Arquitectura

```
Documentos (.txt, .pdf, imágenes)
  → loader.py          (ingesta y extracción de texto)
  → adapter.py         (perfila el caso, adapta prompts y blocklist)
  → pseudonymizer.py   (extrae entidades, genera alias, reemplaza nombres)
  → chunker.py         (fragmenta en chunks de ~100K chars con overlap)
  → analyzers/         (análisis LLM con prompts adaptados al caso)
  → report.py          (consolida hallazgos, restaura nombres, genera reporte)
  → output/            (resultados + visor HTML)
```

## Requisitos

### Sistema
- Python 3.10+
- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) v5+ con paquete de idioma `spa`
- `poppler-utils` (para `pdftotext`)

### Python
```bash
pip install requests pymupdf pytesseract pdf2image Pillow
```

### API
- Cuenta con acceso a API compatible con OpenAI (ej: Ollama, MiniMax, OpenRouter)
- Variable de entorno `OLLAMA_API_KEY` requerida

## Uso Rápido

```bash
# Configurar
export OLLAMA_API_KEY="tu-api-key"
export INPUT_DIR="/ruta/a/expediente"

# Ejecutar pipeline completo
python runner.py
```

### Argumentos CLI

| Flag | Descripción |
|---|---|
| `--input-dir`, `-i` | Directorio con documentos fuente |
| `--output-dir`, `-o` | Directorio de salida (default: `./output`) |
| `--tomos`, `-t` | Filtrar por nombre de tomo (ej: `T30,T45`) |
| `--reuse-alias` | Reutilizar tabla de alias existente |
| `--reuse-profile` | Reutilizar perfil de caso existente |
| `--skip-profiling` | Omitir perfilado, usar prompts genéricos |

### Variables de Entorno

| Variable | Default | Descripción |
|---|---|---|
| `INPUT_DIR` | (requerido) | Directorio con documentos del caso |
| `OUTPUT_DIR` | `./output` | Directorio para archivos generados |
| `OLLAMA_API_KEY` | (requerido) | API key para el LLM |
| `OLLAMA_BASE_URL` | `https://ollama.com/v1` | URL base de la API |
| `MODELO_RAPIDO` | `minimax-m3:cloud` | Modelo para análisis |
| `MODELO_PROFUNDO` | `kimi-k2.7-code:cloud` | Modelo para contexto largo (reservado) |
| `CHUNK_SIZE` | `100000` | Tamaño de chunk en caracteres |
| `CHUNK_OVERLAP` | `5000` | Overlap entre chunks en caracteres |
| `OCR_DPI` | `300` | Resolución DPI para OCR en PDFs |
| `OCR_LANG` | `spa` | Idioma de Tesseract |
| `TOMOS_INCLUIDOS` | (todos) | Filtro de tomos separados por coma |

## Adaptación Automática por Caso

El motor perfila automáticamente cada expediente antes de analizarlo:

1. Toma una muestra del corpus (~30K chars)
2. El LLM genera un perfil: jurisdicción, tipo de caso, términos legales, roles jurídicos, alertas de calibración
3. El perfil se usa para:
   - **Adaptar los prompts** de análisis con contexto específico del caso
   - **Aumentar el blocklist** del pseudonymizer con lugares e instituciones del caso
   - **Incluir tipos de entidad adicionales** si el caso los requiere (armas, organizaciones, etc.)

El perfil se guarda como `case_profile.json` en el directorio de salida y se reutiliza con `--reuse-profile`.

## Formatos de Entrada Soportados

| Formato | Extensión | Método de extracción |
|---|---|---|
| Texto plano | `.txt` | Lectura directa UTF-8 |
| PDF con texto nativo | `.pdf` | PyMuPDF (extracción de texto) |
| PDF escaneado | `.pdf` | PyMuPDF render + Tesseract OCR |
| Imágenes | `.png`, `.jpg`, `.jpeg`, `.tiff`, `.tif`, `.bmp`, `.webp` | Tesseract OCR directo |

## Salida

Todos los archivos se generan en `OUTPUT_DIR` (gitignored por contener PII):

| Archivo | Descripción |
|---|---|
| `case_profile.json` | Perfil auto-generado del caso |
| `alias_map.json` | Mapeo seudónimo → nombre real (sensible) |
| `resultados_unificados.json` | Resultados crudos por chunk |
| `reporte_forense.json` | Análisis consolidado (programático) |
| `reporte_forense.md` | Reporte legible con nombres reales |
| `ui/` | Visor HTML estático para navegar resultados |

## Estructura del Proyecto

```
motor-analisis-forense/
├── runner.py                  # Orquestador del pipeline (CLI)
├── adapter.py                 # Adaptación automática por caso
├── loader.py                  # Carga de documentos (txt, pdf, imágenes)
├── pseudonymizer.py           # Extracción de entidades y seudonimización
├── chunker.py                 # Fragmentación de texto
├── llm_client.py              # Cliente LLM (OpenAI-compatible)
├── config.py                  # Configuración vía env vars
├── report.py                  # Generación de reportes
├── analyzers/
│   ├── analizador_unificado.py    # Analyzer 4-en-1 (activo)
│   ├── inconsistencias.py         # Contradicciones entre declaraciones
│   ├── congruencia.py             # Incongruencias internas
│   ├── trazabilidad.py            # Trazabilidad de entidades
│   └── declaracion_vs_evidencia.py # Declaraciones vs. evidencia
├── output/                    # Resultados (gitignored)
└── AGENTS.md                  # Documentación para agentes AI
```

## Licencia

Privado — uso interno.

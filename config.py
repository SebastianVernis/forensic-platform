"""
Configuración del motor de análisis forense.
NO escribir API keys en este archivo. Se leen del entorno.
Todos los paths se configuran vía variables de entorno.
"""
import os
from pathlib import Path

# Cargar .env si existe (sin dependencia externa)
_env_path = Path(__file__).with_name(".env")
if _env_path.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_path)
    except Exception:
        # fallback: leer lineas KEY=VAL manualmente
        with open(_env_path, "r", encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if not _line or _line.startswith("#") or "=" not in _line:
                    continue
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

# --- API ---
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY", "not-needed-for-local")

# Cloud endpoint (Ollama Cloud subscription)
OLLAMA_CLOUD_URL = os.getenv("OLLAMA_CLOUD_URL", "https://ollama.com/v1")
OLLAMA_CLOUD_API_KEY = os.getenv("OLLAMA_CLOUD_API_KEY", "")

# Mimo (OpenAI-compatible API, suscripción del usuario)
# Este es el cloud primario cuando se prefiere calidad sobre costo.
MIMO_BASE_URL = os.getenv("MIMO_BASE_URL", "")
MIMO_API_KEY = os.getenv("MIMO_API_KEY", "")
MIMO_MODEL = os.getenv("MIMO_MODEL", "mimo-v2.5-pro")
MIMO_TIMEOUT = int(os.getenv("MIMO_TIMEOUT", "300"))  # 5 min

# Modelos (default local por privacidad)
# IMPORTANTE: solo se cargan al importar; cambios en .env requieren reiniciar el proceso.
# MODELO_CLOUD default = minimax-m3:cloud (unico modelo flagship accesible
# sin subscripcion Pro/Max/Team). Alternativas verificadas: gpt-oss:20b, gpt-oss:120b.
# Si MIMO_API_KEY está configurado, mimo-v2.5-pro toma precedencia.
MODELO_RAPIDO = os.getenv("MODELO_RAPIDO", "llama3.1:latest")
MODELO_PROFUNDO = os.getenv("MODELO_PROFUNDO", "llama3.1:latest")
MODELO_CLOUD = os.getenv("MODELO_CLOUD", "minimax-m3:cloud")

# Timeout LLM (s)
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "1200"))  # 20 min - modelo 8B CPU tarda 500-800s/call
LLM_TIMEOUT_CLOUD = int(os.getenv("LLM_TIMEOUT_CLOUD", "180"))

# Retry
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))
LLM_BACKOFF_FACTOR = float(os.getenv("LLM_BACKOFF_FACTOR", "2.0"))

# --- Corpus ---
# Directorio de entrada: .txt, .pdf, .png, .jpg, .tiff
# OBLIGATORIO: setear INPUT_DIR antes de ejecutar
INPUT_DIR = os.getenv("INPUT_DIR", "")
OUTPUT_DIR = os.getenv("OUTPUT_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "output"))
ALIAS_FILE = os.path.join(OUTPUT_DIR, "alias_map.json")
REPORT_FILE = os.path.join(OUTPUT_DIR, "reporte_forense.md")
REPORT_JSON = os.path.join(OUTPUT_DIR, "reporte_forense.json")

# --- Chunker ---
# Tamaño de chunk en caracteres. Los modelos tienen 200K tokens de contexto.
# 100K chars ≈ 25K tokens (español), deja espacio para el prompt + respuesta.
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "100000"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "5000"))

# --- Pseudonimización ---
# Entidades a seudonimizar (separadas por coma en env var)
ENTIDADES_ENV = os.getenv("ENTIDADES_A_SEUDONIMIZAR", "PERSONA,VEHICULO")
ENTIDADES_A_SEUDONIMIZAR = [e.strip() for e in ENTIDADES_ENV.split(",")]

# --- Análisis ---
# Tomos a analizar (None = todos los archivos de INPUT_DIR)
# Separados por coma en env var, ej: TOMOS_INCLUIDOS=T30,T45
_tomos_env = os.getenv("TOMOS_INCLUIDOS", "")
TOMOS_INCLUIDOS = [t.strip() for t in _tomos_env.split(",") if t.strip()] or None

# --- OCR (para PDF e imágenes) ---
OCR_DPI = int(os.getenv("OCR_DPI", "300"))
OCR_LANG = os.getenv("OCR_LANG", "spa")

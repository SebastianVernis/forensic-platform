"""
Analyzer unificado de análisis forense.
Combina las 4 tareas en una sola llamada LLM:
1. Inconsistencias entre declaraciones
2. Incongruencias internas
3. Trazabilidad de entidades
4. Declaraciones vs. evidencia

El prompt se genera dinámicamente por caso vía adapter.py.
Si no se proporciona prompt, usa un prompt genérico de fallback.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm_client import call_llm_json
from llm_selector import call_llm_json_hybrid
from config import MODELO_RAPIDO, MODELO_CLOUD

PROMPT_UNIFICADO_GENERICO = """Eres un analista forense senior revisando un expediente legal.

CONTEXTO:
- Los nombres reales de personas se reemplazaron por alias del tipo PERSONA_001.
- Los vehículos se reemplazaron por alias del tipo VEHICULO_001.
- Los alias son consistentes en todo el corpus.
- El texto puede contener errores menores de OCR.
- Sé riguroso, no inventes hechos que no estén en el texto.

TU TAREA: Analiza el fragmento del expediente y responde en JSON con CUATRO secciones:

1. inconsistencias: Contradicciones entre declaraciones de diferentes personas. Incluye: contradicción directa, discrepancia de tiempo/lugar/secuencia/identidad, cambio de versión, omisión.
2. incongruencias: Problemas internos de lógica/fechas/lugares/cantidades/secuencia. Incluye: fechas imposibles, lugares imposibles, cantidades inconsistentes, identidades cruzadas.
3. trazabilidad: Para cada alias PERSONA_XXX o VEHICULO_XXX mencionado, indica su rol probable, acciones principales, relaciones con otros aliases, y menciones clave textuales.
4. declaracion_vs_evidencia: Discrepancias entre lo que dice una persona y la evidencia objetiva (dictámenes, pruebas, documentos oficiales).

FORMATO JSON EXACTO (sin texto extra, sin markdown):
{
  "inconsistencias": [
    {
      "id": "INC-001",
      "tipo": "contradiccion_directa|discrepancia_tiempo|discrepancia_lugar|discrepancia_secuencia|discrepancia_identidad|cambio_version|omision",
      "severidad": "alta|media|baja",
      "descripcion": "MÁXIMO 200 caracteres",
      "personas_involucradas": ["PERSONA_001"],
      "evidencia_tomo_actual": "MÁXIMO 120 caracteres"
    }
  ],
  "incongruencias": [
    {
      "id": "ING-001",
      "tipo": "fecha|logica|cantidad|secuencia|identidad",
      "severidad": "alta|media|baja",
      "descripcion": "MÁXIMO 200 caracteres",
      "evidencia": "MÁXIMO 120 caracteres",
      "validacion_requerida": "MÁXIMO 100 caracteres"
    }
  ],
  "trazabilidad": {
    "PERSONA_001": {
      "rol_probable": "acusado|testigo|victima|oficial|perito|otro|desconocido",
      "acciones_principales": ["MÁXIMO 100 caracteres"],
      "personas_relacionadas": ["PERSONA_003"],
      "vehiculos_relacionados": ["VEHICULO_002"],
      "menciones_clave": ["MÁXIMO 80 caracteres"]
    }
  },
  "declaracion_vs_evidencia": [
    {
      "id": "DEV-001",
      "tipo": "sin_respaldo|contradiccion|evidencia_ignorada|sobreestimacion|subestimacion|cadena_custodia",
      "severidad": "alta|media|baja",
      "descripcion": "MÁXIMO 200 caracteres",
      "declaracion": "MÁXIMO 120 caracteres",
      "evidencia": "MÁXIMO 120 caracteres",
      "persona": "PERSONA_XXX",
      "implicacion_legal": "MÁXIMO 100 caracteres"
    }
  ]
}

Si alguna categoría no tiene hallazgos, usa un array vacío [] o {} para trazabilidad.
CIERRA TODAS LAS LLAVES Y COMILLAS. SOLO JSON."""


def analizar_fragmento(texto_chunk: str, chunk_meta: dict, prompt: str = None) -> dict:
    """
    Analiza un fragmento del expediente.
    Usa local por defecto (barato, privado). Fallback a cloud si local falla.
    """
    system_prompt = prompt or PROMPT_UNIFICADO_GENERICO

    user_msg = f"""Analiza el siguiente fragmento del expediente:

METADATA: {chunk_meta}

TEXTO:
---
{texto_chunk}
---

Proporciona el análisis en el formato JSON unificado solicitado."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_msg}
    ]

    # 2026-08-19: Cambiado de local -> cloud porque Ollama local tarda 170s/call
    # (cuello de botella en disco/CPU para llama3.1 con KV cache 60K chars).
    # Cloud (minimax-m3:cloud) responde en 3-5s. Si cloud falla, fallback automatico.
    return call_llm_json_hybrid(messages, model=MODELO_RAPIDO, prefer_backend="cloud") or {}

"""
Analyzer 4: Declaraciones vs. evidencia.
Compara lo que dicen las personas con lo que dicen los dictámenes periciales,
pruebas físicas, y otros elementos de evidencia objetiva.
Detecta afirmaciones no respaldadas o contradichas por la evidencia.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm_client import call_llm, call_llm_json
from config import MODELO_RAPIDO

PROMPT_DECLARACION_EVIDENCIA_GENERICO = """Eres un analista forense revisando congruencia entre DECLARACIONES y EVIDENCIA.

Tu trabajo: detectar cuando lo que dice una persona NO coincide con lo que dice 
la evidencia objetiva del caso (dictámenes periciales, pruebas, documentos oficiales, etc.)

CONTEXTO:
- Expediente legal real
- Alias consistentes

TIPOS DE DISCREPANCIAS:
1. Afirmación sin respaldo: alguien dice X, no hay evidencia que respalde X
2. Afirmación contradicha: alguien dice X, hay evidencia que muestra NO-X
3. Evidencia ignorada: hay evidencia objetiva que nadie menciona
4. Sobreestimación: alguien exagera lo que la evidencia muestra
5. Subestimación: alguien minimiza lo que la evidencia muestra
6. Cadenas de custodia rotas: se mencionan pruebas sin cadena documentada

FORMATO DE SALIDA (JSON estricto):
{
  "discrepancias": [
    {
      "id": "DEV-001",
      "tipo": "sin_respaldo|contradiccion|evidencia_ignorada|sobreestimacion|subestimacion|cadena_custodia",
      "severidad": "alta|media|baja",
      "descripcion": "Descripción clara de la discrepancia",
      "declaracion": "Lo que dice la persona (cita o parafrasis)",
      "evidencia": "Lo que muestra la evidencia objetiva (cita o parafrasis)",
      "persona": "PERSONA_XXX que hizo la declaración",
      "implicacion_legal": "Qué significa esto legalmente"
    }
  ]
}

Si no encuentras discrepancias: {"discrepancias": []}
Sé riguroso: solo reporta discrepancias con base clara en el texto.
"""


def analizar_declaracion_evidencia(texto_chunk: str, chunk_meta: dict, prompt: str = None) -> dict:
    system_prompt = prompt or PROMPT_DECLARACION_EVIDENCIA_GENERICO
    user_msg = f"""Analiza el siguiente fragmento del expediente:

METADATA: {chunk_meta}

TEXTO:
\"\"\"
{texto_chunk}
\"\"\"

Detecta discrepancias entre declaraciones y evidencia objetiva. 
Responde SOLO con JSON válido."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_msg}
    ]

    resultado = call_llm_json(messages, model=MODELO_RAPIDO)
    if resultado is None:
        return {"error": "no_se_pudo_parsear", "chunk": chunk_meta}
    return resultado

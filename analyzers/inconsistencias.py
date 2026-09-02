"""
Analyzer 1: Detector de inconsistencias entre declaraciones.
Compara lo que dicen diferentes personas (acusados, testigos, víctimas, oficiales)
sobre los mismos hechos y detecta contradicciones.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm_client import call_llm, call_llm_json
from config import MODELO_RAPIDO, MODELO_PROFUNDO

PROMPT_INCONSISTENCIAS_GENERICO = """Eres un analista forense senior revisando un expediente legal.

Tu trabajo: detectar INCONSISTENCIAS entre las declaraciones de diferentes personas.

CONTEXTO IMPORTANTE:
- Los nombres reales han sido reemplazados por alias (PERSONA_001, etc.)
- Los vehículos también (VEHICULO_001, etc.)
- Los alias son consistentes: misma persona = mismo alias en todo el texto
- Esto es un caso real de procuración de justicia, sé riguroso

TIPO DE INCONSISTENCIAS A BUSCAR:
1. Contradicciones directas: A dice X, B dice NO-X sobre el mismo hecho
2. Discrepancias de tiempo: A dice que pasó a las 14:00, B dice a las 16:00
3. Discrepancias de lugar: A dice "en la ciudad X", B dice "en la ciudad Y"
4. Discrepancias de secuencia: el orden de eventos no coincide entre declarantes
5. Discrepancias de identidad: A identifica a persona X, B identifica a persona Y como el autor
6. Cambios de versión: la misma persona cuenta cosas diferentes en distintas declaraciones
7. Omisiones críticas: alguien no menciona algo que todos los demás mencionan

FORMATO DE SALIDA (JSON estricto):
{
  "inconsistencias": [
    {
      "id": "INC-001",
      "tipo": "contradiccion_directa|discrepancia_tiempo|discrepancia_lugar|discrepancia_secuencia|discrepancia_identidad|cambio_version|omision",
      "severidad": "alta|media|baja",
      "descripcion": "Descripción clara y concisa de la inconsistencia",
      "personas_involucradas": ["PERSONA_001", "PERSONA_003"],
      "evidencia_tomo_actual": "Cita textual relevante del texto actual",
      "referencia_cruzada": "Si hay referencia a otro tomo/sección",
      "posible_explicacion": "Si hay una explicación inocente posible, indícala; si no, 'ninguna'"
    }
  ]
}

Si NO encuentras inconsistencias en este fragmento, retorna:
{"inconsistencias": []}

IMPORTANTE: Solo reporta inconsistencias con evidencia clara en el texto. No inventes.
"""


def analizar_inconsistencias(texto_chunk: str, chunk_meta: dict, prompt: str = None) -> dict:
    """Analiza un chunk buscando inconsistencias internas."""
    system_prompt = prompt or PROMPT_INCONSISTENCIAS_GENERICO
    user_msg = f"""Analiza el siguiente fragmento del expediente:

METADATA DEL CHUNK: {chunk_meta}

TEXTO:
\"\"\"
{texto_chunk}
\"\"\"

Detecta inconsistencias entre las declaraciones mencionadas en este fragmento. 
Responde SOLO con JSON válido."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_msg}
    ]

    resultado = call_llm_json(messages, model=MODELO_RAPIDO)
    if resultado is None:
        return {"error": "no_se_pudo_parsear", "chunk": chunk_meta}
    return resultado

"""
Analyzer 3: Trazabilidad de entidades.
Rastrea menciones de personas, vehículos, armas, oficios a través del expediente.
Genera un mapa de quién aparece dónde, cuántas veces, en qué contexto.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm_client import call_llm, call_llm_json
from config import MODELO_RAPIDO

PROMPT_TRAZABILIDAD_GENERICO = """Eres un analista forense creando un mapa de trazabilidad de entidades.

Tu trabajo: para cada alias (PERSONA_XXX o VEHICULO_XXX) que aparezca en el texto, 
extraer su rol, sus acciones, y referencias clave.

CONTEXTO:
- Expediente legal real
- Alias son consistentes en todo el corpus

TIPO DE TRAZABILIDAD:
1. Rol de la persona: acusado, testigo, víctima, oficial, perito, etc.
2. Acciones principales: qué hizo esta persona según el texto
3. Relaciones: con quién interactúa, en qué contexto
4. Línea temporal: en qué orden aparecen sus menciones

FORMATO DE SALIDA (JSON estricto):
{
  "entidades": {
    "PERSONA_001": {
      "rol_probable": "acusado|testigo|víctima|oficial|perito|otro|desconocido",
      "acciones_principales": ["acción 1", "acción 2"],
      "personas_relacionadas": ["PERSONA_003"],
      "vehiculos_relacionados": ["VEHICULO_002"],
      "menciones_clave": ["cita textual relevante 1"]
    }
  }
}

Solo incluye entidades que tengan al menos una mención clara en el texto.
Si no hay entidades relevantes: {"entidades": {}}
"""


def analizar_trazabilidad(texto_chunk: str, chunk_meta: dict, prompt: str = None) -> dict:
    system_prompt = prompt or PROMPT_TRAZABILIDAD_GENERICO
    user_msg = f"""Analiza el siguiente fragmento del expediente:

METADATA: {chunk_meta}

TEXTO:
\"\"\"
{texto_chunk}
\"\"\"

Crea el mapa de trazabilidad de entidades en este fragmento. 
Responde SOLO con JSON válido."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_msg}
    ]

    resultado = call_llm_json(messages, model=MODELO_RAPIDO)
    if resultado is None:
        return {"error": "no_se_pudo_parsear", "chunk": chunk_meta}
    return resultado

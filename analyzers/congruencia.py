"""
Analyzer 2: Validador de congruencia interna.
Detecta hechos, fechas, lugares, cantidades o secuencias que no cuadran
internamente en el mismo documento.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm_client import call_llm, call_llm_json
from config import MODELO_RAPIDO

PROMPT_CONGRUENCIA_GENERICO = """Eres un analista forense revisando un expediente legal.

Tu trabajo: detectar INCONGRUENCIAS INTERNAS en el texto (hechos, fechas, lugares, 
cantidades, secuencias que no cuadran entre sí).

CONTEXTO:
- Los nombres reales están como alias (PERSONA_001, VEHICULO_001, etc.)
- Expediente real de procuración de justicia
- Sé riguroso pero no inventes problemas donde no los hay

TIPO DE INCONGRUENCIAS A BUSCAR:
1. Fechas imposibles: orden cronológico invertido, fechas futuras
2. Lugares imposibles: persona en dos lugares al mismo tiempo, distancias no cubiertas
3. Cantidades incongruentes: "10 armas" y luego "3 pistolas" sin explicación
4. Secuencias ilógicas: eventos que no pueden haber ocurrido en el orden descrito
5. Identidades cruzadas: misma persona con dos alias, dos personas con mismo alias
6. Hechos contradictorios: el texto dice X y luego dice lo opuesto sobre el mismo hecho
7. Anomalías numéricas: identificadores repetidos, sumas incorrectas

FORMATO DE SALIDA (JSON estricto):
{
  "incongruencias": [
    {
      "id": "INC-001",
      "tipo": "fecha|imposible_logica|cantidad|secuencia|identidad|contradiccion|anomalia",
      "severidad": "alta|media|baja",
      "descripcion": "Descripción clara",
      "evidencia": "Cita textual del problema",
      "ubicacion": "Tomo/sección si es identificable",
      "validacion_requerida": "Qué dato oficial habría que revisar"
    }
  ]
}

Si NO encuentras incongruencias: {"incongruencias": []}
No inventes problemas. Sé estricto: si dudas, no lo reportes.
"""


def analizar_congruencia(texto_chunk: str, chunk_meta: dict, prompt: str = None) -> dict:
    system_prompt = prompt or PROMPT_CONGRUENCIA_GENERICO
    user_msg = f"""Analiza el siguiente fragmento del expediente:

METADATA: {chunk_meta}

TEXTO:
\"\"\"
{texto_chunk}
\"\"\"

Detecta incongruencias internas. Responde SOLO con JSON válido."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_msg}
    ]

    resultado = call_llm_json(messages, model=MODELO_RAPIDO)
    if resultado is None:
        return {"error": "no_se_pudo_parsear", "chunk": chunk_meta}
    return resultado

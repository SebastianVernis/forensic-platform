"""
Adaptador por caso: genera un perfil del expediente y adapta los prompts
y la configuración del pseudonymizer automáticamente.

Flujo:
1. Toma una muestra del corpus cargado
2. Llama al LLM para generar un CaseProfile (jurisdicción, tipo de caso,
   términos legales relevantes, tipos de entidades, etc.)
3. Usa el perfil para:
   - Inyectar contexto específico en los prompts de análisis
   - Agregar lugares/nombres al blocklist del pseudonymizer
   - Ajustar tipos de entidad a seudonimizar
"""
from llm_client import call_llm_json
from llm_selector import call_llm_json_hybrid
from config import MODELO_RAPIDO, MODELO_CLOUD


PROMPT_PERFIL = """Eres un analista forense que debe caracterizar un expediente legal a partir de un fragmento de texto.

Tu trabajo: generar un PERFIL DEL CASO que ayude a calibrar un sistema de análisis automático.

Analiza el fragmento y responde en JSON con:

{
  "jurisdiccion": "país y estado/provincia (ej: México, Sonora; Argentina, CABA; Colombia, Bogotá)",
  "tipo_caso": "penal|civil|administrativo|laboral|familiar|otro",
  "subtipo_caso": "descripción corta del tipo específico (ej: homicidio múltiple, fraude, narcotráfico, corrupción)",
  "sistema_legal": "common_law|civil_law|mixto",
  "idioma_principal": "código ISO (ej: spa, eng, por)",
  "lugares_mencionados": ["lista de ciudades/estados/países mencionados como lugares geográficos, NO como nombres de persona"],
  "instituciones_mencionadas": ["lista de instituciones/fiscalías/tribunales mencionados"],
  "terminos_legales_relevantes": ["términos legales específicos de esta jurisdicción que aparecen frecuentemente (ej: carpeta de investigación, averiguación previa, sumario, auto de vinculación)"],
  "tipos_entidad_adicionales": ["tipos de entidad que aparecen y deberían seudonimizarse aparte de personas y vehículos (ej: ARMAS, ORGANIZACIONES, EMPRESAS, CUENTAS_BANCARIAS)"],
  "roles_juridicos": ["roles que aparecen en el caso (ej: imputado, testigo protector, perito, ministerio público, defensor público)"],
  "temas_recurrentes": ["temas que se repiten en el expediente (ej: cadena de custodia, derecho a la defensa, debido proceso)"],
  "alertas_calibracion": ["ajustes que el sistema de análisis debería hacer para este tipo de caso (ej: prestar atención a fechas de captura, verificar cadenas de custodia de armas)"]
}

Sé preciso y específico. No inventes información que no esté en el texto.
Si algo no es claro, usa null o lista vacía."""


import os
import sys
import json
import re
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def generar_muestra(textos: Dict[str, str], max_chars: int = 30_000) -> str:
    """
    Genera una muestra representativa del corpus.
    Toma los primeros ~max_chars del primer documento y
    fragmentos del inicio/medio de los demás.
    """
    partes = []
    remaining = max_chars

    for nombre, texto in textos.items():
        if remaining <= 0:
            break
        if len(textos) <= 2:
            # Pocos documentos: tomar más de cada uno
            chunk = texto[:remaining]
        else:
            # Muchos documentos: inicio + medio de cada uno
            mitad = len(texto) // 2
            chunk = texto[:min(5000, remaining)]
            remaining -= len(chunk)
            if remaining > 3000:
                mid_chunk = texto[mitad:mitad + min(3000, remaining)]
                chunk += "\n\n[...FRAGMENTO MEDIO...]\n\n" + mid_chunk
                remaining -= len(mid_chunk) + 30

        partes.append(f"=== {nombre} ===\n{chunk}")
        remaining -= len(chunk)

    return "\n\n".join(partes)


def perfilar_caso(textos: Dict[str, str]) -> Dict:
    """
    Genera el perfil del caso analizando una muestra del corpus.
    Usa cloud (modelo grande) por defecto para mejor razonamiento.
    Fallback a local si cloud falla.
    """
    muestra = generar_muestra(textos)

    print(f"  Generando perfil del caso ({len(muestra):,} chars de muestra)...")

    messages = [
        {"role": "system", "content": PROMPT_PERFIL},
        {"role": "user", "content": f"Analiza el siguiente fragmento representativo del expediente:\n\n\"\"\"\n{muestra}\n\"\"\""}
    ]

    perfil = call_llm_json_hybrid(messages, model=MODELO_CLOUD, prefer_backend="cloud")
    if not perfil:
        print("  WARNING: No se pudo generar perfil con cloud. Fallback a local.")
        perfil = call_llm_json(messages, model=MODELO_RAPIDO)
    if not perfil:
        print("  No se pudo generar perfil. Usando perfil genérico.")
        return _perfil_generico()

    print(f"  Perfil generado: {perfil.get('jurisdiccion', '?')} / {perfil.get('subtipo_caso', '?')}")
    return perfil


def _perfil_generico() -> Dict:
    """Perfil genérico de fallback."""
    return {
        "jurisdiccion": "No determinada",
        "tipo_caso": "penal",
        "subtipo_caso": "general",
        "sistema_legal": "civil_law",
        "idioma_principal": "spa",
        "lugares_mencionados": [],
        "instituciones_mencionadas": [],
        "terminos_legales_relevantes": [],
        "tipos_entidad_adicionales": [],
        "roles_juridicos": [],
        "temas_recurrentes": [],
        "alertas_calibracion": [],
    }


def generar_blocklist_perfil(perfil: Dict) -> set:
    """
    Genera entradas adicionales para el blocklist del pseudonymizer
    basándose en el perfil del caso (lugares, instituciones, etc.).
    """
    extras = set()
    for lugar in perfil.get("lugares_mencionados", []):
        if lugar and len(lugar) >= 3:
            extras.add(lugar)
            # Agregar componentes individuales de lugares compuestos
            for parte in lugar.split():
                if len(parte) >= 3 and parte[0].isupper():
                    extras.add(parte)
    for inst in perfil.get("instituciones_mencionadas", []):
        if inst and len(inst) >= 3:
            for parte in inst.split():
                if len(parte) >= 3 and parte[0].isupper():
                    extras.add(parte)
    for termino in perfil.get("terminos_legales_relevantes", []):
        if termino and len(termino) >= 3:
            for parte in termino.split():
                if len(parte) >= 3 and parte[0].isupper():
                    extras.add(parte)
    return extras


def generar_prompt_unificado(perfil: Dict) -> str:
    """
    Genera el prompt del analizador unificado adaptado al perfil del caso.
    Inyecta jurisdicción, tipo de caso, términos legales, roles y alertas.
    """
    jurisdiccion = perfil.get("jurisdiccion", "No determinada")
    tipo_caso = perfil.get("tipo_caso", "penal")
    subtipo = perfil.get("subtipo_caso", "general")
    sistema = perfil.get("sistema_legal", "civil_law")
    idioma = perfil.get("idioma_principal", "spa")

    # Roles jurídicos del caso
    roles = perfil.get("roles_juridicos", [])
    roles_texto = ", ".join(roles) if roles else "acusados, testigos, oficiales, peritos, víctimas"

    # Términos legales relevantes
    terminos = perfil.get("terminos_legales_relevantes", [])
    terminos_texto = ""
    if terminos:
        terminos_texto = f"\n- Términos legales frecuentes en este caso: {', '.join(terminos[:10])}"

    # Alertas de calibración
    alertas = perfil.get("alertas_calibracion", [])
    alertas_texto = ""
    if alertas:
        alertas_texto = f"\n- ATENCIÓN ESPECIAL: {'; '.join(alertas[:5])}"

    # Temas recurrentes
    temas = perfil.get("temas_recurrentes", [])
    temas_texto = ""
    if temas:
        temas_texto = f"\n- Temas recurrentes del caso: {', '.join(temas[:5])}"

    # Tipos de entidad adicionales
    entidades_extra = perfil.get("tipos_entidad_adicionales", [])
    alias_extra = ""
    trazabilidad_extra = ""
    if entidades_extra:
        alias_parts = []
        traz_parts = []
        for ent in entidades_extra:
            tag = ent.upper().replace(" ", "_")
            alias_parts.append(f"- Las {ent.lower()} se reemplazaron por alias del tipo {tag}_001.")
            traz_parts.append(f"      \"{tag}_001\": {{\n        \"rol_probable\": \"tipo de {ent.lower()}\",\n        \"acciones_principales\": [\"MÁXIMO 100 caracteres\"],\n        \"relacionado_con\": [\"PERSONA_003\", \"VEHICULO_002\"],\n        \"menciones_clave\": [\"MÁXIMO 80 caracteres\"]\n      }}")
        alias_extra = "\n" + "\n".join(alias_parts)
        trazabilidad_extra = ",\n" + ",\n".join(traz_parts)

    # Adaptar roles en trazabilidad según el caso
    roles_traz = roles if roles else ["acusado", "testigo", "victima", "oficial", "perito", "otro", "desconocido"]
    roles_traz_str = "|".join(r.lower().replace(" ", "_") for r in roles_traz)

    prompt = f"""Eres un analista forense senior revisando un expediente legal.

CONTEXTO DEL CASO:
- Jurisdicción: {jurisdiccion}
- Tipo de caso: {tipo_caso} ({subtipo})
- Sistema legal: {sistema}
- Idioma: {idioma}

CONTEXTO TÉCNICO:
- Los nombres reales de personas se reemplazaron por alias del tipo PERSONA_001.
- Los vehículos se reemplazaron por alias del tipo VEHICULO_001.{alias_extra}
- Los alias son consistentes en todo el corpus.
- El texto puede contener errores menores de OCR.
- Sé riguroso, no inventes hechos que no estén en el texto.{terminos_texto}{alertas_texto}{temas_texto}

TU TAREA: Analiza el fragmento del expediente y responde en JSON con CUATRO secciones:

1. inconsistencias: Contradicciones entre declaraciones de diferentes personas ({roles_texto}). Incluye: contradicción directa, discrepancia de tiempo/lugar/secuencia/identidad, cambio de versión, omisión.
2. incongruencias: Problemas internos de lógica/fechas/lugares/cantidades/secuencia. Incluye: fechas imposibles, lugares imposibles, cantidades inconsistentes, identidades cruzadas.
3. trazabilidad: Para cada alias PERSONA_XXX, VEHICULO_XXX{', ' + ', '.join(e.upper().replace(' ','_') + '_XXX' for e in entidades_extra) if entidades_extra else ''} mencionado, indica su rol probable, acciones principales, relaciones con otros aliases, y menciones clave textuales.
4. declaracion_vs_evidencia: Discrepancias entre lo que dice una persona y la evidencia objetiva (dictámenes, pruebas, documentos oficiales).

FORMATO JSON EXACTO (sin texto extra, sin markdown):
{{
  "inconsistencias": [
    {{
      "id": "INC-001",
      "tipo": "contradiccion_directa|discrepancia_tiempo|discrepancia_lugar|discrepancia_secuencia|discrepancia_identidad|cambio_version|omision",
      "severidad": "alta|media|baja",
      "descripcion": "MÁXIMO 200 caracteres",
      "personas_involucradas": ["PERSONA_001"],
      "evidencia_tomo_actual": "MÁXIMO 120 caracteres"
    }}
  ],
  "incongruencias": [
    {{
      "id": "ING-001",
      "tipo": "fecha|logica|cantidad|secuencia|identidad",
      "severidad": "alta|media|baja",
      "descripcion": "MÁXIMO 200 caracteres",
      "evidencia": "MÁXIMO 120 caracteres",
      "validacion_requerida": "MÁXIMO 100 caracteres"
    }}
  ],
  "trazabilidad": {{
    "PERSONA_001": {{
      "rol_probable": "{roles_traz_str}",
      "acciones_principales": ["MÁXIMO 100 caracteres"],
      "personas_relacionadas": ["PERSONA_003"],
      "vehiculos_relacionados": ["VEHICULO_002"],
      "menciones_clave": ["MÁXIMO 80 caracteres"]
    }}{trazabilidad_extra}
  }},
  "declaracion_vs_evidencia": [
    {{
      "id": "DEV-001",
      "tipo": "sin_respaldo|contradiccion|evidencia_ignorada|sobreestimacion|subestimacion|cadena_custodia",
      "severidad": "alta|media|baja",
      "descripcion": "MÁXIMO 200 caracteres",
      "declaracion": "MÁXIMO 120 caracteres",
      "evidencia": "MÁXIMO 120 caracteres",
      "persona": "PERSONA_XXX",
      "implicacion_legal": "MÁXIMO 100 caracteres"
    }}
  ]
}}

Si alguna categoría no tiene hallazgos, usa un array vacío [] o {{}} para trazabilidad.
CIERRA TODAS LAS LLAVES Y COMILLAS. SOLO JSON."""

    return prompt


def generar_prompt_inconsistencias(perfil: Dict) -> str:
    """Genera prompt adaptado para el analyzer de inconsistencias."""
    jurisdiccion = perfil.get("jurisdiccion", "No determinada")
    tipo_caso = perfil.get("tipo_caso", "penal")
    subtipo = perfil.get("subtipo_caso", "general")
    roles = perfil.get("roles_juridicos", [])
    roles_texto = ", ".join(roles) if roles else "acusados, testigos, víctimas, oficiales"
    alertas = perfil.get("alertas_calibracion", [])
    alertas_texto = ""
    if alertas:
        alertas_texto = f"\n\nATENCIÓN ESPECIAL: {'; '.join(alertas[:5])}"

    return f"""Eres un analista forense senior revisando un expediente legal de {jurisdiccion}.

Tu trabajo: detectar INCONSISTENCIAS entre las declaraciones de diferentes personas.

CONTEXTO IMPORTANTE:
- Caso de tipo {tipo_caso} ({subtipo}), jurisdicción {jurisdiccion}
- Los nombres reales han sido reemplazados por alias (PERSONA_001, etc.)
- Los vehículos también (VEHICULO_001, etc.)
- Los alias son consistentes: misma persona = mismo alias en todo el texto
- Sé riguroso{alertas_texto}

TIPO DE INCONSISTENCIAS A BUSCAR:
1. Contradicciones directas: A dice X, B dice NO-X sobre el mismo hecho
2. Discrepancias de tiempo: A dice que pasó a las 14:00, B dice a las 16:00
3. Discrepancias de lugar: A dice "en un lugar", B dice "en otro lugar"
4. Discrepancias de secuencia: el orden de eventos no coincide entre declarantes
5. Discrepancias de identidad: A identifica a persona X, B identifica a persona Y como el autor
6. Cambios de versión: la misma persona cuenta cosas diferentes en distintas declaraciones
7. Omisiones críticas: alguien no menciona algo que todos los demás mencionan

FORMATO DE SALIDA (JSON estricto):
{{
  "inconsistencias": [
    {{
      "id": "INC-001",
      "tipo": "contradiccion_directa|discrepancia_tiempo|discrepancia_lugar|discrepancia_secuencia|discrepancia_identidad|cambio_version|omision",
      "severidad": "alta|media|baja",
      "descripcion": "Descripción clara y concisa de la inconsistencia",
      "personas_involucradas": ["PERSONA_001", "PERSONA_003"],
      "evidencia_tomo_actual": "Cita textual relevante del texto actual",
      "referencia_cruzada": "Si hay referencia a otro tomo/sección",
      "posible_explicacion": "Si hay una explicación inocente posible, indícala; si no, 'ninguna'"
    }}
  ]
}}

Si NO encuentras inconsistencias en este fragmento, retorna:
{{"inconsistencias": []}}

IMPORTANTE: Solo reporta inconsistencias con evidencia clara en el texto. No inventes."""


def generar_prompt_congruencia(perfil: Dict) -> str:
    """Genera prompt adaptado para el analyzer de congruencia."""
    jurisdiccion = perfil.get("jurisdiccion", "No determinada")
    tipo_caso = perfil.get("tipo_caso", "penal")
    subtipo = perfil.get("subtipo_caso", "general")
    alertas = perfil.get("alertas_calibracion", [])
    alertas_texto = ""
    if alertas:
        alertas_texto = f"\n\nATENCIÓN ESPECIAL: {'; '.join(alertas[:5])}"

    return f"""Eres un analista forense revisando un expediente legal de {jurisdiccion}.

Tu trabajo: detectar INCONGRUENCIAS INTERNAS en el texto (hechos, fechas, lugares,
cantidades, secuencias que no cuadran entre sí).

CONTEXTO:
- Caso de tipo {tipo_caso} ({subtipo})
- Los nombres reales están como alias (PERSONA_001, VEHICULO_001, etc.)
- Sé riguroso pero no inventes problemas donde no los hay{alertas_texto}

TIPO DE INCONGRUENCIAS A BUSCAR:
1. Fechas imposibles: orden cronológico invertido, fechas futuras
2. Lugares imposibles: persona en dos lugares al mismo tiempo, distancias no cubiertas
3. Cantidades incongruentes: números que no cuadran entre sí
4. Secuencias ilógicas: eventos que no pueden haber ocurrido en el orden descrito
5. Identidades cruzadas: misma persona con dos alias, dos personas con mismo alias
6. Hechos contradictorios: el texto dice X y luego dice lo opuesto sobre el mismo hecho
7. Anomalías numéricas: identificadores repetidos, sumas incorrectas

FORMATO DE SALIDA (JSON estricto):
{{
  "incongruencias": [
    {{
      "id": "INC-001",
      "tipo": "fecha|imposible_logica|cantidad|secuencia|identidad|contradiccion|anomalia",
      "severidad": "alta|media|baja",
      "descripcion": "Descripción clara",
      "evidencia": "Cita textual del problema",
      "ubicacion": "Tomo/sección si es identificable",
      "validacion_requerida": "Qué dato oficial habría que revisar"
    }}
  ]
}}

Si NO encuentras incongruencias: {{"incongruencias": []}}
No inventes problemas. Sé estricto: si dudas, no lo reportes."""


def generar_prompt_trazabilidad(perfil: Dict) -> str:
    """Genera prompt adaptado para el analyzer de trazabilidad."""
    jurisdiccion = perfil.get("jurisdiccion", "No determinada")
    roles = perfil.get("roles_juridicos", [])
    roles_str = "|".join(r.lower().replace(" ", "_") for r in roles) if roles else "acusado|testigo|víctima|oficial|perito|otro|desconocido"

    return f"""Eres un analista forense creando un mapa de trazabilidad de entidades en un expediente de {jurisdiccion}.

Tu trabajo: para cada alias (PERSONA_XXX o VEHICULO_XXX) que aparezca en el texto,
extraer su rol, sus acciones, y referencias clave.

CONTEXTO:
- Expediente legal real
- Alias son consistentes en todo el corpus

TIPO DE TRAZABILIDAD:
1. Rol de la persona: {roles_str}
2. Acciones principales: qué hizo esta persona según el texto
3. Relaciones: con quién interactúa, en qué contexto
4. Línea temporal: en qué orden aparecen sus menciones

FORMATO DE SALIDA (JSON estricto):
{{
  "entidades": {{
    "PERSONA_001": {{
      "rol_probable": "{roles_str}",
      "acciones_principales": ["acción 1", "acción 2"],
      "personas_relacionadas": ["PERSONA_003"],
      "vehiculos_relacionados": ["VEHICULO_002"],
      "menciones_clave": ["cita textual relevante 1"]
    }}
  }}
}}

Solo incluye entidades que tengan al menos una mención clara en el texto.
Si no hay entidades relevantes: {{"entidades": {{}}}}"""


def generar_prompt_declaracion_evidencia(perfil: Dict) -> str:
    """Genera prompt adaptado para el analyzer de declaración vs evidencia."""
    jurisdiccion = perfil.get("jurisdiccion", "No determinada")
    tipo_caso = perfil.get("tipo_caso", "penal")
    subtipo = perfil.get("subtipo_caso", "general")
    alertas = perfil.get("alertas_calibracion", [])
    alertas_texto = ""
    if alertas:
        alertas_texto = f"\n\nATENCIÓN ESPECIAL: {'; '.join(alertas[:5])}"

    return f"""Eres un analista forense revisando congruencia entre DECLARACIONES y EVIDENCIA en un caso de {jurisdiccion} ({tipo_caso}: {subtipo}).

Tu trabajo: detectar cuando lo que dice una persona NO coincide con lo que dice
la evidencia objetiva del caso (dictámenes periciales, pruebas, documentos oficiales, etc.)

CONTEXTO:
- Expediente legal real
- Alias consistentes{alertas_texto}

TIPOS DE DISCREPANCIAS:
1. Afirmación sin respaldo: alguien dice X, no hay evidencia que respalde X
2. Afirmación contradicha: alguien dice X, hay evidencia que muestra NO-X
3. Evidencia ignorada: hay evidencia objetiva que nadie menciona
4. Sobreestimación: alguien exagera lo que la evidencia muestra
5. Subestimación: alguien minimiza lo que la evidencia muestra
6. Cadenas de custodia rotas: se mencionan pruebas sin cadena documentada

FORMATO DE SALIDA (JSON estricto):
{{
  "discrepancias": [
    {{
      "id": "DEV-001",
      "tipo": "sin_respaldo|contradiccion|evidencia_ignorada|sobreestimacion|subestimacion|cadena_custodia",
      "severidad": "alta|media|baja",
      "descripcion": "Descripción clara de la discrepancia",
      "declaracion": "Lo que dice la persona (cita o parafrasis)",
      "evidencia": "Lo que muestra la evidencia objetiva (cita o parafrasis)",
      "persona": "PERSONA_XXX que hizo la declaración",
      "implicacion_legal": "Qué significa esto legalmente"
    }}
  ]
}}

Si no encuentras discrepancias: {{"discrepancias": []}}
Sé riguroso: solo reporta discrepancias con base clara en el texto."""


def guardar_perfil(perfil: Dict, path: str):
    """Guarda el perfil en JSON."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(perfil, f, ensure_ascii=False, indent=2)


def cargar_perfil(path: str) -> Dict:
    """Carga un perfil previamente generado."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

"""
Extractor de entidades y pseudonymizer.
- Detecta personas (PERSONA_NN) y vehículos (VEHICULO_NN)
- Genera tabla de alias canónica y reversible
- Reemplaza todas las menciones en el texto

Usa regex forense custom (sin spaCy) para velocidad y portabilidad.
Si se necesita NER más sofisticado, se puede extender con spaCy después.
"""
import json
import re
import os
from collections import defaultdict, Counter
from typing import Dict, List, Tuple, Optional


# Patrones regex para extracción de entidades

# Nombres propios: secuencia de 2-5 palabras que inician con mayúscula
# (excluyendo palabras comunes en español que inician oración)
NOMBRES_COMUNES_FALSO_POSITIVO = {
    "Estados", "Unidos", "Mexicanos", "Federación", "República",
    "Ciudad", "Estado",
    "Fiscalía", "Ministerio", "Pública", "Carpeta", "Investigación",
    "Tribunal", "Juzgado", "Distrito", "Magistrado", "Magistrada",
    "Secretario", "Secretaria", "Oficial", "Cabo", "Sargento", "Teniente",
    "Capitán", "Comandante", "Inspector", "Director", "Subdirector",
    "Agente", "Perito", "Médico", "Doctor", "Enfermera", "Enfermero",
    "Presidente", "Defensor", "Fiscal", "Juez", "Actuario",
    "Policía", "Ministerio", "Acusado", "Acusada", "Víctima", "Testigo",
    "Denunciante", "Querellante", "Imputado", "Imputada", "Procesado", "Procesada",
    "Sentenciado", "Sentenciada", "Quejoso", "Quejosa", "Tercero",
    "Día", "Mes", "Año", "Fecha", "Hora", "Minuto", "Segundo",
    "Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo",
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
    "Cabo", "San", "Santa", "El", "La", "Los", "Las", "De", "Del",
    "Que", "Con", "Por", "Para", "Sin", "Sobre", "Bajo",
    "Acto", "Hecho", "Dato", "Documento", "Oficio", "Acuerdo",
    "Audiencia", "Diligencia", "Comparecencia", "Declaración", "Testimonio",
    "Expediente", "Causa", "Proceso", "Juicio", "Sumario",
}

# Vehículos: patrones como "vehículo", "automóvil", "camioneta", seguido de
# marca/modelo, o placas (3 letras + 3-4 dígitos)
VEHICULO_PATTERNS = [
    # "vehículo marca Ford", "camioneta tipo Pickup", etc.
    re.compile(r"(?:veh[íi]culo|autom[óo]vil|camioneta|motocicleta|cami[óo]n)\s+(?:marca|tipo|modelo|color)\s+([A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ0-9\-\s]{2,40})", re.IGNORECASE),
    # Placas mexicanas: 3 letras + 3-4 dígitos
    re.compile(r"\b([A-Z]{3}\-?\d{3,4})\b"),
    # "vehículo con placas XXX-1234"
    re.compile(r"placas?\s+([A-Z]{3}\-?\d{3,4})", re.IGNORECASE),
]

# Patrón para nombres de personas: 2-5 palabras capitalizadas
# que NO estén en la lista de falsos positivos
# Acepta: "Juan Pérez" (Title case) O "JUAN PEREZ" (ALL CAPS)
NOMBRE_PATTERN = re.compile(
    r"\b("
    r"(?:[A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,15})"           # Title case
    r"|(?:[A-ZÁÉÍÓÚÑ]{3,15})"                       # ALL CAPS
    r")"
    r"(?:\s+(?:de|del|de\s+la|de\s+los|y|la|las)\s+)?"
    r"(?:\s+(?:[A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,15}|[A-ZÁÉÍÓÚÑ]{3,15})){1,4}"
    r"\b"
)

# Cargos + nombre: "Cabo Juan Pérez", "Agente María López"
CARGO_NOMBRE_PATTERN = re.compile(
    r"\b((?:Cabo|Agente|Oficial|Inspector|Comandante|Capit[áa]n|Teniente|"
    r"Sargento|Subdirector|Director|Doctor|Dr\.|Lic\.|Licenciado|Mtro\.|Maestro|"
    r"Abogado|Defensor|Fiscal|Magistrado|Magistrada|Juez|Jueza|Actuario)\s+"
    r"(?:[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\s+){1,4}"
    r"[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)\b"
)

# Iniciales tipo "J.P.P." o "J. P. P."
INICIALES_PATTERN = re.compile(r"\b([A-Z]\.\s*[A-Z]\.?\s*[A-Z]?\.?)\b")


def extraer_nombres(texto: str) -> List[Tuple[str, int]]:
    """Extrae candidatos a nombres de personas con sus frecuencias."""
    candidatos = Counter()

    # 1) Cargo + nombre (alta confianza)
    for m in CARGO_NOMBRE_PATTERN.finditer(texto):
        nombre = m.group(1).strip()
        # Quitar el cargo inicial para guardar solo el nombre
        nombre_limpio = re.sub(
            r"^(Cabo|Agente|Oficial|Inspector|Comandante|Capit[áa]n|Teniente|"
            r"Sargento|Subdirector|Director|Doctor|Dr\.|Lic\.|Licenciado|Mtro\.|Maestro|"
            r"Abogado|Defensor|Fiscal|Magistrado|Magistrada|Juez|Jueza|Actuario)\s+",
            "", nombre
        ).strip()
        if nombre_limpio:
            candidatos[nombre_limpio] += 1

    # 2) Secuencia de palabras capitalizadas
    for m in NOMBRE_PATTERN.finditer(texto):
        nombre = m.group(1).strip()
        palabras = nombre.split()
        # Filtro: todas las palabras (excepto conectores) deben tener > 2 chars
        # y NO estar en la lista de falso positivo
        if any(p in NOMBRES_COMUNES_FALSO_POSITIVO for p in palabras):
            continue
        if len(palabras) < 2:
            continue  # Una sola palabra capitalizada = probablemente sustantivo común
        if len(palabras) > 5:
            continue
        # todas las palabras reales deben tener al menos 3 letras
        reales = [p for p in palabras if p.lower() not in {"de", "del", "la", "las", "y"}]
        if all(len(p) >= 3 for p in reales):
            candidatos[nombre] += 1

    return candidatos.most_common()


def extraer_vehiculos(texto: str) -> List[Tuple[str, int]]:
    """Extrae vehículos (marcas, modelos, placas) con sus frecuencias."""
    candidatos = Counter()
    
    for patron in VEHICULO_PATTERNS:
        for m in patron.finditer(texto):
            v = m.group(1).strip()
            # filtrar palabras sueltas que no son vehículos
            if v and len(v) >= 3:
                candidatos[v] += 1
    
    return candidatos.most_common()


def construir_tabla_alias(texto_por_archivo: Dict[str, str]) -> Dict:
    """
    Construye la tabla canónica de alias.
    texto_por_archivo: {nombre_archivo: contenido}
    Retorna: {
      "personas": {alias: {nombre_real, menciones, archivos}},
      "vehiculos": {alias: {nombre_real, menciones, archivos}},
      "stats": {...}
    }
    """
    personas_global = Counter()
    archivos_por_persona = defaultdict(set)
    
    vehiculos_global = Counter()
    archivos_por_vehiculo = defaultdict(set)
    
    for archivo, texto in texto_por_archivo.items():
        for nombre, freq in extraer_nombres(texto):
            personas_global[nombre] += freq
            archivos_por_persona[nombre].add(archivo)
        for vehiculo, freq in extraer_vehiculos(texto):
            vehiculos_global[vehiculo] += freq
            archivos_por_vehiculo[vehiculo].add(archivo)
    
    # Filtrar: solo nombres con >= 1 mención (el corpus entero tendrá muchas menciones)
    # Las menciones únicas se mantienen pero quedan al final del ranking
    personas_filtradas = {
        nombre: freq for nombre, freq in personas_global.most_common()
        if freq >= 1
    }
    vehiculos_filtrados = {
        v: freq for v, freq in vehiculos_global.most_common()
        if freq >= 1
    }

    # Deduplicar: si un nombre es substring de otro, mantener el más largo/frecuente
    # ej: "Juan Pérez" es substring de "Juan Pérez Mendoza"
    nombres_ordenados = list(personas_filtradas.items())
    nombres_deduplicados = []
    nombres_descartados = set()
    for i, (nombre_i, freq_i) in enumerate(nombres_ordenados):
        if nombre_i in nombres_descartados:
            continue
        # ¿hay un nombre más largo que lo contiene?
        es_substring = False
        for nombre_j, freq_j in nombres_ordenados:
            if nombre_i != nombre_j and nombre_i in nombre_j and freq_j >= freq_i:
                es_substring = True
                break
        if not es_substring:
            nombres_deduplicados.append((nombre_i, freq_i))
        else:
            nombres_descartados.add(nombre_i)
    personas_filtradas = dict(nombres_deduplicados)
    
    # Asignar alias
    alias_personas = {}
    for i, (nombre, freq) in enumerate(personas_filtradas.items(), 1):
        alias = f"PERSONA_{i:03d}"
        alias_personas[alias] = {
            "nombre_real": nombre,
            "menciones": freq,
            "archivos": sorted(archivos_por_persona[nombre])
        }
    
    alias_vehiculos = {}
    for i, (vehiculo, freq) in enumerate(vehiculos_filtrados.items(), 1):
        alias = f"VEHICULO_{i:03d}"
        alias_vehiculos[alias] = {
            "nombre_real": vehiculo,
            "menciones": freq,
            "archivos": sorted(archivos_por_vehiculo[vehiculo])
        }
    
    return {
        "personas": alias_personas,
        "vehiculos": alias_vehiculos,
        "stats": {
            "personas_unicas": len(alias_personas),
            "vehiculos_unicos": len(alias_vehiculos),
            "archivos_procesados": len(texto_por_archivo)
        }
    }


def _build_master_pattern(tabla: Dict) -> Tuple[re.Pattern, Dict[str, str]]:
    """Pre-compila UN regex maestro con todos los alias, en lugar de N regex.

    Para textos de 186M chars con miles de personas/vehiculos, este cambio
    pasa el tiempo de seudonimizacion de ~40 min a <2 min.

    Returns:
        (compiled_pattern, name_to_alias_map)  <- invertido: nombre -> alias
    """
    # Combinar todos los nombres a reemplazar
    name_to_alias = {}  # nombre_real_lower -> alias
    items = []  # lista de (nombre_real_largo, alias)

    # Vehiculos primero (mas especificos con prefijos como Placa/Modelo)
    for alias, info in tabla.get("vehiculos", {}).items():
        real = info.get("nombre_real", "")
        if real:
            items.append((real, alias))
            name_to_alias[real.lower()] = alias

    # Personas, ordenadas por longitud descendente (mas largas primero)
    for alias, info in sorted(
        tabla.get("personas", {}).items(),
        key=lambda x: -len(x[1].get("nombre_real", ""))
    ):
        real = info.get("nombre_real", "")
        if real:
            items.append((real, alias))
            name_to_alias[real.lower()] = alias

    if not items:
        # No hay nada que reemplazar
        return re.compile(r"(?!)"), {}

    # Construir regex con alternation. Sort por longitud para que
    # "Juan Perez Mendoza" se pruebe antes que "Juan Perez".
    items.sort(key=lambda x: -len(x[0]))
    pattern_parts = []
    for nombre, alias in items:
        # Usar re.escape() para manejar caracteres especiales.
        # NO usamos lookbehind/lookahead porque con miles de alternativas
        # eso causa backtracking exponencial. En su lugar, usamos
        # \b que funciona bien en Python 3.13+ con Unicode.
        pattern_parts.append(r"\b" + re.escape(nombre) + r"\b")
    pattern = re.compile("|".join(pattern_parts), re.IGNORECASE)
    return pattern, name_to_alias


def seudonimizar(texto: str, tabla: Dict) -> str:
    """Reemplaza todas las menciones de personas/vehiculos con sus alias.

    Usa UN regex maestro pre-compilado (en lugar de N regex por persona).
    Antes: O(N*M) donde N=num_alias, M=tam_texto. Cada alias compilaba
    su propio re.compile y hacia un scan completo del texto.
    Ahora: O(M) con UN solo scan, gracias a la alternation con todos los alias.
    """
    pattern, name_to_alias = _build_master_pattern(tabla)
    if not name_to_alias:
        return texto
    # Sub con lookup O(1) por nombre matchado (lowercase)
    return pattern.sub(
        lambda m: name_to_alias.get(m.group(0).lower(), m.group(0)),
        texto
    )


def desudonimizar(texto: str, tabla: Dict) -> str:
    """Invierte el proceso: alias -> nombre real."""
    texto_orig = texto
    
    for alias, info in tabla["vehiculos"].items():
        texto_orig = texto_orig.replace(alias, info["nombre_real"])
    
    for alias, info in tabla["personas"].items():
        texto_orig = texto_orig.replace(alias, info["nombre_real"])
    
    return texto_orig


def guardar_tabla(tabla: Dict, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(tabla, f, ensure_ascii=False, indent=2)


def cargar_tabla(path: str) -> Dict:
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


# --- Test rápido ---
if __name__ == "__main__":
    texto_test = """
    El Cabo Juan Pérez Mendoza se entrevistó con María López García.
    El vehículo marca Ford F-150, color blanco, con placas VFK-1234,
    fue visto en la calle. También estaba el Teniente Carlos Ramírez.
    HUGO ALBERTO PARRA CHAIDEZ fue detenido.
    En otra ocasión, el Cabo Juan Pérez se entrevistó con REY DAVID GARCIA MORA.
    El vehículo placas ABC-9876 cruzó la frontera.
    """
    
    tabla = construir_tabla_alias({"test.txt": texto_test})
    print(json.dumps(tabla, ensure_ascii=False, indent=2))
    
    seudo = seudonimizar(texto_test, tabla)
    print("\n--- SEUDONIMIZADO ---")
    print(seudo)
    
    print("\n--- DESEUDONIMIZADO ---")
    print(desudonimizar(seudo, tabla))

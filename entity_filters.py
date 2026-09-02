"""
Filtros de calidad para entidades (personas y vehiculos) del motor forense.

El pseudonymizer detecta entidades por heuristica (NER + regex de placas), lo cual
produce falsos positivos: fragmentos de frases, ordinales, roles, colores de vehiculo,
marcas/modelos, hashes, etc.

Este modulo clasifica cada entidad como VALIDA o RUIDO, y normaliza nombres para
colapsar duplicados (OCR sucio, variantes con/sin tilde, saltos de linea).

Funciones publicas:
  - es_persona_valida(nombre, menciones) -> bool
  - es_vehiculo_valido(nombre, menciones) -> bool
  - normalizar_nombre(nombre) -> str  (para deduplicacion)
  - deduplicar_personas(tabla_personas) -> dict  (canonico -> [aliases])
"""
import re
import unicodedata
from difflib import SequenceMatcher


# ── Normalizacion ──────────────────────────────────────────────────────────────
def normalizar_nombre(nombre: str) -> str:
    """Quita acentos, mayusculas, espacios extra y saltos de linea para comparar."""
    if not nombre:
        return ""
    # quitar saltos de linea y espacios multiples
    s = re.sub(r'\s+', ' ', nombre).strip()
    # quitar acentos
    s = unicodedata.normalize('NFD', s)
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    # colapsar espacios
    s = re.sub(r'\s+', ' ', s).strip()
    return s.lower()


# ── Patrones de ruido en PERSONAS ──────────────────────────────────────────────
# Roles juridicos que el pseudonymizer suele capturar como si fueran personas
PERSONA_ROLES = {
    'juez', 'jueza', 'magistrado', 'magistrada', 'secretario', 'secretaria',
    'director', 'directora', 'subdirector', 'subdirectora',
    'coordinador', 'coordinadora', 'jefe', 'jefa',
    'fiscal', 'fiscales', 'agente', 'agentes',
    'ministerio', 'publico', 'publica', 'ministerio publico',
    'ministerio publico federal',
    'ministerio publico de la',
    'ministerio publico del',
    'ministerio publico estatal',
    'tribunal', 'juzgado', 'sala', 'corte',
    'oficial', 'capitan', 'teniente', 'cabo', 'sargento',
    'comandante', 'investigador', 'investigadores', 'perito', 'peritos',
    'policia', 'guardia', 'custodio',
    'procurador', 'procuradora',
    'defensor', 'defensora', 'asesor', 'asesora',
    'general adjunto', 'general de justicia', 'procuraduria',
    'cuarto especializado', 'colectiva', 'decanato',
    'actuario', 'actuaria',
}

# Substrings que indican fragmento o ruido
PERSONA_RUIDO_SUBSTRINGS = [
    'ministerio publico de la',  # fragmento cortado
    'ministerio publico federal',
    'ministerio publico del',
    'ministerio publico estatal',
    'ministerio\npublico',
    'del\nministerio',
    'de la republica',
    'de la fiscalia',
    'cuarto tribunal',
    'cuarto especializado',
    'colectiva no',
    'general adjunto',
    'general de justicia',
    'poder judicial',
    'poder ejecutivo',
    'poder legislativo',
    'estado de',
    'tribunal superior',
    'juzgado de',
    'juzgado primero',
    'juzgado segundo',
    'juzgado tercero',
    'juzgado cuarto',
    'juzgado quinto',
    'juzgado sexto',
    'juzgado septimo',
    'juzgado octavo',
    'juzgado noveno',
    'juzgado decimo',
]

# Ordinales
_PERSONA_ORDINAL = re.compile(
    r'^\s*(primer|segund|tercer|cuart|quint|sext|s[eé]ptim|octav|noven|d[eé]cim|pr[oó]xim|'
    r'vig[eé]sim|decimo\s+(primer|segund|tercer|cuart|quint))',
    re.IGNORECASE
)

# Numero solo
_PERSONA_NUMERO_SOLO = re.compile(r'^\s*\d+\s*$')

# "De la X", "Del X" como fragmento
_PERSONA_FRAGMENTO = re.compile(
    r'^\s*(de\s+(la|los|el|las)\s+|del\s+|la\s+|el\s+|los\s+|las\s+)[\w\s]{0,30}$',
    re.IGNORECASE
)


def es_persona_valida(nombre: str, menciones: int = 0) -> tuple:
    """Devuelve (es_valida, motivo_rechazo). motivo_rechazo=None si es valida."""
    if not nombre or not nombre.strip():
        return False, "nombre_vacio"

    n = nombre.strip()

    # saltos de linea en el medio (casi siempre OCR mal partido)
    if '\n' in n and len(n.replace('\n', '').strip()) < 15:
        return False, "salto_linea_basura"

    # menos de 2 palabras (probable fragmento)
    palabras = re.split(r'\s+', n.replace('\n', ' ').strip())
    if len(palabras) < 2:
        return False, "muy_corto"

    # numero solo
    if _PERSONA_NUMERO_SOLO.match(n):
        return False, "numero_solo"

    # empieza con ordinal
    if _PERSONA_ORDINAL.match(n):
        return False, "ordinal"

    # fragmento "De la X"
    if _PERSONA_FRAGMENTO.match(n) and len(palabras) <= 3:
        return False, "fragmento"

    # substrings problematicos (normalizamos para que tildes no estorben)
    n_lower = n.lower()
    n_norm = normalizar_nombre(n)
    for sub in PERSONA_RUIDO_SUBSTRINGS:
        sub_norm = normalizar_nombre(sub)
        if sub_norm in n_norm:
            return False, f"contiene:{sub[:20]}"
    if n_lower in PERSONA_ROLES:
        return False, "rol_exacto"

    # roles como palabra en CUALQUIER posicion (no solo al final)
    # normalizamos cada palabra para que 'pública' == 'publica'
    palabras_norm = [normalizar_nombre(p) for p in palabras]
    palabras_norm_set = set(palabras_norm)
    if palabras_norm_set & PERSONA_ROLES:
        # Si tiene 4+ palabras y empieza con nombres propios, probablemente es
        # persona + rol (ej "Fabiola Padilla Cruz Agente") -> valida
        # Si tiene 2-3 palabras y contiene rol, rechazar
        if len(palabras) <= 3:
            return False, "rol_en_palabras"

    # menciones muy bajas + nombre corto = probable basura OCR
    if menciones <= 2 and len(palabras) <= 2:
        return False, "menciones_bajas_corto"

    return True, None


# ── Patrones de ruido en VEHICULOS ─────────────────────────────────────────────
# Colores (no son vehiculos especificos)
VEHICULO_COLORES = {
    'negro', 'blanco', 'rojo', 'azul', 'verde', 'gris', 'plata', 'oro',
    'beige', 'amarillo', 'marron', 'rosa', 'morado', 'cafe', 'dorado',
    'vino', 'naranja', 'celeste',
}

# Marcas y modelos genericos (no son vehiculos especificos)
VEHICULO_MARCAS_MODELOS = {
    'chevrolet', 'dodge', 'ford', 'nissan', 'toyota', 'honda', 'jeep',
    'volkswagen', 'vw', 'bmw', 'audi', 'mercedes', 'renault', 'kia',
    'hyundai', 'mazda', 'suburban', 'tahoe', 'explorer', 'yaris',
    'hilux', 'tacoma', 'silverado', 'colorado', 'frontier', 'wrangler',
    'rav4', 'crv', 'equinox', 'tiida', 'civic', 'corolla', 'sentra',
    'versa', 'march', 'avenger', 'durango', 'ram', 'f150', 'f250',
    'f350', 'suv', 'pickup', 'camioneta', 'pick up',
}

# Hashes criptograficos / identificadores no-vehiculo
_HASH_PATTERN = re.compile(r'^[a-f0-9]{8,}$', re.IGNORECASE)  # hex estricto 8+ chars
_SHA_PATTERN = re.compile(r'^(sha|sha1|sha256|sha512|md5|hash)[\d_-]?', re.IGNORECASE)

# Palabras sueltas que no son vehiculos
VEHICULO_RUIDO_SUBSTRINGS = [
    'sedan', 'camioneta', 'pickup', 'pick up',
    'camion', 'tractor', 'motocicleta', 'motociclo',
    'tractocamion', 'autobus',
    'vagoneta', 'vagon',
    'buick', 'gmc',
    'automovil', 'vehiculo',
]


def es_vehiculo_valido(nombre: str, menciones: int = 0) -> tuple:
    """Devuelve (es_valido, motivo_rechazo)."""
    if not nombre or not nombre.strip():
        return False, "nombre_vacio"

    n = nombre.strip()
    n_lower = n.lower()
    n_norm = normalizar_nombre(n)

    # patrones que indican descripcion, no vehiculo especifico
    RUIDO_PATTERNS_VEH = [
        'sin placas', 'no se aprecia', 'no presenta',
        'completamente', 'marca ', 'camioneta de', 'vagoneta',
        'implicadas', 'no especific', 'se ignora',
        'calcinada', 'incinerada',
    ]
    for p in RUIDO_PATTERNS_VEH:
        if p in n_lower:
            return False, f"patron:{p[:15]}"

    # colores
    if n_norm in VEHICULO_COLORES:
        return False, "color"

    # marca o modelo exacto
    if n_norm in VEHICULO_MARCAS_MODELOS:
        return False, "marca_modelo"

    # hash criptografico
    if _HASH_PATTERN.match(n) or _SHA_PATTERN.match(n_lower):
        return False, "hash"

    # substrings ruido
    for sub in VEHICULO_RUIDO_SUBSTRINGS:
        if n_norm == sub:
            return False, f"tipo_generico:{sub}"

    # 1-4 chars sueltos sin numeros = basura OCR (ej "zoa", "zor", "de", "PICK")
    if len(n_norm) <= 4 and not re.search(r'\d', n):
        return False, "muy_corto_sin_numeros"
    # 1-3 chars sueltos
    if len(n_norm) <= 3 and not re.match(r'^[A-Z]{3}[-\s]?\d{3,4}$', n):
        return False, "muy_corto"

    # "de la X", "del X" como fragmento
    if re.match(r'^(de|del|la|el|los|las)\s', n_lower) and len(norm := n_norm.split()) <= 3:
        return False, "fragmento"

    # todo minusculas sin numeros = nombre comun no-placa
    # o todo MAYUSCULAS cortas (marcas/modelos como "BUICK", "GMC")
    if n.islower() and len(n) > 4 and not re.search(r'\d', n):
        # "pick up", "camioneta", "camion", etc
        if n_lower in VEHICULO_RUIDO_SUBSTRINGS or 'camion' in n_lower or 'pick' in n_lower or 'vagon' in n_lower:
            return False, "palabra_generica"
    if n.isupper() and len(n) <= 6 and not re.search(r'\d', n):
        # "BUICK", "GMC" - marca corta, no placa
        if n_lower in VEHICULO_RUIDO_SUBSTRINGS or n_norm in VEHICULO_MARCAS_MODELOS:
            return False, "marca_mayuscula_corta"

    return True, None


# ── Deduplicacion ──────────────────────────────────────────────────────────────
def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _similitud(a: str, b: str) -> float:
    """Ratio de similitud entre dos strings normalizados.
    Usa SequenceMatcher (ratio = 2*M / T, donde M=matches, T=total chars)."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _agrupar_fuzzy(nombres: list, umbral: float = 0.80) -> list:
    """Agrupa nombres por similitud usando union-find.
    Dos nombres se consideran el mismo si similitud >= umbral.
    Esto captura variantes OCR como SEDWICK/SEDWUICK, WOOLLEY/WOLLER, etc.
    """
    if not nombres:
        return []
    n = len(nombres)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(n):
        for j in range(i+1, n):
            sim = _similitud(nombres[i], nombres[j])
            if sim >= umbral:
                union(i, j)

    grupos = {}
    for i in range(n):
        r = find(i)
        grupos.setdefault(r, []).append(i)
    return list(grupos.values())


def deduplicar_personas(tabla_personas: dict, umbral: float = 0.80) -> dict:
    """Colapsa personas con nombres casi identicos (OCR, tildes, saltos linea,
    variantes de mayusculas, sufijos LeBaron/Le Baron, typos OCR como
    SEDWICK/SEDWUICK, WOOLLEY/WOLLER, etc).

    Estrategia de dos pasos:
      1) Agrupar por nombre normalizado exacto (colapsa tildes/mayusc/
)
      2) Para cada grupo, fuzzy-match entre grupos para colapsar variantes
         OCR (SEDWICK == SEDWUICK con similitud ~0.94)

    Devuelve dict {alias_canonico: {'aliases': [...], 'menciones_total': N,
                                     'nombre_canonico': str}}.
    """
    # Paso 1: agrupar por nombre normalizado
    grupos_exactos = {}
    for alias, info in tabla_personas.items():
        nombre = info.get('nombre_real', alias)
        norm = normalizar_nombre(nombre)
        if not norm:
            continue
        grupos_exactos.setdefault(norm, []).append((alias, info, nombre))

    # Paso 2: para cada grupo exacto, calcular representante y luego
    # fuzzy-match entre grupos para colapsar variantes OCR.
    representantes = []  # (norm_key, [(alias, info, nombre), ...])
    for norm, items in grupos_exactos.items():
        representantes.append((norm, items))

    # Fuzzy union-find entre los representatives (basado en norm keys)
    norm_keys = [r[0] for r in representantes]
    clusters_fuzzy = _agrupar_fuzzy(norm_keys, umbral=umbral)

    # Mapear cada cluster a sus items
    clusters_items = []
    for cluster_indices in clusters_fuzzy:
        merged_items = []
        for idx in cluster_indices:
            merged_items.extend(representantes[idx][1])
        clusters_items.append(merged_items)

    resultado = {}
    for items in clusters_items:
        if not items: continue
        if len(items) == 1:
            alias, info, nombre = items[0]
            resultado[alias] = {
                'alias_principal': alias,
                'aliases': [alias],
                'nombre_canonico': nombre,
                'nombre_normalizado': normalizar_nombre(nombre),
                'menciones_total': info.get('menciones', 0),
            }
        else:
            # hay varios aliases del mismo nombre canonico (fuzzy agrupados)
            # Prioridad para nombre canonico:
            #   1) el de mas menciones
            #   2) si empata, el mas largo
            #   3) preferir variante con "LeBaron" si existe (es el apellido legal)
            items.sort(key=lambda x: (-x[1].get('menciones', 0), -len(x[2])))
            alias_principal, info_principal, nombre_principal = items[0]
            menciones_total = sum(i[1].get('menciones', 0) for i in items)
            nombres_limpios = [i[2] for i in items if '\n' not in i[2]]
            # preferir variante con LeBaron si existe
            con_lebaron = [n for n in nombres_limpios if 'lebaron' in n.lower() or 'le baron' in n.lower()]
            if con_lebaron:
                # Entre las que tienen LeBaron, elegir la MAS COMPLETA
                # (la que tenga mas letras LeBaron correctas, no truncadas como LEBARO)
                nombre_canon = max(con_lebaron, key=lambda n: (
                    'le baron' in n.lower(),  # preferir "le baron" sobre "lebaron"
                    len(n),
                    n.count('LEBARON') + n.count('Le Baron') + n.count('LeBaron')
                ))
            elif nombres_limpios:
                nombre_canon = max(nombres_limpios, key=len)
            else:
                nombre_canon = nombre_principal
            # Si hay una variante LeBaron y otra sin LeBaron, preferir la completa
            if not any('le baron' in n.lower() or 'lebaron' in n.lower() for n in nombres_limpios):
                pass  # no hay variante LeBaron
            else:
                con_lebaron = [n for n in nombres_limpios if 'le baron' in n.lower() or 'lebaron' in n.lower()]
                if con_lebaron:
                    nombre_canon = max(con_lebaron, key=len)
            resultado[alias_principal] = {
                'alias_principal': alias_principal,
                'aliases': [i[0] for i in items],
                'nombre_canonico': nombre_canon,
                'nombre_normalizado': normalizar_nombre(nombre_canon),
                'menciones_total': menciones_total,
            }
    return resultado


def deduplicar_vehiculos(tabla_vehiculos: dict) -> dict:
    """Colapsa vehiculos duplicados por normalizacion (marcas con/sin tilde, etc)."""
    grupos = {}
    for alias, info in tabla_vehiculos.items():
        nombre = info.get('nombre_real', alias)
        norm = normalizar_nombre(nombre)
        grupos.setdefault(norm, []).append((alias, info, nombre))

    resultado = {}
    for norm, items in grupos.items():
        if len(items) == 1:
            alias, info, nombre = items[0]
            resultado[alias] = {
                'alias_principal': alias,
                'aliases': [alias],
                'nombre_canonico': nombre,
                'nombre_normalizado': norm,
                'menciones_total': info.get('menciones', 0),
            }
        else:
            items.sort(key=lambda x: -x[1].get('menciones', 0))
            alias_principal, info_principal, _ = items[0]
            menciones_total = sum(i[1].get('menciones', 0) for i in items)
            nombres_limpios = [i[2] for i in items if '\n' not in i[2]]
            nombre_canon = nombres_limpios[0] if nombres_limpios else info_principal.get('nombre_real', alias_principal)
            resultado[alias_principal] = {
                'alias_principal': alias_principal,
                'aliases': [i[0] for i in items],
                'nombre_canonico': nombre_canon,
                'nombre_normalizado': norm,
                'menciones_total': menciones_total,
            }
    return resultado


# ── Test rapido ────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    tests_persona = [
        ("David Zepeda", 560, True),
        ("Del\nMinisterio Público", 2, False),
        ("Décimo Primero", 2, False),
        ("Colectiva No", 16, False),
        ("General Adjunto", 16, False),
        ("Cuarto Especializado", 12, False),
        ("Pública Federal", 15, False),
        ("Fabiola Padilla Cruz", 105, True),
        ("Fabiola Padilla Cruz\nAgente", 11, True),  # 5 palabras, valida
        ("Manuel Angel Barrios\nMacario", 33, True),
        ("Antonio Pérez García", 260, True),
        ("Padilla Cruz Fabiola", 0, True),
        ("Maria", 1, False),  # muy corto
        ("Carla Bermudez", 38, True),
    ]
    print("=== TEST PERSONAS ===")
    for nombre, menciones, esperado in tests_persona:
        ok, motivo = es_persona_valida(nombre, menciones)
        status = "OK" if ok == esperado else "FALLO"
        print(f"  [{status}] '{nombre[:40]}' ({menciones}m) -> valida={ok} motivo={motivo} (esperado={esperado})")

    tests_vehiculo = [
        ("VXR-024", 705, True),
        ("SHA256", 677, False),
        ("AXL-309", 520, True),
        ("Chevrolet", 314, False),
        ("CHEVROLET", 110, False),
        ("negro", 15, False),
        ("oro", 1, False),
        ("pick up", 30, False),
        ("Suburban", 30, False),
        ("TOYOTA", 28, False),
        ("DAA1362", 10, True),  # placa real
        ("zoa", 2, False),
        ("Nissan", 95, False),
        ("AXL-1119", 60, True),
    ]
    print("\n=== TEST VEHICULOS ===")
    for nombre, menciones, esperado in tests_vehiculo:
        ok, motivo = es_vehiculo_valido(nombre, menciones)
        status = "OK" if ok == esperado else "FALLO"
        print(f"  [{status}] '{nombre[:30]}' ({menciones}m) -> valido={ok} motivo={motivo} (esperado={esperado})")

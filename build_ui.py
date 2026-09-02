"""
Generador de grafo relacional + visor HTML estatico.

Produce dos archivos en output/ui/:
  - graph_data.json  : nodos y aristas listos para vis.js
  - index.html       : visor autocontenido con busqueda, filtros y zoom

El grafo tiene 4 tipos de nodos:
  - PERSONA          (272 nombres reales del LLM + fallback a pseudonymizer)
  - VEHICULO         (20+ tipos detectados por LLM)
  - LUGAR            (11+ ubicaciones del expediente)
  - HALLAZGO         (los hallazgos unicos del reporte)

Aristas (4 tipos):
  - persona --rol:...--> persona       (35+ relaciones juridicas)
  - persona --victima--> hecho         (19+ relaciones victima-hecho)
  - persona --involucrado_en--> hallazgo
  - persona --co-ocurr xN--> persona   (pares en mismo chunk)
  - persona --en_lugar--> lugar
  - persona --vehiculo_visto--> vehiculo (testimonios que mencionan vehiculo)
"""
import os
import sys
import json
import re
from collections import defaultdict, Counter
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pseudonymizer import cargar_tabla, desudonimizar
from entity_filters import (
    es_persona_valida, es_vehiculo_valido,
    deduplicar_personas, deduplicar_vehiculos,
)
from config import OUTPUT_DIR, ALIAS_FILE

UI_DIR = os.path.join(OUTPUT_DIR, "ui")
GRAPH_JSON = os.path.join(UI_DIR, "graph_data.json")
INDEX_HTML = os.path.join(UI_DIR, "index.html")

# Umbrales para mantener el grafo navegable
MIN_MENCIONES_PERSONA = 1
MIN_MENCIONES_VEHICULO = 1
MIN_MENCIONES_LUGAR = 1
MAX_NODOS_PERSONA = 80       # ampliado: ahora priorizamos entidades con relaciones
MAX_NODOS_VEHICULO = 30
MAX_NODOS_LUGAR = 15
MAX_HALLAZGOS = 25


def _personas_de_campo(res, campo):
    """Extrae nombres de personas de un campo del JSON del LLM.
    Acepta tanto listas de dicts (con 'nombre') como listas de strings."""
    resultado = []
    field = res.get(campo)
    if not isinstance(field, list):
        return resultado
    for item in field:
        if isinstance(item, dict):
            nombre = item.get('nombre') or item.get('testigo') or ''
            if nombre:
                resultado.append(nombre)
        elif isinstance(item, str) and item:
            resultado.append(item)
    return resultado


def _vehiculos_de_campo(res, alias_map=None):
    """Extrae identificadores de vehiculos del campo 'vehiculos' del LLM.
    El campo tiene tipo, color, marca, placas. Si alias_map esta disponible,
    traduce alias VEHICULO_XXX -> nombre real (placa).
    """
    resultado = []
    alias_vehs = alias_map.get('vehiculos', {}) if alias_map else {}
    RUIDO_VEHICULO = {
        'SHA256', 'negro', 'blanco', 'oro',
        'Chevrolet', 'CHEVROLET', 'Nissan', 'Dodge', 'DODGE',
        'Ford', 'TOYOTA', 'Suburban', 'vagoneta', 'pick up',
        'Camioneta', 'Cami\u00f3n', 'Gamioneta',
        'sin placas', 'sin placas de circulaci\u00f3n',
        'sin placas, ni permisos', 'no presenta', 'no se aprecian',
        'implicadas', 'RAM SRT 10', 'F 150',
    }
    # Tambien rechazar cualquier cosa que contenga "sin placas" o "marca"
    RUIDO_PATTERNS = ['sin placas', 'marca ', 'completamente']

    def _es_ruido(nombre):
        n = nombre.lower()
        if nombre in RUIDO_VEHICULO:
            return True
        for p in RUIDO_PATTERNS:
            if p in n:
                return True
        return False
    for v in res.get('vehiculos', []) or []:
        if not isinstance(v, dict): continue
        tipo = v.get('tipo', '') or ''
        placas = v.get('placas', '') or ''
        # preferir placas si existen y son reales
        if placas and 'VEHICULO_' not in str(placas).upper() and len(str(placas)) > 3:
            resultado.append(placas)
        elif tipo and 'VEHICULO_' in str(tipo).upper():
            # extraer el alias
            match = re.search(r'VEHICULO_\d+', str(tipo), re.IGNORECASE)
            if match:
                alias_id = match.group(0).upper()
                # traducir al nombre real si esta en el alias_map
                nombre_real = alias_vehs.get(alias_id, {}).get('nombre_real')
                # Regla: si el nombre real es valido, usarlo
                # Si no, descartar completamente (NUNCA usar alias desnudo)
                if (nombre_real and not nombre_real.startswith('VEHICULO_')
                        and not _es_ruido(nombre_real)):
                    resultado.append(nombre_real)
                # else: descartar el vehiculo ruidoso (no agregar alias desnudo)
    return resultado


def _lugares_de_campo(res):
    """Extrae nombres de ubicaciones del campo 'ubicaciones'."""
    resultado = []
    for u in res.get('ubicaciones', []) or []:
        if not isinstance(u, dict): continue
        nombre = u.get('nombre', '') or ''
        if nombre:
            resultado.append(nombre)
    return resultado


def cargar_trazabilidad_y_hallazgos(tabla):
    """Carga resultados brutos y consolida.

    Fuentes de entidades (en orden de prioridad):
      1) 'partes'         - personas con cargo juridico (50 chunks)
      2) 'testigos'       - personas que declaran (31 chunks)
      3) 'victimas'       - victimas con relacion al hecho (21 chunks)
      4) 'declaraciones'  - testigo + info (23 chunks)
      5) 'participantes'  - personas con acciones (16 chunks)
      6) 'peritos'        - peritos tecnicos (22 chunks)
      7) 'vehiculos'      - vehiculos (66 chunks)
      8) 'ubicaciones'    - lugares (16 chunks)
      9) campos top (inc/ing/dev/trazabilidad) para hallazgos y trazabilidad juridica
    """
    with open(os.path.join(OUTPUT_DIR, "resultados_unificados.json"), encoding="utf-8") as f:
        items = json.load(f)

    # ── Entidades: nombre real -> info ─────────────────────────────────
    personas = defaultdict(lambda: {
        'menciones': 0,
        'fuentes': set(),
        'cargos': [],
        'relaciones_victima': [],
        'chunks': [],
        'archivos': [],
    })
    vehiculos = defaultdict(lambda: {
        'menciones': 0,
        'chunks': [],
        'archivos': [],
    })
    lugares = defaultdict(lambda: {
        'menciones': 0,
        'chunks': [],
        'archivos': [],
    })

    # ── Relaciones ─────────────────────────────────────────────────────
    # juridica[persona] = lista de (cargo, chunk)
    rel_juridica = defaultdict(list)
    # victima[persona] = lista de (relacion, chunk)
    rel_victima = defaultdict(list)
    # cooc[persona][persona] = count
    cooc = defaultdict(lambda: defaultdict(int))
    # persona-lugar
    persona_lugar = defaultdict(lambda: defaultdict(int))
    # persona-vehiculo (testimonio donde se mencionan juntos)
    persona_vehiculo = defaultdict(lambda: defaultdict(int))

    trazabilidad_por_alias = {}  # alias -> {rol, personas_rel, vehs_rel, chunks}
    hallazgos_unicos = []
    seen = set()

    for r in items:
        if not r.get("resultado"):
            continue
        res = r["resultado"]
        meta = r.get("chunk", {})
        chunk_id = (meta.get("archivo"), meta.get("chunk"))

        # ── 1) PARTES: personas con cargo ────────────────────────────────
        partes_personas_chunk = set()
        for p in res.get('partes', []) or []:
            if not isinstance(p, dict): continue
            nombre = p.get('nombre', '')
            cargo = p.get('cargo', '')
            if not nombre: continue
            # Si el nombre contiene un alias PERSONA_XXX embebido
            # (ej: "Agencia del PERSONA_1329"), traducirlo
            nombre_limpio = re.sub(r'PERSONA_\d+', lambda m: desudonimizar(m.group(0), tabla) or m.group(0), nombre)
            partes_personas_chunk.add(nombre_limpio)
            personas[nombre_limpio]['menciones'] += 1
            personas[nombre_limpio]['fuentes'].add('partes')
            personas[nombre_limpio]['chunks'].append(chunk_id)
            personas[nombre_limpio]['archivos'].append(meta.get('archivo'))
            if cargo:
                personas[nombre_limpio]['cargos'].append(cargo)
                rel_juridica[nombre_limpio].append((cargo, chunk_id))

        # ── 2) TESTIGOS ──────────────────────────────────────────────────
        for nombre in _personas_de_campo(res, 'testigos'):
            personas[nombre]['menciones'] += 1
            personas[nombre]['fuentes'].add('testigos')
            personas[nombre]['chunks'].append(chunk_id)
            personas[nombre]['archivos'].append(meta.get('archivo'))

        # ── 3) VICTIMAS: persona + relacion al hecho ─────────────────────
        for v in res.get('víctimas', []) or []:
            if not isinstance(v, dict): continue
            nombre = v.get('nombre', '')
            relacion = v.get('relacion', '')
            if not nombre: continue
            personas[nombre]['menciones'] += 1
            personas[nombre]['fuentes'].add('víctimas')
            personas[nombre]['chunks'].append(chunk_id)
            personas[nombre]['archivos'].append(meta.get('archivo'))
            if relacion:
                personas[nombre]['relaciones_victima'].append(relacion)
                rel_victima[nombre].append((relacion, chunk_id))

        # ── 4) DECLARACIONES: testigo ────────────────────────────────────
        for d in res.get('declaraciones', []) or []:
            if not isinstance(d, dict): continue
            t = d.get('testigo', '') or ''
            if t:
                personas[t]['menciones'] += 1
                personas[t]['fuentes'].add('declaraciones')
                personas[t]['chunks'].append(chunk_id)
                personas[t]['archivos'].append(meta.get('archivo'))

        # ── 5) PARTICIPANTES ─────────────────────────────────────────────
        for p in res.get('participantes', []) or []:
            if not isinstance(p, dict): continue
            n = p.get('nombre', '') or ''
            if n:
                personas[n]['menciones'] += 1
                personas[n]['fuentes'].add('participantes')
                personas[n]['chunks'].append(chunk_id)
                personas[n]['archivos'].append(meta.get('archivo'))

        # ── 6) PERITOS (puede ser list o int) ────────────────────────────
        peritos_field = res.get('peritos')
        if isinstance(peritos_field, list):
            for p in peritos_field:
                if isinstance(p, str) and p:
                    personas[p]['menciones'] += 1
                    personas[p]['fuentes'].add('peritos')
                    personas[p]['chunks'].append(chunk_id)
                    personas[p]['archivos'].append(meta.get('archivo'))

        # ── 7) VEHICULOS ─────────────────────────────────────────────────
        vehs_chunk = set()
        for vid in _vehiculos_de_campo(res, tabla):
            vehs_chunk.add(vid)
            vehiculos[vid]['menciones'] += 1
            vehiculos[vid]['chunks'].append(chunk_id)
            vehiculos[vid]['archivos'].append(meta.get('archivo'))
        # Tambien incluir vehiculos del alias_map (placas con muchas menciones en corpus)
        alias_vehs = tabla.get('vehiculos', {}) if tabla else {}
        RUIDO_VEH_NOMBRES = {
            'SHA256', 'negro', 'blanco', 'oro',
            'Chevrolet', 'CHEVROLET', 'Nissan', 'Dodge', 'DODGE',
            'Ford', 'TOYOTA', 'Suburban', 'vagoneta', 'pick up',
            'Camioneta', 'Cami\u00f3n', 'Gamioneta',
        }
        RUIDO_VEH_PATTERNS = ['sin placas', 'marca ', 'completamente', 'calcinada']
        def _alias_es_ruido(n):
            if not n: return True
            if n in RUIDO_VEH_NOMBRES: return True
            nlow = n.lower()
            for p in RUIDO_VEH_PATTERNS:
                if p in nlow: return True
            return False
        # NOTA: este bloque corre POR CADA chunk que tiene vehiculos del LLM.
        # Si sumamos menc_alias cada vez, vamos a inflar mucho los counts.
        # Solo agregamos el vehiculo la primera vez (cuando no existe en vehiculos).
        vehiculos_ya_agregados = set()
        for vid_alia, info_alias in alias_vehs.items():
            nr = info_alias.get('nombre_real', vid_alia)
            menc_alias = info_alias.get('menciones', 0)
            # Solo placas reales con >50 menciones (vehiculos importantes del caso)
            if menc_alias < 50: continue
            if not nr or nr.startswith('VEHICULO_'): continue
            if _alias_es_ruido(nr): continue
            # Agregar como vehiculo del corpus (solo si no existe)
            if nr in vehiculos:
                continue  # ya fue agregado por el LLM o en una pasada previa
            if nr in vehiculos_ya_agregados:
                continue
            vehiculos[nr] = {'menciones': menc_alias, 'chunks': [], 'fuente_alias_map': True}
            vehiculos[nr]['alias_origen'] = vid_alia
            vehiculos_ya_agregados.add(nr)

        # ── 8) LUGARES ───────────────────────────────────────────────────
        lugares_chunk = set()
        for l in _lugares_de_campo(res):
            lugares_chunk.add(l)
            lugares[l]['menciones'] += 1
            lugares[l]['chunks'].append(chunk_id)
            lugares[l]['archivos'].append(meta.get('archivo'))

        # ── 9) CAMPOS TOP: trazabilidad + hallazgos ──────────────────────
        entidades_aqui = set()
        for inc in res.get("inconsistencias", []):
            for p in inc.get("personas_involucradas", []) or []:
                entidades_aqui.add(p)
        for inc in res.get("incongruencias", []):
            for p in inc.get("personas_involucradas", []) or []:
                entidades_aqui.add(p)
        for d in res.get("declaracion_vs_evidencia", []):
            if d.get("persona"):
                entidades_aqui.add(d["persona"])

        # trazabilidad (alias PERSONA_/VEHICULO_ con rol juridico)
        for alias, info in res.get("trazabilidad", {}).items():
            entidades_aqui.add(alias)
            ent = trazabilidad_por_alias.setdefault(alias, {
                "rol": info.get("rol_probable", "desconocido"),
                "personas_rel": set(),
                "vehs_rel": set(),
                "chunks": set(),
            })
            for pr in info.get("personas_relacionadas", []) or []:
                entidades_aqui.add(pr)
                ent["personas_rel"].add(pr)
            for vr in info.get("vehiculos_relacionados", []) or []:
                entidades_aqui.add(vr)
                ent["vehs_rel"].add(vr)
            ent["chunks"].add(chunk_id)

            # Resolver alias a nombre real y agregar a personas/vehiculos
            if alias.startswith("PERSONA_"):
                nombre_real = desudonimizar(alias, tabla)
                if nombre_real and nombre_real != alias:
                    personas[nombre_real]['menciones'] += 1
                    personas[nombre_real]['fuentes'].add('trazabilidad')
                    personas[nombre_real]['chunks'].append(chunk_id)
                    personas[nombre_real]['archivos'].append(meta.get('archivo'))
                    if info.get('rol_probable'):
                        personas[nombre_real]['cargos'].append(info['rol_probable'])
            elif alias.startswith("VEHICULO_"):
                nombre_real = desudonimizar(alias, tabla)
                if nombre_real and nombre_real != alias:
                    vehiculos[nombre_real]['menciones'] += 1
                    vehiculos[nombre_real]['chunks'].append(chunk_id)
                    vehiculos[nombre_real]['archivos'].append(meta.get('archivo'))

        entidades_personas_alias = {e for e in entidades_aqui if e.startswith("PERSONA_")}
        entidades_vehs_alias    = {e for e in entidades_aqui if e.startswith("VEHICULO_")}

        # ── Co-ocurrencia: personas nombradas en mismo chunk ────────────
        # Combinar nombres reales + aliases resueltos
        personas_chunk = set(partes_personas_chunk)
        for fuente in ('testigos', 'víctimas', 'declaraciones', 'participantes'):
            for n in _personas_de_campo(res, fuente):
                personas_chunk.add(n)
        peritos_field = res.get('peritos')
        if isinstance(peritos_field, list):
            for p in peritos_field:
                if isinstance(p, str) and p:
                    personas_chunk.add(p)
        # agregar aliases resueltos a sus nombres reales
        for alias in entidades_personas_alias:
            nombre_real = desudonimizar(alias, tabla)
            if nombre_real and nombre_real != alias:
                personas_chunk.add(nombre_real)

        personas_chunk_list = sorted(personas_chunk)
        for i, p1 in enumerate(personas_chunk_list):
            for p2 in personas_chunk_list[i+1:]:
                cooc[p1][p2] += 1
                cooc[p2][p1] += 1

        # persona-lugar (todos los mencionados en el chunk)
        for persona in personas_chunk_list:
            for lugar in lugares_chunk:
                persona_lugar[persona][lugar] += 1

        # persona-vehiculo (todos los mencionados en el chunk)
        for persona in personas_chunk_list:
            for veh in vehs_chunk:
                persona_vehiculo[persona][veh] += 1

        # ── hallazgos unicos ─────────────────────────────────────────────
        def es_debil(texto):
            if not texto:
                return True
            t = texto.lower()
            return any(p in t for p in [
                "no se proporciona", "no hay evidencia adicional",
                "no se menciona explicitamente", "no se proporciona informacion",
                "no queda claro", "informacion insuficiente",
            ])

        for inc in res.get("inconsistencias", []):
            desc = inc.get("descripcion", "")
            if es_debil(desc):
                continue
            key = desc[:80].lower()
            if key in seen:
                continue
            seen.add(key)
            hallazgos_unicos.append({
                "id": f"INC-{len(hallazgos_unicos)+1:03d}",
                "tipo": "inconsistencia",
                "descripcion": desc,
                "severidad": inc.get("severidad", "media"),
                "archivo": meta.get("archivo"),
                "chunk": meta.get("chunk"),
                "personas": inc.get("personas_involucradas", []) or [],
                "vehiculos": [],
            })
            if len(hallazgos_unicos) >= MAX_HALLAZGOS:
                break

        for inc in res.get("incongruencias", []):
            desc = inc.get("descripcion", "")
            if es_debil(desc):
                continue
            key = desc[:80].lower()
            if key in seen:
                continue
            seen.add(key)
            hallazgos_unicos.append({
                "id": f"ING-{len(hallazgos_unicos)+1:03d}",
                "tipo": "incongruencia",
                "descripcion": desc,
                "severidad": inc.get("severidad", "media"),
                "archivo": meta.get("archivo"),
                "chunk": meta.get("chunk"),
                "personas": inc.get("personas_involucradas", []) or [],
                "vehiculos": [],
            })
            if len(hallazgos_unicos) >= MAX_HALLAZGOS:
                break

        for d in res.get("declaracion_vs_evidencia", []):
            desc = d.get("descripcion", "")
            if es_debil(desc):
                continue
            key = desc[:80].lower()
            if key in seen:
                continue
            seen.add(key)
            hallazgos_unicos.append({
                "id": f"DEV-{len(hallazgos_unicos)+1:03d}",
                "tipo": "discrepancia",
                "descripcion": desc,
                "severidad": d.get("severidad", "media"),
                "archivo": meta.get("archivo"),
                "chunk": meta.get("chunk"),
                "personas": [d["persona"]] if d.get("persona") else [],
                "vehiculos": [],
            })
            if len(hallazgos_unicos) >= MAX_HALLAZGOS:
                break

        if len(hallazgos_unicos) >= MAX_HALLAZGOS:
            break

    return (personas, vehiculos, lugares, rel_juridica, rel_victima,
            cooc, persona_lugar, persona_vehiculo, hallazgos_unicos,
            trazabilidad_por_alias)


def construir_grafo(tabla):
    """Construye el grafo con todas las entidades y relaciones del LLM.

    Flujo:
      1) Recibe entidades de cargar_trazabilidad_y_hallazgos
      2) Filtra ruido (fragmentos, roles, ordinales, basura OCR)
      3) Deduplica variantes (OCR, tildes, \\n)
      4) Calcula score combinado: rol juridico > victima > coocurrencia > menciones
      5) Selecciona top-N
      6) Construye nodos y aristas
    """
    (personas_llm, vehiculos_llm, lugares_llm, rel_juridica, rel_victima,
     cooc, persona_lugar, persona_vehiculo, hallazgos, traz) = cargar_trazabilidad_y_hallazgos(tabla)

    # ── 1) FILTRAR personas: quitar ruido ─────────────────────────────────
    print(f"  LLM detecto: {len(personas_llm)} personas, {len(vehiculos_llm)} vehiculos, {len(lugares_llm)} lugares")

    personas_limpias = {}
    rechazados_persona = []
    for nombre, info in personas_llm.items():
        # validar el nombre (no fragmento, no ordinal, no rol)
        ok, motivo = es_persona_valida(nombre, info['menciones'])
        if ok:
            personas_limpias[nombre] = info
        else:
            rechazados_persona.append((nombre, motivo))

    veh_limpios = {}
    rechazados_veh = []
    for nombre, info in vehiculos_llm.items():
        ok, motivo = es_vehiculo_valido(nombre, info['menciones'])
        if ok:
            veh_limpios[nombre] = info
        else:
            rechazados_veh.append((nombre, motivo))

    lugares_limpios = {}
    for nombre, info in lugares_llm.items():
        # lugares: rechazar si es muy corto o solo tiene 1 mencion
        if info['menciones'] >= MIN_MENCIONES_LUGAR and len(nombre) >= 5:
            lugares_limpios[nombre] = info

    print(f"  Filtrado: {len(personas_limpias)}/{len(personas_llm)} personas, "
          f"{len(veh_limpios)}/{len(vehiculos_llm)} vehiculos, "
          f"{len(lugares_limpios)}/{len(lugares_llm)} lugares")

    # ── 2) DEDUPLICAR: colapsar variantes (OCR, tildes, mayusculas) ────────
    # Como las personas del LLM vienen con nombre real (no alias), usamos
    # deduplicar_personas con formato de alias_map
    tabla_personas_alias = {}
    for nombre, info in personas_limpias.items():
        # crear pseudo-alias: nombre es tanto alias como nombre_real
        tabla_personas_alias[nombre] = {
            'nombre_real': nombre,
            'menciones': info['menciones'],
            'fuentes': info['fuentes'],
        }
    personas_dedup = deduplicar_personas(tabla_personas_alias)

    tabla_veh_alias = {}
    for nombre, info in veh_limpios.items():
        tabla_veh_alias[nombre] = {
            'nombre_real': nombre,
            'menciones': info['menciones'],
        }
    veh_dedup = deduplicar_vehiculos(tabla_veh_alias)

    print(f"  Dedup: {len(personas_dedup)} personas unicas, {len(veh_dedup)} vehiculos unicos")

    # mapa canon -> aliases para redirigir cooc/relaciones
    canon_to_aliases_persona = {}
    for canon, info in personas_dedup.items():
        canon_to_aliases_persona[canon] = set(info['aliases'])

    # ── 3) Redirigir cooc/relaciones al canon ─────────────────────────────
    def canon_persona(nombre):
        """Encuentra el alias canonico de un nombre."""
        if nombre in personas_dedup:
            return nombre
        for canon, aliases in canon_to_aliases_persona.items():
            if nombre in aliases:
                return canon
        return None  # nombre fue filtrado como ruido

    cooc_canon = Counter()
    for p1, otros in cooc.items():
        cp1 = canon_persona(p1)
        if cp1 is None: continue
        for p2, count in otros.items():
            cp2 = canon_persona(p2)
            if cp2 is None or cp1 == cp2: continue
            key = tuple(sorted([cp1, cp2]))
            cooc_canon[key] += count

    rel_juridica_canon = defaultdict(list)
    for nombre, lst in rel_juridica.items():
        cn = canon_persona(nombre)
        if cn: rel_juridica_canon[cn].extend(lst)

    rel_victima_canon = defaultdict(list)
    for nombre, lst in rel_victima.items():
        cn = canon_persona(nombre)
        if cn: rel_victima_canon[cn].extend(lst)

    # ── 4) Scoring: priorizar entidades CON relaciones ────────────────────
    score_persona = Counter()
    # boost por tener cargo juridico identificado
    for nombre, cargos in rel_juridica_canon.items():
        score_persona[nombre] += 50 * len(cargos)  # muy importante
    # boost por ser victima
    for nombre in rel_victima_canon:
        score_persona[nombre] += 30
    # boost por coocurrencia (familiar LeBaron = red densa)
    for (cp1, cp2), n in cooc_canon.items():
        score_persona[cp1] += min(n * 3, 20)
        score_persona[cp2] += min(n * 3, 20)
    # boost por aparecer en hallazgos consolidados
    for h in hallazgos:
        for p_alias in h.get('personas', []):
            cn = canon_persona(p_alias) if not p_alias.startswith('PERSONA_') else canon_persona(desudonimizar(p_alias, tabla))
            if cn:
                score_persona[cn] += 25
    # base: menciones del LLM (escala logaritmica para que no domine)
    for canon, info in personas_dedup.items():
        score_persona[canon] += info['menciones_total']

    # top-N
    personas_top = [a for a, _ in score_persona.most_common(MAX_NODOS_PERSONA * 2)
                    if a in personas_dedup][:MAX_NODOS_PERSONA]
    personas_set = set(personas_top)

    score_vehiculo = Counter()
    for nombre, info in veh_dedup.items():
        score_vehiculo[nombre] += info['menciones_total']
    vehiculos_top = [a for a, _ in score_vehiculo.most_common(MAX_NODOS_VEHICULO * 2)
                     if a in veh_dedup][:MAX_NODOS_VEHICULO]
    vehiculos_set = set(vehiculos_top)

    lugares_top = list(lugares_limpios.keys())[:MAX_NODOS_LUGAR]
    lugares_set = set(lugares_top)

    # ── 5) Construir nodos ────────────────────────────────────────────────
    nodes = []
    edges = []

    # Helper: id estable para un nombre canonico
    def pid(nombre):
        return re.sub(r'[^a-zA-Z0-9_]', '_', nombre)[:80]

    # Personas
    for canon in personas_top:
        dedup_info = personas_dedup[canon]
        # buscar info LLM original (con fuentes y cargos)
        fuentes = []
        cargos = []
        victimas_rel = []
        archivos_persona = []
        chunks_persona = []
        for alias in dedup_info['aliases']:
            if alias in personas_limpias:
                pi = personas_limpias[alias]
                fuentes.extend(pi.get('fuentes', set()))
                cargos.extend(pi.get('cargos', []))
                victimas_rel.extend(pi.get('relaciones_victima', []))
                archivos_persona.extend([a for a in pi.get('archivos', []) if a])
                chunks_persona.extend([tuple(c) if isinstance(c, list) else c for c in pi.get('chunks', [])])

        title_lines = [f"{canon}", f"{dedup_info['menciones_total']} menciones"]
        if cargos:
            cargo_principal = cargos[0][:60]
            title_lines.append(f"Cargo: {cargo_principal}")
        if victimas_rel:
            title_lines.append(f"Víctima: {victimas_rel[0]}")
        if fuentes:
            title_lines.append(f"Fuentes: {', '.join(sorted(set(fuentes)))[:50]}")
        if len(dedup_info['aliases']) > 1:
            title_lines.append(f"Aliases: {', '.join(dedup_info['aliases'][:5])}")

        archivos_unicos = sorted(set(archivos_persona))[:5]
        if archivos_unicos:
            title_lines.append(f"Expediente(s): {', '.join(archivos_unicos)}")

        nodes.append({
            "id": canon,
            "label": canon[:38],
            "group": "persona",
            "title": "\n".join(title_lines),
            "value": max(dedup_info['menciones_total'], 1),
            "menciones": dedup_info['menciones_total'],
            "aliases": dedup_info['aliases'],
            "cargos": list(set(cargos)),
            "relaciones_victima": list(set(victimas_rel)),
            "fuentes": sorted(set(fuentes)),
            "archivos": sorted(set(archivos_persona)),
            "chunks": [list(c) if isinstance(c, tuple) else c for c in sorted(set(chunks_persona))],
        })

    # Vehiculos
    for canon in vehiculos_top:
        dedup_info = veh_dedup[canon]
        archivos_veh = []
        chunks_veh = []
        for alias in dedup_info['aliases']:
            if alias in veh_limpios:
                vi = veh_limpios[alias]
                archivos_veh.extend([a for a in vi.get('archivos', []) if a])
                chunks_veh.extend([tuple(c) if isinstance(c, list) else c for c in vi.get('chunks', [])])
        archivos_veh_u = sorted(set(archivos_veh))[:5]
        title_lines = [f"{canon}", f"{dedup_info['menciones_total']} menciones",
                         f"Aliases: {', '.join(dedup_info['aliases'][:5])}"]
        if archivos_veh_u:
            title_lines.append(f"Expediente(s): {', '.join(archivos_veh_u)}")
        nodes.append({
            "id": canon,
            "label": canon[:30],
            "group": "vehiculo",
            "title": "\n".join(title_lines),
            "value": max(dedup_info['menciones_total'], 1),
            "menciones": dedup_info['menciones_total'],
            "aliases": dedup_info['aliases'],
            "archivos": sorted(set(archivos_veh)),
            "chunks": [list(c) if isinstance(c, tuple) else c for c in sorted(set(chunks_veh))],
        })

    # Lugares
    for lugar in lugares_top:
        info = lugares_limpios[lugar]
        archivos_lugar = sorted(set([a for a in info.get('archivos', []) if a]))[:5]
        chunks_lugar = [list(c) if isinstance(c, tuple) else c for c in sorted(set([tuple(c) if isinstance(c, list) else c for c in info.get('chunks', [])]))]
        title_lines = [f"{lugar}", f"{info['menciones']} menciones como ubicación"]
        if archivos_lugar:
            title_lines.append(f"Expediente(s): {', '.join(archivos_lugar)}")
        nodes.append({
            "id": f"LUGAR_{pid(lugar)}",
            "label": lugar[:35],
            "group": "lugar",
            "title": "\n".join(title_lines),
            "value": max(info['menciones'], 1),
            "menciones": info['menciones'],
            "nombre_real": lugar,
            "archivos": archivos_lugar,
            "chunks": chunks_lugar,
        })

    # ── 6) Construir aristas ──────────────────────────────────────────────
    seen_edges = set()
    def add_edge(f, t, lbl, color, width=1):
        if (f, t) in seen_edges or (t, f) in seen_edges:
            return
        seen_edges.add((f, t))
        edges.append({"from": f, "to": t, "label": lbl, "color": color, "width": width})

    # 6a) Hallazgos -> persona
    for h in hallazgos:
        nodes.append({
            "id": h["id"],
            "label": h["id"],
            "group": "hallazgo",
            "title": f"{h['id']} ({h['tipo']}, sev={h['severidad']})\n"
                     f"{h['descripcion'][:200]}\n"
                     f"{h['archivo']}, chunk {h['chunk']}",
            "value": 10,
            "tipo": h["tipo"],
            "descripcion": h["descripcion"],
            "severidad": h["severidad"],
            "archivo": h["archivo"],
            "chunk": h["chunk"],
        })
        for p_alias in h.get("personas", []):
            # resolver alias si es PERSONA_XXX
            cn = canon_persona(p_alias)
            if cn is None and p_alias.startswith('PERSONA_'):
                cn = canon_persona(desudonimizar(p_alias, tabla))
            if cn and cn in personas_set:
                add_edge(cn, h["id"], "involucrado", "#3b82f6")

    # 6b) Rol juridico: persona -> persona (acorde a rel_juridica_canon)
    # NOTA: rel_juridica tiene (cargo, chunk) por persona.
    # Para crear aristas entre personas juridicas, miramos quien aparece
    # en el mismo chunk con un cargo distinto (acusado vs defensor, etc).
    # Implementacion: pares de personas en mismo chunk de rel_juridica.
    pares_juridicos_por_chunk = defaultdict(set)
    for nombre, lst in rel_juridica_canon.items():
        for cargo, chunk in lst:
            if chunk:
                pares_juridicos_por_chunk[chunk].add(nombre)

    for chunk, personas_en_chunk in pares_juridicos_por_chunk.items():
        personas_en_chunk = [p for p in personas_en_chunk if p in personas_set]
        for i, p1 in enumerate(personas_en_chunk):
            for p2 in personas_en_chunk[i+1:]:
                # etiqueta corta con cargo de p1
                cargos_p1 = [c[:25] for c, _ in rel_juridica_canon[p1] if c]
                lbl = f"rol: {cargos_p1[0]}" if cargos_p1 else "proceso"
                add_edge(p1, p2, lbl, "#a78bfa", 2)

    # 6c) Victima -> hecho (etiqueta tipo "victima directa")
    for nombre, lst in rel_victima_canon.items():
        if nombre not in personas_set: continue
        for relacion, chunk in lst:
            # crear nodo "hecho" si no existe
            chunk_str = f"HECHO_{pid(str(chunk))}"
            if not any(n['id'] == chunk_str for n in nodes):
                nodes.append({
                    "id": chunk_str,
                    "label": f"Hecho {chunk[0][:25] if chunk else '?'}",
                    "group": "hecho",
                    "title": f"Hecho del expediente\nArchivo: {chunk[0] if chunk else '?'}Chunk: {chunk[1] if chunk else '?'}",
                    "value": 5,
                })
            add_edge(nombre, chunk_str, f"víctima: {relacion[:20]}", "#ef4444")

    # 6d) Co-ocurrencia: pares en mismo chunk
    # Filtrar por >=4 menciones para no saturar visualmente.
    # A 80 personas, mas de 367 aristas vuelven el grafo ilegible.
    # Con >=4 mantenemos las relaciones fuertes (familia LeBaron, fiscales)
    # y eliminamos las parejas que solo coinciden una vez.
    for (cp1, cp2), count in cooc_canon.items():
        if cp1 not in personas_set or cp2 not in personas_set: continue
        if count < 4: continue
        add_edge(cp1, cp2, f"co-ocurr x{count}", "#94a3b8", min(count + 1, 4))

    # 6e) Persona -> lugar
    # Mismo chunk: aristas solidas. Como solo 6 chunks los tienen juntos,
    # agregamos tambien aristas debiles "mismo expediente" para densidad visual.
    aristas_lugar = 0
    aristas_lugar_exp = 0
    lugares_por_expediente = defaultdict(set)
    for lugar_nombre in lugares_llm:
        for info in lugares_llm[lugar_nombre]['chunks']:
            archivo = info[0] if info else None
            if archivo:
                lugares_por_expediente[lugar_nombre].add(archivo)

    for persona, lugares in persona_lugar.items():
        cn = canon_persona(persona)
        if cn is None or cn not in personas_set: continue
        for lugar, count in lugares.items():
            if lugar not in lugares_set or count < 1: continue
            add_edge(cn, f"LUGAR_{pid(lugar)}", f"presente x{count}", "#22c55e")
            aristas_lugar += 1

    # aristas debiles: persona juridica -> lugar del mismo expediente
    # Solo para personas CON rol juridico (acusado, defensor, etc)
    for cn in personas_set:
        # obtener expedientes donde esta la persona (de chunks LLM)
        expedientes_p = set()
        if cn in personas_dedup:
            for alias in personas_dedup[cn]['aliases']:
                if alias in personas_limpias:
                    for info_chunk in personas_limpias[alias]['chunks']:
                        if info_chunk and info_chunk[0]:
                            expedientes_p.add(info_chunk[0])
        # conectar con lugares de esos expedientes
        for lugar_nombre in lugares_top:
            if lugar_nombre in lugares_por_expediente:
                exps_lugar = lugares_por_expediente[lugar_nombre]
                if exps_lugar & expedientes_p:
                    add_edge(cn, f"LUGAR_{pid(lugar_nombre)}", "exp.comun", "#16a34a", 1)
                    aristas_lugar_exp += 1
                    if aristas_lugar_exp > 200:  # limite de seguridad
                        break
        if aristas_lugar_exp > 200:
            break

    print(f"  Aristas persona-lugar: {aristas_lugar} fuertes + {aristas_lugar_exp} expediente-comun")

    # 6f) Persona -> vehiculo
    # Mismo chunk: aristas solidas. Para vehiculos del LLM (no en alias_map),
    # conectamos con personas juridicas del mismo expediente.
    aristas_veh = 0
    aristas_veh_exp = 0
    vehs_por_expediente = defaultdict(set)
    for veh_nombre in vehiculos_llm:
        for info in vehiculos_llm[veh_nombre]['chunks']:
            archivo = info[0] if info else None
            if archivo:
                vehs_por_expediente[veh_nombre].add(archivo)

    # vehiculos del alias_map (VXR-024, AXL-309) NO se asocian con personas
    # directamente porque el corpus general no tiene esa estructura.
    # Solo los vehiculos del LLM (tipo Camioneta, Camion, etc.) se asocian.
    vehs_llm_set = set(vehiculos_llm.keys())

    for persona, vehs in persona_vehiculo.items():
        cn = canon_persona(persona)
        if cn is None or cn not in personas_set: continue
        for veh, count in vehs.items():
            if veh not in vehiculos_set or count < 1: continue
            add_edge(cn, veh, f"menciona x{count}", "#f59e0b")
            aristas_veh += 1

    # aristas debiles por expediente compartido
    for cn in personas_set:
        expedientes_p = set()
        if cn in personas_dedup:
            for alias in personas_dedup[cn]['aliases']:
                if alias in personas_limpias:
                    for info_chunk in personas_limpias[alias]['chunks']:
                        if info_chunk and info_chunk[0]:
                            expedientes_p.add(info_chunk[0])
        for veh_nombre in vehiculos_top:
            if veh_nombre in vehs_por_expediente:
                exps_veh = vehs_por_expediente[veh_nombre]
                if exps_veh & expedientes_p:
                    add_edge(cn, veh_nombre, "exp.comun", "#fb923c", 1)
                    aristas_veh_exp += 1
                    if aristas_veh_exp > 200:
                        break
        if aristas_veh_exp > 200:
            break

    print(f"  Aristas persona-vehiculo: {aristas_veh} fuertes + {aristas_veh_exp} expediente-comun")

    # ── 6.5) Asignar coordenadas X,Y fijas por region ──────────────────
    # Layout manual en 5 regiones:
    #   Region A (x: -1200 a -700, y: -1000 a +1000) -> hallazgos en filas
    #   Region B (x: -500 a +200, y: -700 a +700) -> personas juridicas (acusados/defensores/fiscales)
    #   Region C (x: +400 a +1100, y: -1100 a -300) -> familia LeBaron (juntos)
    #   Region D (x: +400 a +1100, y: -100 a +700) -> otros testigos/peritos
    #   Region E (x: -400 a +200, y: +900 a +1900) -> vehiculos en columnas
    #   Region F (x: +1400 a +2100, y: -200 a +500) -> lugares

    import math

    personas_con_cargo = set()
    victimas_set = set()
    lebaron_set = set()  # personas que son parte de la familia LeBaron
    for canon in personas_dedup:
        for alias in personas_dedup[canon]['aliases']:
            if alias in personas_limpias:
                pi = personas_limpias[alias]
                if pi.get('cargos'):
                    personas_con_cargo.add(canon)
                if pi.get('relaciones_victima'):
                    victimas_set.add(canon)
                # detectar LeBaron por apellido
                lc = canon.lower()
                if any(s in lc for s in ('langford', 'lebaron', 'le baron', 'miller', 'langford ray', 'tuckwer', 'tucker')):
                    lebaron_set.add(canon)
                break

    # Region A: hallazgos en grilla (4 columnas x N filas)
    hallazgos_nodos = [n for n in nodes if n['group'] == 'hallazgo']
    cols_h = 4
    for i, n in enumerate(hallazgos_nodos):
        col = i % cols_h
        row = i // cols_h
        n['x'] = -1200 + col * 140
        n['y'] = -900 + row * 130
        # fijar posicion sin physics
        n['fixed'] = True

    # Region B: personas juridicas (con cargo) en 2 columnas
    juridicas_nodos = [n for n in nodes if n['group'] == 'persona' and n['id'] in personas_con_cargo and n['id'] not in lebaron_set]
    cols_b = 2
    for i, n in enumerate(juridicas_nodos):
        col = i % cols_b
        row = i // cols_b
        n['x'] = -500 + col * 200
        n['y'] = -600 + row * 120
        n['fixed'] = True

    # Region C: familia LeBaron arriba a la derecha
    lebaron_nodos = [n for n in nodes if n['group'] == 'persona' and n['id'] in lebaron_set]
    # ordenar por menciones para que los mas importantes vayan primero
    lebaron_nodos.sort(key=lambda n: -n.get('menciones', 0))
    cols_c = 6
    for i, n in enumerate(lebaron_nodos):
        col = i % cols_c
        row = i // cols_c
        n['x'] = 500 + col * 140
        n['y'] = -1200 + row * 110
        n['fixed'] = True

    # Region D: otros testigos/peritos
    otros_personas = [n for n in nodes if n['group'] == 'persona' and n['id'] not in personas_con_cargo and n['id'] not in lebaron_set]
    cols_d = 5
    for i, n in enumerate(otros_personas):
        col = i % cols_d
        row = i // cols_d
        n['x'] = 500 + col * 140
        n['y'] = 100 + row * 100
        n['fixed'] = True

    # Region E: vehiculos en columnas (ordenados por menciones)
    vehiculos_nodos = [n for n in nodes if n['group'] == 'vehiculo']
    vehiculos_nodos.sort(key=lambda n: -n.get('menciones', 0))
    cols_e = 8
    for i, n in enumerate(vehiculos_nodos):
        col = i % cols_e
        row = i // cols_e
        n['x'] = -600 + col * 110
        n['y'] = 950 + row * 100
        n['fixed'] = True

    # Region F: lugares
    lugares_nodos = [n for n in nodes if n['group'] == 'lugar']
    cols_f = 3
    for i, n in enumerate(lugares_nodos):
        col = i % cols_f
        row = i // cols_f
        n['x'] = 1500 + col * 180
        n['y'] = -200 + row * 130
        n['fixed'] = True

    # Region G (caso borde): el nodo "hecho" cerca de hallazgos
    hecho_nodos = [n for n in nodes if n['group'] == 'hecho']
    for i, n in enumerate(hecho_nodos):
        n['x'] = -700 + i * 200
        n['y'] = -1100
        n['fixed'] = True

    # Quitar campo level si existe (ya no usamos jerarquico)
    for n in nodes:
        n.pop('level', None)

    # ── 7) Stats ──────────────────────────────────────────────────────────
    stats = {
        "personas_llm_detectadas": len(personas_llm),
        "vehiculos_llm_detectados": len(vehiculos_llm),
        "lugares_llm_detectados": len(lugares_llm),
        "personas_filtradas_rechazadas": len(rechazados_persona),
        "vehiculos_filtrados_rechazados": len(rechazados_veh),
        "personas_unicas_despues_dedup": len(personas_dedup),
        "vehiculos_unicos_despues_dedup": len(veh_dedup),
        "relaciones_juridicas": sum(len(v) for v in rel_juridica_canon.values()),
        "relaciones_victima": sum(len(v) for v in rel_victima_canon.values()),
        "coocurrencias_observadas": sum(cooc_canon.values()),
        "personas_en_grafo": len(personas_top),
        "vehiculos_en_grafo": len(vehiculos_top),
        "lugares_en_grafo": len(lugares_top),
        "hallazgos_en_grafo": len(hallazgos),
    }

    print(f"  Nodos finales: {len(nodes)} ({len(personas_top)} personas + {len(vehiculos_top)} vehiculos + {len(lugares_top)} lugares + {len(hallazgos)} hallazgos + {len([n for n in nodes if n['group']=='hecho'])} hechos)")
    print(f"  Aristas finales: {len(edges)}")

    return {"nodes": nodes, "edges": edges, "stats": stats}


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Grafo Forense — Caso LeBaron</title>
<script src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>
<style>
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; height: 100%; overflow: hidden; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #e2e8f0; }
  body { display: flex; flex-direction: column; }
  header { padding: 18px 24px; background: #1e293b; border-bottom: 1px solid #334155; flex-shrink: 0; }
  header h1 { margin: 0 0 4px 0; font-size: 20px; }
  header .meta { color: #94a3b8; font-size: 13px; }
  .container { display: grid; grid-template-columns: 280px 1fr; flex: 1; min-height: 0; overflow: hidden; }
  aside { background: #1e293b; border-right: 1px solid #334155; overflow-y: auto; padding: 16px; }
  main { position: relative; overflow: hidden; min-width: 0; min-height: 0; }
  #network { width: 100%; height: 100%; background: #0f172a; overflow: hidden; }
  .stats { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 16px; }
  .stat-card { background: #334155; border-radius: 6px; padding: 10px; }
  .stat-card .num { font-size: 22px; font-weight: bold; color: #f1f5f9; }
  .stat-card .lbl { font-size: 11px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; }
  .filters { margin-bottom: 16px; }
  .filter-row { display: flex; align-items: center; padding: 6px 0; font-size: 14px; cursor: pointer; user-select: none; }
  .filter-row input { margin-right: 8px; }
  .filter-row .swatch { display: inline-block; width: 12px; height: 12px; border-radius: 50%; margin-right: 8px; }
  .search-box { width: 100%; padding: 8px 12px; background: #0f172a; color: #e2e8f0; border: 1px solid #334155; border-radius: 6px; font-size: 14px; margin-bottom: 12px; }
  .legenda { font-size: 12px; color: #94a3b8; margin-top: 16px; padding-top: 16px; border-top: 1px solid #334155; }
  .legend-item { display: flex; align-items: center; padding: 3px 0; }
  .legend-item .swatch { display: inline-block; width: 12px; height: 12px; border-radius: 50%; margin-right: 8px; }
  #seleccion-info { position: absolute; top: 16px; right: 16px; background: rgba(30,41,59,0.95); border: 1px solid #334155; border-radius: 6px; padding: 12px; max-width: 320px; font-size: 13px; display: none; z-index: 10; }
  #seleccion-info h3 { margin: 0 0 6px 0; font-size: 14px; }
  #seleccion-info .close { float: right; cursor: pointer; color: #94a3b8; }
  .btn-action { flex: 1; background: #334155; color: #e2e8f0; border: 1px solid #475569; border-radius: 4px; padding: 6px 10px; font-size: 11px; cursor: pointer; }
  .btn-action:hover { background: #475569; border-color: #64748b; }
</style>
</head>
<body>
<header>
  <h1>Grafo Forense — Caso LeBaron</h1>
  <div class="meta">Expediente FED-SEIDO-UEITA-SON-0001337-2019 + FED-FEMDO-UEITA-SON-0000307-2020 · Generado __TIMESTAMP__</div>
</header>
<div class="container">
<aside>
  <div class="stats">
    <div class="stat-card"><div class="num" id="stat-personas">0</div><div class="lbl">Personas</div></div>
    <div class="stat-card"><div class="num" id="stat-vehiculos">0</div><div class="lbl">Vehículos</div></div>
    <div class="stat-card"><div class="num" id="stat-hallazgos">0</div><div class="lbl">Hallazgos</div></div>
    <div class="stat-card"><div class="num" id="stat-aristas">0</div><div class="lbl">Aristas</div></div>
  </div>
  <div id="stat-info" style="font-size:11px;color:#94a3b8;margin-bottom:8px"></div>
  <input class="search-box" id="buscar" type="text" placeholder="Buscar persona, vehículo o hallazgo…">
  <div class="filters">
    <label class="filter-row"><input type="checkbox" id="f-persona" checked> <span class="swatch" style="background:#3b82f6"></span> Personas</label>
    <label class="filter-row"><input type="checkbox" id="f-vehiculo" checked> <span class="swatch" style="background:#f59e0b"></span> Vehículos</label>
    <label class="filter-row"><input type="checkbox" id="f-hallazgo" checked> <span class="swatch" style="background:#ef4444"></span> Hallazgos</label>
    <label class="filter-row"><input type="checkbox" id="f-lugar" checked> <span class="swatch" style="background:#22c55e"></span> Lugares</label>
    <label class="filter-row"><input type="checkbox" id="f-hecho" checked> <span class="swatch" style="background:#a78bfa"></span> Hechos</label>
  </div>
  <div style="display:flex;gap:6px;margin-top:8px;flex-wrap:wrap">
    <button id="btn-solo-conectados" class="btn-action">Solo conectados</button>
    <button id="btn-reset" class="btn-action">Reset</button>
    <button id="btn-fit" class="btn-action" style="flex-basis:100%;margin-top:4px">⊡ Centrar vista</button>
  </div>
  <div style="margin-top:8px;font-size:11px;color:#64748b;line-height:1.4">
    <b>Regiones:</b><br>
    <span style="color:#ef4444">■</span> Hallazgos (izquierda)<br>
    <span style="color:#3b82f6">■</span> Personas jurídicas (centro)<br>
    <span style="color:#3b82f6">■</span> Familia LeBaron (arriba-dcha)<br>
    <span style="color:#3b82f6">■</span> Testigos/peritos (dcha)<br>
    <span style="color:#f59e0b">■</span> Vehículos (abajo)<br>
    <span style="color:#22c55e">■</span> Lugares (esquina inf-dcha)
  </div>
  <div class="legenda">
    <div class="legend-item"><span class="swatch" style="background:#3b82f6"></span> Persona</div>
    <div class="legend-item"><span class="swatch" style="background:#f59e0b"></span> Vehículo</div>
    <div class="legend-item"><span class="swatch" style="background:#ef4444"></span> Hallazgo</div>
    <div class="legend-item"><span class="swatch" style="background:#475569"></span> Sin relaciones (dimmed)</div>
    <div style="margin-top:12px;line-height:1.5">
      <b>Tip:</b> clic en un nodo para ver detalle. Arrastra para mover. Scroll para zoom.
    </div>
  </div>
</aside>
<main>
  <div id="network"></div>
  <div id="seleccion-info">
    <span class="close" onclick="document.getElementById('seleccion-info').style.display='none'">✕</span>
    <h3 id="sel-titulo"></h3>
    <div id="sel-detalle"></div>
  </div>
</main>
</div>

<script>
const DATA = __DATA__;

// ─── Stats ──────────────────────────────────────────────────────────────────
const personas = DATA.nodes.filter(n => n.group === 'persona').map(n => n.id);
const vehiculos = new Set(DATA.nodes.filter(n => n.group === 'vehiculo').map(n => n.id));
const hallazgos = new Set(DATA.nodes.filter(n => n.group === 'hallazgo').map(n => n.id));
const totalAristas = DATA.edges.length;

document.getElementById('stat-personas').textContent = personas.length;
document.getElementById('stat-vehiculos').textContent = vehiculos.size;
document.getElementById('stat-hallazgos').textContent = hallazgos.size;
document.getElementById('stat-aristas').textContent = totalAristas;
document.getElementById('stat-info').textContent = DATA.nodes.length + ' nodos · ' + totalAristas + ' aristas';

// ─── Pre-procesar nodos: truncar labels largos, calcular sizes ─────────────
function truncate(s, n) {
  if (!s) return '';
  return s.length > n ? s.substring(0, n - 1) + '…' : s;
}

// nodos conectados vs sueltos
const connectedIds = new Set();
DATA.edges.forEach(e => { connectedIds.add(e.from); connectedIds.add(e.to); });

// Tooltips ya vienen en texto plano desde Python.

const NODES = DATA.nodes.map(n => {
  // tamaño basado en menciones (escala log) - reducido para evitar amontonamiento
  const m = n.menciones || 1;
  const size = Math.max(10, Math.min(36, 6 + Math.log10(m + 1) * 9));
  // label legible: hasta 22 chars en una linea
  const label = truncate(n.label.split(String.fromCharCode(10)).join(' '), 36);
  // color por grupo
  let bg = '#3b82f6', border = '#1e40af';  // persona (azul)
  let shape = 'dot';
  if (n.group === 'vehiculo') { bg = '#f59e0b'; border = '#b45309'; }   // vehiculo (naranja)
  if (n.group === 'hallazgo') { bg = '#ef4444'; border = '#991b1b'; shape = 'square'; }   // hallazgo (rojo, cuadrado)
  if (n.group === 'lugar')    { bg = '#22c55e'; border = '#166534'; shape = 'box'; }     // lugar (verde, rect)
  if (n.group === 'hecho')    { bg = '#a78bfa'; border = '#6d28d9'; shape = 'diamond'; } // hecho (morado, diamante)
  // dimming para nodos sueltos (sin aristas)
  const dimmed = !connectedIds.has(n.id);
  return Object.assign({}, n, {
    label,
    size,
    shape,
    title: n.title,   // texto plano generado en Python
    color: dimmed ? { background: '#475569', border: '#334155' } : { background: bg, border },
    font: {
      color: dimmed ? '#64748b' : '#e2e8f0',
      size: n.group === 'hallazgo' ? 11 : 10,
      face: 'system-ui, -apple-system, sans-serif',
      strokeWidth: 3,
      strokeColor: '#0f172a',
    },
    shape: n.group === 'hallazgo' ? 'triangle' : (n.group === 'vehiculo' ? 'box' : 'dot'),
    borderWidth: n.group === 'hallazgo' ? 3 : 2,
    margin: 8,
  });
});

const EDGES = DATA.edges.map(e => Object.assign({}, e, {
  width: e.width || 1.5,
  font: { color: '#94a3b8', size: 9, strokeWidth: 3, strokeColor: '#0f172a', align: 'horizontal' },
  smooth: { type: 'continuous', roundness: 0.4 },
  arrows: { to: { enabled: false } },
}));

// ─── Network ────────────────────────────────────────────────────────────────
const container = document.getElementById('network');
const nodesDS = new vis.DataSet(NODES);
const edgesDS = new vis.DataSet(EDGES);

const options = {
  // Layout con coordenadas fijas (calculadas en Python).
  // Sin jerarquico ni physics: el diseno lo hicimos nosotros.
  layout: {
    hierarchical: { enabled: false },
    improvedLayout: false,   // no mover los nodos de su posicion fija
  },
  physics: {
    enabled: false,         // posiciones fijas, no usar physics
  },
  interaction: {
    hover: true,
    tooltipDelay: 80,
    navigationButtons: true,
    keyboard: { enabled: true, speed: { x: 10, y: 10, zoom: 0.03 } },
    zoomView: true,
    dragView: true,
    zoomSpeed: 0.5,
    dragNodes: true,
  },
  nodes: {
    shadow: false,
    // Para layout jerarquico, vis.js necesita saber el nivel de cada nodo.
    // Lo definimos via el campo 'level' del nodo.
  },
  edges: {
    shadow: false,
    smooth: { enabled: true, type: 'cubicBezier', roundness: 0.5 },
    color: { opacity: 0.4 },  // aristas semi-transparentes para no saturar
  },
};

const network = new vis.Network(container, { nodes: nodesDS, edges: edgesDS }, options);

// Centrar vista al inicio (despues de 1 segundo para que se estabilice)
setTimeout(() => network.fit({ animation: false }), 200);

// ─── Auto-fit después de estabilizar ────────────────────────────────────────
network.once('stabilizationIterationsDone', function() {
  network.setOptions({ physics: { enabled: false } });
  network.fit({ animation: { duration: 600, easingFunction: 'easeInOutQuad' } });
});

// ─── Click handler ──────────────────────────────────────────────────────────
network.on('selectNode', function(params) {
  const nodeId = params.nodes[0];
  const node = NODES.find(n => n.id === nodeId);
  if (!node) return;
  const info = document.getElementById('seleccion-info');
  const origNode = DATA.nodes.find(n => n.id === nodeId);
  document.getElementById('sel-titulo').textContent = origNode.label;
  let detalle = '';
  if (origNode.group === 'persona') {
    detalle = `<p><b>Persona</b></p>
      <p style="margin:4px 0">${origNode.menciones} menciones en el corpus</p>
      ${origNode.aliases && origNode.aliases.length > 1
        ? `<p style="margin:4px 0;color:#94a3b8;font-size:11px">Aliases colapsados: ${origNode.aliases.length}</p>`
        : ''}`;
  } else if (origNode.group === 'vehiculo') {
    detalle = `<p><b>Vehículo</b></p>
      <p style="margin:4px 0">${origNode.menciones} menciones</p>`;
  } else if (origNode.group === 'hallazgo') {
    detalle = `<p><b>${origNode.id}</b> — ${origNode.tipo} (severidad ${origNode.severidad})</p>
      <p style="margin:6px 0">${origNode.descripcion}</p>
      <p style="margin:6px 0;color:#94a3b8;font-size:11px"><i>${origNode.archivo}, chunk ${origNode.chunk}</i></p>`;
  }
  document.getElementById('sel-detalle').innerHTML = detalle;
  info.style.display = 'block';
});

network.on('deselectNode', function() {
  document.getElementById('seleccion-info').style.display = 'none';
});

// ─── Filtros ────────────────────────────────────────────────────────────────
function aplicarFiltros() {
  const verP = document.getElementById('f-persona').checked;
  const verV = document.getElementById('f-vehiculo').checked;
  const verH = document.getElementById('f-hallazgo').checked;
  const verL = document.getElementById('f-lugar').checked;
  const verF = document.getElementById('f-hecho').checked;
  const visibles = NODES.filter(n => {
    if (n.group === 'persona' && !verP) return false;
    if (n.group === 'vehiculo' && !verV) return false;
    if (n.group === 'hallazgo' && !verH) return false;
    if (n.group === 'lugar' && !verL) return false;
    if (n.group === 'hecho' && !verF) return false;
    return true;
  }).map(n => n.id);
  nodesDS.update(NODES.map(n => Object.assign({}, n, { hidden: !visibles.includes(n.id) })));
  const visiblesSet = new Set(visibles);
  edgesDS.update(EDGES.map(e => Object.assign({}, e, { hidden: !(visiblesSet.has(e.from) && visiblesSet.has(e.to)) })));
}
['f-persona', 'f-vehiculo', 'f-hallazgo', 'f-lugar', 'f-hecho'].forEach(id => {
  document.getElementById(id).addEventListener('change', aplicarFiltros);
});

// ─── Búsqueda ──────────────────────────────────────────────────────────────
document.getElementById('buscar').addEventListener('input', function(e) {
  const q = e.target.value.toLowerCase().trim();
  if (!q) {
    nodesDS.update(NODES.map(n => Object.assign({}, n, { hidden: false })));
    edgesDS.update(EDGES.map(e => Object.assign({}, e, { hidden: false })));
    return;
  }
  const visibles = NODES.filter(n =>
    n.label.toLowerCase().includes(q) ||
    (n.id && n.id.toLowerCase().includes(q)) ||
    (DATA.nodes.find(o => o.id === n.id)?.label || '').toLowerCase().includes(q)
  ).map(n => n.id);
  const visiblesSet = new Set(visibles);
  nodesDS.update(NODES.map(n => Object.assign({}, n, { hidden: !visiblesSet.has(n.id) })));
  edgesDS.update(EDGES.map(e => Object.assign({}, e, { hidden: !(visiblesSet.has(e.from) && visiblesSet.has(e.to)) })));
  if (visibles.length > 0) network.fit({ nodes: visibles, animation: { duration: 500 } });
});

// ─── Botón "solo conectados" ────────────────────────────────────────────────
document.getElementById('btn-solo-conectados').addEventListener('click', function() {
  const visibles = NODES.filter(n => connectedIds.has(n.id)).map(n => n.id);
  const visiblesSet = new Set(visibles);
  nodesDS.update(NODES.map(n => Object.assign({}, n, { hidden: !visiblesSet.has(n.id) })));
  edgesDS.update(EDGES.map(e => Object.assign({}, e, { hidden: !(visiblesSet.has(e.from) && visiblesSet.has(e.to)) })));
  network.fit({ animation: { duration: 600 } });
});

// ─── Botón "reset" ──────────────────────────────────────────────────────────
document.getElementById('btn-reset').addEventListener('click', function() {
  nodesDS.update(NODES.map(n => Object.assign({}, n, { hidden: false })));
  edgesDS.update(EDGES.map(e => Object.assign({}, e, { hidden: false })));
  document.getElementById('buscar').value = '';
  document.getElementById('f-persona').checked = true;
  document.getElementById('f-vehiculo').checked = true;
  document.getElementById('f-hallazgo').checked = true;
  network.setOptions({ physics: { enabled: true } });
  network.stabilize(300);
  setTimeout(() => {
    network.setOptions({ physics: { enabled: false } });
    network.fit({ animation: { duration: 600 } });
  }, 2500);
});

// ─── Centrar vista (re-fit) ────────────────────────────────────────────────
document.getElementById('btn-fit').addEventListener('click', function() {
  network.fit({ animation: { duration: 600, easingFunction: 'easeInOutQuad' } });
});
</script>
</body>
</html>
"""


def main():
    tabla = cargar_tabla(ALIAS_FILE)
    os.makedirs(UI_DIR, exist_ok=True)

    print("Construyendo grafo...")
    data = construir_grafo(tabla)

    with open(GRAPH_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    print(f"Grafo JSON: {GRAPH_JSON}")
    print(f"  Nodos: {len(data['nodes'])}")
    print(f"  Aristas: {len(data['edges'])}")

    print("Generando visor HTML...")
    # Escapar </  en el JSON para que no cierre el bloque <script> prematuramente
    # cuando los tooltips contienen HTML (<b>, <br>, <i>, etc.).
    # Tecnica: reemplazar '</' por '<\/' que es valido en JS pero rompe el matcher HTML.
    data_json = json.dumps(data, ensure_ascii=False)
    data_json_safe = data_json.replace('</', '<\\/')
    html = (HTML_TEMPLATE
            .replace("__DATA__", data_json_safe)
            .replace("__TIMESTAMP__", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    with open(INDEX_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Visor HTML: {INDEX_HTML}")


if __name__ == "__main__":
    main()

"""
Generador del modelo EER (Enhanced Entity-Relationship) para el caso LeBaron.

Produce:
  - output/eer/schema.sql         DDL: CREATE TABLE con PK/FK
  - output/eer/data.sql           DML: INSERT statements
  - output/eer/diagrama.html      Diagrama EER visual (tablas con FK)
  - output/eer/tablas/            JSON por tabla para export individual

Entidades:
  PERSONA, VEHICULO, LUGAR, HALLAZGO, HECHO, EXPEDIENTE

Relaciones (tablas FK):
  REL_JURIDICA, REL_VICTIMA, REL_COOCURRENCIA,
  REL_INVOLUCRADO, REL_PRESENTE_LUGAR, REL_MENCIONA_VEHICULO

Backup tables (snapshot historico):
  backup_persona_YYYYMMDD_HHMMSS, etc.
"""
import os
import sys
import json
import sqlite3
from collections import defaultdict, Counter
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from build_ui import (
    cargar_trazabilidad_y_hallazgos, construir_grafo,
)
from pseudonymizer import cargar_tabla, desudonimizar
from config import OUTPUT_DIR, ALIAS_FILE

EER_DIR = os.path.join(OUTPUT_DIR, "eer")
TABLAS_DIR = os.path.join(EER_DIR, "tablas")


# ───────────────────────────────────────────────────────────────────────────
# 1. Definicion del esquema EER
# ───────────────────────────────────────────────────────────────────────────

EER_SCHEMA = {
    "persona": {
        "pk": "persona_id",
        "columns": [
            ("persona_id", "TEXT PRIMARY KEY", "alias normalizado del corpus"),
            ("nombre_canonico", "TEXT NOT NULL", "nombre real mas comun"),
            ("menciones_total", "INTEGER DEFAULT 0", "menciones en corpus"),
            ("tipo_rol", "TEXT", "acusado/defensor/juez/testigo/victima/fiscal/perito/quejoso/desconocido"),
            ("fuentes", "TEXT", "JSON array de fuentes donde aparece (partes, testigos, etc)"),
            ("aliases_origen", "TEXT", "JSON array de alias crudos (PERSONA_XXX colapsados)"),
            ("cargos", "TEXT", "JSON array de cargos juridicos identificados"),
            ("archivos", "TEXT", "JSON array de archivos del tomo donde aparece"),
            ("chunks", "TEXT", "JSON array de chunks donde aparece"),
        ],
    },
    "vehiculo": {
        "pk": "vehiculo_id",
        "columns": [
            ("vehiculo_id", "TEXT PRIMARY KEY", "placa o descripcion normalizada"),
            ("placa", "TEXT", "placa si existe"),
            ("tipo", "TEXT", "tipo de vehiculo (sedan, camioneta, etc)"),
            ("marca", "TEXT", "marca si se conoce"),
            ("modelo", "TEXT", "modelo si se conoce"),
            ("color", "TEXT", "color si se conoce"),
            ("menciones_total", "INTEGER DEFAULT 0", "menciones en corpus"),
            ("expedientes", "TEXT", "JSON array de archivos donde aparece"),
            ("chunks", "TEXT", "JSON array de chunks donde aparece"),
        ],
    },
    "lugar": {
        "pk": "lugar_id",
        "columns": [
            ("lugar_id", "TEXT PRIMARY KEY", "nombre normalizado"),
            ("nombre", "TEXT NOT NULL", "nombre del lugar"),
            ("direccion", "TEXT", "direccion si se conoce"),
            ("menciones_total", "INTEGER DEFAULT 0", "menciones en corpus"),
            ("expedientes", "TEXT", "JSON array de archivos donde aparece"),
            ("chunks", "TEXT", "JSON array de chunks donde aparece"),
        ],
    },
    "hallazgo": {
        "pk": "hallazgo_id",
        "columns": [
            ("hallazgo_id", "TEXT PRIMARY KEY", "INC-XXX / ING-XXX / DEV-XXX"),
            ("tipo", "TEXT NOT NULL", "inconsistencia/incongruencia/discrepancia"),
            ("descripcion", "TEXT NOT NULL", "descripcion del hallazgo"),
            ("severidad", "TEXT", "alta/media/baja"),
            ("archivo", "TEXT", "archivo de tomo"),
            ("chunk", "INTEGER", "numero de chunk en el tomo"),
        ],
    },
    "hecho": {
        "pk": "hecho_id",
        "columns": [
            ("hecho_id", "TEXT PRIMARY KEY", "archivo + chunk como identificador"),
            ("archivo", "TEXT NOT NULL", "archivo de tomo"),
            ("chunk", "INTEGER NOT NULL", "numero de chunk"),
            ("descripcion_implicita", "TEXT", "resumen del hecho (si lo hay)"),
        ],
    },
    "expediente": {
        "pk": "expediente_id",
        "columns": [
            ("expediente_id", "TEXT PRIMARY KEY", "nombre del archivo sin extension"),
            ("nombre_archivo", "TEXT NOT NULL", "FED-SEIDO-UEITA-SON-XXX-YYYY TTT.txt"),
            ("anio", "INTEGER", "2019 o 2020"),
            ("tipo_caso", "TEXT", "SEIDO / FEMDO / UEITA"),
            ("delito", "TEXT", "delito del expediente"),
        ],
    },
    "rel_juridica": {
        "pk": "rel_juridica_id",
        "columns": [
            ("rel_juridica_id", "INTEGER PRIMARY KEY AUTOINCREMENT", ""),
            ("persona_id", "TEXT NOT NULL REFERENCES persona(persona_id)", "FK -> persona"),
            ("persona_rel_id", "TEXT NOT NULL REFERENCES persona(persona_id)", "FK -> persona (otra persona del caso)"),
            ("cargo", "TEXT", "cargo juridico (acusado, defensor, etc)"),
            ("archivo", "TEXT", "archivo del tomo donde aparece"),
            ("chunk", "INTEGER", "chunk donde aparece"),
        ],
    },
    "rel_victima": {
        "pk": "rel_victima_id",
        "columns": [
            ("rel_victima_id", "INTEGER PRIMARY KEY AUTOINCREMENT", ""),
            ("persona_id", "TEXT NOT NULL REFERENCES persona(persona_id)", "FK -> persona (la victima)"),
            ("hecho_id", "TEXT NOT NULL REFERENCES hecho(hecho_id)", "FK -> hecho"),
            ("tipo_relacion", "TEXT", "victima directa/indirecta/ofendida"),
            ("archivo", "TEXT", ""),
            ("chunk", "INTEGER", ""),
        ],
    },
    "rel_coocurrencia": {
        "pk": "rel_coocurrencia_id",
        "columns": [
            ("rel_coocurrencia_id", "INTEGER PRIMARY KEY AUTOINCREMENT", ""),
            ("persona1_id", "TEXT NOT NULL REFERENCES persona(persona_id)", "FK -> persona"),
            ("persona2_id", "TEXT NOT NULL REFERENCES persona(persona_id)", "FK -> persona"),
            ("count", "INTEGER DEFAULT 1", "veces que aparecen juntos"),
        ],
    },
    "rel_involucrado": {
        "pk": "rel_involucrado_id",
        "columns": [
            ("rel_involucrado_id", "INTEGER PRIMARY KEY AUTOINCREMENT", ""),
            ("persona_id", "TEXT NOT NULL REFERENCES persona(persona_id)", "FK -> persona"),
            ("hallazgo_id", "TEXT NOT NULL REFERENCES hallazgo(hallazgo_id)", "FK -> hallazgo"),
        ],
    },
    "rel_presente_lugar": {
        "pk": "rel_presente_lugar_id",
        "columns": [
            ("rel_presente_lugar_id", "INTEGER PRIMARY KEY AUTOINCREMENT", ""),
            ("persona_id", "TEXT NOT NULL REFERENCES persona(persona_id)", "FK -> persona"),
            ("lugar_id", "TEXT NOT NULL REFERENCES lugar(lugar_id)", "FK -> lugar"),
            ("count", "INTEGER DEFAULT 1", ""),
        ],
    },
    "rel_menciona_vehiculo": {
        "pk": "rel_menciona_vehiculo_id",
        "columns": [
            ("rel_menciona_vehiculo_id", "INTEGER PRIMARY KEY AUTOINCREMENT", ""),
            ("persona_id", "TEXT NOT NULL REFERENCES persona(persona_id)", "FK -> persona"),
            ("vehiculo_id", "TEXT NOT NULL REFERENCES vehiculo(vehiculo_id)", "FK -> vehiculo"),
            ("count", "INTEGER DEFAULT 1", ""),
        ],
    },
}


def make_schema_sql():
    """Genera DDL: CREATE TABLE ... para SQLite.
    Las columnas ya declaran las FK inline (ej 'TEXT REFERENCES otra(col)').
    """
    lines = ["-- Esquema EER para Caso LeBaron",
             "-- Generado automaticamente",
             f"-- Fecha: {datetime.now().isoformat()}",
             ""]
    for tabla, info in EER_SCHEMA.items():
        cols = []
        for nombre, tipo, _comentario in info["columns"]:
            cols.append(f"  {nombre} {tipo}")
        lines.append(f"CREATE TABLE IF NOT EXISTS {tabla} (")
        lines.append(",\n".join(cols))
        lines.append(");")
        lines.append("")
    return "\n".join(lines)


# El resto se hara en el siguiente paso
if __name__ == "__main__":
    print("Inicializando EER...")
    os.makedirs(EER_DIR, exist_ok=True)
    os.makedirs(TABLAS_DIR, exist_ok=True)

    sql_schema = make_schema_sql()
    with open(os.path.join(EER_DIR, "schema.sql"), "w") as f:
        f.write(sql_schema)
    print(f"Schema SQL: {EER_DIR}/schema.sql")
    print(f"Tablas: {len(EER_SCHEMA)}")



# ───────────────────────────────────────────────────────────────────────────
# 2. Poblado de la BD con datos del grafo
# ───────────────────────────────────────────────────────────────────────────

def _slugify(s: str) -> str:
    """Convierte un string a un id SQL-safe. Preserva guiones originales."""
    import re
    if not s: return 'anon'
    # Reemplazar espacios por _, pero dejar guiones y puntos
    s = s.strip()
    s = re.sub(r'\s+', '_', s)
    s = re.sub(r'[^a-zA-Z0-9_.-]', '', s)
    return s[:80] if s else 'anon'


def _safe_str(v) -> str:
    if v is None: return ''
    return str(v).replace("'", "''")


def _json_arr(items) -> str:
    """Serializa una lista como JSON string para guardar en TEXT."""
    return json.dumps(list(items), ensure_ascii=False)


def _inferir_tipo_rol(nodo_persona: dict) -> str:
    """Infiere el tipo_rol de una persona con jerarquia de prioridad,
    para que NINGUNA persona quede con 'desconocido' si hay informacion
    disponible en cargos, relaciones_victima o fuentes.

    Prioridad (mas especifico -> mas generico):
      1. cargos[0]              -> rol juridico explicito (Acusado, Defensor, Juez, Perito con cargo, etc.)
      2. relaciones_victima     -> 'victima' si hay relacion explicita de victima->hecho
      3. fuentes incluye 'víctimas'      -> 'victima'
      4. fuentes incluye 'testigos' o 'declaraciones' -> 'testigo'
      5. fuentes incluye 'peritos'       -> 'perito'
      6. fuentes incluye 'partes'        -> 'parte procesal'
      7. fuentes incluye 'participantes' -> 'participante'
      8. sin ninguna fuente identificable -> 'desconocido' (ultimo recurso)
    """
    cargos = nodo_persona.get('cargos') or []
    if cargos:
        return cargos[0]

    if nodo_persona.get('relaciones_victima'):
        return 'víctima'

    fuentes = set(nodo_persona.get('fuentes') or [])
    if 'víctimas' in fuentes:
        return 'víctima'
    if 'testigos' in fuentes or 'declaraciones' in fuentes:
        return 'testigo'
    if 'peritos' in fuentes:
        return 'perito'
    if 'partes' in fuentes:
        return 'parte procesal'
    if 'participantes' in fuentes:
        return 'participante'

    return 'desconocido'


def poblar_desde_grafo(grafo: dict, hallazgos: list, alias_map: dict):
    """Extrae entidades y relaciones del grafo JSON y genera INSERTs.

    Devuelve dict {tabla: [rows_as_dict]} con los datos.
    """
    db_data = {tabla: [] for tabla in EER_SCHEMA}

    # ── EXPEDIENTES: agrupar archivos únicos ────────────────────────────
    expedientes_vistos = set()
    archivos_todos = []
    for n in grafo['nodes']:
        if 'archivos' in n:
            archivos_todos.extend(n['archivos'])
    for h in hallazgos:
        if h.get('archivo'):
            archivos_todos.append(h['archivo'])
    for archivo in set(archivos_todos):
        # archivo es como 'FED-SEIDO-UEITA-SON-0001337-2019 T30.txt'
        # expediente_id = nombre sin extension ni espacios
        exp_id = archivo.replace('.txt', '').replace(' ', '_')
        if exp_id in expedientes_vistos:
            continue
        expedientes_vistos.add(exp_id)
        anio = None
        if '2019' in archivo: anio = 2019
        elif '2020' in archivo: anio = 2020
        tipo = 'SEIDO' if 'SEIDO' in archivo else ('FEMDO' if 'FEMDO' in archivo else 'UEITA')
        db_data['expediente'].append({
            'expediente_id': exp_id,
            'nombre_archivo': archivo,
            'anio': anio,
            'tipo_caso': tipo,
            'delito': None,
        })

    # ── PERSONAS ────────────────────────────────────────────────────────
    personas_por_id = {}
    for n in grafo['nodes']:
        if n['group'] != 'persona': continue
        pid = _slugify(n['label'])
        if pid in personas_por_id:
            continue
        personas_por_id[pid] = {
            'persona_id': pid,
            'nombre_canonico': n['label'],
            'menciones_total': n.get('menciones', 0),
            'tipo_rol': _inferir_tipo_rol(n),
            'fuentes': _json_arr(n.get('fuentes', [])),
            'aliases_origen': _json_arr(n.get('aliases', [])),
            'cargos': _json_arr(n.get('cargos', [])),
            'archivos': _json_arr(n.get('archivos', [])),
            'chunks': _json_arr(n.get('chunks', [])),
        }
        db_data['persona'].append(personas_por_id[pid])

    # ── VEHICULOS ──────────────────────────────────────────────────────
    for n in grafo['nodes']:
        if n['group'] != 'vehiculo': continue
        vid = _slugify(n['label'])
        label = n['label']
        db_data['vehiculo'].append({
            'vehiculo_id': vid,
            'placa': label,
            'tipo': None,
            'marca': None,
            'modelo': None,
            'color': None,
            'menciones_total': n.get('menciones', 0),
            'expedientes': _json_arr(n.get('archivos', [])),
            'chunks': _json_arr(n.get('chunks', [])),
        })

    # ── LUGARES ────────────────────────────────────────────────────────
    for n in grafo['nodes']:
        if n['group'] != 'lugar': continue
        nombre_lugar = n.get('nombre_real', n['label']).replace('LUGAR_', '')
        lid = _slugify(nombre_lugar)
        db_data['lugar'].append({
            'lugar_id': lid,
            'nombre': n.get('nombre_real', n['label']),
            'direccion': None,
            'menciones_total': n.get('menciones', 0),
            'expedientes': _json_arr(n.get('archivos', [])),
            'chunks': _json_arr(n.get('chunks', [])),
        })

    # ── HALLAZGOS ──────────────────────────────────────────────────────
    for n in grafo['nodes']:
        if n['group'] != 'hallazgo': continue
        hid = n['id']
        db_data['hallazgo'].append({
            'hallazgo_id': hid,
            'tipo': n.get('tipo', ''),
            'descripcion': n.get('descripcion', '')[:500],
            'severidad': n.get('severidad', ''),
            'archivo': n.get('archivo', ''),
            'chunk': n.get('chunk', None),
        })

    # ── HECHOS ─────────────────────────────────────────────────────────
    for n in grafo['nodes']:
        if n['group'] != 'hecho': continue
        hid = _slugify(n['id'])
        archivo = n.get('archivo', '')
        chunk = n.get('chunk', 0)
        db_data['hecho'].append({
            'hecho_id': hid,
            'archivo': archivo,
            'chunk': chunk,
            'descripcion_implicita': None,
        })

    # construir mapa id -> tipo de nodo
    node_tipos = {}
    for n in grafo['nodes']:
        node_tipos[n['id']] = n['group']

    # ── RELACIONES ────────────────────────────────────────────────
    for e in grafo['edges']:
        f_id_orig = e['from']
        t_id_orig = e['to']
        f_id = _slugify(f_id_orig)
        t_id = _slugify(t_id_orig)
        f_tipo = node_tipos.get(f_id_orig, '')
        t_tipo = node_tipos.get(t_id_orig, '')
        label = e.get('label', '')
        color = e.get('color', '')
        # extraer count si esta al final "x12"
        import re as _re
        count_m = _re.search(r'x(\d+)$', label)
        count = int(count_m.group(1)) if count_m else 1

        # decision segun tipos de nodos
        if f_tipo == 'persona' and t_tipo == 'persona':
            # persona-persona: puede ser rol juridico o coocurrencia
            if 'rol:' in label or color == '#a78bfa':
                db_data['rel_juridica'].append({
                    'persona_id': f_id,
                    'persona_rel_id': t_id,
                    'cargo': label.replace('rol: ', '').strip(),
                    'archivo': '',
                    'chunk': None,
                })
            else:
                # coocurrencia (default para persona-persona)
                db_data['rel_coocurrencia'].append({
                    'persona1_id': f_id,
                    'persona2_id': t_id,
                    'count': count,
                })
        elif f_tipo == 'persona' and t_tipo == 'hecho':
            # victima -> hecho
            db_data['rel_victima'].append({
                'persona_id': f_id,
                'hecho_id': _slugify(t_id_orig),
                'tipo_relacion': label,
                'archivo': '',
                'chunk': None,
            })
        elif f_tipo == 'hallazgo' and t_tipo == 'persona':
            db_data['rel_involucrado'].append({
                'persona_id': t_id,
                'hallazgo_id': f_id,
            })
        elif f_tipo == 'persona' and t_tipo == 'hallazgo':
            db_data['rel_involucrado'].append({
                'persona_id': f_id,
                'hallazgo_id': t_id,
            })
        elif f_tipo == 'persona' and t_tipo == 'lugar':
            lugar_id = t_id.replace('LUGAR_', '')
            db_data['rel_presente_lugar'].append({
                'persona_id': f_id,
                'lugar_id': lugar_id,
                'count': count,
            })
        elif f_tipo == 'persona' and t_tipo == 'vehiculo':
            db_data['rel_menciona_vehiculo'].append({
                'persona_id': f_id,
                'vehiculo_id': t_id,
                'count': count,
            })

    return db_data


# ───────────────────────────────────────────────────────────────────────────
# 3. Crear SQLite, INSERTs, exports por tabla
# ───────────────────────────────────────────────────────────────────────────

def make_inserts(db_data: dict) -> str:
    """Genera DML: INSERT INTO ... para todas las tablas."""
    lines = ["-- Datos para Caso LeBaron",
             "-- Generado automaticamente",
             f"-- Fecha: {datetime.now().isoformat()}",
             ""]
    for tabla, rows in db_data.items():
        if not rows:
            continue
        info = EER_SCHEMA[tabla]
        cols = [c[0] for c in info['columns'] if 'FOREIGN_KEY' not in c[0]]
        col_list = ', '.join(cols)
        lines.append(f"-- {len(rows)} filas en {tabla}")
        for row in rows:
            values = []
            for c in cols:
                v = row.get(c)
                if v is None:
                    values.append('NULL')
                elif isinstance(v, (int, float)):
                    values.append(str(v))
                else:
                    values.append(f"'{_safe_str(v)}'")
            lines.append(f"INSERT INTO {tabla} ({col_list}) VALUES ({', '.join(values)});")
        lines.append("")
    return "\n".join(lines)


def create_sqlite(db_data: dict) -> str:
    """Crea archivo SQLite con todas las tablas y datos. Devuelve la ruta."""
    import re
    db_path = os.path.join(EER_DIR, "caso_lebaron.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    # ejecutar schema stmts uno por uno
    schema_sql = make_schema_sql()
    schema_clean = re.sub(r'--[^\n]*\n', '\n', schema_sql)
    stmts = [s.strip() for s in schema_clean.split(';') if s.strip()]
    for s in stmts:
        try:
            cur.execute(s)
        except sqlite3.OperationalError as e:
            print(f"Schema ERROR: {e}\n  STMT: {s[:300]}")
            raise
    # inserts stmts uno por uno
    insert_sql = make_inserts(db_data)
    insert_clean = re.sub(r'--[^\n]*\n', '\n', insert_sql)
    stmts = [s.strip() for s in insert_clean.split(';') if s.strip()]
    errs = 0
    for s in stmts:
        try:
            cur.execute(s)
        except sqlite3.IntegrityError as e:
            errs += 1
            if errs <= 3:
                print(f"Insert WARN: {e}\n  STMT: {s[:200]}")
    conn.commit()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tablas = [r[0] for r in cur.fetchall()]
    size = os.path.getsize(db_path)
    print(f"SQLite creado: {db_path} ({size//1024} KB, {errs} warnings)")
    print(f"Tablas: {len(tablas)} -> {tablas}")
    conn.close()
    return db_path


def _parse_json_fields(rows):
    """Convierte campos que son strings JSON serializados (via _json_arr)
    de vuelta a objetos Python reales, para que el JSON exportado tenga
    arrays/objetos anidados en vez de strings escapados."""
    parsed_rows = []
    for row in rows:
        new_row = {}
        for k, v in row.items():
            if isinstance(v, str) and len(v) >= 2 and v[0] in '[{' and v[-1] in ']}':
                try:
                    new_row[k] = json.loads(v)
                    continue
                except (json.JSONDecodeError, ValueError):
                    pass
            new_row[k] = v
        parsed_rows.append(new_row)
    return parsed_rows


def export_por_tabla(db_data: dict):
    """Exporta cada tabla como JSON y CSV para descarga individual."""
    os.makedirs(TABLAS_DIR, exist_ok=True)
    manifest = {"timestamp": datetime.now().isoformat(), "tablas": {}}
    for tabla, rows_raw in db_data.items():
        if not rows_raw:
            continue
        # Parsear campos JSON-string a objetos reales antes de exportar
        rows = _parse_json_fields(rows_raw)
        # JSON
        json_path = os.path.join(TABLAS_DIR, f"{tabla}.json")
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
        # CSV
        csv_path = os.path.join(TABLAS_DIR, f"{tabla}.csv")
        if rows:
            cols = list(rows[0].keys())
            with open(csv_path, 'w', encoding='utf-8') as f:
                f.write(','.join(cols) + '\n')
                for r in rows:
                    cells = []
                    for c in cols:
                        v = r.get(c, '')
                        if v is None: v = ''
                        s = str(v).replace('"', '""').replace('\n', ' ')
                        cells.append(f'"{s}"')
                    f.write(','.join(cells) + '\n')
        manifest['tablas'][tabla] = {
            'filas': len(rows),
            'json_size': os.path.getsize(json_path),
            'csv_size': os.path.getsize(csv_path),
        }
    with open(os.path.join(EER_DIR, 'manifest.json'), 'w') as f:
        json.dump(manifest, f, indent=2)
    print(f"Exports por tabla: {TABLAS_DIR}")
    return manifest


def make_backup_sql(db_data: dict) -> str:
    """Genera backup tables: tablas *_backup_<timestamp> con los mismos datos.
    Pensado para restauracion historica."""
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    lines = [f"-- Backup tables generadas {ts}", ""]
    for tabla, rows in db_data.items():
        if not rows: continue
        backup_name = f"backup_{tabla}_{ts}"
        info = EER_SCHEMA[tabla]
        cols = [c[0] for c in info['columns'] if 'FOREIGN_KEY' not in c[0]]
        # CREATE backup table
        lines.append(f"CREATE TABLE {backup_name} AS SELECT * FROM {tabla} WHERE 0=1;")
        # INSERTs
        for row in rows:
            values = []
            for c in cols:
                v = row.get(c)
                if v is None: values.append('NULL')
                elif isinstance(v, (int, float)): values.append(str(v))
                else: values.append(f"'{_safe_str(v)}'")
            col_list = ', '.join(cols)
            lines.append(f"INSERT INTO {backup_name} ({col_list}) VALUES ({', '.join(values)});")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    # ejecutar todo el flujo
    print("=" * 60)
    print("BUILD_EER: Modelo EER del Caso LeBaron")
    print("=" * 60)

    # 1. Cargar tabla de alias y construir el grafo
    print("\n[1/5] Cargando tabla de alias...")
    tabla = cargar_tabla(ALIAS_FILE)

    print("[2/5] Cargando resultados del LLM...")
    (personas_llm, vehiculos_llm, lugares_llm, rel_juridica, rel_victima,
     cooc, persona_lugar, persona_vehiculo, hallazgos_unicos, traz) = \
        cargar_trazabilidad_y_hallazgos(tabla)

    print("[3/5] Construyendo grafo...")
    grafo = construir_grafo(tabla)

    # 2. Poblar
    print("[4/5] Poblando EER desde el grafo...")
    db_data = poblar_desde_grafo(grafo, hallazgos_unicos, tabla)
    for t, rs in db_data.items():
        print(f"  {t}: {len(rs)} filas")

    # 3. Crear SQLite
    print("[5/5] Creando SQLite y exports...")
    db_path = create_sqlite(db_data)

    # 4. SQL schema y data
    schema_sql = make_schema_sql()
    with open(os.path.join(EER_DIR, 'schema.sql'), 'w') as f:
        f.write(schema_sql)
    data_sql = make_inserts(db_data)
    with open(os.path.join(EER_DIR, 'data.sql'), 'w') as f:
        f.write(data_sql)
    print(f"SQL: schema.sql ({os.path.getsize(os.path.join(EER_DIR, 'schema.sql'))//1024} KB), data.sql ({os.path.getsize(os.path.join(EER_DIR, 'data.sql'))//1024} KB)")

    # 5. Backup tables
    backup_sql = make_backup_sql(db_data)
    with open(os.path.join(EER_DIR, 'backup.sql'), 'w') as f:
        f.write(backup_sql)

    # 6. Exports por tabla
    manifest = export_por_tabla(db_data)
    print(f"\nManifest:")
    for t, info in manifest['tablas'].items():
        print(f"  {t}: {info['filas']} filas, JSON {info['json_size']}b, CSV {info['csv_size']}b")

    print("\n=== EER generado ===")
    print(f"  Directorio: {EER_DIR}")
    print(f"  Base de datos: {db_path}")
    print(f"  Tablas: {len(EER_SCHEMA)}")
    print(f"  Total filas: {sum(len(rows) for rows in db_data.values())}")

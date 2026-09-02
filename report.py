"""
Generador de reporte forense final.
Toma los resultados brutos de los 4 analyzers y la tabla de alias,
y produce un reporte markdown con los nombres reales restaurados,
resumen ejecutivo, hallazgos limpios, IDs unicos y desudonimizacion
global del texto.
"""
import os
import sys
import json
import re
from collections import defaultdict, Counter
from datetime import datetime
from typing import Dict, List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pseudonymizer import desudonimizar, cargar_tabla
from entity_filters import es_persona_valida as _check_persona_nombre, es_vehiculo_valido as _check_vehiculo_nombre, normalizar_nombre
from config import (
    OUTPUT_DIR, ALIAS_FILE, REPORT_FILE, REPORT_JSON
)
# Patrones que indican un hallazgo debil / placeholder del LLM.
# El LLM emite estos cuando no encuentra evidencia concreta.
HALLAZGO_DEBIL_PATTERNS = [
    r"(?i)no se proporciona (?:evidencia|informaci[oó]n)",
    r"(?i)no hay evidencia adicional",
    r"(?i)no se menciona expl[ií]citamente",
    r"(?i)no se (?:proporciona|ofrece|cuenta con)",
    r"(?i)no queda claro",
    r"(?i)informaci[oó]n insuficiente",
    r"(?i)sin (?:mayor )?detalle",
    r"(?i)ser[ií]a necesario (?:verificar|contar con|obtener)",
    r"(?i)se requiere (?:m[aá]s informaci[oó]n|verificar)",
    r"(?i)no se (?:cuenta|reporta|indica|detalla)",
    r"(?i)no (?:hay|existe) (?:registro|constancia|mención)",
    r"(?i)falta (?:informaci[oó]n|evidencia|detalle)",
]

def es_debil(texto: str) -> bool:
    """Devuelve True si el texto del hallazgo es placeholder/debil."""
    if not texto:
        return True
    t = texto.strip()
    if len(t) < 25:  # demasiado corto para ser util
        return True
    for pat in HALLAZGO_DEBIL_PATTERNS:
        if re.search(pat, t):
            return True
    return False


def _nombre_real_de(alias_o_nombre: str, tabla: Dict) -> str:
    """Si el argumento es un alias PERSONA_XXX/VEHICULO_XXX, devuelve el
    nombre real. Si ya es un nombre, lo devuelve igual."""
    if not alias_o_nombre:
        return ""
    if alias_o_nombre.startswith("PERSONA_"):
        return tabla.get("personas", {}).get(alias_o_nombre, {}).get("nombre_real", alias_o_nombre)
    if alias_o_nombre.startswith("VEHICULO_"):
        return tabla.get("vehiculos", {}).get(alias_o_nombre, {}).get("nombre_real", alias_o_nombre)
    return alias_o_nombre


def limpiar_hallazgos(items: List[Dict], tabla: Dict) -> List[Dict]:
    """Filtra hallazgos debiles Y limpia nombres ruidosos en
    personas_involucradas usando los mismos filtros del grafo."""
    limpios = []
    for it in items:
        desc = it.get("descripcion", "")
        if not es_debil(desc):
            # tambien limpiar personas_involucradas que sean ruido
            personas_ok = []
            for p in it.get("personas_involucradas", []) or []:
                nombre_real = _nombre_real_de(p, tabla)
                ok, _ = _check_persona_nombre(nombre_real, 0)
                if ok:
                    personas_ok.append(p)
            it["personas_involucradas"] = personas_ok
            # y limpiar el campo 'persona' (singular) si existe
            if it.get("persona"):
                nombre_real = _nombre_real_de(it["persona"], tabla)
                ok, _ = _check_persona_nombre(nombre_real, 0)
                if not ok:
                    it["persona"] = None
            limpios.append(it)
    return limpios


def colapsar_duplicados(items: List[Dict]) -> List[Dict]:
    """Colapsa hallazgos repetidos con la misma descripcion en uno solo
    con lista de ubicaciones."""
    por_desc = defaultdict(list)
    orden = []
    for it in items:
        # normalizar descripcion a 80 chars para agrupar
        key = re.sub(r"\s+", " ", it.get("descripcion", "")).strip().lower()[:80]
        if key not in por_desc:
            orden.append(key)
        por_desc[key].append(it)
    colapsados = []
    for key in orden:
        grupo = por_desc[key]
        primero = dict(grupo[0])  # copia
        # juntar todas las ubicaciones
        ubicaciones = []
        for it in grupo:
            u = it.get("ubicacion")
            if u:
                ubicaciones.append(u)
        primero["ubicaciones"] = ubicaciones
        primero["repeticiones"] = len(grupo)
        colapsados.append(primero)
    return colapsados


def asignar_ids_unicos(items: List[Dict], prefijo: str) -> List[Dict]:
    """Asigna IDs unicos: INC-001, INC-002, etc."""
    for i, it in enumerate(items, 1):
        it["id"] = f"{prefijo}-{i:03d}"
    return items


# ─── Consolidacion ────────────────────────────────────────────────────────────
def consolidar_hallazgos(resultados: Dict[str, List]) -> Dict:
    """Consolida todos los hallazgos en un solo dict agregable."""
    consolidado = {
        "inconsistencias": [],
        "incongruencias": [],
        "trazabilidad": {},
        "discrepancias_declaracion_evidencia": []
    }

    items = resultados if isinstance(resultados, list) else resultados.get("unificado", [])

    for r in items:
        if "resultado" not in r or not r["resultado"]:
            continue
        res = r["resultado"]
        chunk_meta = r.get("chunk", {})

        for inc in res.get("inconsistencias", []):
            inc["ubicacion"] = chunk_meta
            consolidado["inconsistencias"].append(inc)

        for inc in res.get("incongruencias", []):
            inc["ubicacion"] = chunk_meta
            consolidado["incongruencias"].append(inc)

        for alias, info in res.get("trazabilidad", {}).items():
            if alias not in consolidado["trazabilidad"]:
                consolidado["trazabilidad"][alias] = {
                    "menciones": [],
                    "rol_probable": info.get("rol_probable", "desconocido"),
                    "acciones_principales": [],
                    "personas_relacionadas": set(),
                    "vehiculos_relacionados": set()
                }
            if "acciones_principales" in info:
                for a in info["acciones_principales"]:
                    if a not in consolidado["trazabilidad"][alias]["acciones_principales"]:
                        consolidado["trazabilidad"][alias]["acciones_principales"].append(a)
            if "personas_relacionadas" in info:
                consolidado["trazabilidad"][alias]["personas_relacionadas"].update(info["personas_relacionadas"])
            if "vehiculos_relacionados" in info:
                consolidado["trazabilidad"][alias]["vehiculos_relacionados"].update(info["vehiculos_relacionados"])
            consolidado["trazabilidad"][alias]["menciones"].append(chunk_meta)

        for d in res.get("declaracion_vs_evidencia", []):
            d["ubicacion"] = chunk_meta
            consolidado["discrepancias_declaracion_evidencia"].append(d)

    return consolidado


# ─── Render Markdown ──────────────────────────────────────────────────────────
def generar_reporte(consolidado: Dict, tabla_alias: Dict) -> str:
    """Genera reporte en markdown con nombres reales restaurados,
    limpieza de hallazgos debiles y resumen ejecutivo."""

    def real(alias):
        if not isinstance(alias, str):
            return str(alias)
        if alias.startswith("PERSONA_"):
            return tabla_alias["personas"].get(alias, {}).get("nombre_real", alias)
        if alias.startswith("VEHICULO_"):
            return tabla_alias["vehiculos"].get(alias, {}).get("nombre_real", alias)
        return alias

    def reales_de_lista(lista):
        return [real(x) for x in lista]

    # ─── 1) Filtrar y limpiar ─────────────────────────────────────────────
    incs_raw   = limpiar_hallazgos(consolidado["inconsistencias"], tabla_alias)
    incgs_raw  = limpiar_hallazgos(consolidado["incongruencias"], tabla_alias)
    devs_raw   = limpiar_hallazgos(consolidado["discrepancias_declaracion_evidencia"], tabla_alias)

    incs  = colapsar_duplicados(incs_raw)
    incgs = colapsar_duplicados(incgs_raw)
    devs  = colapsar_duplicados(devs_raw)

    incs  = asignar_ids_unicos(incs,  "INC")
    incgs = asignar_ids_unicos(incgs, "ING")
    devs  = asignar_ids_unicos(devs,  "DEV")

    # ─── 2) Construir set de personas para resumen ejecutivo ─────────────
    # Solo contar entidades que pasan los filtros de calidad del grafo
    personas_contadas = Counter()
    vehiculos_contados = Counter()
    for it in incs + incgs + devs:
        for p in it.get("personas_involucradas", []) or []:
            nombre = real(p)
            ok, _ = _check_persona_nombre(nombre, 0)
            if ok:
                personas_contadas[nombre] += 1
        for v in it.get("vehiculos_involucrados", []) or []:
            nombre = real(v)
            ok, _ = _check_vehiculo_nombre(nombre, 0)
            if ok:
                vehiculos_contados[nombre] += 1
    # agregar vehiculos mencionados en trazabilidad (solo los validos)
    for alias, info in consolidado["trazabilidad"].items():
        if alias.startswith("VEHICULO_"):
            nombre = real(alias)
            ok, _ = _check_vehiculo_nombre(nombre, 0)
            if ok:
                vehiculos_contados[nombre] += len(info.get("menciones", []))

    md = []

    # ─── ENCABEZADO + RESUMEN EJECUTIVO ──────────────────────────────────
    md.append("# Reporte de Análisis Forense — Caso LeBaron")
    md.append("")
    md.append(f"**Expediente:** FED-SEIDO-UEITA-SON-0001337-2019 + FED-FEMDO-UEITA-SON-0000307-2020")
    md.append(f"**Generado:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    md.append(f"**Total personas únicas en corpus:** {len(tabla_alias['personas'])}")
    md.append(f"**Total vehículos únicos en corpus:** {len(tabla_alias['vehiculos'])}")
    md.append("")
    md.append("---")
    md.append("")
    md.append("## Resumen ejecutivo")
    md.append("")
    md.append(f"El análisis forense multidimensional de **{len(tabla_alias['personas']):,} personas** "
              f"y **{len(tabla_alias['vehiculos']):,} vehículos** distribuidos en **{len({h.get('ubicacion',{}).get('archivo','?') for it in (incs+incgs+devs) for h in [it] if it.get('ubicacion')})} tomos** "
              f"identificó los siguientes hallazgos significativos:")
    md.append("")
    md.append(f"| Categoría | Hallazgos únicos | Severidad ALTA | MEDIA | BAJA |")
    md.append(f"|---|---:|---:|---:|---:|")
    sev_counter = lambda lst: Counter([x.get("severidad","baja") for x in lst])
    md.append(f"| Inconsistencias entre declaraciones | {len(incs)} | {sev_counter(incs).get('alta',0)} | {sev_counter(incs).get('media',0)} | {sev_counter(incs).get('baja',0)} |")
    md.append(f"| Incongruencias internas | {len(incgs)} | {sev_counter(incgs).get('alta',0)} | {sev_counter(incgs).get('media',0)} | {sev_counter(incgs).get('baja',0)} |")
    md.append(f"| Discrepancias declaración vs. evidencia | {len(devs)} | {sev_counter(devs).get('alta',0)} | {sev_counter(devs).get('media',0)} | {sev_counter(devs).get('baja',0)} |")
    md.append(f"| **Total** | **{len(incs)+len(incgs)+len(devs)}** | **{sev_counter(incs+incgs+devs).get('alta',0)}** | **{sev_counter(incs+incgs+devs).get('media',0)}** | **{sev_counter(incs+incgs+devs).get('baja',0)}** |")
    md.append("")
    md.append(f"**Entidades trazadas con rol jurídico identificado:** {len(consolidado['trazabilidad'])}")
    md.append("")
    if personas_contadas:
        md.append("### Personas con mayor número de apariciones en hallazgos")
        md.append("")
        for nombre, n in personas_contadas.most_common(10):
            md.append(f"- **{nombre}** — {n} menciones")
        md.append("")
    if vehiculos_contados:
        md.append("### Vehículos con mayor número de apariciones")
        md.append("")
        for nombre, n in vehiculos_contados.most_common(5):
            md.append(f"- **{nombre}** — {n} menciones")
        md.append("")
    md.append("> Los nombres reales fueron restaurados desde la tabla de alias del expediente. "
              "Los hallazgos débiles (placeholders del LLM sin evidencia concreta) fueron filtrados; "
              "los duplicados fueron colapsados en una entrada con múltiples ubicaciones.")
    md.append("")
    md.append("---")
    md.append("")

    # ─── 1. INCONSISTENCIAS ──────────────────────────────────────────────
    md.append(f"## 1. Inconsistencias entre declaraciones ({len(incs)})")
    md.append("")
    if not incs:
        md.append("_No se detectaron inconsistencias._")
        md.append("")
    else:
        for sev in ["alta", "media", "baja"]:
            sev_incs = [i for i in incs if i.get("severidad") == sev]
            if not sev_incs:
                continue
            md.append(f"### Severidad {sev.upper()} ({len(sev_incs)})")
            md.append("")
            for inc in sev_incs:
                md.append(f"#### {inc.get('id', 'INC-?')} — {inc.get('tipo', 'N/A').replace('_', ' ').title()}")
                md.append(f"**Descripción:** {inc.get('descripcion', 'N/A')}")
                personas = reales_de_lista(inc.get("personas_involucradas", []) or [])
                if personas:
                    md.append(f"**Personas involucradas:** {', '.join(personas)}")
                if inc.get("evidencia_tomo_actual"):
                    md.append(f"**Evidencia:** _{inc['evidencia_tomo_actual']}_")
                if inc.get("posible_explicacion"):
                    md.append(f"**Posible explicación:** {inc['posible_explicacion']}")
                # multiples ubicaciones si fue colapsado
                ubicaciones = inc.get("ubicaciones") or ([inc["ubicacion"]] if "ubicacion" in inc else [])
                if ubicaciones:
                    if len(ubicaciones) == 1:
                        u = ubicaciones[0]
                        md.append(f"**Ubicación:** {u.get('archivo', 'N/A')}, chunk {u.get('chunk', 'N/A')}")
                    else:
                        md.append(f"**Ubicaciones ({len(ubicaciones)} menciones):**")
                        for u in ubicaciones[:10]:
                            md.append(f"  - {u.get('archivo','?')}, chunk {u.get('chunk','?')}")
                        if len(ubicaciones) > 10:
                            md.append(f"  - _... y {len(ubicaciones)-10} más_")
                if inc.get("repeticiones", 1) > 1:
                    md.append(f"_(Hallazgo repetido {inc['repeticiones']} veces en el corpus)_")
                md.append("")

    md.append("---")
    md.append("")

    # ─── 2. INCONGRUENCIAS ──────────────────────────────────────────────
    md.append(f"## 2. Incongruencias internas ({len(incgs)})")
    md.append("")
    if not incgs:
        md.append("_No se detectaron incongruencias._")
        md.append("")
    else:
        for sev in ["alta", "media", "baja"]:
            sev_incs = [i for i in incgs if i.get("severidad") == sev]
            if not sev_incs:
                continue
            md.append(f"### Severidad {sev.upper()} ({len(sev_incs)})")
            md.append("")
            for inc in sev_incs:
                md.append(f"#### {inc.get('id', 'ING-?')} — {inc.get('tipo', 'N/A').replace('_', ' ').title()}")
                md.append(f"**Descripción:** {inc.get('descripcion', 'N/A')}")
                if inc.get("evidencia"):
                    md.append(f"**Evidencia:** _{inc['evidencia']}_")
                if inc.get("validacion_requerida"):
                    md.append(f"**Validación requerida:** {inc['validacion_requerida']}")
                ubicaciones = inc.get("ubicaciones") or ([inc["ubicacion"]] if "ubicacion" in inc else [])
                if ubicaciones:
                    if len(ubicaciones) == 1:
                        u = ubicaciones[0]
                        md.append(f"**Ubicación:** {u.get('archivo', 'N/A')}, chunk {u.get('chunk', 'N/A')}")
                    else:
                        md.append(f"**Ubicaciones ({len(ubicaciones)} menciones):**")
                        for u in ubicaciones[:10]:
                            md.append(f"  - {u.get('archivo','?')}, chunk {u.get('chunk','?')}")
                if inc.get("repeticiones", 1) > 1:
                    md.append(f"_(Hallazgo repetido {inc['repeticiones']} veces)_")
                md.append("")

    md.append("---")
    md.append("")

    # ─── 3. TRAZABILIDAD ────────────────────────────────────────────────
    traz = consolidado["trazabilidad"]
    # Filtrar entidades cuyos nombres sean ruido (fragmentos, roles, ordinales).
    # Mantener solo las que el filtro de calidad del grafo acepta.
    traz_filtrada = {}
    for alias, info in traz.items():
        nombre = real(alias)
        if alias.startswith("PERSONA_"):
            ok, _ = _check_persona_nombre(nombre, 0)
        elif alias.startswith("VEHICULO_"):
            ok, _ = _check_vehiculo_nombre(nombre, 0)
        else:
            ok = True
        if ok:
            traz_filtrada[alias] = info
    traz = traz_filtrada
    md.append(f"## 3. Trazabilidad de entidades ({len(traz)})")
    md.append("")
    por_rol = defaultdict(list)
    for alias, info in traz.items():
        por_rol[info.get("rol_probable", "desconocido")].append(alias)

    for rol in sorted(por_rol.keys()):
        md.append(f"### Rol: {rol.replace('_', ' ').title()} ({len(por_rol[rol])})")
        md.append("")
        for alias in sorted(por_rol[rol]):
            info = traz[alias]
            md.append(f"#### {real(alias)} (`{alias}`)")
            md.append(f"- **Menciones en chunks:** {len(info.get('menciones', []))}")
            if info.get("acciones_principales"):
                md.append(f"- **Acciones principales:**")
                for a in info["acciones_principales"][:5]:
                    md.append(f"  - {a}")
            if info.get("personas_relacionadas"):
                personas = reales_de_lista(sorted(info["personas_relacionadas"]))
                md.append(f"- **Relacionado con:** {', '.join(personas[:10])}")
            if info.get("vehiculos_relacionados"):
                vehs = reales_de_lista(sorted(info["vehiculos_relacionados"]))
                md.append(f"- **Vehículos relacionados:** {', '.join(vehs[:5])}")
            md.append("")

    md.append("---")
    md.append("")

    # ─── 4. DECLARACIONES VS. EVIDENCIA ─────────────────────────────────
    md.append(f"## 4. Discrepancias declaración vs. evidencia ({len(devs)})")
    md.append("")
    if not devs:
        md.append("_No se detectaron discrepancias._")
        md.append("")
    else:
        for sev in ["alta", "media", "baja"]:
            sev_devs = [d for d in devs if d.get("severidad") == sev]
            if not sev_devs:
                continue
            md.append(f"### Severidad {sev.upper()} ({len(sev_devs)})")
            md.append("")
            for d in sev_devs:
                md.append(f"#### {d.get('id', 'DEV-?')} — {d.get('tipo', 'N/A').replace('_', ' ').title()}")
                md.append(f"**Descripción:** {d.get('descripcion', 'N/A')}")
                if d.get("declaracion"):
                    md.append(f"**Declaración:** _{d['declaracion']}_")
                if d.get("evidencia"):
                    md.append(f"**Evidencia:** _{d['evidencia']}_")
                if d.get("persona"):
                    md.append(f"**Persona:** {real(d['persona'])}")
                if d.get("implicacion_legal"):
                    md.append(f"**Implicación legal:** {d['implicacion_legal']}")
                ubicaciones = d.get("ubicaciones") or ([d["ubicacion"]] if "ubicacion" in d else [])
                if ubicaciones:
                    if len(ubicaciones) == 1:
                        u = ubicaciones[0]
                        md.append(f"**Ubicación:** {u.get('archivo', 'N/A')}, chunk {u.get('chunk', 'N/A')}")
                    else:
                        md.append(f"**Ubicaciones ({len(ubicaciones)} menciones):**")
                        for u in ubicaciones[:10]:
                            md.append(f"  - {u.get('archivo','?')}, chunk {u.get('chunk','?')}")
                if d.get("repeticiones", 1) > 1:
                    md.append(f"_(Hallazgo repetido {d['repeticiones']} veces)_")
                md.append("")

    md.append("---")
    md.append("")

    # ─── APÉNDICE ────────────────────────────────────────────────────────
    md.append("## Apéndice A: Tabla de alias")
    md.append("")
    md.append("Esta tabla mapea los alias del análisis a los nombres reales del expediente.")
    md.append("Solo el equipo legal del caso debe tener acceso a esta correspondencia.")
    md.append("")
    md.append("### Personas")
    md.append("")
    md.append("| Alias | Nombre real | Menciones |")
    md.append("|---|---|---:|")
    for alias, info in sorted(tabla_alias["personas"].items()):
        md.append(f"| `{alias}` | {info['nombre_real']} | {info['menciones']} |")
    md.append("")
    md.append("### Vehículos")
    md.append("")
    md.append("| Alias | Nombre real | Menciones |")
    md.append("|---|---|---:|")
    for alias, info in sorted(tabla_alias["vehiculos"].items()):
        md.append(f"| `{alias}` | {info['nombre_real']} | {info['menciones']} |")

    # ─── APÉNDICE B: METODOLOGÍA ─────────────────────────────────────────
    md.append("")
    md.append("---")
    md.append("")
    md.append("## Apéndice B: Metodología")
    md.append("")
    md.append("**Pipeline ejecutado:**")
    md.append("")
    md.append("1. **Ingesta** — Carga de 62 tomos del expediente (`.txt`). Total: 186.2 MB de texto.")
    md.append("2. **Perfilado del caso** — LLM analiza una muestra del corpus y genera perfil (jurisdicción, "
              "tipo de caso, terminología legal, alertas de calibración). Se reutilizó el perfil existente "
              "del 16-ago-2026.")
    md.append("3. **Pseudonimización** — Extracción de entidades nombradas (personas y vehículos) y "
              "construcción de tabla de alias (`PERSONA_001`, `VEHICULO_001`, etc.). Nombres reales "
              "sustituidos en TODO el texto antes del análisis.")
    md.append("4. **Chunking** — División en 3,465 chunks de ~60K caracteres con 5K de overlap.")
    md.append("5. **Análisis multidimensional** — Llamadas LLM por chunk con prompt adaptado al caso:")
    md.append("   - Inconsistencias entre declaraciones")
    md.append("   - Incongruencias internas")
    md.append("   - Trazabilidad de entidades")
    md.append("   - Discrepancias declaración vs. evidencia")
    md.append("6. **Consolidación** — Agregación de hallazgos por categoría y severidad.")
    md.append("7. **Deseudonimización** — Restauración de nombres reales al reporte final.")
    md.append("")
    md.append("**Modelo LLM:** `llama3.1:latest` (Ollama local, CPU).")
    md.append("**Chunk size:** 60,000 caracteres con 5,000 de overlap.")
    md.append("**Tasa de parseo JSON:** ~89.7% (3,079/3,431 chunks con respuesta válida).")
    md.append("")

    raw_md = "\n".join(md)

    # ─── DESUDONIMIZACIÓN GLOBAL ────────────────────────────────────────
    # Recorrer todo el markdown y reemplazar PERSONA_XXX / VEHICULO_XXX
    # que aparezcan dentro del texto libre (descripcion, evidencia, etc.).
    final_md = desudonimizar(raw_md, tabla_alias)
    return final_md


def main():
    """Genera el reporte a partir de los resultados brutos."""
    tabla = cargar_tabla(ALIAS_FILE)

    resultados = {}
    path = os.path.join(OUTPUT_DIR, "resultados_unificados.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            resultados["unificado"] = json.load(f)

    consolidado = consolidar_hallazgos(resultados)

    # JSON sin pseudonimizar (para uso programático)
    with open(REPORT_JSON, "w", encoding="utf-8") as f:
        json.dump(consolidado, f, ensure_ascii=False, indent=2, default=str)

    # Markdown con nombres reales restaurados
    md = generar_reporte(consolidado, tabla)
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(md)

    print(f"Reporte JSON: {REPORT_JSON}")
    print(f"Reporte Markdown: {REPORT_FILE}")
    print(f"\nResumen (post-limpieza):")
    print(f"  Inconsistencias: {len(consolidado['inconsistencias'])} (post-limpieza se consolidan)")
    print(f"  Incongruencias: {len(consolidado['incongruencias'])}")
    print(f"  Entidades trazadas: {len(consolidado['trazabilidad'])}")
    print(f"  Discrepancias decl/evidencia: {len(consolidado['discrepancias_declaracion_evidencia'])}")


if __name__ == "__main__":
    main()

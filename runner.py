"""
Runner: orquesta el pipeline completo de análisis forense.
0. Carga los documentos (.txt, .pdf, imágenes)
1. Genera el perfil del caso (adaptación automática)
2. Construye la tabla de alias (con blocklist adaptado)
3. Seudonimiza los textos
4. Ejecuta el analizador unificado sobre cada chunk (con prompt adaptado)
5. Guarda resultados intermedios
"""
import os
import sys
import json
import time
import argparse
from datetime import datetime
from typing import Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pseudonymizer import (
    construir_tabla_alias, seudonimizar, guardar_tabla, cargar_tabla,
    NOMBRES_COMUNES_FALSO_POSITIVO
)
from chunker import chunk_texto
from loader import cargar_documentos
from adapter import (
    perfilar_caso, generar_blocklist_perfil,
    generar_prompt_unificado,
    guardar_perfil, cargar_perfil,
)
from config import (
    INPUT_DIR, OUTPUT_DIR, ALIAS_FILE, REPORT_FILE, REPORT_JSON,
    CHUNK_SIZE, CHUNK_OVERLAP, MODELO_RAPIDO, MODELO_PROFUNDO,
    TOMOS_INCLUIDOS
)

from analyzers.analizador_unificado import analizar_fragmento


# Workers concurrentes para llamadas LLM (I/O-bound). El default es 2 workers,
# que es seguro para Ollama local sin saturar VRAM. Subir a 4+ si Ollama local
# tiene suficiente VRAM o si se usa cloud (donde el paralelismo es solo de
# requests HTTP, no de inferencia).
CONCURRENCY = int(os.getenv("PIPELINE_CONCURRENCY", "2"))


def ejecutar(
    usar_alias_existente: bool = False,
    tomos_incluidos: List[str] = None,
    usar_perfil_existente: bool = False,
    skip_profiling: bool = False,
):
    """Pipeline completo."""
    if not INPUT_DIR:
        print("ERROR: INPUT_DIR no está configurado. Setea la variable de entorno INPUT_DIR.")
        print("  Ej: export INPUT_DIR=/ruta/a/expediente")
        sys.exit(1)

    if not os.path.isdir(INPUT_DIR):
        print(f"ERROR: INPUT_DIR no existe: {INPUT_DIR}")
        sys.exit(1)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print(f"[{datetime.now().isoformat()}] Iniciando motor de análisis forense")
    print(f"  INPUT_DIR: {INPUT_DIR}")
    print(f"  OUTPUT_DIR: {OUTPUT_DIR}")
    print(f"  Modelo LLM: {MODELO_RAPIDO}")

    # ─── Paso 0: Cargar documentos ───
    print("\n[0/6] Cargando documentos...")
    tomos = cargar_documentos(INPUT_DIR)
    if tomos_incluidos:
        tomos = {k: v for k, v in tomos.items() if any(t in k for t in tomos_incluidos)}
    elif TOMOS_INCLUIDOS:
        tomos = {k: v for k, v in tomos.items() if any(t in k for t in TOMOS_INCLUIDOS)}
    if not tomos:
        print("ERROR: No se encontraron documentos para analizar.")
        sys.exit(1)
    total_chars = sum(len(v) for v in tomos.values())
    print(f"  {len(tomos)} documentos cargados, {total_chars:,} chars totales")

    # ─── Paso 1: Generar perfil del caso ───
    print("\n[1/6] Generando perfil del caso...")
    perfil_path = os.path.join(OUTPUT_DIR, "case_profile.json")
    if skip_profiling:
        perfil = None
        prompt_adaptado = None
        print("  Perfilado omitido (--skip-profiling). Usando prompts genéricos.")
    elif usar_perfil_existente and os.path.exists(perfil_path):
        perfil = cargar_perfil(perfil_path)
        print(f"  Perfil cargado de {perfil_path}")
        prompt_adaptado = generar_prompt_unificado(perfil)
        print(f"  Prompt adaptado generado ({len(prompt_adaptado):,} chars)")
    else:
        perfil = perfilar_caso(tomos)
        guardar_perfil(perfil, perfil_path)
        print(f"  Perfil guardado en {perfil_path}")
        prompt_adaptado = generar_prompt_unificado(perfil)
        print(f"  Prompt adaptado generado ({len(prompt_adaptado):,} chars)")

        # ─── Paso 1b: Augmentar blocklist del pseudonymizer ───
        extras = generar_blocklist_perfil(perfil)
        if extras:
            NOMBRES_COMUNES_FALSO_POSITIVO.update(extras)
            print(f"  Blocklist aumentada con {len(extras)} términos del perfil")

    # ─── Paso 2: Construir tabla de alias ───
    print("\n[2/6] Construyendo tabla de alias...")
    if usar_alias_existente and os.path.exists(ALIAS_FILE):
        tabla = cargar_tabla(ALIAS_FILE)
        print(f"  Tabla cargada de {ALIAS_FILE}")
    else:
        t0 = time.time()
        tabla = construir_tabla_alias(tomos)
        guardar_tabla(tabla, ALIAS_FILE)
        print(f"  {tabla['stats']['personas_unicas']} personas, "
              f"{tabla['stats']['vehiculos_unicos']} vehículos "
              f"({time.time()-t0:.1f}s)")

    # ─── Paso 3: Seudonimizar ───
    print("\n[3/6] Seudonimizando textos...")
    t0 = time.time()
    tomos_seudo = {}
    # CPU-bound pero regex.sub libera el GIL en C. El nuevo regex maestro
    # pre-compilado en pseudonymizer.py reduce el costo ~50x sobre el original.
    items = list(tomos.items())
    with ThreadPoolExecutor(max_workers=min(8, max(2, (os.cpu_count() or 4)))) as ex:
        futuros = {ex.submit(seudonimizar, texto, tabla): (nombre, texto)
                   for nombre, texto in items}
        for fut in as_completed(futuros):
            nombre, texto_orig = futuros[fut]
            try:
                tomos_seudo[nombre] = fut.result()
            except Exception as exc:
                # Si la regex explota (regex muy grande), fallback secuencial sin paralelismo
                print(f"  WARN seudonimizando {nombre} en paralelo: {exc}; reintentando secuencial")
                try:
                    tomos_seudo[nombre] = seudonimizar(texto_orig, tabla)
                except Exception as exc2:
                    print(f"  ERROR {nombre}: {exc2}; texto sin seudonimizar")
                    tomos_seudo[nombre] = texto_orig
    print(f"  {len(tomos_seudo)} documentos seudonimizados ({time.time()-t0:.1f}s)")

    # ─── Paso 4: Chunking ───
    print("\n[4/6] Generando chunks...")
    todos_los_chunks = []
    for nombre, texto in tomos_seudo.items():
        chunks = chunk_texto(texto, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP)
        for c in chunks:
            c["archivo"] = nombre
            todos_los_chunks.append(c)
    print(f"  {len(todos_los_chunks)} chunks generados")

    # ─── Paso 5: Análisis con prompt adaptado ───
    # 2026-08-19: con local `llama3.1` (8B) en CPU, concurrencia alta satura
    # Ollama y causa timeouts 600s. Bajamos a 2 workers max.
    est_calls = len(todos_los_chunks)
    # Limitar concurrencia efectiva: Ollama local solo aguanta 2 requests
    # simultaneas en CPU sin saturarse.
    effective_concurrency = max(1, min(CONCURRENCY, 2))
    if effective_concurrency != CONCURRENCY:
        print(f"  [INFO] Concurrencia limitada {CONCURRENCY} -> {effective_concurrency} por saturación Ollama local")
    print(f"\n[5/6] Ejecutando analizador unificado sobre {est_calls} chunks...")
    if prompt_adaptado:
        print(f"  Usando prompt adaptado al caso ({perfil.get('jurisdiccion', '?')} / {perfil.get('subtipo_caso', '?')})")
    else:
        print(f"  Usando prompt genérico")
    print(f"  Estimado: ~{est_calls * 80 / 60:.0f} minutos de API ({est_calls} llamadas)")

    resultados = []
    t_inicio = time.time()
    completados = 0

    def procesar_chunk(i_chunk):
        """Procesa un chunk y devuelve (i, chunk_meta, resultado, duracion, error)."""
        i, chunk = i_chunk
        chunk_meta = {
            "archivo": chunk["archivo"],
            "chunk": chunk["numero"],
            "inicio": chunk["inicio"],
            "fin": chunk["fin"]
        }
        try:
            t0 = time.time()
            resultado = analizar_fragmento(chunk["texto"], chunk_meta, prompt=prompt_adaptado)
            duracion = time.time() - t0
            return (i, chunk_meta, resultado, duracion, None)
        except Exception as e:
            return (i, chunk_meta, None, 0, str(e))

    # Procesamiento paralelo (I/O-bound). ThreadPoolExecutor libera el GIL
    # durante las requests HTTP, así que los workers sí paralelizan.
    with ThreadPoolExecutor(max_workers=effective_concurrency) as executor:
        futures = {
            executor.submit(procesar_chunk, (i, chunk)): i
            for i, chunk in enumerate(todos_los_chunks)
        }
        for future in as_completed(futures):
            i, chunk_meta, resultado, duracion, error = future.result()
            completados += 1
            if error is None:
                resultados.append({
                    "chunk": chunk_meta,
                    "resultado": resultado,
                    "duracion_s": round(duracion, 2)
                })
            else:
                print(f"  ERROR chunk {chunk_meta}: {error}")
                resultados.append({
                    "chunk": chunk_meta,
                    "error": error
                })

            # Progreso cada 5 chunks (o cuando sea el último)
            if completados % 5 == 0 or completados == est_calls:
                elapsed = time.time() - t_inicio
                avg = elapsed / completados
                eta = avg * (est_calls - completados)
                workers_tag = f"x{effective_concurrency}workers"
                print(f"  [{completados}/{est_calls}] {duracion:.1f}s/call | "
                      f"transcurrido: {elapsed/60:.1f}min | ETA: {eta/60:.1f}min "
                      f"({workers_tag})")

    # ─── Paso 6: Guardar resultados ───
    print(f"\n[6/6] Guardando resultados...")
    path = os.path.join(OUTPUT_DIR, "resultados_unificados.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(resultados, f, ensure_ascii=False, indent=2)
    print(f"  Guardado: {path}")

    print(f"\n[{datetime.now().isoformat()}] Pipeline completado en {(time.time()-t_inicio)/60:.1f} min")
    return resultados, tabla


def main():
    parser = argparse.ArgumentParser(description="Motor de análisis forense")
    parser.add_argument(
        "--input-dir", "-i",
        help="Directorio con documentos (.txt, .pdf, imágenes)",
        default=None
    )
    parser.add_argument(
        "--output-dir", "-o",
        help="Directorio de salida",
        default=None
    )
    parser.add_argument(
        "--tomos", "-t",
        help="Filtrar por nombre de tomo (separados por coma, ej: T30,T45)",
        default=None
    )
    parser.add_argument(
        "--reuse-alias",
        action="store_true",
        help="Reutilizar tabla de alias existente en vez de reconstruirla"
    )
    parser.add_argument(
        "--reuse-profile",
        action="store_true",
        help="Reutilizar perfil de caso existente en vez de generarlo"
    )
    parser.add_argument(
        "--skip-profiling",
        action="store_true",
        help="Omitir perfilado del caso (usar prompts genéricos)"
    )
    parser.add_argument(
        "--concurrency", "-c",
        type=int,
        default=None,
        help=f"Número de workers concurrentes para llamadas LLM (default: {CONCURRENCY}, env: PIPELINE_CONCURRENCY)"
    )
    args = parser.parse_args()

    # Sobreescribir config con CLI args si se proporcionan
    if args.input_dir:
        import config
        config.INPUT_DIR = args.input_dir
    if args.output_dir:
        import config
        config.OUTPUT_DIR = args.output_dir
        config.ALIAS_FILE = os.path.join(args.output_dir, "alias_map.json")
        config.REPORT_FILE = os.path.join(args.output_dir, "reporte_forense.md")
        config.REPORT_JSON = os.path.join(args.output_dir, "reporte_forense.json")

    # Sobreescribir concurrencia con CLI args
    if args.concurrency:
        import runner as _runner_mod
        _runner_mod.CONCURRENCY = args.concurrency

    tomos = None
    if args.tomos:
        tomos = [t.strip() for t in args.tomos.split(",")]

    ejecutar(
        usar_alias_existente=args.reuse_alias,
        tomos_incluidos=tomos,
        usar_perfil_existente=args.reuse_profile,
        skip_profiling=args.skip_profiling,
    )


if __name__ == "__main__":
    main()

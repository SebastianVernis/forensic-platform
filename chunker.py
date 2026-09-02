"""
Chunker: divide un texto en chunks manejables por el LLM.
Mantiene overlap para que las declaraciones no se corten.
"""
import re
from typing import List, Dict


def chunk_texto(texto: str, chunk_size: int = 100_000, overlap: int = 5_000) -> List[Dict]:
    """
    Divide texto en chunks de ~chunk_size caracteres.
    Cada chunk incluye metadata: inicio, fin, número.
    """
    chunks = []
    n = len(texto)
    inicio = 0
    i = 0
    while inicio < n:
        fin = min(inicio + chunk_size, n)
        # si no es el último chunk, intenta cortar en un límite de párrafo
        if fin < n:
            # buscar el último salto de párrafo dentro de los últimos 5K chars del chunk
            ventana = texto[fin - 5000:fin]
            ultimo_salto = ventana.rfind("\n\n")
            if ultimo_salto != -1:
                fin = fin - 5000 + ultimo_salto + 2  # +2 para incluir el \n\n
        chunk_text = texto[inicio:fin]
        chunks.append({
            "numero": i,
            "inicio": inicio,
            "fin": fin,
            "texto": chunk_text,
            "tamano": len(chunk_text)
        })
        if fin >= n:
            break
        inicio = fin - overlap
        i += 1
    return chunks


def chunk_por_secciones(texto: str, patron: str = r"\n\s*\n") -> List[Dict]:
    """
    Divide texto por párrafos/secciones en lugar de por tamaño fijo.
    Útil cuando el documento tiene estructura clara.
    """
    secciones = re.split(patron, texto)
    chunks = []
    buffer = ""
    n = 0
    for sec in secciones:
        if len(buffer) + len(sec) > 100_000 and buffer:
            chunks.append({
                "numero": n,
                "texto": buffer.strip(),
                "tamano": len(buffer)
            })
            n += 1
            buffer = sec
        else:
            buffer += "\n\n" + sec if buffer else sec
    if buffer:
        chunks.append({
            "numero": n,
            "texto": buffer.strip(),
            "tamano": len(buffer)
        })
    return chunks

"""
Cargador de documentos: soporta .txt, .pdf e imágenes (.png, .jpg, .tiff, .bmp).
Para PDFs: extrae texto nativo; si hay páginas escaneadas, aplica OCR.
Para imágenes: aplica OCR directo vía Tesseract.
"""
import os
import re
import subprocess
from typing import Dict, Optional

from PIL import Image
import pytesseract
import fitz  # pymupdf

from config import OCR_DPI, OCR_LANG

EXTENSIONES_SOPORTADAS = {
    ".txt", ".pdf",
    ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp",
}


def _ocr_image(image_path: str, lang: str = OCR_LANG) -> str:
    """Ejecuta Tesseract OCR sobre una imagen y retorna el texto."""
    img = Image.open(image_path)
    text = pytesseract.image_to_string(img, lang=lang)
    return text


def _load_pdf(pdf_path: str) -> str:
    """
    Carga un PDF. Extrae texto nativo por página.
    Si una página no tiene texto nativo (escaneada), aplica OCR sobre la imagen renderizada.
    """
    doc = fitz.open(pdf_path)
    pages = []

    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text("text").strip()

        if len(text) < 30:
            # Página escaneada: renderizar y OCR
            pix = page.get_pixmap(dpi=OCR_DPI)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            text = pytesseract.image_to_string(img, lang=OCR_LANG)

        if text.strip():
            pages.append(f"--- Página {page_num + 1} ---\n{text.strip()}")

    doc.close()
    return "\n\n".join(pages)


def load_document(path: str) -> Optional[str]:
    """
    Carga un documento según su extensión.
    Retorna el texto extraído o None si no se pudo procesar.
    """
    ext = os.path.splitext(path)[1].lower()

    if ext == ".txt":
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()

    elif ext == ".pdf":
        return _load_pdf(path)

    elif ext in {".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"}:
        return _ocr_image(path)

    else:
        return None


def cargar_documentos(input_dir: str) -> Dict[str, str]:
    """
    Carga todos los documentos soportados del directorio.
    Retorna: {nombre_archivo: texto_extraído}
    """
    documentos = {}
    for f in sorted(os.listdir(input_dir)):
        ext = os.path.splitext(f)[1].lower()
        if ext not in EXTENSIONES_SOPORTADAS:
            continue
        path = os.path.join(input_dir, f)
        try:
            texto = load_document(path)
            if texto and texto.strip():
                documentos[f] = texto
                print(f"  Cargado: {f} ({len(texto):,} chars)")
            else:
                print(f"  Vacío/sin texto: {f}")
        except Exception as e:
            print(f"  Error cargando {f}: {e}")
    return documentos

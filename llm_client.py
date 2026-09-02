"""
Cliente LLM compartido (compatible con API OpenAI).
Soporta multiples endpoints: Ollama local, Ollama Cloud, u otro compatible.
"""
import os
import time
import json
from typing import List, Dict, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import (
    OLLAMA_BASE_URL,
    OLLAMA_API_KEY,
    MODELO_RAPIDO,
    LLM_TIMEOUT,
    LLM_MAX_RETRIES,
    LLM_BACKOFF_FACTOR,
)


def get_session(timeout: int = LLM_TIMEOUT):
    """Session sin retries automaticos.

    Importante: NO montar Retry con status_forcelist=[429,...] porque
    Ollama Cloud devuelve 429 "too many concurrent requests" y los
    reintentos automaticos con backoff generan un loop de 429 -> espera ->
    reintento -> 429 que termina en timeout/404 reportado. El manejo de
    reintentos esta en call_llm_hybrid (controlado, con timing visible)
    via la variable de control last_error / sleep entre backends.
    """
    s = requests.Session()
    # Sin retries: cada llamada es unica y visible en logs
    s.mount("http://", HTTPAdapter(max_retries=0, pool_connections=4, pool_maxsize=8))
    s.mount("https://", HTTPAdapter(max_retries=0, pool_connections=4, pool_maxsize=8))
    return s


def call_endpoint(
    session: requests.Session,
    base_url: str,
    api_key: str,
    messages: List[Dict[str, str]],
    model: str,
    temperature: float = 0.0,
    max_tokens: int = 8000,
    timeout: int = LLM_TIMEOUT,
) -> str:
    """POST a /chat/completions. Devuelve el content del assistant."""
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key and api_key not in ("not-needed-for-local", "not-needed-for-cloud"):
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    r = session.post(url, json=payload, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def call_llm(
    messages: List[Dict[str, str]],
    model: str = MODELO_RAPIDO,
    temperature: float = 0.0,
    max_tokens: int = 8000,
    timeout: int = LLM_TIMEOUT,
) -> str:
    """Llama al LLM default (local). Mantenido para compatibilidad."""
    session = get_session(timeout)
    try:
        return call_endpoint(session, OLLAMA_BASE_URL, OLLAMA_API_KEY, messages, model, temperature, max_tokens, timeout)
    finally:
        session.close()


def call_llm_json(
    messages: List[Dict[str, str]],
    model: str = MODELO_RAPIDO,
    max_retries: int = 2,
) -> Optional[Dict]:
    """Llama al LLM default y parsea la respuesta como JSON."""
    messages = list(messages)
    if messages and messages[-1].get("role") == "user":
        messages[-1] = {
            **messages[-1],
            "content": messages[-1]["content"] +
                "\n\nIMPORTANTE: Responde EXCLUSIVAMENTE con JSON valido. "
                "Sin texto antes, sin texto despues, sin markdown, sin ```. "
                "Solo el objeto JSON crudo. "
                "Si no encuentras nada relevante, retorna {}. "
                "Asegurate de cerrar TODAS las comillas y llaves."
        }

    for attempt in range(max_retries):
        try:
            content = call_llm(messages, model=model)
        except Exception as e:
            print(f"  LLM call failed (intento {attempt+1}/{max_retries}): {e}")
            time.sleep(2 ** attempt)
            continue

        content = content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1])
        content = content.strip()
        if not content:
            continue
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            print(f"  No se pudo parsear JSON: {e}")
            print(f"  Contenido (primeros 500): {content[:500]}")
            try:
                fixed = _repair_json(content)
                if fixed:
                    return fixed
            except Exception:
                pass
            if attempt == max_retries - 1:
                return None
    return None


def _repair_json(content: str) -> Optional[Dict]:
    """Intenta reparar JSON truncado o malformado."""
    n_open_brace = content.count('{')
    n_close_brace = content.count('}')
    n_open_bracket = content.count('[')
    n_close_bracket = content.count(']')
    fixed = content
    if fixed.count('"') % 2 != 0:
        fixed = fixed + '"'
    fixed += '}' * max(0, n_open_brace - n_close_brace)
    fixed += ']' * max(0, n_open_bracket - n_close_bracket)
    try:
        return json.loads(fixed)
    except (json.JSONDecodeError, ValueError):
        pass
    first_brace = content.find('{')
    if first_brace == -1:
        return None
    depth = 0
    in_string = False
    for i, c in enumerate(content[first_brace:], start=first_brace):
        if c == '"' and (i == 0 or content[i-1] != '\\'):
            in_string = not in_string
        if not in_string:
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
        if depth == 0 and c == '}':
            try:
                return json.loads(content[first_brace:i+1])
            except Exception:
                return None
    return None

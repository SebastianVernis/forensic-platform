"""
Selector hibrido de backend LLM.
Orquesta Ollama Local y Ollama Cloud para balancear privacidad, costo y calidad.

Estrategia:
- Tareas de perfilado y reporte final: preferir cloud (modelo grande, mejor razonamiento).
- Tareas de analisis por chunk: preferir local (barato, privado). Fallback a cloud si local falla.
- Si cloud no esta configurado: todo local.
- Si local no responde: todo cloud (si esta configurado).

El selector expone funciones drop-in para reemplazar call_llm y call_llm_json.

NOTA sobre Cloud:
- Ollama Cloud NO expone /v1/chat/completions (devuelve 404). Solo expone /api/chat nativo.
- Cuando se usa cloud, se llama a POST /api/chat con formato nativo de Ollama.
- Cuando se usa local, se asume Ollama local que SÍ implementa /v1/chat/completions (OpenAI compat).
"""
import os
import time
from typing import List, Dict, Optional

import requests

from llm_client import call_endpoint, get_session
from config import (
    OLLAMA_BASE_URL, OLLAMA_API_KEY,
    OLLAMA_CLOUD_URL, OLLAMA_CLOUD_API_KEY,
    MIMO_BASE_URL, MIMO_API_KEY, MIMO_MODEL, MIMO_TIMEOUT,
    MODELO_RAPIDO, MODELO_CLOUD,
    LLM_TIMEOUT, LLM_TIMEOUT_CLOUD,
)


"""Module-level llm rate limiter (para Ollama Cloud free tier y Mimo).

El free tier de Ollama aguanta ~10 req/min. Mimo también se beneficia de un
intervalo mínimo para no saturar el endpoint compartido.
Este semaforo global serializa las llamadas al cloud con un backoff obligatorio
entre requests consecutivos.
"""
import threading
import time

_last_cloud_call_ts = 0.0
_cloud_lock = threading.Lock()
MIN_INTERVAL_BETWEEN_CLOUD_CALLS = 1.5
# Circuit breaker: si el cloud falla N veces seguidas, se desactiva por mucho tiempo.
# El plan free de Ollama Cloud tiene un limite de sesion que se resetea en dias,
# no en minutos. Por eso CIRCUIT_RESET_SECONDS = 30 minutos.
_consecutive_cloud_failures = 0
_circuit_open_until = 0.0
CIRCUIT_FAILURE_THRESHOLD = 3
CIRCUIT_RESET_SECONDS = 1800  # 30 min - el free tier se resetea por horas, no min
CIRCUIT_LOCK = threading.Lock()

# Mimo: sin circuit breaker (es tu suscripción de pago), pero con rate limit suave
# para no saturar el endpoint compartido con otros workers.
MIN_INTERVAL_BETWEEN_MIMO_CALLS = 0.5
_last_mimo_call_ts = 0.0
_mimo_lock = threading.Lock()


def wait_for_cloud_slot():
    """Bloquea hasta que se respete el intervalo minimo entre calls al cloud.

    Si el circuit breaker esta abierto (demasiados fallos recientes),
    levanta una excepcion para que el caller vaya directo al fallback local.
    """
    global _circuit_open_until, _last_cloud_call_ts
    now = time.time()
    with CIRCUIT_LOCK:
        if now < _circuit_open_until:
            raise RuntimeError(
                f"circuit breaker abierto: cloud deshabilitado por {_circuit_open_until - now:.0f}s"
            )
    with _cloud_lock:
        elapsed = time.time() - _last_cloud_call_ts
        wait = MIN_INTERVAL_BETWEEN_CLOUD_CALLS - elapsed
        if wait > 0:
            time.sleep(wait)
        _last_cloud_call_ts = time.time()


def wait_for_mimo_slot():
    """Bloquea hasta que se respete el intervalo minimo entre calls a Mimo.

    Mimo no tiene circuit breaker (es suscripcion de pago), solo rate limit suave.
    """
    global _last_mimo_call_ts
    with _mimo_lock:
        elapsed = time.time() - _last_mimo_call_ts
        wait = MIN_INTERVAL_BETWEEN_MIMO_CALLS - elapsed
        if wait > 0:
            time.sleep(wait)
        _last_mimo_call_ts = time.time()


def record_cloud_success():
    """Llamar cuando el cloud responde OK. Resetea el breaker."""
    global _consecutive_cloud_failures, _circuit_open_until
    with CIRCUIT_LOCK:
        _consecutive_cloud_failures = 0
        _circuit_open_until = 0


def record_cloud_failure():
    """Llamar cuando el cloud falla. Si llega al threshold, abre el breaker."""
    global _consecutive_cloud_failures, _circuit_open_until
    with CIRCUIT_LOCK:
        _consecutive_cloud_failures += 1
        if _consecutive_cloud_failures >= CIRCUIT_FAILURE_THRESHOLD:
            _circuit_open_until = time.time() + CIRCUIT_RESET_SECONDS
            print(
                f"  [CIRCUIT] cloud abierto {_circuit_open_until - time.time():.0f}s "
                f"tras {_consecutive_cloud_failures} fallos seguidos"
            )


def _local_available() -> bool:
    """Verifica si Ollama local responde."""
    host = OLLAMA_BASE_URL.rstrip("/").replace("/v1", "")
    try:
        import requests
        r = requests.get(f"{host}/api/tags", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def _call_ollama_cloud_native(
    messages: List[Dict[str, str]],
    model: str,
    temperature: float = 0.0,
    max_tokens: int = 8000,
    timeout: int = 180,
) -> str:
    """Llama al endpoint nativo /api/chat de Ollama Cloud (NO OpenAI compat).

    Esquema de Ollama Cloud:
      POST https://ollama.com/api/chat
      Headers: Authorization: Bearer ***
      Body: {"model": "...", "messages": [...], "stream": false,
             "options": {"temperature": ..., "num_predict": ...}}

    Devuelve el content del primer choice.
    """
    # Limpiar URL: si termina en /v1 lo quitamos
    base = OLLAMA_CLOUD_URL.rstrip("/").replace("/v1", "")
    url = f"{base}/api/chat"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OLLAMA_CLOUD_API_KEY}",
    }
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    }
    r = requests.post(url, json=payload, headers=headers, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    # Formato nativo Ollama: {"message": {"content": "..."}}
    return data.get("message", {}).get("content", "")


def _cloud_available() -> bool:
    """Verifica si Ollama Cloud esta configurado Y no tiene circuit breaker abierto."""
    import time
    if not (OLLAMA_CLOUD_URL and OLLAMA_CLOUD_API_KEY
            and OLLAMA_CLOUD_API_KEY != "not-needed-for-cloud"):
        return False
    # Si el circuito esta abierto, no reportar cloud como disponible
    with CIRCUIT_LOCK:
        if time.time() < _circuit_open_until:
            return False
    return True


def _mimo_available() -> bool:
    """Mimo es el cloud primario del usuario (OpenAI-compatible)."""
    return bool(MIMO_BASE_URL and MIMO_API_KEY)


def _model_for_backend(model: str, backend: str) -> str:
    """Ajusta el nombre del modelo segun el backend.

    - Local: si no tiene tag de version, agregar :latest (Ollama local lo requiere)
    - Cloud: si el modelo NO existe en cloud (ej: llama3.1 es local-only),
      usar el MODELO_CLOUD como fallback. Modelos con sufijo :cloud, :preview
      o que sean nombres simples de cloud (gpt-oss, deepseek, etc.) se pasan tal cual.
    """
    if backend == "local":
        if ":" not in model:
            return f"{model}:latest"
        return model

    # Backend cloud
    # Si el modelo luce local-only (llama3, mistral, qwen2 sin sufijo cloud
    # y sin prefix de cloud como "deepseek-", "gpt-", "minimax-", "kimi-",
    # "nemotron-", "gemma-"), forzar MODELO_CLOUD.
    cloud_native_prefixes = ("deepseek-", "gpt-", "minimax-", "kimi-", "nemotron-", "gemma", "mimo-")
    looks_local = model.startswith(("llama", "mistral", "qwen", "phi", "llava", "codellama"))
    if looks_local:
        return MODELO_CLOUD  # modelo local-only -> usar cloud

    # Tiene prefix nativo de cloud (deepseek-, gpt-, mimo-, etc.) -> pasa tal cual,
    # aceptando cualquier tag (:20b, :cloud, :preview, etc.)
    if any(model.startswith(prefix) for prefix in cloud_native_prefixes):
        return model

    # Cualquier otro caso (modelos sin prefix conocido) -> forzar cloud default
    return MODELO_CLOUD


def call_llm_hybrid(
    messages: List[Dict[str, str]],
    model: str = None,
    temperature: float = 0.0,
    max_tokens: int = 8000,
    prefer_backend: str = "local",
    timeout: int = None,
) -> str:
    """
    Llama al LLM usando backend preferido, con fallback al otro.

    prefer_backend: 'local' | 'cloud' | 'auto'
    'auto' = local para chunks, cloud para perfil/reporte (no se usa aqui, se elige afuera).
    """
    use_local_first = prefer_backend in ("local", "auto") and _local_available()
    backends = []
    # Mimo es el cloud primario del usuario (OpenAI-compatible, sin rate limit agresivo).
    # Lo agregamos primero si está disponible, sin importar prefer_backend.
    if _mimo_available():
        backends.append(("mimo", MIMO_BASE_URL, MIMO_API_KEY, MIMO_TIMEOUT))
    if use_local_first:
        backends.append(("local", OLLAMA_BASE_URL, OLLAMA_API_KEY, LLM_TIMEOUT))
    if _cloud_available():
        backends.append(("cloud", OLLAMA_CLOUD_URL, OLLAMA_CLOUD_API_KEY, LLM_TIMEOUT_CLOUD))
    if not use_local_first and _local_available() and not _mimo_available():
        # Solo agregar local como fallback si NO estamos prefiriendo cloud
        # (es decir, prefer_backend='cloud' y no hay mimo, queremos intentar local antes que ollama cloud)
        backends.append(("local", OLLAMA_BASE_URL, OLLAMA_API_KEY, LLM_TIMEOUT))

    if not backends:
        raise RuntimeError(
            "No hay backend LLM disponible. Configura MIMO_API_KEY, "
            "OLLAMA_BASE_URL u OLLAMA_CLOUD_URL."
        )

    last_error = None
    for backend, url, key, to in backends:
        effective_model = _model_for_backend(model or MODELO_RAPIDO, backend)
        # Si el backend es mimo, siempre usar el modelo de mimo (no el del .env)
        if backend == "mimo":
            effective_model = MIMO_MODEL
        effective_timeout = timeout or to
        try:
            if backend == "cloud":
                # Si el circuit breaker esta abierto, saltar inmediatamente al local
                # sin esperar el rate limit (1.5s perdido por call).
                try:
                    wait_for_cloud_slot()  # levanta RuntimeError si circuito abierto
                except RuntimeError as ce:
                    print(f"  [CIRCUIT] skip cloud: {ce}")
                    raise
                # 2026-08-19: Registrador de fallos para circuit breaker
                try:
                    # Ollama Cloud usa /api/chat nativo, NO /v1/chat/completions
                    session = get_session(effective_timeout)
                    try:
                        result = _call_ollama_cloud_native(
                            messages, effective_model, temperature, max_tokens, effective_timeout,
                        )
                    finally:
                        session.close()
                    record_cloud_success()
                except Exception as cloud_exc:
                    record_cloud_failure()
                    raise cloud_exc
            else:
                # Local o Mimo: usa OpenAI-compat /v1/chat/completions
                if backend == "mimo":
                    wait_for_mimo_slot()
                session = get_session(effective_timeout)
                try:
                    result = call_endpoint(
                        session, url, key, messages, effective_model,
                        temperature, max_tokens, effective_timeout,
                    )
                finally:
                    session.close()
            print(f"  [{backend}] OK modelo={effective_model}")
            return result
        except Exception as e:
            last_error = e
            err_str = str(e)
            # Rate limit: 429 o textual "too many concurrent". Hacer backoff
            # visible y continuo al proximo backend o intento.
            if '429' in err_str or 'too many concurrent' in err_str.lower():
                print(f"  [{backend}] RATE LIMIT: {e}")
                time.sleep(3)  # backoff visible
            else:
                print(f"  [{backend}] FAIL: {e}")
                time.sleep(1)
            continue

    raise RuntimeError(f"Todos los backends fallaron. Ultimo error: {last_error}")


def call_llm_json_hybrid(
    messages: List[Dict[str, str]],
    model: str = None,
    prefer_backend: str = "local",
    max_retries: int = 2,
) -> Optional[Dict]:
    """Llama al LLM hibrido y parsea JSON."""
    import json

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
            content = call_llm_hybrid(messages, model=model, prefer_backend=prefer_backend)
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
            print(f"  JSON parse failed: {e}")
            # Reparacion basica
            try:
                n_open_brace = content.count('{')
                n_close_brace = content.count('}')
                n_open_bracket = content.count('[')
                n_close_bracket = content.count(']')
                fixed = content
                if fixed.count('"') % 2 != 0:
                    fixed = fixed + '"'
                fixed += '}' * max(0, n_open_brace - n_close_brace)
                fixed += ']' * max(0, n_open_bracket - n_close_bracket)
                return json.loads(fixed)
            except Exception:
                pass
            # Extraer primer objeto JSON balanceado
            try:
                first_brace = content.find('{')
                if first_brace == -1:
                    continue
                depth = 0
                in_string = False
                for i, c in enumerate(content[first_brace:], start=first_brace):
                    if c == '"' and (i == 0 or content[i-1] != '\\'):
                        in_string = not in_string
                    if not in_string:
                        if c == '{': depth += 1
                        elif c == '}': depth -= 1
                    if depth == 0 and c == '}':
                        return json.loads(content[first_brace:i+1])
            except Exception:
                pass

    print(f"  No se pudo parsear JSON despues de {max_retries} intentos")
    return None

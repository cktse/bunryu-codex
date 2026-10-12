"""Bounded service diagnostics shared by Responses, OAuth, and Gmail."""
import http.client
import re
import socket
import ssl
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .errors import DeliveryError, ModelOutputError, ServiceError
from .jsonio import dumps, loads

ERROR_BODY_LIMIT = 65536
DIAGNOSTIC_LIMIT = 4096
RESPONSE_LIMIT = 2 * 1024 * 1024
CREDENTIAL = r'(?:authorization|proxy-authorization|access[_-]?token|refresh[_-]?token|id[_-]?token|api[_-]?key|client[_-]?secret|llm_api_key|google_refresh_token|google_client_secret)'


def sanitize(value, secrets=(), limit=DIAGNOSTIC_LIMIT):
    text = str(value)
    for secret in sorted(set(secrets), key=len, reverse=True):
        if not secret:
            continue
        text = text.replace(secret, '[REDACTED]')
        # A bounded body read can end inside a credential. Remove that suffix too.
        for length in range(min(len(secret) - 1, len(text)), 0, -1):
            if text.endswith(secret[:length]):
                text = text[:-length] + '[REDACTED]'
                break
    text = re.sub(r'(?i)(["\']?' + CREDENTIAL + r'["\']?\s*[:=]\s*)(?:["\'][^"\']*["\']?|(?:Bearer\s+|Basic\s+)?[^\s,;}]+)',
                  r'\1[REDACTED]', text)
    text = re.sub(r'(?i)\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/-]+=*', '[REDACTED]', text)
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)
    text = re.sub(r'[\x00-\x1f\x7f-\x9f]', ' ', text)
    if len(text) > limit:
        text = text[:limit - len(' [truncated]')] + ' [truncated]'
    return text


def details(value, secrets=()):
    """Select explanatory fields and redact credentials even when echoed elsewhere."""
    tokens = list(secrets)
    fields = []
    allowed = {'message', 'type', 'code', 'param', 'parameter', 'reason', 'error_description', 'status'}

    def visit(node):
        if isinstance(node, dict):
            for key, item in node.items():
                if re.fullmatch(CREDENTIAL, key, re.I) and isinstance(item, str):
                    tokens.append(item)
                elif key in allowed and isinstance(item, (str, int, float)):
                    fields.append(f'{key}: {item}')
                elif key == 'error' and isinstance(item, str):
                    fields.append(f'error: {item}')
                elif isinstance(item, (dict, list)) and key not in ('output', 'reasoning'):
                    visit(item)
        elif isinstance(node, list):
            for item in node:
                visit(item)
    visit(value)
    return sanitize('; '.join(fields), tokens) if fields else ''


def diagnostic(operation, error, secrets=()):
    prefix = f'{operation}: HTTP {error.code}'
    headers = error.headers or {}
    for name in ('x-request-id', 'request-id', 'x-goog-request-id', 'Retry-After'):
        if headers.get(name):
            prefix += f'; {name}: {headers[name]}'
    try:
        body = error.read(ERROR_BODY_LIMIT + 1)
        text = body[:ERROR_BODY_LIMIT].decode('utf-8', errors='replace')
        try:
            explanation = details(loads(text), secrets) or sanitize(text, secrets)
        except ValueError:
            explanation = sanitize(text, secrets)
        prefix += '; ' + (explanation or 'empty error response body')
        if len(body) > ERROR_BODY_LIMIT:
            prefix += ' [body truncated]'
    except Exception as exc:
        prefix += f'; error response body could not be read ({type(exc).__name__})'
    finally:
        try:
            error.close()
        except Exception:
            pass
    return sanitize(prefix, secrets)


def transport_detail(operation, error, secrets):
    cause = error.reason if isinstance(error, URLError) else error
    if isinstance(cause, (TimeoutError, socket.timeout)):
        kind = 'timeout'
    elif isinstance(cause, ssl.SSLError):
        kind = 'TLS failure'
    elif isinstance(cause, socket.gaierror):
        kind = 'DNS failure'
    elif isinstance(cause, http.client.IncompleteRead):
        kind = 'interrupted read'
    else:
        kind = 'connection/request failure'
    return sanitize(f'{operation}: {kind} ({type(cause).__name__}): {cause}', secrets)


def request_json(operation, url, payload, headers, timeout, secrets=(), *, sending=False):
    data = payload if isinstance(payload, bytes) else dumps(payload).encode()
    request = Request(url, data=data, headers={'Content-Type': 'application/json', **headers}, method='POST')
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(RESPONSE_LIMIT + 1)
            length = response.headers.get('Content-Length')
            if length and length.isdecimal() and int(length) != len(raw) and len(raw) <= RESPONSE_LIMIT:
                raise http.client.IncompleteRead(raw, int(length) - len(raw))
    except HTTPError as exc:
        message = diagnostic(operation, exc, secrets)
        if sending:
            raise DeliveryError(message, uncertain=exc.code == 408 or exc.code >= 500) from exc
        raise ServiceError(message) from exc
    except (OSError, URLError, http.client.HTTPException) as exc:
        message = transport_detail(operation, exc, secrets)
        if sending:
            raise DeliveryError(message, uncertain=True) from exc
        raise ServiceError(message) from exc
    try:
        if len(raw) > RESPONSE_LIMIT:
            raise ValueError('response exceeds 2 MiB')
        result = loads(raw.decode('utf-8'))
        if not isinstance(result, dict):
            raise ValueError('response is not a JSON object')
        return result
    except (ValueError, UnicodeError) as exc:
        message = f'{operation}: unreadable or invalid JSON response ({type(exc).__name__})'
        if sending:
            raise DeliveryError(message, uncertain=True) from exc
        if operation.startswith('LLM'):
            raise ModelOutputError(message) from exc
        raise ServiceError(message) from exc

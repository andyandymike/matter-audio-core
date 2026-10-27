"""Bounded standard-library HTTP transport, with no downloads or automatic retries."""

from __future__ import annotations

import ipaddress
from http.client import HTTPException
import os
import re
import socket
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from matter_audio_core.contracts import MAX_JSON_BYTES, object_schema, parse_json, read_json, validate
from matter_audio_core.errors import AudioError

CONFIG_SCHEMA = object_schema({
    "schema": {"const": "matter-voicestudio-config/v1"},
    "base_url": {"type": "string", "minLength": 1, "maxLength": 2048},
    "model": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_./:-]{0,127}$"},
    "api_key_env": {"type": "string", "pattern": r"^[A-Za-z_][A-Za-z0-9_]{0,127}$"},
    "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 600},
    "service_revision": {"type": "string", "minLength": 1, "maxLength": 128},
}, ["schema", "base_url", "model"])


def load_config(path):
    try:
        value = read_json(path)
        validate(value, CONFIG_SCHEMA)
    except (OSError, AudioError) as exc:
        # Configuration may accidentally contain a credential. Never echo it.
        raise AudioError("invalid_adapter_config", "Read a config matching config.example.json; credentials belong in the named environment variable.") from exc
    try:
        parts = urlsplit(value["base_url"])
        hostname = parts.hostname
        port = parts.port
        if (parts.scheme not in ("http", "https") or not hostname or parts.username is not None
                or parts.password is not None or parts.query or parts.fragment
                or re.search(r"[\s\\\x00-\x1f]", value["base_url"])):
            raise ValueError()
        if parts.scheme == "http":
            # No DNS lookup: plaintext is restricted to literal loopback hosts.
            if hostname != "localhost" and not ipaddress.ip_address(hostname).is_loopback:
                raise ValueError()
        if port is not None and not 1 <= port <= 65535:
            raise ValueError()
    except ValueError as exc:
        raise AudioError("invalid_service_url", "Use HTTPS, or HTTP on a literal loopback address; omit credentials, query strings and fragments.") from exc
    return {**value, "base_url": urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", "")),
            "timeout_seconds": value.get("timeout_seconds", 120)}


def identity(config):
    return {"base_url": config["base_url"], "requested_model": config["model"],
            "declared_service_revision": config.get("service_revision"),
            "identity_evidence": "caller_configuration_not_server_attestation",
            "timeout_seconds": config["timeout_seconds"]}


def credential(config):
    name = config.get("api_key_env")
    if name is None:
        return None
    token = os.environ.get(name)
    if not token or any(ord(c) < 32 or ord(c) > 126 for c in token):
        raise AudioError("service_credential_missing", "Set the configured API-key environment variable to a printable ASCII token.")
    return token


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def transcribe(config, audio, options, token):
    boundary = "matter-" + uuid.uuid4().hex
    fields = {"model": config["model"], "response_format": "verbose_json"}
    if options.get("language"):
        fields["language"] = options["language"]
    fields["timestamp_granularities[]"] = options["timestamps"]
    pieces = []
    for name, value in fields.items():
        pieces.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n').encode("utf-8"))
    pieces.extend([(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="source.wav"\r\nContent-Type: audio/wav\r\n\r\n').encode("ascii"),
                   audio, f"\r\n--{boundary}--\r\n".encode("ascii")])
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}", "Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = Request(config["base_url"] + "/v1/audio/transcriptions", data=b"".join(pieces), headers=headers, method="POST")
    # Explicit service only: ignore machine proxy settings and refuse redirects.
    opener = build_opener(ProxyHandler({}), NoRedirects())
    try:
        with opener.open(request, timeout=config["timeout_seconds"]) as response:
            if response.status != 200:
                raise AudioError("remote_response_invalid", "The transcription service returned an unexpected response.")
            raw = response.read(MAX_JSON_BYTES + 1)
            if len(raw) > MAX_JSON_BYTES:
                raise AudioError("remote_response_too_large", "Transcription response exceeds 1 MiB; use shorter source audio.")
            try:
                document = parse_json(raw)
            except AudioError as exc:
                raise AudioError("remote_response_invalid", "The service did not return bounded, strict JSON.") from exc
            if not isinstance(document, dict):
                raise AudioError("remote_response_invalid", "Expected a transcription JSON object.")
            return document
    except HTTPError as exc:
        code = exc.code
        exc.close()
        raise AudioError("remote_http_error", "The transcription service rejected the request; no automatic retry was made.",
                         details={"http_status": code, "remote_outcome": "unconfirmed"}) from None
    except (HTTPException, URLError, TimeoutError, socket.timeout, OSError):
        raise AudioError("remote_outcome_unknown", "The request did not complete locally. The service may still be working; do not retry blindly.",
                         details={"remote_outcome": "unknown", "automatic_retry": False}) from None

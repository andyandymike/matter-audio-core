"""Read-only environment reports; no backend imports or execution probes."""

from __future__ import annotations

from copy import deepcopy
import platform
import re
import sys

from . import __version__
from .media import MAX_AUDIO_BYTES

SCHEMA = "matter-diagnostics/v1"
_CATEGORIES = {"configuration", "dependency", "file", "device"}
_STATUSES = {"ok", "warning", "error", "unknown"}
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\Z")
_DETAIL_KEY = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_DETAIL_VALUE = re.compile(r"[A-Za-z0-9_.:+-]{0,128}\Z")
_PRIVATE_KEY = re.compile(r"(?:secret|password|credential|token|path|url|endpoint|api_key)", re.I)


def _text(value, maximum):
    return (isinstance(value, str) and 0 < len(value) <= maximum
            and not any(ord(character) < 32 for character in value))


def check(check_id: str, category: str, status: str, code: str, message: str, *,
          remedy: str | None = None, details: dict | None = None) -> dict:
    """Build one check using static public messages and bounded safe facts.

    Providers must never pass configuration values, exception text or secrets as
    messages. Details admit identifiers, versions, hashes, counts and flags, not
    paths, URLs or arbitrary nested runtime/configuration dumps.
    """
    if (not isinstance(check_id, str) or not _IDENTIFIER.fullmatch(check_id)
            or not isinstance(category, str) or category not in _CATEGORIES
            or not isinstance(status, str) or status not in _STATUSES
            or not isinstance(code, str) or not _IDENTIFIER.fullmatch(code)
            or not _text(message, 800) or (remedy is not None and not _text(remedy, 800))):
        raise ValueError("Invalid diagnostic check")
    details = {} if details is None else details
    if not isinstance(details, dict) or len(details) > 32:
        raise ValueError("Invalid diagnostic details")
    for key, value in details.items():
        if (not isinstance(key, str) or not _DETAIL_KEY.fullmatch(key) or _PRIVATE_KEY.search(key)
                or not (value is None or type(value) is bool
                        or (type(value) is int and abs(value) <= 9007199254740991)
                        or (isinstance(value, str) and _DETAIL_VALUE.fullmatch(value)))):
            raise ValueError("Invalid diagnostic details")
    return {"id": check_id, "category": category, "status": status,
            "reason": {"code": code, "message": message}, "remedy": remedy,
            "details": dict(details)}


def backend_report(backend_id: str, *, configured: bool, checks: list,
                   optional: bool = True) -> dict:
    """Summarize static prerequisites, separately from loaded/execution state."""
    if (not isinstance(backend_id, str) or not _IDENTIFIER.fullmatch(backend_id)
            or type(configured) is not bool or type(optional) is not bool
            or not isinstance(checks, list) or not 1 <= len(checks) <= 128):
        raise ValueError("Invalid backend diagnostic report")
    copied, ids = [], set()
    for item in checks:
        if (not isinstance(item, dict)
                or set(item) != {"id", "category", "status", "reason", "remedy", "details"}
                or not isinstance(item["reason"], dict)
                or set(item["reason"]) != {"code", "message"}):
            raise ValueError("Invalid diagnostic check")
        validated = check(item["id"], item["category"], item["status"],
                          item["reason"]["code"], item["reason"]["message"],
                          remedy=item["remedy"], details=item["details"])
        if validated["id"] in ids:
            raise ValueError("Duplicate diagnostic check ID")
        ids.add(validated["id"])
        copied.append(validated)
    prerequisites = [item["status"] for item in copied if item["category"] != "device"]
    availability = ("unavailable" if not configured or "error" in prerequisites else
                    "unknown" if not prerequisites or any(value in {"unknown", "warning"}
                                                           for value in prerequisites) else "available")
    return {"id": backend_id, "optional": optional, "configured": configured,
            "availability": availability, "loaded": "unknown", "execution_device": "unknown",
            "checks": copied}


def _core_report():
    supported = sys.platform == "win32" or sys.platform.startswith("linux")
    return backend_report("matter-audio-core", configured=True, optional=False, checks=[
        check("python", "dependency", "ok" if sys.version_info >= (3, 10) else "error",
              "python_supported" if sys.version_info >= (3, 10) else "python_unsupported",
              "Python 3.10 or later is required.", remedy="Use the supported Python environment."),
        check("platform", "configuration", "ok" if supported else "error",
              "platform_supported" if supported else "platform_unsupported",
              "Workspace publication and owned backend processes support Windows and Linux.",
              remedy="Use a supported Windows or Linux authoring environment."),
    ])


def build_report(product: str, backend_reports=()) -> dict:
    """Return a local report without opening a workspace or starting a backend.

    A successful report may describe unavailable optional backends. Providers
    supply already-collected static checks; this function does not call them.
    """
    if not isinstance(product, str) or not _IDENTIFIER.fullmatch(product):
        raise ValueError("Invalid diagnostic product")
    if not isinstance(backend_reports, (list, tuple)) or len(backend_reports) > 32:
        raise ValueError("Invalid backend diagnostic reports")
    backends, ids = [], {"matter-audio-core"}
    for item in backend_reports:
        if (not isinstance(item, dict)
                or set(item) != {"id", "optional", "configured", "availability", "loaded",
                                 "execution_device", "checks"}):
            raise ValueError("Invalid backend diagnostic report")
        validated = backend_report(item["id"], configured=item["configured"],
                                   optional=item["optional"], checks=item["checks"])
        if item != validated or item["id"] in ids:
            raise ValueError("Invalid or duplicate backend diagnostic report")
        ids.add(item["id"])
        backends.append(validated)
    core = _core_report()
    affected = [item["id"] for item in [core, *backends] if item["availability"] != "available"]
    result = {
        "schema": SCHEMA, "product": product, "status": "succeeded",
        "summary": {"status": "attention" if affected else "ok", "attention_backends": affected},
        "runtime": {"core_version": __version__, "python_version": platform.python_version(),
                    "python_implementation": platform.python_implementation(),
                    "platform": sys.platform, "machine": platform.machine()},
        "pcm": {"container": "wav", "codec": "pcm_s16le", "channels": [1, 2],
                "sample_rate_hz": {"minimum": 8000, "maximum": 192000},
                "max_audio_bytes": MAX_AUDIO_BYTES},
        "core": core, "backends": backends,
        "scope": {"read_only": True, "network_calls": 0, "downloads": 0,
                  "subprocesses": 0, "audio_model_calls": 0, "workspace_opened": False},
        "limitations": [
            "Available means static prerequisites passed, not successful import, model loading or inference.",
            "Loaded models and actual execution devices are not probed.",
            "Missing optional backends do not make the core unavailable.",
            "No audio was decoded, generated or judged for listening quality.",
        ],
    }
    return deepcopy(result)

from __future__ import annotations

import fcntl
import logging
import json
from json import JSONDecoder
import os
import subprocess
import tempfile
import time
from django.conf import settings

logger = logging.getLogger(__name__)


class TradeRepublicBridgeError(Exception):
    """Erreur générique de communication avec le bridge Trade Republic."""


class TradeRepublicBridgeAuthRequired(TradeRepublicBridgeError):
    """Le bridge indique qu'une authentification manuelle est requise."""


def _bun_bin() -> str:
    return (getattr(settings, "TR_BRIDGE_BUN_BIN", "") or "").strip() or "bun"


def _script_path() -> str:
    script = (getattr(settings, "TR_BRIDGE_SCRIPT_PATH", "") or "").strip()
    if script:
        return script
    raise TradeRepublicBridgeError("bridge_script_not_configured")


def _working_directory(script_path: str) -> str:
    cwd = (getattr(settings, "TR_BRIDGE_CWD", "") or "").strip()
    if cwd:
        return cwd
    return os.path.dirname(script_path) or "."


def _is_manual_auth_hint(output: str) -> bool:
    lowered = output.lower()
    return (
        "needs_manual_auth" in lowered
        or "device pin" in lowered
        or "3003" in lowered
        or "please enter the pin received on your phone" in lowered
    )


def _is_login_flow_hint(output: str) -> bool:
    lowered = output.lower()
    return (
        "attempting to log in" in lowered
        or "retrieving aws waf token" in lowered
        or "saved session invalid or not found" in lowered
        or "session file not found" in lowered
        or "loaded session from file" in lowered
        or "validating session via websocket" in lowered
        or "attempting to connect to websocket" in lowered
        or "websocket acknowledged connection" in lowered
        or "session validation subscription" in lowered
    )


_BRIDGE_LOCK_FILE = os.path.join(tempfile.gettempdir(), "tr_bridge.lock")


def fetch_tr_valuation(
    *,
    phone_number: str,
    pin: str,
    device_pin: str | None = None,
    timeout_seconds: int = 45,
) -> dict:
    timeout_seconds = int(
        getattr(settings, "TR_BRIDGE_TIMEOUT_SECONDS", timeout_seconds) or timeout_seconds
    )
    script_path = _script_path()
    command = [
        _bun_bin(),
        "run",
        script_path,
        phone_number,
        pin,
        "--json",
    ]
    tx_limit = int(getattr(settings, "TR_BRIDGE_TX_LIMIT", 80) or 80)
    command.extend(["--tx-limit", str(tx_limit)])
    if device_pin:
        command.extend(["--device-pin", device_pin.strip()])

    # Verrou exclusif: Chromium consomme beaucoup de RAM, on évite les lancements parallèles.
    lock_file = open(_BRIDGE_LOCK_FILE, "w")
    lock_acquired = False
    try:
        deadline = time.monotonic() + 5  # attendre max 5s pour acquérir le verrou
        while time.monotonic() < deadline:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                lock_acquired = True
                break
            except OSError:
                time.sleep(0.5)
        if not lock_acquired:
            raise TradeRepublicBridgeError("bridge_busy: another sync is already running")
    except TradeRepublicBridgeError:
        lock_file.close()
        raise

    try:
        proc = subprocess.run(
            command,
            cwd=_working_directory(script_path),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        partial = f"{exc.stdout or ''}\n{exc.stderr or ''}".strip()
        if _is_manual_auth_hint(partial):
            raise TradeRepublicBridgeAuthRequired("auth_required") from exc
        raise TradeRepublicBridgeError(f"bridge_timeout: {exc}") from exc
    except OSError as exc:
        raise TradeRepublicBridgeError(f"bridge_unreachable: {exc}") from exc
    finally:
        if lock_acquired:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()

    lines = [line.strip() for line in stdout.splitlines() if line.strip()]

    def _is_bridge_payload(candidate) -> bool:
        return (
            isinstance(candidate, dict)
            and isinstance(candidate.get("accounts"), list)
            and isinstance(candidate.get("global"), dict)
        )

    def _extract_json_payload(raw_lines: list[str], raw_stdout: str):
        # 1) Cas simple: dernière ligne JSON
        for line in reversed(raw_lines):
            try:
                parsed = json.loads(line)
                if _is_bridge_payload(parsed):
                    return parsed
            except ValueError:
                continue

        # 2) Cas robuste: stdout contient logs + gros JSON sur plusieurs segments
        # On scanne tout le texte et on garde le dernier objet JSON décodable.
        decoder = JSONDecoder()
        idx = 0
        best = None
        text = raw_stdout or ""
        while idx < len(text):
            if text[idx] != "{":
                idx += 1
                continue
            try:
                candidate, end = decoder.raw_decode(text[idx:])
                if _is_bridge_payload(candidate):
                    best = candidate
                idx += max(end, 1)
            except ValueError:
                idx += 1

        # 3) Fallback ultra-robuste: extraction par accolades équilibrées
        # à partir d'un objet racine bridge {"timestamp": ...}
        start_token = '{"timestamp"'
        search_from = 0
        while True:
            start = text.find(start_token, search_from)
            if start == -1:
                break
            depth = 0
            in_string = False
            escaped = False
            for pos in range(start, len(text)):
                ch = text[pos]
                if in_string:
                    if escaped:
                        escaped = False
                    elif ch == "\\":
                        escaped = True
                    elif ch == '"':
                        in_string = False
                    continue

                if ch == '"':
                    in_string = True
                    continue
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        candidate_raw = text[start : pos + 1]
                        try:
                            candidate = json.loads(candidate_raw)
                            if _is_bridge_payload(candidate):
                                return candidate
                        except ValueError:
                            pass
                        break
            search_from = start + 1
        return best

    payload = _extract_json_payload(lines, stdout)

    if proc.returncode != 0:
        if isinstance(payload, dict) and payload.get("status") == "needs_manual_auth":
            raise TradeRepublicBridgeAuthRequired("auth_required")
        if isinstance(payload, dict) and payload.get("status") == "rate_limited":
            retry = payload.get("retry_after_seconds")
            raise TradeRepublicBridgeError(
                f"rate_limited: trop de tentatives, réessaie dans {retry}s" if retry
                else "rate_limited: trop de tentatives"
            )
        if _is_manual_auth_hint(f"{stdout}\n{stderr}"):
            raise TradeRepublicBridgeAuthRequired("auth_required")
        raise TradeRepublicBridgeError(
            f"bridge_error status={proc.returncode} stderr={stderr or stdout}"
        )

    if not payload:
        raw_output = (stdout or stderr or "").strip()
        max_chars = int(getattr(settings, "TR_BRIDGE_RAW_OUTPUT_MAX_CHARS", 20000) or 20000)
        if len(raw_output) > max_chars:
            raw_output = raw_output[:max_chars] + "\n...[truncated]"
        raise TradeRepublicBridgeError(
            "bridge_invalid_json_raw_output:\n"
            f"{raw_output or '<empty output>'}"
        )

    if isinstance(payload, dict) and payload.get("status") == "needs_manual_auth":
        raise TradeRepublicBridgeAuthRequired("auth_required")
    return payload


def fetch_tr_auth_status(*, phone_number: str | None = None, pin: str | None = None) -> dict:
    # En mode script local, le statut est déduit du dernier essai.
    # Cette méthode reste compatible avec le contrat attendu côté Django.
    return {"status": "authenticated", "reason": "local_script_mode"}


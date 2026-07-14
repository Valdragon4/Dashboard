from __future__ import annotations

import fcntl
import logging
import json
import os
import shutil
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
        or "tr_bridge_status:needs_manual_auth" in lowered
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
    # Le fichier de sortie contient des données financières. On l'isole dans un
    # dossier privé (mkdtemp = 0700, propriété du process Django) plutôt que dans
    # le /tmp partagé, et on le détruit sur toutes les sorties (voir _cleanup_tmp).
    tmp_dir = tempfile.mkdtemp(prefix="tr_bridge_")
    out_file = os.path.join(tmp_dir, "out.json")

    def _cleanup_tmp() -> None:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    command = [
        _bun_bin(),
        "run",
        script_path,
        phone_number,
        pin,
        "--json",
        "--out-file",
        out_file,
    ]
    tx_limit_setting = getattr(settings, "TR_BRIDGE_TX_LIMIT", None)
    # Important: on veut que TR_BRIDGE_TX_LIMIT=0 signifie "illimité".
    # Donc on ne doit pas utiliser un `... or 80` qui écraserait 0.
    tx_limit = 80 if tx_limit_setting is None else int(tx_limit_setting)
    max_pages_setting = getattr(settings, "TR_BRIDGE_MAX_PAGES", None)
    # Important: on veut que TR_BRIDGE_MAX_PAGES=0 signifie "illimité".
    max_pages = 5000 if max_pages_setting is None else int(max_pages_setting)

    logger.info(
        "tr_bridge_client launch_settings tx_limit_setting=%s computed_tx_limit=%s max_pages_setting=%s computed_max_pages=%s",
        tx_limit_setting,
        tx_limit,
        max_pages_setting,
        max_pages,
    )

    command.extend(["--tx-limit", str(tx_limit), "--max-pages", str(max_pages)])
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
        _cleanup_tmp()
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
        _cleanup_tmp()
        if _is_manual_auth_hint(partial):
            raise TradeRepublicBridgeAuthRequired("auth_required") from exc
        raise TradeRepublicBridgeError(f"bridge_timeout: {exc}") from exc
    except OSError as exc:
        _cleanup_tmp()
        raise TradeRepublicBridgeError(f"bridge_unreachable: {exc}") from exc
    finally:
        if lock_acquired:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()

    def _is_bridge_payload(candidate) -> bool:
        return (
            isinstance(candidate, dict)
            and isinstance(candidate.get("accounts"), list)
            and isinstance(candidate.get("global"), dict)
        )

    def _extract_bridge_status_marker(raw_stdout: str) -> str | None:
        for line in raw_stdout.splitlines():
            line = line.strip()
            if line.startswith("TR_BRIDGE_STATUS:"):
                return line.split("TR_BRIDGE_STATUS:", 1)[1].strip()
        return None

    bridge_status = _extract_bridge_status_marker(stdout or "")

    payload = None
    raw_file = ""
    try:
        # Objectif: si 2FA est requis, inutile de parser le JSON du fichier.
        if bridge_status != "needs_manual_auth" and os.path.exists(out_file):
            with open(out_file, "r", encoding="utf-8") as f:
                raw_file = f.read()
            if raw_file.strip():
                candidate = json.loads(raw_file)
                if _is_bridge_payload(candidate) or (
                    isinstance(candidate, dict)
                    and candidate.get("status") in {"needs_manual_auth", "rate_limited"}
                ):
                    payload = candidate
    finally:
        # Détruit le dossier privé (out.json + éventuel .tmp_<pid> laissé par Bun).
        _cleanup_tmp()

    if bridge_status == "needs_manual_auth" or (
        isinstance(payload, dict) and payload.get("status") == "needs_manual_auth"
    ):
        raise TradeRepublicBridgeAuthRequired("auth_required")

    if bridge_status == "rate_limited" or (
        isinstance(payload, dict) and payload.get("status") == "rate_limited"
    ):
        retry = payload.get("retry_after_seconds") if isinstance(payload, dict) else None
        raise TradeRepublicBridgeError(
            f"rate_limited: trop de tentatives, réessaie dans {retry}s" if retry else "rate_limited: trop de tentatives"
        )

    if proc.returncode != 0:
        if _is_manual_auth_hint(f"{stdout}\n{stderr}"):
            raise TradeRepublicBridgeAuthRequired("auth_required")
        raise TradeRepublicBridgeError(
            f"bridge_error status={proc.returncode} stderr={stderr or stdout}"
        )

    if not payload:
        max_chars = int(getattr(settings, "TR_BRIDGE_RAW_OUTPUT_MAX_CHARS", 20000) or 20000)
        raw_output = (raw_file or stdout or stderr or "").strip()
        if len(raw_output) > max_chars:
            raw_output = raw_output[:max_chars] + "\n...[truncated]"
        raise TradeRepublicBridgeError(
            "bridge_invalid_json_outfile_output:\n"
            f"{raw_output or '<empty output>'}"
        )

    return payload


def fetch_tr_auth_status(*, phone_number: str | None = None, pin: str | None = None) -> dict:
    # En mode script local, le statut est déduit du dernier essai.
    # Cette méthode reste compatible avec le contrat attendu côté Django.
    return {"status": "authenticated", "reason": "local_script_mode"}


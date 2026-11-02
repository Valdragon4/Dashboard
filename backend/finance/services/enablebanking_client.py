"""
Client minimal de l'API Enable Banking (agrégateur DSP2, lecture seule).

Pourquoi cet agrégateur : l'espace client de certaines banques (Hello Bank) est
protégé par un anti-robot qui refuse tout navigateur automatisé ; la DSP2 donne
un accès officiel en lecture, sans stocker le mot de passe bancaire.

Authentification : chaque requête porte un JWT RS256 signé par la clé privée de
l'application (`kid` = identifiant de l'application). La clé ne quitte jamais le
serveur ; son chemin est donné par ENABLEBANKING_KEY_PATH.

Flux d'accès à une banque :
  1. start_authorization() -> URL de la banque, où l'utilisateur valide (SCA) ;
  2. la banque renvoie vers notre callback avec `code` ;
  3. create_session(code) -> session_id + comptes (valable ~90 à 180 jours).
"""

from __future__ import annotations

import base64
import json
import time
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from typing import Dict, List, Optional

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from django.conf import settings

API_URL = "https://api.enablebanking.com"
JWT_TTL_SECONDS = 3600


class EnableBankingError(Exception):
    """Erreur générique de l'API Enable Banking."""

    def __init__(self, message: str, status: Optional[int] = None, payload: Optional[dict] = None):
        super().__init__(message)
        self.status = status
        self.payload = payload or {}


class EnableBankingSessionExpired(EnableBankingError):
    """Consentement expiré ou révoqué : l'utilisateur doit le renouveler."""


class EnableBankingRateLimited(EnableBankingError):
    """Quota d'accès de la banque atteint (DSP2 : ~4 accès non sollicités par jour)."""


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class EnableBankingClient:
    def __init__(
        self,
        app_id: Optional[str] = None,
        key_path: Optional[str] = None,
        timeout: int = 60,
        session: Optional[requests.Session] = None,
    ):
        self.app_id = app_id or getattr(settings, "ENABLEBANKING_APP_ID", "")
        self.key_path = key_path or getattr(settings, "ENABLEBANKING_KEY_PATH", "")
        if not self.app_id or not self.key_path:
            raise EnableBankingError(
                "Enable Banking non configuré (ENABLEBANKING_APP_ID / ENABLEBANKING_KEY_PATH)."
            )
        self.timeout = timeout
        self.http = session or requests.Session()
        self._token: Optional[str] = None
        self._token_exp = 0

    # ------------------------------------------------------------------
    # JWT
    # ------------------------------------------------------------------

    def _load_key(self):
        pem = Path(self.key_path).read_bytes()
        return serialization.load_pem_private_key(pem, password=None)

    def _jwt(self) -> str:
        now = int(time.time())
        if self._token and now < self._token_exp - 60:
            return self._token
        header = {"typ": "JWT", "alg": "RS256", "kid": self.app_id}
        body = {
            "iss": "enablebanking.com",
            "aud": "api.enablebanking.com",
            "iat": now,
            "exp": now + JWT_TTL_SECONDS,
        }
        signing_input = (
            _b64url(json.dumps(header, separators=(",", ":")).encode())
            + "."
            + _b64url(json.dumps(body, separators=(",", ":")).encode())
        )
        signature = self._load_key().sign(
            signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256()
        )
        self._token = signing_input + "." + _b64url(signature)
        self._token_exp = now + JWT_TTL_SECONDS
        return self._token

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs) -> dict:
        headers = {"Authorization": f"Bearer {self._jwt()}"}
        try:
            resp = self.http.request(
                method, API_URL + path, headers=headers, timeout=self.timeout, **kwargs
            )
        except requests.Timeout as e:
            raise EnableBankingError(f"Timeout Enable Banking ({path})") from e
        except requests.RequestException as e:
            raise EnableBankingError(f"Erreur réseau Enable Banking ({path}): {e}") from e

        try:
            payload = resp.json() if resp.content else {}
        except ValueError:
            payload = {"raw": resp.text[:300]}

        if resp.status_code < 400:
            return payload

        message = str(payload.get("message") or payload.get("error") or payload)[:300]
        code = str(payload.get("error") or "").upper()
        if resp.status_code == 429 or "LIMIT" in code:
            raise EnableBankingRateLimited(message, resp.status_code, payload)
        if resp.status_code in (401, 403) or "EXPIRED" in code or ("SESSION" in code and "INVALID" in code):
            raise EnableBankingSessionExpired(message, resp.status_code, payload)
        raise EnableBankingError(
            f"Enable Banking {resp.status_code} sur {path}: {message}", resp.status_code, payload
        )

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------

    def list_aspsps(self, country: str = "FR", psu_type: Optional[str] = None) -> List[Dict]:
        params = {"country": country, "service": "AIS"}
        if psu_type:
            params["psu_type"] = psu_type
        return self._request("GET", "/aspsps", params=params).get("aspsps", [])

    def start_authorization(
        self,
        aspsp_name: str,
        country: str,
        redirect_url: str,
        state: str,
        psu_type: str = "personal",
        valid_days: int = 180,
    ) -> Dict:
        """Retourne {"url": ..., "authorization_id": ...}."""
        valid_until = datetime.now(dt_timezone.utc) + timedelta(days=valid_days)
        body = {
            "access": {"valid_until": valid_until.strftime("%Y-%m-%dT%H:%M:%SZ")},
            "aspsp": {"name": aspsp_name, "country": country},
            "state": state,
            "redirect_url": redirect_url,
            "psu_type": psu_type,
        }
        return self._request("POST", "/auth", json=body)

    def create_session(self, code: str) -> Dict:
        return self._request("POST", "/sessions", json={"code": code})

    def get_session(self, session_id: str) -> Dict:
        return self._request("GET", f"/sessions/{session_id}")

    def get_account_details(self, account_uid: str) -> Dict:
        return self._request("GET", f"/accounts/{account_uid}/details")

    def get_balances(self, account_uid: str) -> List[Dict]:
        return self._request("GET", f"/accounts/{account_uid}/balances").get("balances", [])

    def get_transactions(
        self, account_uid: str, date_from: Optional[str] = None, max_pages: int = 50
    ) -> List[Dict]:
        """Toutes les transactions depuis `date_from` (AAAA-MM-JJ), pagination comprise."""
        out: List[Dict] = []
        continuation_key = None
        for _ in range(max_pages):
            params = {}
            if date_from:
                params["date_from"] = date_from
            if continuation_key:
                params["continuation_key"] = continuation_key
            data = self._request("GET", f"/accounts/{account_uid}/transactions", params=params)
            out.extend(data.get("transactions") or [])
            continuation_key = data.get("continuation_key")
            if not continuation_key:
                break
        return out

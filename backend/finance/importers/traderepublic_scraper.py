from __future__ import annotations

import os
import json
import logging
import re
import asyncio
import base64
import hashlib
import uuid
import configparser
import websockets
import requests
import pandas as pd

try:
    from curl_cffi.requests import Session as _CurlCffiSession
except ImportError:
    _CurlCffiSession = None  # type: ignore[misc, assignment]
from pathlib import Path
from typing import Optional
from decimal import Decimal

from django.conf import settings

logger = logging.getLogger(__name__)

# Aligné sur le client web officiel (évite certains 403 « navigateur » / CORS côté WAF)
_TR_APP_ORIGIN = "https://app.traderepublic.com"
_TR_LOGIN_URL = f"{_TR_APP_ORIGIN}/login"

# Aligné sur https://github.com/BenjaminOddou/trade_republic_scraper (AWS WAF + app mobile web)
_TR_APP_VERSION = "13.40.5"


def generate_tr_device_info() -> str:
    """Device info Base64 attendu par l’API (stableDeviceId), comme le script upstream."""
    device_id = hashlib.sha512(uuid.uuid4().bytes).hexdigest()
    payload = {"stableDeviceId": device_id}
    return base64.b64encode(json.dumps(payload).encode()).decode()


def tr_api_extra_headers(waf_token: str, device_info_b64: str) -> dict[str, str]:
    """En-têtes requis par l’API derrière AWS WAF (voir trade_republic_scraper main.py)."""
    h: dict[str, str] = {
        "Accept": "*/*",
        "Accept-Language": "fr",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "x-tr-app-version": _TR_APP_VERSION,
        "x-tr-device-info": device_info_b64,
        "x-tr-platform": "web",
    }
    if waf_token:
        h["x-aws-waf-token"] = waf_token
    return h


def tr_merged_auth_headers(waf_token: str, device_info_b64: str) -> dict[str, str]:
    """Navigateur + en-têtes Trade Republic / WAF."""
    return {**tr_browser_headers(), **tr_api_extra_headers(waf_token, device_info_b64)}


def tr_countdown_from_login_payload(data: dict) -> Optional[int]:
    """L’API a renvoyé countdownSeconds ou countdownInSeconds selon les versions."""
    if not data:
        return None
    v = data.get("countdownSeconds")
    if v is None:
        v = data.get("countdownInSeconds")
    return v


def tr_browser_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "fr-FR,fr;q=0.9,de;q=0.8,en;q=0.7",
        "Origin": _TR_APP_ORIGIN,
        "Referer": _TR_LOGIN_URL,
        'Sec-Ch-Ua': '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"macOS"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
    }


def create_tr_requests_session():
    """
    Session HTTP pour l’API Trade Republic.

    Utilise curl_cffi (empreinte TLS proche de Chrome) si disponible : ``requests`` seul
    est souvent identifié comme client « script » (JA3 différent de Chrome), ce qui peut
    provoquer un 403 même avec de bons en-têtes HTTP — y compris depuis un réseau résidentiel.
    Retombe sur ``requests`` si curl_cffi n’est pas installé.
    """
    if _CurlCffiSession is not None:
        s = _CurlCffiSession(impersonate="chrome131")
        s.headers.update(tr_browser_headers())
        return s
    s = requests.Session()
    s.headers.update(tr_browser_headers())
    return s


def tr_warmup_login_page(session) -> None:
    """Charge la page de login (cookies / contexte) avant l’appel API."""
    try:
        session.get(_TR_LOGIN_URL, timeout=15.0, allow_redirects=True)
    except Exception:
        pass


def traderepublic_error_message_for_status_and_text(status: int, text: str) -> str:
    """Message utilisateur pour une réponse API non-200 (souvent HTML pare-feu sur 403)."""
    low = (text or "").lower()
    if status == 403 and ("<html" in low or "forbidden" in low or "cloudflare" in low):
        return (
            "Accès refusé (403, pare-feu / WAF). Causes fréquentes : IP datacenter ou mal notée ; "
            "ou filtrage des requêtes qui ne viennent pas du navigateur officiel. "
            "L’import tente d’abord Playwright (Chromium), puis curl_cffi. Si le 403 persiste "
            "alors que app.traderepublic.com s’ouvre dans votre navigateur : essayez import CSV/PDF "
            "ou lancez le conteneur web sur la même machine que votre navigateur. "
            "Vérifiez aussi VPN / DNS / box (certaines IP sont listées)."
        )
    if status == 403:
        return (
            "Accès refusé (403). Essayez import CSV/PDF, ou une autre connexion réseau "
            "(4G, autre box) si le blocage persiste."
        )
    try:
        err = json.loads(text)
        if isinstance(err, dict):
            return str(err.get("message") or err.get("error") or err)
        return str(err)
    except json.JSONDecodeError:
        snippet = (text or "").strip()[:400]
        return snippet or f"HTTP {status}"


def traderepublic_error_message_for_failed_response(response) -> str:
    return traderepublic_error_message_for_status_and_text(
        response.status_code, response.text or ""
    )


def export_cookies_from_requests_session(session) -> list[dict]:
    """Sérialise les cookies d’une session requests/curl_cffi pour la session Django."""
    out: list[dict] = []
    jar = session.cookies
    for item in jar:
        # curl_cffi peut itérer sur les noms (str) au lieu d’objets http.cookiejar.Cookie
        if isinstance(item, str):
            val = jar.get(item)
            out.append(
                {
                    "name": item,
                    "value": val if val is not None else "",
                    "domain": "",
                    "path": "/",
                }
            )
            continue
        if not hasattr(item, "name"):
            continue
        out.append(
            {
                "name": item.name,
                "value": getattr(item, "value", "") or "",
                "domain": getattr(item, "domain", None) or "",
                "path": getattr(item, "path", None) or "/",
            }
        )
    return out


def apply_cookies_to_http_session(session, cookies_list: list | None) -> None:
    """Réinjecte des cookies (format Playwright ou export interne) dans la session HTTP."""
    if not cookies_list:
        return
    for c in cookies_list:
        if not isinstance(c, dict):
            continue
        name = c.get("name")
        value = c.get("value")
        if name is None or value is None:
            continue
        domain = (c.get("domain") or "").strip().lstrip(".")
        path = c.get("path") or "/"
        # curl_cffi : domain=None lève AttributeError sur domain.startswith(".")
        session.cookies.set(name, value, domain=domain or "", path=path)


def headers_to_dict(response):
    """Transforme les en-têtes de réponse HTTP en dictionnaire structuré."""
    extracted_headers = {}
    for header, header_value in response.headers.items():
        parsed_dict = {}
        entries = header_value.split(", ")
        for entry in entries:
            key_value = entry.split(";")[0]
            if "=" in key_value:
                key, value = key_value.split("=", 1)
                parsed_dict[key.strip()] = value.strip()
        extracted_headers[header] = parsed_dict if parsed_dict else header_value
    return extracted_headers


async def connect_to_websocket():
    """Établit une connexion WebSocket à l'API de TradeRepublic."""
    # Ajouter un timeout de 10 secondes pour la connexion
    websocket = await asyncio.wait_for(
        websockets.connect("wss://api.traderepublic.com"),
        timeout=10.0
    )
    locale_config = {
        "locale": "fr",
        "platformId": "webtrading",
        "platformVersion": "safari - 18.3.0",
        "clientId": "app.traderepublic.com",
        "clientVersion": "3.151.3",
    }
    await websocket.send(f"connect 31 {json.dumps(locale_config)}")
    # Ajouter un timeout de 5 secondes pour la réponse de connexion
    try:
        await asyncio.wait_for(websocket.recv(), timeout=5.0)
    except asyncio.TimeoutError:
        await websocket.close()
        raise TimeoutError("Timeout lors de la connexion WebSocket")
    return websocket


async def fetch_transaction_details(websocket, transaction_id, token, message_id):
    """Récupère les détails d'une transaction spécifique via WebSocket."""
    payload = {"type": "timelineDetailV2", "id": transaction_id, "token": token}
    message_id += 1
    await websocket.send(f"sub {message_id} {json.dumps(payload)}")
    # Ajouter un timeout de 10 secondes
    try:
        response = await asyncio.wait_for(websocket.recv(), timeout=10.0)
    except asyncio.TimeoutError:
        return {}, message_id
    await websocket.send(f"unsub {message_id}")
    try:
        await asyncio.wait_for(websocket.recv(), timeout=5.0)
    except asyncio.TimeoutError:
        pass  # Ignorer le timeout sur la réponse unsub

    start_index = response.find("{")
    end_index = response.rfind("}")
    try:
        response_data = json.loads(
            response[start_index : end_index + 1]
            if start_index != -1 and end_index != -1
            else "{}"
        )
    except json.JSONDecodeError:
        return {}, message_id

    transaction_data = {}
    for section in response_data.get("sections", []):
        if section.get("title") == "Transaction":
            for item in section.get("data", []):
                header = item.get("title")
                value = item.get("detail", {}).get("text")
                if header and value:
                    transaction_data[header] = value

    return transaction_data, message_id


async def fetch_all_transactions(token, extract_details=False):
    """Récupère toutes les transactions via WebSocket."""
    all_data = []
    message_id = 0
    max_iterations = 1000  # Limite de sécurité pour éviter les boucles infinies
    iteration_count = 0

    websocket = await connect_to_websocket()
    try:
        after_cursor = None
        while iteration_count < max_iterations:
            iteration_count += 1
            
            payload = {"type": "timelineTransactions", "token": token}
            if after_cursor:
                payload["after"] = after_cursor

            message_id += 1
            await websocket.send(f"sub {message_id} {json.dumps(payload)}")
            # Ajouter un timeout de 30 secondes par requête
            try:
                response = await asyncio.wait_for(websocket.recv(), timeout=30.0)
            except asyncio.TimeoutError:
                # Si le timeout est dépassé, on arrête la boucle
                break
            await websocket.send(f"unsub {message_id}")
            try:
                await asyncio.wait_for(websocket.recv(), timeout=5.0)
            except asyncio.TimeoutError:
                pass  # Ignorer le timeout sur la réponse unsub
            
            start_index = response.find("{")
            end_index = response.rfind("}")
            response = (
                response[start_index : end_index + 1]
                if start_index != -1 and end_index != -1
                else "{}"
            )
            
            try:
                data = json.loads(response)
            except json.JSONDecodeError:
                # Si le parsing JSON échoue, on arrête la boucle
                break

            items = data.get("items", [])
            if not items:
                break

            if extract_details:
                for transaction in items:
                    transaction_id = transaction.get("id")
                    if transaction_id:
                        try:
                            details, message_id = await fetch_transaction_details(
                                websocket, transaction_id, token, message_id
                            )
                            transaction.update(details)
                        except Exception:
                            # Si la récupération des détails échoue, on continue quand même
                            pass
                    all_data.append(transaction)
            else:
                all_data.extend(items)

            # Vérifier si on a un cursor pour la pagination suivante
            after_cursor = data.get("cursors", {}).get("after")
            if not after_cursor:
                break
            
            # Vérifier si on a déjà récupéré toutes les transactions
            # (si le cursor est le même que précédemment, on arrête)
            if after_cursor == payload.get("after"):
                break
    finally:
        if websocket:
            await websocket.close()

    return all_data


async def fetch_available_cash(token):
    """Récupère les liquidités disponibles du compte Trade Republic."""
    websocket = await connect_to_websocket()
    try:
        payload = {"type": "availableCash", "token": token}
        await websocket.send(f"sub 1 {json.dumps(payload)}")
        # Ajouter un timeout de 10 secondes
        try:
            response = await asyncio.wait_for(websocket.recv(), timeout=10.0)
        except asyncio.TimeoutError:
            return []
        await websocket.send(f"unsub 1")
        try:
            await asyncio.wait_for(websocket.recv(), timeout=5.0)
        except asyncio.TimeoutError:
            pass  # Ignorer le timeout sur la réponse unsub

        start_index = response.find("[")
        end_index = response.rfind("]")
        try:
            response_data = json.loads(
                response[start_index : end_index + 1]
                if start_index != -1 and end_index != -1
                else "[]"
            )
            return response_data
        except json.JSONDecodeError:
            return []
    finally:
        if websocket:
            await websocket.close()


async def fetch_portfolio(token):
    """Récupère le portefeuille (CTO/PEA) du compte Trade Republic."""
    websocket = await connect_to_websocket()
    try:
        # Essayer différents types de requêtes pour récupérer le portefeuille
        # Limiter à 2 tentatives pour éviter les timeouts
        portfolio_types = ["portfolio", "account"]
        portfolio_data = {}
        
        for portfolio_type in portfolio_types:
            try:
                payload = {"type": portfolio_type, "token": token}
                await websocket.send(f"sub 1 {json.dumps(payload)}")
                # Ajouter un timeout de 10 secondes par requête
                response = await asyncio.wait_for(websocket.recv(), timeout=10.0)
                await websocket.send(f"unsub 1")
                await asyncio.wait_for(websocket.recv(), timeout=5.0)
                
                # Chercher un objet JSON ou un tableau
                start_index = response.find("{")
                end_index = response.rfind("}")
                if start_index == -1 or end_index == -1:
                    start_index = response.find("[")
                    end_index = response.rfind("]")
                
                if start_index != -1 and end_index != -1:
                    try:
                        response_data = json.loads(response[start_index : end_index + 1])
                        if response_data:
                            portfolio_data[portfolio_type] = response_data
                            break  # Si on trouve des données, on s'arrête
                    except json.JSONDecodeError:
                        # Si le parsing JSON échoue, on essaie la requête suivante
                        continue
            except asyncio.TimeoutError:
                # Si le timeout est dépassé, on passe à la requête suivante
                continue
            except Exception:
                # Si cette requête échoue, on essaie la suivante
                continue
        
        return portfolio_data
    finally:
        if websocket:
            await websocket.close()


def flatten_and_clean_json(all_data, sep="."):
    """Aplatit des données JSON imbriquées et préserve l'ordre des colonnes."""
    all_keys = []
    flattened_data = []

    def flatten(nested_json, parent_key=""):
        flat_dict = {}
        for key, value in nested_json.items():
            new_key = f"{parent_key}{sep}{key}" if parent_key else key
            if isinstance(value, dict):
                flat_dict.update(flatten(value, new_key))
            else:
                flat_dict[new_key] = value
            if new_key not in all_keys:
                all_keys.append(new_key)
        return flat_dict

    for item in all_data:
        flat_item = flatten(item)
        flattened_data.append(flat_item)

    complete_data = [
        {key: item.get(key, None) for key in all_keys} for item in flattened_data
    ]
    return complete_data


def save_transactions_to_csv(all_data, output_path: Path):
    """Sauvegarde les transactions dans un fichier CSV."""
    import logging
    logger = logging.getLogger(__name__)
    
    flattened_data = flatten_and_clean_json(all_data)
    if not flattened_data:
        logger.warning("⚠️ Aucune donnée après aplatissement JSON")
        return

    df = pd.DataFrame(flattened_data)
    df = df.dropna(axis=1, how="all")
        
    # Convertir les colonnes de date au format DD/MM/YYYY
    timestamp_columns = ["timestamp"]
    for col in timestamp_columns:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce").dt.strftime("%d/%m/%Y")
    
    df.to_csv(output_path, index=False, sep=";", encoding="utf-8-sig")
    logger.info(f"✅ CSV sauvegardé: {output_path} ({len(all_data)} transactions → {len(df)} lignes CSV)")


def normalize_phone_number_for_tr(raw: str) -> str:
    """
    Convertit un numéro saisi (ex. français 06 XX XX XX XX) au format E.164 attendu par l'API TR (+33...).
    """
    if not raw or not str(raw).strip():
        raise ValueError("Numéro de téléphone vide.")
    s = re.sub(r"[\s\-\.]", "", str(raw).strip())
    if s.startswith("00"):
        s = "+" + s[2:]
    if s.startswith("+"):
        return s
    # France : 0X XX XX XX XX (10 chiffres)
    if s.startswith("0") and len(s) == 10 and s.isdigit():
        return "+33" + s[1:]
    # France : 9 chiffres sans le 0 initial
    if len(s) == 9 and s.isdigit() and s[0] in "123456789":
        return "+33" + s
    # 33XXXXXXXXX sans préfixe +
    if s.startswith("33") and len(s) >= 11 and s.isdigit():
        return "+" + s
    return s


class TradeRepublicScraper:
    """Classe pour gérer le scraping Trade Republic depuis Django."""

    def __init__(
        self,
        phone_number: str,
        pin: str,
        api_cookies: list | None = None,
        waf_token: str = "",
        device_info: str = "",
    ):
        self.phone_number = normalize_phone_number_for_tr(phone_number)
        self.pin = pin
        self.process_id: Optional[str] = None
        self.countdown: Optional[int] = None
        self._api_cookies: list[dict] = list(api_cookies or [])
        self._waf_token: str = waf_token or ""
        self._device_info: str = device_info or ""
        self._http = create_tr_requests_session()
        apply_cookies_to_http_session(self._http, self._api_cookies)
        self._apply_tr_auth_headers()

    def _apply_tr_auth_headers(self) -> None:
        """Applique les en-têtes WAF / device (repo BenjaminOddou) sur la session HTTP."""
        dev = self._device_info or generate_tr_device_info()
        if not self._device_info:
            self._device_info = dev
        merged = tr_merged_auth_headers(self._waf_token, dev)
        self._http.headers.update(merged)

    def export_api_cookies_for_session(self) -> list[dict]:
        """Cookies à stocker en session Django après une étape API."""
        return export_cookies_from_requests_session(self._http)

    def initiate_login(self) -> dict:
        """Initie la connexion et retourne le process_id et countdown."""
        from django.conf import settings

        use_pw = getattr(settings, "TRADEPUBLIC_USE_PLAYWRIGHT_INITIATE", True)
        if use_pw:
            try:
                from .traderepublic_playwright import initiate_login_playwright

                data, cookies, waf_token, device_info = initiate_login_playwright(
                    self.phone_number, self.pin
                )
                self._api_cookies = cookies
                self._waf_token = waf_token
                self._device_info = device_info
                apply_cookies_to_http_session(self._http, self._api_cookies)
                self._apply_tr_auth_headers()
                self.process_id = data["process_id"]
                self.countdown = data.get("countdown")
                return {"process_id": self.process_id, "countdown": self.countdown}
            except ValueError:
                raise
            except Exception as e:
                logger.warning(
                    "Trade Republic: login Playwright indisponible ou en échec (%s), fallback HTTP.",
                    e,
                )
        return self._initiate_login_http()

    def _initiate_login_http(self) -> dict:
        """Fallback : curl_cffi / requests ; tente d’obtenir le jeton WAF via Playwright."""
        if not self._waf_token:
            try:
                from .traderepublic_playwright import fetch_tr_waf_context_playwright

                waf_token, extra_cookies = fetch_tr_waf_context_playwright()
                self._waf_token = waf_token
                if extra_cookies:
                    apply_cookies_to_http_session(self._http, extra_cookies)
            except Exception as e:
                logger.debug("Trade Republic: récupération WAF optionnelle ignorée: %s", e)
        if not self._device_info:
            self._device_info = generate_tr_device_info()
        self._apply_tr_auth_headers()

        tr_warmup_login_page(self._http)
        response = self._http.post(
            "https://api.traderepublic.com/api/v1/auth/web/login",
            json={"phoneNumber": self.phone_number, "pin": self.pin},
            timeout=30.0,
        )

        if response.status_code != 200:
            msg = traderepublic_error_message_for_failed_response(response)
            raise ValueError(
                f"Échec de la connexion Trade Republic ({response.status_code}): {msg}"
            )

        if not (response.content or b"").strip():
            raise ValueError(
                "Réponse vide de l'API Trade Republic. Utilisez le format international "
                "(ex. +33 6 12 34 56 78) et vérifiez votre PIN."
            )

        try:
            data = response.json()
        except json.JSONDecodeError as e:
            raise ValueError(
                "Réponse API illisible (corps non JSON). Vérifiez le numéro au format +33… "
                "et que le serveur peut joindre api.traderepublic.com."
            ) from e

        self.process_id = data.get("processId")
        self.countdown = tr_countdown_from_login_payload(data)

        if not self.process_id:
            raise ValueError("Échec de l'initialisation de la connexion. Vérifiez votre numéro de téléphone et PIN.")

        self._api_cookies = export_cookies_from_requests_session(self._http)
        return {
            "process_id": self.process_id,
            "countdown": self.countdown,
        }

    def resend_2fa(self) -> None:
        """Renvoyer le code 2FA par SMS."""
        if not self.process_id:
            raise ValueError("Process ID manquant. Initiez d'abord la connexion.")

        apply_cookies_to_http_session(self._http, self._api_cookies)
        self._apply_tr_auth_headers()
        self._http.post(
            f"https://api.traderepublic.com/api/v1/auth/web/login/{self.process_id}/resend",
            timeout=30.0,
        )

    def verify_2fa(self, code: str) -> str:
        """Vérifie le code 2FA et retourne le token de session."""
        if not self.process_id:
            raise ValueError("Process ID manquant. Initiez d'abord la connexion.")
        
        if not code or not code.strip():
            raise ValueError("Code 2FA vide.")
        
        # Nettoyer le code (enlever les espaces)
        code = code.strip()

        apply_cookies_to_http_session(self._http, self._api_cookies)
        self._apply_tr_auth_headers()
        response = self._http.post(
            f"https://api.traderepublic.com/api/v1/auth/web/login/{self.process_id}/{code}",
            timeout=60.0,
        )
        
        if response.status_code != 200:
            error_msg = "Échec de la vérification. Vérifiez le code et réessayez."
            try:
                error_data = response.json()
                if "message" in error_data:
                    error_msg = error_data["message"]
            except:
                pass
            raise ValueError(f"{error_msg} (Status: {response.status_code})")
        
        # Extraire le token depuis les cookies
        session_token = response.cookies.get("tr_session")
        if not session_token:
            # Essayer avec headers_to_dict en fallback
            response_headers = headers_to_dict(response)
            session_token = response_headers.get("Set-Cookie", {}).get("tr_session")
        
        if not session_token:
            raise ValueError("Token de connexion introuvable dans les cookies.")
        
        return session_token

    def scrape_transactions(self, token: str, extract_details: bool = False) -> list:
        """Scrape toutes les transactions et retourne les données."""
        return asyncio.run(fetch_all_transactions(token, extract_details))

    def scrape_and_save(self, token: str, output_path: Path, extract_details: bool = False) -> None:
        """Scrape les transactions et les sauvegarde dans un fichier CSV."""
        import logging
        logger = logging.getLogger(__name__)
        
        logger.info("=" * 100)
        logger.info("🚀 DÉBUT DU SCRAPING TRADE REPUBLIC")
        logger.info("=" * 100)
        
        all_data = self.scrape_transactions(token, extract_details)
        
        logger.info("=" * 100)
        logger.info(f"📦 DONNÉES BRUTES COMPLÈTES REÇUES DU WEBSOCKET - Total: {len(all_data)} transactions")
        logger.info("=" * 100)
        
        # Afficher TOUTES les transactions en JSON brut
        for idx, transaction in enumerate(all_data, 1):
            logger.info(f"\n{'='*100}")
            logger.info(f"🔸 TRANSACTION BRUTE #{idx}/{len(all_data)}")
            logger.info(f"{'='*100}")
            logger.info(json.dumps(transaction, indent=2, ensure_ascii=False))
            logger.info(f"{'='*100}\n")
        
        logger.info("=" * 100)
        logger.info("💾 DÉBUT DE LA CONVERSION EN CSV")
        logger.info("=" * 100)
        
        save_transactions_to_csv(all_data, output_path)

    def get_available_cash(self, token: str) -> dict:
        """Récupère les liquidités disponibles du compte Trade Republic."""
        cash_data = asyncio.run(fetch_available_cash(token))
        # Le format est une liste, on prend le premier élément s'il existe
        if cash_data and isinstance(cash_data, list) and len(cash_data) > 0:
            return cash_data[0]
        return {}

    def get_portfolio(self, token: str) -> dict:
        """Récupère le portefeuille (CTO/PEA) du compte Trade Republic."""
        portfolio_data = asyncio.run(fetch_portfolio(token))
        # Retourner les données du portefeuille si disponibles
        if portfolio_data:
            # Prendre la première clé (le type de requête qui a fonctionné)
            first_key = next(iter(portfolio_data), None)
            if first_key:
                return portfolio_data[first_key]
        return {}

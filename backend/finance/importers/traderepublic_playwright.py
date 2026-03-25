"""
Chromium + jeton AWS WAF, aligné sur le script amont
https://github.com/BenjaminOddou/trade_republic_scraper (main.py).

Sans visiter app.traderepublic.com et sans en-têtes x-aws-waf-token / x-tr-device-info,
l’API peut répondre 403 même si le site web s’affiche dans un navigateur.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

from finance.importers.traderepublic_scraper import (
    generate_tr_device_info,
    traderepublic_error_message_for_status_and_text,
    tr_countdown_from_login_payload,
    tr_merged_auth_headers,
)

_TR_APP_ROOT = "https://app.traderepublic.com/"
_TR_LOGIN_API = "https://api.traderepublic.com/api/v1/auth/web/login"


def _extract_waf_token_from_cookies(cookies: list[dict[str, Any]]) -> str:
    for c in cookies:
        name = (c.get("name") or "").lower()
        if "aws-waf-token" in name:
            return str(c.get("value") or "")
    return ""


def _extract_waf_token_from_page(page) -> str:
    try:
        tok = page.evaluate(
            "() => (window.AWSWafIntegration && window.AWSWafIntegration.getToken "
            "&& window.AWSWafIntegration.getToken()) || ''"
        )
        return str(tok or "")
    except Exception:
        return ""


def fetch_tr_waf_context_playwright() -> tuple[str, list[dict[str, Any]]]:
    """
    Ouvre la page d’accueil (comme Selenium dans le repo amont), attend le cookie WAF.
    Retourne (waf_token, cookies du contexte).
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError("Playwright n'est pas disponible.") from e

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        try:
            context = browser.new_context(
                locale="fr-FR",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 720},
            )
            page = context.new_page()
            page.goto(_TR_APP_ROOT, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)

            cookies = context.cookies()
            waf = _extract_waf_token_from_cookies(cookies) or _extract_waf_token_from_page(page)
            return waf, cookies
        finally:
            browser.close()


def initiate_login_playwright(
    phone_e164: str, pin: str
) -> tuple[dict, list[dict[str, Any]], str, str]:
    """
    Retourne (payload login, cookies navigateur, waf_token, device_info_b64).

    APIRequestContext.post n’accepte pas le paramètre json= (Playwright Python) :
    on envoie data=json.dumps(...) + Content-Type.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError(
            "Playwright n'est pas disponible. Installez les dépendances et exécutez playwright install chromium."
        ) from e

    device_info = generate_tr_device_info()
    body_obj = {"phoneNumber": phone_e164, "pin": pin}
    body_bytes = json.dumps(body_obj)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        try:
            context = browser.new_context(
                locale="fr-FR",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 720},
            )
            page = context.new_page()
            page.goto(_TR_APP_ROOT, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)

            cookies = context.cookies()
            waf = _extract_waf_token_from_cookies(cookies) or _extract_waf_token_from_page(page)

            headers = tr_merged_auth_headers(waf, device_info)
            headers["Content-Type"] = "application/json"

            response = context.request.post(
                _TR_LOGIN_API,
                data=body_bytes,
                headers=headers,
            )
            status = response.status
            body_text = response.text()
            cookies = context.cookies()

            if status != 200:
                msg = traderepublic_error_message_for_status_and_text(status, body_text)
                raise ValueError(f"Échec de la connexion Trade Republic ({status}): {msg}")

            try:
                payload = response.json()
            except Exception:
                try:
                    payload = json.loads(body_text)
                except json.JSONDecodeError as err:
                    raise ValueError(
                        f"Réponse API illisible (non JSON): {(body_text or '')[:400]}"
                    ) from err

            process_id = payload.get("processId")
            if not process_id:
                raise ValueError(
                    "Échec de l'initialisation de la connexion. Vérifiez votre numéro de téléphone et PIN."
                )

            countdown = tr_countdown_from_login_payload(payload)
            return (
                {
                    "process_id": process_id,
                    "countdown": countdown,
                },
                cookies,
                waf,
                device_info,
            )
        finally:
            browser.close()

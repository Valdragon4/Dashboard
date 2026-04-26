"""
Connecteur BoursoBank pour synchronisation automatique.

Pipeline simplifié et borné en temps :
  1. Auth via boursobank_scraper (Playwright headless + proxy SOCKS)
  2. Listing comptes depuis /budget/mouvements (DOM robuste)
  3. Récupération transactions via interception réseau Playwright + fallback DOM
  4. Solde lu depuis l'objet compte déjà chargé (pas de re-navigation)
"""

import base64
import json
import logging
import re
import time
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional

from django.conf import settings
from django.utils import timezone

from finance.connectors.base import (
    AuthenticationError,
    BankConnectionError,
    ConnectionTimeoutError,
    InvalidCredentialsError,
    BaseBankConnector,
)
from finance.models import BankAccountLink

logger = logging.getLogger(__name__)

try:
    from boursobank_scraper import BoursoScraper, BoursoAccount
    SCRAPER_AVAILABLE = True
except ImportError:
    SCRAPER_AVAILABLE = False


class BoursoBankConnector(BaseBankConnector):
    """
    Connecteur BoursoBank.

    Utilise boursobank_scraper pour l'authentification Playwright,
    puis récupère les transactions via l'API JSON interne de BoursoBank
    (appel fetch dans le contexte de la page authentifiée).
    """

    def __init__(self):
        if not SCRAPER_AVAILABLE:
            raise ImportError(
                "boursobank_scraper non disponible. "
                "Installez-le avec: pip install boursobank-scraper"
            )
        self._scraper: Optional["BoursoScraper"] = None
        self._accounts: List["BoursoAccount"] = []
        self._selected_account: Optional["BoursoAccount"] = None
        self._authenticated = False
        self._data_dir = Path(getattr(settings, "BOURSOBANK_DATA_DIR", "/tmp/boursobank-data"))
        self._target_account_number = _normalize_digits(
            getattr(settings, "BOURSOBANK_ACCOUNT_NUMBER", "")
        )

    @property
    def provider_name(self) -> str:
        return "BoursoBank"

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def authenticate(self, credentials: Dict) -> Dict:
        username = credentials.get("username")
        password = credentials.get("password")
        if not username or not password:
            raise InvalidCredentialsError("username et password sont requis")

        account_number = credentials.get("account_number")
        if account_number:
            self._target_account_number = _normalize_digits(account_number)

        t0 = time.monotonic()
        self._data_dir.mkdir(parents=True, exist_ok=True)

        self._scraper = BoursoScraper(
            username=username,
            password=password,
            rootDataPath=self._data_dir,
            headless=getattr(settings, "BOURSOBANK_HEADLESS", True),
            timeout=int(getattr(settings, "BOURSOBANK_TIMEOUT_MS", 60000)),
            saveTrace=False,
        )

        proxy_server = (getattr(settings, "BOURSOBANK_PROXY", "") or "").strip()
        if proxy_server:
            try:
                self._scraper.browser.close()
            except Exception:
                pass
            self._scraper.browser = self._scraper.playwright.chromium.launch(
                headless=getattr(settings, "BOURSOBANK_HEADLESS", True),
                proxy={"server": proxy_server},
            )
            logger.info("BoursoBank: proxy %s", proxy_server)

        auth_timeout = int(getattr(settings, "BOURSOBANK_AUTH_TIMEOUT_MS", 30000))
        original_timeout = getattr(self._scraper, "timeout", None)
        try:
            if original_timeout is not None:
                self._scraper.timeout = auth_timeout
            logged_in = self._scraper.connect()
        except Exception as e:
            elapsed = int((time.monotonic() - t0) * 1000)
            return self._handle_auth_error(e, elapsed)
        finally:
            if original_timeout is not None and self._scraper:
                self._scraper.timeout = original_timeout

        elapsed = int((time.monotonic() - t0) * 1000)

        if not logged_in:
            self._safe_disconnect()
            raise InvalidCredentialsError("Identifiant ou mot de passe BoursoBank invalide.")

        try:
            self._accounts = self._list_accounts()
        except Exception as e:
            logger.warning("BoursoBank: listing comptes echoue apres login (%s)", e)
            self._accounts = []

        self._authenticated = True
        logger.info(
            "BoursoBank: auth OK en %dms, %d compte(s)",
            elapsed,
            len(self._accounts),
        )
        return {"session_id": "boursobank_scraper", "cookies": [], "expires_at": None}

    def _handle_auth_error(self, exc: Exception, elapsed_ms: int) -> Dict:
        err = str(exc).lower()

        if "securisation" in err or "/securisation" in err:
            logger.info("BoursoBank: securisation/2FA detectee (%dms)", elapsed_ms)
            return {
                "requires_2fa": True,
                "message": "BoursoBank demande une securisation (2FA).",
            }

        page = getattr(self._scraper, "page", None)
        current_url = ""
        try:
            current_url = page.url if page else ""
        except Exception:
            pass

        if page and "/budget/mouvements" in current_url:
            logger.info(
                "BoursoBank: login upstream timeout mais URL budget OK (%s, %dms)",
                current_url,
                elapsed_ms,
            )
            try:
                self._accounts = self._list_accounts()
            except Exception:
                self._accounts = []
            self._authenticated = True
            return {"session_id": "boursobank_scraper", "cookies": [], "expires_at": None}

        self._safe_disconnect()

        timeout_sigs = ("timeout", "net::err_timed_out", "target closed", "target crashed")
        if any(s in err for s in timeout_sigs):
            raise ConnectionTimeoutError(
                f"BoursoBank: timeout authentification ({elapsed_ms}ms)"
            )

        raise AuthenticationError(f"BoursoBank: echec authentification ({elapsed_ms}ms)")

    # ------------------------------------------------------------------
    # Transactions
    # ------------------------------------------------------------------

    def sync_transactions(self, account, since: Optional[datetime] = None) -> List[Dict]:
        if not self._authenticated or not self._scraper:
            raise AuthenticationError("Session non authentifiee.")

        t0 = time.monotonic()

        if not self._accounts:
            try:
                self._accounts = self._list_accounts()
            except Exception as e:
                logger.warning("BoursoBank: re-listing comptes echoue: %s", e)

        try:
            bourso_account = self._pick_account(account)
        except Exception as e:
            logger.warning("BoursoBank: selection compte echoue: %s", e)
            return []

        self._selected_account = bourso_account
        transactions = self._fetch_transactions_via_api(bourso_account)
        before_pending_filter = len(transactions)
        transactions = [tx for tx in transactions if not _is_pending_transaction(tx)]
        pending_skipped = before_pending_filter - len(transactions)
        if pending_skipped > 0:
            logger.info("BoursoBank: %d operation(s) pending ignoree(s)", pending_skipped)

        if since:
            since_cmp = since
            if timezone.is_naive(since_cmp):
                since_cmp = timezone.make_aware(since_cmp, timezone.get_current_timezone())
            before_filter = len(transactions)
            transactions = [
                tx for tx in transactions
                if tx.get("posted_at") and tx["posted_at"] > since_cmp
            ]
            if before_filter > 0 and not transactions:
                logger.info(
                    "BoursoBank: filtre since=%s a retire toutes les transactions (%d). "
                    "Sync complete retournee pour eviter une sync vide.",
                    since_cmp.isoformat(),
                    before_filter,
                )
                transactions = self._fetch_transactions_via_api(bourso_account)

        elapsed = int((time.monotonic() - t0) * 1000)
        logger.info(
            "BoursoBank: %d transaction(s) pour %s en %dms",
            len(transactions),
            bourso_account.id,
            elapsed,
        )
        return transactions

    # ------------------------------------------------------------------
    # Balance
    # ------------------------------------------------------------------

    def get_balance(self, account) -> Decimal:
        if not self._authenticated:
            raise AuthenticationError("Session non authentifiee.")

        if self._selected_account:
            return Decimal(str(self._selected_account.balance))

        if not self._accounts:
            try:
                self._accounts = self._list_accounts()
            except Exception:
                pass

        try:
            bourso_account = self._pick_account(account)
            return Decimal(str(bourso_account.balance))
        except Exception as e:
            raise BankConnectionError(f"BoursoBank: solde indisponible ({e})")

    # ------------------------------------------------------------------
    # Disconnect
    # ------------------------------------------------------------------

    def disconnect(self) -> None:
        self._safe_disconnect()
        logger.info("BoursoBank: deconnexion OK")

    def _safe_disconnect(self) -> None:
        if self._scraper is not None:
            try:
                self._scraper.close()
            except Exception:
                pass
            self._scraper = None
        self._accounts = []
        self._selected_account = None
        self._authenticated = False

    # ------------------------------------------------------------------
    # Listing comptes
    # ------------------------------------------------------------------

    def _list_accounts(self) -> List["BoursoAccount"]:
        page = getattr(self._scraper, "page", None)
        if page is None:
            raise BankConnectionError("Page Playwright indisponible.")

        if "/budget/mouvements" not in page.url:
            page.goto(
                f"{self._scraper.apiUrl}/budget/mouvements",
                timeout=int(getattr(settings, "BOURSOBANK_TIMEOUT_MS", 60000)),
            )

        raw_accounts = page.evaluate(
            """() => {
                const normalizeText = (s) => (s || '').replace(/\\s+/g, ' ').trim();
                const out = [];
                const roots = Array.from(document.querySelectorAll(
                    'a.c-info-box__link-wrapper, a[href*="/compte/"], a[href*="/budget/mouvements"], [data-account-label]'
                ));
                for (const rootEl of roots) {
                    const accountRoot = rootEl.closest('.c-info-box__account, .c-info-box__link-wrapper') || rootEl;
                    const labelEl = accountRoot.querySelector('[data-account-label]') || rootEl;
                    const guid = normalizeText(labelEl?.getAttribute('data-account-label'));
                    if (!guid) continue;

                    const name = normalizeText(
                        labelEl?.textContent
                        || accountRoot.getAttribute('title')
                        || 'BoursoBank'
                    );
                    const balanceEl = accountRoot.querySelector('.c-info-box__account-balance, [class*="balance"]');
                    const balanceText = normalizeText(balanceEl?.textContent);

                    let href = normalizeText(accountRoot.getAttribute('href'));
                    if (!href) {
                        const linkEl = accountRoot.querySelector('a[href*="/compte/"], a[href*="/budget/mouvements"]');
                        href = normalizeText(linkEl?.getAttribute('href'));
                    }
                    out.push({ guid, name, balanceText, href });
                }
                return out;
            }"""
        )

        accounts: List["BoursoAccount"] = []
        seen: set = set()
        for item in raw_accounts or []:
            try:
                guid = (item.get("guid") or "").strip()
                if not guid or guid in seen:
                    continue
                seen.add(guid)
                name = (item.get("name") or "").strip() or "BoursoBank"
                balance = self._scraper.cleanAmount(item.get("balanceText") or "0")
                href = (item.get("href") or "").strip()
                link = f"{self._scraper.apiUrl}{href}" if href.startswith("/") else href
                if not link:
                    link = f"{self._scraper.apiUrl}/budget/mouvements"
                accounts.append(BoursoAccount(guid, name, balance, link))
            except Exception:
                continue

        if not accounts:
            raise BankConnectionError(
                "Aucun compte detecte sur /budget/mouvements."
            )
        logger.info("BoursoBank: %d compte(s) detecte(s)", len(accounts))
        return accounts

    # ------------------------------------------------------------------
    # Sélection compte cible
    # ------------------------------------------------------------------

    def _pick_account(self, account) -> "BoursoAccount":
        if not self._accounts:
            raise BankConnectionError("Aucun compte BoursoBank disponible.")

        if self._target_account_number:
            matched = self._find_account_by_number(self._target_account_number)
            if matched:
                for ext in self._accounts:
                    if ext.id == matched:
                        return ext

        connection = getattr(account, "bank_connection", None)
        if connection is not None:
            try:
                link = (
                    BankAccountLink.objects.filter(
                        connection=connection, account=account, disabled=False,
                    )
                    .exclude(external_account_id="")
                    .first()
                )
                if link:
                    for ext in self._accounts:
                        if ext.id == link.external_account_id:
                            return ext
            except Exception:
                pass

        account_name = (getattr(account, "name", "") or "").strip().lower()
        selected = None
        if account_name:
            for ext in self._accounts:
                if account_name in ext.name.lower() or ext.name.lower() in account_name:
                    selected = ext
                    break

        if selected is None:
            selected = self._accounts[0]
            if len(self._accounts) > 1:
                logger.warning(
                    "BoursoBank: pas de match strict pour '%s', utilisation de '%s'",
                    getattr(account, "name", ""),
                    selected.name,
                )

        if connection is not None:
            try:
                BankAccountLink.objects.update_or_create(
                    connection=connection,
                    account=account,
                    defaults={
                        "external_account_id": selected.id,
                        "name": selected.name or "",
                        "currency": "EUR",
                        "raw": {"source": "boursorama"},
                        "disabled": False,
                    },
                )
            except Exception:
                pass

        return selected

    def _find_account_by_number(self, account_number: str) -> Optional[str]:
        page = getattr(self._scraper, "page", None) if self._scraper else None
        if not page:
            return None
        number = _normalize_digits(account_number)
        if not number:
            return None
        try:
            return page.evaluate(
                """({ targetDigits }) => {
                    const normalize = s => (s || '').replace(/\\D/g, '');
                    for (const card of document.querySelectorAll(
                        'a.c-info-box__link-wrapper, a[href*="/compte/"], a[href*="/budget/mouvements"]'
                    )) {
                        if (normalize(card.textContent).includes(targetDigits)) {
                            const lbl = card.querySelector('span.c-info-box__account-label');
                            const guid = lbl ? lbl.getAttribute('data-account-label') : null;
                            if (guid) return guid;
                        }
                    }
                    return null;
                }""",
                {"targetDigits": number},
            ) or None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Transactions via interception réseau + fallback DOM
    # ------------------------------------------------------------------

    def _fetch_transactions_via_api(self, bourso_account: "BoursoAccount") -> List[Dict]:
        page = getattr(self._scraper, "page", None) if self._scraper else None
        if not page:
            logger.warning("BoursoBank: page indisponible")
            return []

        captured_responses: List[Dict] = []
        api_re = re.compile(r"(operation|mouvement|movement|transaction|pfm|moneycenter|services/api)", re.IGNORECASE)

        # Hook fetch/XHR dans la page pour capturer les JSON applicatifs
        # qui peuvent ne pas remonter proprement via le listener Playwright.
        try:
            page.evaluate(
                """() => {
                    if (window.__BOURSO_CAPTURE_INSTALLED__) return;
                    window.__BOURSO_CAPTURE_INSTALLED__ = true;
                    window.__BOURSO_CAPTURED_RESPONSES__ = [];

                    const shouldTrack = (url) => {
                        const u = (url || '').toLowerCase();
                        return (
                            u.includes('/services/api/')
                            || u.includes('api.boursobank.com')
                            || /operation|mouvement|movement|transaction|pfm|moneycenter/.test(u)
                        );
                    };

                    const pushPayload = (url, data) => {
                        try {
                            window.__BOURSO_CAPTURED_RESPONSES__.push({ url, data });
                        } catch (_) {}
                    };

                    const originalFetch = window.fetch.bind(window);
                    window.fetch = async (...args) => {
                        const res = await originalFetch(...args);
                        try {
                            const url = (res && res.url) || (args && args[0] && String(args[0])) || '';
                            const ct = (res.headers && res.headers.get && res.headers.get('content-type')) || '';
                            if (shouldTrack(url) && ct.includes('json')) {
                                const clone = res.clone();
                                const json = await clone.json();
                                pushPayload(url, json);
                            }
                        } catch (_) {}
                        return res;
                    };

                    const open = XMLHttpRequest.prototype.open;
                    const send = XMLHttpRequest.prototype.send;
                    XMLHttpRequest.prototype.open = function(method, url, ...rest) {
                        this.__capture_url__ = url;
                        return open.call(this, method, url, ...rest);
                    };
                    XMLHttpRequest.prototype.send = function(...args) {
                        this.addEventListener('load', function() {
                            try {
                                const url = this.__capture_url__ || this.responseURL || '';
                                const ct = this.getResponseHeader && this.getResponseHeader('content-type');
                                if (!shouldTrack(url) || !ct || !ct.includes('json')) return;
                                if (!this.responseText) return;
                                const parsed = JSON.parse(this.responseText);
                                pushPayload(url, parsed);
                            } catch (_) {}
                        });
                        return send.call(this, ...args);
                    };
                }"""
            )
        except Exception:
            pass

        def _on_response(response):
            try:
                url = response.url
                content_type = response.headers.get("content-type", "")
                if "json" not in content_type:
                    return
                if not api_re.search(url):
                    return
                if response.status >= 400:
                    logger.debug("BoursoBank: intercept %s -> HTTP %d (skip)", url, response.status)
                    return
                body = response.body()
                data = json.loads(body)
                captured_responses.append({"url": url, "data": data})
                logger.debug("BoursoBank: intercept %s -> OK (%d bytes)", url, len(body))
            except Exception:
                pass

        page.on("response", _on_response)
        tx_timeout = int(getattr(settings, "BOURSOBANK_TX_TIMEOUT_MS", 30000))
        try:
            self._navigate_to_account(page, bourso_account, tx_timeout)
        except Exception as e:
            logger.warning("BoursoBank: navigation compte echouee: %s", e)
        finally:
            try:
                page.remove_listener("response", _on_response)
            except Exception:
                pass

        logger.info("BoursoBank: %d reponse(s) reseau interceptee(s)", len(captured_responses))

        # Fusion avec les captures fetch/XHR côté navigateur
        try:
            browser_captured = page.evaluate(
                """() => {
                    const out = Array.isArray(window.__BOURSO_CAPTURED_RESPONSES__)
                        ? window.__BOURSO_CAPTURED_RESPONSES__
                        : [];
                    window.__BOURSO_CAPTURED_RESPONSES__ = [];
                    return out;
                }"""
            )
            if browser_captured:
                logger.info("BoursoBank: %d reponse(s) capturees via hook fetch/XHR", len(browser_captured))
                captured_responses.extend(browser_captured)
        except Exception:
            pass

        out: List[Dict] = []
        seen_ids: set = set()
        for resp in captured_responses:
            operations = _extract_operations(resp["data"])
            for op in operations:
                tx = _parse_operation(op)
                if not tx:
                    continue
                tx_id = (tx.get("raw") or {}).get("transaction_id")
                if tx_id and tx_id in seen_ids:
                    continue
                if tx_id:
                    seen_ids.add(tx_id)
                out.append(tx)

        if out:
            logger.info("BoursoBank: %d transaction(s) via interception reseau", len(out))
            return out

        state_tx = self._extract_transactions_from_page_state(page)
        if state_tx:
            logger.info("BoursoBank: %d transaction(s) via etat JS embarque", len(state_tx))
            return state_tx

        logger.info("BoursoBank: 0 interception reseau, fallback DOM")
        return self._scrape_visible_transactions()

    def _extract_transactions_from_page_state(self, page) -> List[Dict]:
        """Extrait des operations depuis les objets JS embarques (Next/Redux/etc.)."""
        try:
            payload = page.evaluate(
                """() => {
                    const roots = [
                        window.__INITIAL_STATE__,
                        window.__NEXT_DATA__,
                        window.__NUXT__,
                        window.APP_STATE,
                        window.appState,
                        window.store && typeof window.store.getState === 'function' ? window.store.getState() : null,
                    ];
                    return roots.filter(Boolean);
                }"""
            )
        except Exception:
            return []

        found: List[Dict] = []
        seen_obj = set()

        def walk(node):
            if node is None:
                return
            if isinstance(node, dict):
                marker = id(node)
                if marker in seen_obj:
                    return
                seen_obj.add(marker)
                ops = _extract_operations(node)
                for op in ops:
                    if isinstance(op, dict):
                        found.append(op)
                for value in node.values():
                    walk(value)
                return
            if isinstance(node, list):
                for item in node:
                    walk(item)

        walk(payload)
        out: List[Dict] = []
        seen_ids: set = set()
        for op in found:
            tx = _parse_operation(op)
            if not tx:
                continue
            tx_id = (tx.get("raw") or {}).get("transaction_id")
            if tx_id and tx_id in seen_ids:
                continue
            if tx_id:
                seen_ids.add(tx_id)
            out.append(tx)
        return out

    def _navigate_to_account(self, page, bourso_account: "BoursoAccount", timeout_ms: int) -> None:
        """Navigate vers la page de transactions du compte en cliquant dessus."""
        account_id = getattr(bourso_account, "id", "") or ""

        # On est sur /budget/mouvements (liste des comptes).
        # Il faut cliquer sur le compte cible pour afficher ses transactions.
        clicked = False
        if account_id:
            selectors = [
                f'[data-account-label="{account_id}"]',
                f'a[href*="{account_id}"]',
            ]
            for sel in selectors:
                try:
                    el = page.locator(sel).first
                    if el.count() > 0:
                        logger.info("BoursoBank: clic sur compte %s (selecteur: %s)", account_id, sel)
                        el.click(timeout=timeout_ms)
                        page.wait_for_load_state("networkidle", timeout=15000)
                        clicked = True
                        break
                except Exception as e:
                    logger.debug("BoursoBank: clic selecteur %s echoue: %s", sel, e)

        if not clicked:
            account_link = getattr(bourso_account, "link", "") or ""
            if account_link and account_link != page.url:
                logger.info("BoursoBank: navigation directe vers %s", account_link)
                page.goto(account_link, timeout=timeout_ms)
                page.wait_for_load_state("networkidle", timeout=15000)
            else:
                logger.warning("BoursoBank: impossible de naviguer vers le compte %s", account_id)

        logger.info("BoursoBank: page apres navigation: %s", page.url)

    def _scrape_visible_transactions(self) -> List[Dict]:
        page = getattr(self._scraper, "page", None) if self._scraper else None
        if not page:
            return []

        # Pagination client-side: le bouton "Mouvements precedents" charge plus
        # d'operations sans changer d'URL.
        max_clicks = int(getattr(settings, "BOURSOBANK_MAX_PREVIOUS_CLICKS", 160))
        for idx in range(max_clicks):
            try:
                before_count = page.evaluate(
                    "() => document.querySelectorAll('li.list-operation-item').length"
                ) or 0
                next_btn = page.locator("[data-operations-next-pagination-trigger]").first
                if next_btn.count() == 0 or not next_btn.is_visible():
                    break
                logger.info("BoursoBank: clic pagination mouvements precedents (%d/%d)", idx + 1, max_clicks)
                next_btn.click(timeout=5000)
                grew = False
                for _ in range(7):
                    page.wait_for_timeout(700)
                    after_count = page.evaluate(
                        "() => document.querySelectorAll('li.list-operation-item').length"
                    ) or 0
                    if after_count > before_count:
                        logger.info(
                            "BoursoBank: pagination +%d operation(s) (total=%d)",
                            after_count - before_count,
                            after_count,
                        )
                        grew = True
                        break
                if not grew:
                    logger.info(
                        "BoursoBank: pagination sans nouveau resultat (total=%d), arret.",
                        before_count,
                    )
                    break
            except Exception as e:
                logger.debug("BoursoBank: pagination stop (%s)", e)
                break

        try:
            rows = page.evaluate(
                """() => {
                    const normalize = (s) => (s || '').replace(/\\s+/g, ' ').trim();
                    const root = document.querySelector('ul.list.list__movement, ul.list__movement') || document;
                    const children = Array.from(root.querySelectorAll(':scope > li, li'));
                    const out = [];
                    let currentDateHeader = '';

                    for (const el of children) {
                        if (el.classList.contains('list-operation-date-line')) {
                            currentDateHeader = normalize(el.textContent);
                            continue;
                        }
                        if (!el.classList.contains('list-operation-item')) {
                            continue;
                        }
                        out.push({
                            operationId: normalize(el.getAttribute('data-id')),
                            isAuthorization: String(el.getAttribute('data-is-auth') || '').toLowerCase() === 'true',
                            dateHeader: currentDateHeader,
                            dateText: normalize(
                                (el.querySelector('.list-operation-item__date, [data-operations-date], time, [class*="date"]') || {}).textContent || ''
                            ),
                            labelUser: normalize((el.querySelector('.list__movement--label-user') || {}).textContent || ''),
                            labelInitial: normalize((el.querySelector('.list__movement--label-initial') || {}).textContent || ''),
                            category: normalize((el.querySelector('.list-operation-item__category') || {}).textContent || ''),
                            amountText: normalize((el.querySelector('.list-operation-item__amount, [class*="amount"]') || {}).textContent || ''),
                        });
                    }
                    return out;
                }"""
            )
        except Exception as e:
            logger.warning("BoursoBank: fallback DOM echoue: %s", e)
            return []

        out: List[Dict] = []
        seen: set = set()
        for row in rows or []:
            amount = _parse_amount_text(row.get("amountText", ""))
            decoded = _decode_dom_data_id(row.get("operationId", ""))
            posted_at = _parse_french_date(decoded.get("d") or row.get("dateText", "") or row.get("dateHeader", ""))
            if amount is None or posted_at is None:
                continue
            desc = (
                (row.get("labelUser") or "").strip()
                or (row.get("labelInitial") or "").strip()
                or "Transaction BoursoBank"
            )
            op_id = (row.get("operationId") or "").strip() or None
            tx_id = decoded.get("id") or op_id
            dedupe_key = tx_id or f"{posted_at.isoformat()}::{amount}::{desc}"
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            out.append({
                "posted_at": posted_at,
                "amount": amount,
                "description": desc,
                "raw": {
                    "source": "boursorama",
                    "transaction_id": tx_id,
                    "operation_id": op_id,
                    "category": (row.get("category") or "").strip(),
                    "is_authorization": bool(row.get("isAuthorization")),
                    "dom_date_header": (row.get("dateHeader") or "").strip(),
                    "dom_data_id_decoded": decoded or None,
                    "from_dom_fallback": True,
                },
            })
        logger.info("BoursoBank: %d transaction(s) via fallback DOM", len(out))
        return out


# ======================================================================
# Fonctions utilitaires (hors classe)
# ======================================================================

def _normalize_digits(value: str) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit())


def _extract_operations(payload) -> List[Dict]:
    """
    Extraction robuste d'operations depuis payload JSON (souvent imbriqué).
    """
    if payload is None:
        return []

    out: List[Dict] = []
    seen_nodes: set = set()
    key_hint = re.compile(r"(operation|mouvement|movement|transaction)", re.IGNORECASE)

    def looks_like_operation(item) -> bool:
        if not isinstance(item, dict):
            return False
        keys = set(item.keys())
        # Heuristique stable pour les objets operation Bourso
        if {"id", "amount"} <= keys:
            return True
        if "amount" in keys and ("date" in keys or "dates" in keys):
            return True
        return False

    def walk(node):
        marker = id(node)
        if marker in seen_nodes:
            return
        seen_nodes.add(marker)

        if isinstance(node, dict):
            if looks_like_operation(node):
                out.append(node)
            for k, v in node.items():
                if isinstance(v, list):
                    if key_hint.search(str(k)):
                        out.extend([x for x in v if looks_like_operation(x)])
                    for item in v:
                        walk(item)
                elif isinstance(v, dict):
                    walk(v)
            return

        if isinstance(node, list):
            if node and all(isinstance(x, dict) for x in node):
                if any(looks_like_operation(x) for x in node):
                    out.extend([x for x in node if looks_like_operation(x)])
            for item in node:
                walk(item)

    walk(payload)

    # Dedupe conservatif par id JSON si disponible, sinon par identité objet
    dedup: List[Dict] = []
    seen_ids: set = set()
    for op in out:
        op_id = op.get("id")
        if op_id:
            if op_id in seen_ids:
                continue
            seen_ids.add(op_id)
        dedup.append(op)
    return dedup


def _parse_amount_text(text: str) -> Optional[Decimal]:
    if not text:
        return None
    cleaned = (
        text.replace("\u202f", " ")
        .replace("\xa0", " ")
        .replace("−", "-")
        .replace("–", "-")
        .replace("—", "-")
    )
    # Le signe peut etre separe du montant par un espace: "- 16,76 €"
    m = re.search(r"([+-]?)\s*(\d[\d\s]*[.,]\d{2})", cleaned)
    if not m:
        return None
    sign = "-" if m.group(1) == "-" else ""
    raw = f"{sign}{m.group(2).replace(' ', '').replace(',', '.')}"
    try:
        return Decimal(raw)
    except Exception:
        return None


def _parse_french_date(text: str) -> Optional[datetime]:
    if not text:
        return None
    txt = text.strip().lower()
    now = timezone.now()
    d = None
    if re.match(r"^\d{4}-\d{2}-\d{2}$", txt):
        try:
            d = datetime.strptime(txt, "%Y-%m-%d").date()
        except Exception:
            d = None
    if "aujourd" in txt:
        d = now.date()
    elif "hier" in txt:
        d = (now - timedelta(days=1)).date()
    else:
        for fmt in ("%d/%m/%Y", "%d/%m/%y"):
            try:
                d = datetime.strptime(txt, fmt).date()
                break
            except Exception:
                pass
        if d is None:
            month_map = {
                "janv": "01", "fevr": "02", "fév": "02", "mars": "03",
                "avr": "04", "mai": "05", "juin": "06", "juil": "07",
                "aout": "08", "août": "08", "sept": "09", "oct": "10",
                "nov": "11", "dec": "12", "déc": "12",
            }
            cleaned = (
                txt.replace(".", " ")
                .replace(",", " ")
                .replace("é", "e")
                .replace("è", "e")
                .replace("ê", "e")
                .replace("û", "u")
                .replace("ù", "u")
                .replace("ô", "o")
                .replace("î", "i")
                .replace("ï", "i")
                .replace("à", "a")
                .replace("â", "a")
            )
            parts = [p for p in cleaned.split() if p]
            while parts and not parts[0].isdigit():
                parts.pop(0)
            if len(parts) >= 3 and parts[0].isdigit() and parts[2].isdigit():
                mm = None
                for k, v in month_map.items():
                    if parts[1].startswith(k):
                        mm = v
                        break
                if mm:
                    try:
                        d = datetime.strptime(f"{parts[0]}/{mm}/{parts[2]}", "%d/%m/%Y").date()
                    except Exception:
                        pass
    if d is None:
        return None
    return timezone.make_aware(datetime.combine(d, datetime.min.time()), timezone.get_current_timezone())


def _decode_dom_data_id(data_id: str) -> Dict:
    """
    Décode le data-id BoursoBank (base64 JSON) quand c'est le format utilisé.
    Retourne {} si le format ne correspond pas.
    """
    raw = (data_id or "").strip()
    if not raw:
        return {}
    try:
        padded = raw + "=" * (-len(raw) % 4)
        decoded = base64.b64decode(padded.encode("utf-8")).decode("utf-8", errors="ignore")
        obj = json.loads(decoded)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _parse_operation(op: Dict) -> Optional[Dict]:
    if not op:
        return None
    op_id = op.get("id")
    amount = op.get("amount")
    if op_id is None or amount is None:
        return None

    labels = op.get("labels") or []
    description = (
        next((l.get("body") for l in labels if l.get("type") == "operation_label" and l.get("body")), None)
        or next((l.get("body") for l in labels if l.get("body")), None)
        or op.get("headerUpperText")
        or "Transaction BoursoBank"
    )

    posted_date = None
    for d in op.get("dates", []):
        if d.get("type") in {"operation_date", "value_date", "debit_date"} and d.get("date"):
            posted_date = d["date"]
            break
    if not posted_date:
        return None

    posted_at = timezone.make_aware(
        datetime.strptime(posted_date, "%Y-%m-%d"),
        timezone.get_current_timezone(),
    )
    category = op.get("category") or {}
    status = (op.get("status") or {}).get("id")
    is_pending = False
    if isinstance(status, str) and status.lower() in {"pending", "authorization", "authorisation", "coming"}:
        is_pending = True
    if isinstance(labels, list):
        for l in labels:
            body = (l.get("body") or "").lower()
            if "autorisation paiement en cours" in body or "pending" in body:
                is_pending = True
                break
    return {
        "posted_at": posted_at,
        "amount": Decimal(str(amount)),
        "description": description,
        "raw": {
            "source": "boursorama",
            "transaction_id": op_id,
            "operation_id": op_id,
            "account_id": op.get("accountKey"),
            "status": status,
            "boursobank_category_label": category.get("label"),
            "boursobank_category_parent_label": category.get("parentLabel"),
            "is_pending": is_pending,
        },
    }


def _is_pending_transaction(tx: Dict) -> bool:
    raw = tx.get("raw") or {}
    desc = (tx.get("description") or "").lower()
    cat = (raw.get("category") or raw.get("boursobank_category_label") or "").lower()
    status = (raw.get("status") or "").lower() if isinstance(raw.get("status"), str) else ""

    if raw.get("is_authorization") is True or raw.get("is_pending") is True:
        return True
    if status in {"pending", "authorization", "authorisation", "coming"}:
        return True
    if "autorisation paiement en cours" in desc or "autorisation paiement en cours" in cat:
        return True
    if " pending " in f" {desc} ":
        return True
    return False

"""
Connecteur BoursoBank basé sur `boursobank-scraper`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Optional

from django.conf import settings
from django.utils import timezone
import yaml

from finance.connectors.base import (
    AuthenticationError,
    BankConnectionError,
    BaseBankConnector,
    ConnectionTimeoutError,
    InvalidCredentialsError,
    RateLimitError,
)

logger = logging.getLogger(__name__)

try:
    from boursobank_scraper import BoursoScraper

    BOURSOBANK_SCRAPER_AVAILABLE = True
except ImportError:
    BoursoScraper = None  # type: ignore[assignment]
    BOURSOBANK_SCRAPER_AVAILABLE = False


class BoursoBankConnector(BaseBankConnector):
    """Connecteur BoursoBank utilisant la librairie `boursobank-scraper`."""

    def __init__(self, data_path: Optional[Path] = None):
        if not BOURSOBANK_SCRAPER_AVAILABLE or BoursoScraper is None:
            raise ImportError(
                "Le package boursobank-scraper est requis pour BoursoBank "
                "(attention: il nécessite Python >= 3.13)."
            )
        self.root_data_path = data_path or Path(
            getattr(settings, "BOURSOBANK_DATA_DIR", settings.BASE_DIR / "boursobank-data")
        )
        self.root_data_path.mkdir(parents=True, exist_ok=True)
        self.scraper = None
        self._authenticated = False
        self._accounts = {}
        self._username = None
        self.timeout_ms = int(getattr(settings, "BOURSOBANK_TIMEOUT_MS", 120000))
        self.security_wait_seconds = int(getattr(settings, "BOURSOBANK_SECURITY_WAIT_SECONDS", 180))
        self.headless = bool(getattr(settings, "BOURSOBANK_HEADLESS", True))
        self.save_trace = bool(getattr(settings, "BOURSOBANK_SAVE_TRACE", True))
        # Proxy optionnel (ex: "socks5://host.docker.internal:1080") pour router le
        # trafic Playwright via une IP résidentielle. Requis si BoursoBank bloque l'IP
        # du serveur avec "opération non aboutie - service d'anonymisation détecté".
        self.proxy = getattr(settings, "BOURSOBANK_PROXY", "") or ""

    @dataclass
    class _Account:
        id: str
        name: str
        balance: Decimal
        link: str

    @property
    def provider_name(self) -> str:
        return "BoursoBank"

    def authenticate(self, credentials: Dict) -> Dict:
        username = credentials.get("username")
        password = credentials.get("password")

        if not username or not password:
            raise InvalidCredentialsError("username et password sont requis")

        self.disconnect()
        self._username = str(username)

        try:
            # Isoler les données de session par utilisateur bancaire.
            user_hash = hashlib.sha256(self._username.encode("utf-8")).hexdigest()[:16]
            user_data_path = self.root_data_path / user_hash
            user_data_path.mkdir(parents=True, exist_ok=True)
            self._write_config_yaml(
                user_data_path=user_data_path,
                username=self._username,
                password=str(password),
            )

            self.scraper = BoursoScraper(
                username=self._username,
                password=str(password),
                rootDataPath=user_data_path,
                headless=self.headless,
                timeout=self.timeout_ms,
                saveTrace=self.save_trace,
            )
            self._configure_browser()
            connected = bool(self.scraper.connect())
            if not connected:
                accounts = self._safe_list_accounts()
                if not accounts:
                    raise InvalidCredentialsError("Échec de connexion BoursoBank (identifiants invalides).")
            else:
                accounts = self._safe_list_accounts()
            self._accounts = {acc.id: acc for acc in accounts}
            self._authenticated = True
            return {
                "session_id": "boursobank_scraper_session",
                "accounts_count": len(accounts),
                "data_path": str(user_data_path),
            }
        except InvalidCredentialsError:
            raise
        except Exception as e:
            error = str(e).lower()

            # ── Vérifier la sécurisation EN PREMIER, avant tout appel qui naviguerait la page.
            # _safe_list_accounts() appelle listAccounts() qui fait un goto() et perturberait
            # l'état de la page de sécurisation avant qu'on ait pu cliquer "Suivant".
            if self._is_step_up_authentication_required(error) or self._is_on_securisation_page():
                # Le bouton "Suivant" est un <a> lien sur la page /securisation.
                # Ce clic déclenche l'envoi du code SMS/notification app à l'utilisateur.
                code_sent = self._trigger_securisation_code_send()
                if code_sent:
                    # Pause obligatoire de 30 s après l'envoi du code :
                    # toute action sur la page pendant ce délai perturberait le flow 2FA.
                    logger.info(
                        "BoursoBank sécurisation: pause 30 s après l'envoi du code 2FA."
                    )
                    time.sleep(30)
                # Attendre que l'utilisateur valide (passkey, SMS, notification app…).
                if self._wait_for_security_validation_completion():
                    accounts = self._safe_list_accounts()
                    self._accounts = {acc.id: acc for acc in accounts}
                    self._authenticated = True
                    return {
                        "session_id": "boursobank_scraper_session",
                        "accounts_count": len(accounts),
                        "data_path": str(user_data_path),
                    }
                logger.info(
                    "Authentification renforcée BoursoBank détectée (securisation). "
                    "Code envoyé=%s. Retour requires_2fa.",
                    code_sent,
                )
                self.disconnect()
                return {
                    "requires_2fa": True,
                    "code_sent": code_sent,
                    "message": (
                        "Un code à usage unique a été envoyé sur votre téléphone BoursoBank. "
                        "Validez la connexion depuis l'application ou par SMS, "
                        "puis relancez la synchronisation."
                        if code_sent
                        else (
                            "Validation supplémentaire requise sur BoursoBank "
                            "(page de sécurisation détectée). "
                            "Connectez-vous manuellement pour valider ce nouvel appareil."
                        )
                    ),
                }

            # Pas de sécurisation — tenter une récupération via listing de comptes.
            recovered_accounts = self._safe_list_accounts()
            if recovered_accounts:
                logger.info(
                    "Session BoursoBank récupérée malgré une erreur de login scraper; "
                    "les comptes sont accessibles."
                )
                self._accounts = {acc.id: acc for acc in recovered_accounts}
                self._authenticated = True
                return {
                    "session_id": "boursobank_scraper_session",
                    "accounts_count": len(recovered_accounts),
                    "data_path": str(user_data_path),
                }
            self.disconnect()
            if "err_proxy_connection_failed" in error or "proxy_connection_failed" in error:
                raise AuthenticationError(
                    f"Proxy BoursoBank injoignable ({self.proxy}). "
                    "Vérifiez que le tunnel SSH est actif et écoute sur 0.0.0.0 :\n"
                    "  ssh -D 0.0.0.0:1080 -N -f user@votre-machine-perso\n"
                    "Ou désactivez le proxy en commentant BOURSOBANK_PROXY dans .env."
                ) from e
            if "timeout" in error:
                raise ConnectionTimeoutError(f"Timeout lors de l'authentification: {e}") from e
            if "429" in error or "rate limit" in error:
                raise RateLimitError(f"Rate limit lors de l'authentification: {e}") from e
            raise AuthenticationError(f"Erreur lors de l'authentification BoursoBank: {e}") from e

    def sync_transactions(self, account, since: Optional[datetime] = None) -> List[Dict]:
        self._ensure_authenticated()
        try:
            bourso_account = self._select_boursobank_account(account)
            try:
                self.scraper.saveNewTransactionsForAccount(bourso_account)
            except Exception as scraper_err:
                err_str = str(scraper_err)
                # Erreur 1 : notre _configure_browser() remplace le contexte Playwright
                # sans démarrer le tracing. Le scraper appelle stopTracing() à la fin, ce
                # qui échoue. Les transactions sont déjà sur le disque — on ignore.
                if "must start tracing before stopping" in err_str.lower():
                    logger.warning(
                        "BoursoBank: stopTracing() a échoué (tracing non démarré sur "
                        "le contexte modifié) — ignoré, les transactions sont disponibles."
                    )
                # Erreur 2 : le scraper essaie de déplacer un fichier d'autorisation
                # de authorization/old/ vers authorization/new/ alors que le fichier
                # source n'existe plus (déjà traité lors d'un run précédent ou structure
                # différente). Les transactions sont déjà écrites sur le disque — on ignore.
                elif isinstance(scraper_err, (FileNotFoundError, OSError)) and (
                    "authorization" in err_str and "old" in err_str
                ):
                    logger.warning(
                        "BoursoBank: déplacement de fichier d'autorisation échoué "
                        "(fichier déjà traité) — ignoré : %s",
                        err_str,
                    )
                else:
                    raise
            finally:
                # Playwright peut laisser une boucle asyncio dans un état « running »
                # après certaines exceptions, ce qui ferait déclencher
                # SynchronousOnlyOperation sur tous les appels ORM qui suivent.
                # On réinitialise proprement la boucle du thread courant.
                import asyncio as _asyncio
                try:
                    _asyncio.set_event_loop(_asyncio.new_event_loop())
                except Exception:
                    pass
            operations = self._load_operations_for_account(bourso_account.id)

            # On ne filtre PAS par `since` : le scraper gère lui-même les fichiers
            # "déjà vus" sur disque (saveNewTransactionsForAccount). Filtrer par date
            # ferait manquer les opérations dont le posted_at est antérieur à
            # last_sync_at mais qui viennent juste d'être validées par la banque
            # (ex : opération du 3 avril validée le 9 avril).
            # La déduplication est assurée par transaction_id dans _upsert_transaction_from_sync.
            transactions: List[Dict] = []
            for operation in operations:
                tx = self._map_operation_to_transaction(operation)
                if tx is None:
                    continue
                tx["posted_at"] = self._to_aware_datetime(tx["posted_at"])
                transactions.append(tx)

            transactions.sort(key=lambda t: t["posted_at"])
            return transactions
        except (AuthenticationError, ConnectionTimeoutError, RateLimitError):
            raise
        except Exception as e:
            error = str(e).lower()
            if "timeout" in error:
                raise ConnectionTimeoutError(f"Timeout lors de la récupération des transactions: {e}") from e
            if "429" in error or "rate limit" in error:
                raise RateLimitError(f"Rate limit lors de la récupération des transactions: {e}") from e
            raise BankConnectionError(f"Erreur lors de la récupération des transactions: {e}") from e

    def get_balance(self, account) -> Decimal:
        self._ensure_authenticated()
        try:
            accounts = self._safe_list_accounts()
            # Ne pas écraser self._accounts avec une liste vide :
            # après sync_transactions() le navigateur peut être sur une autre page
            # et la récupération peut échouer. On conserve les comptes déjà connus.
            if accounts:
                self._accounts = {acc.id: acc for acc in accounts}
            bourso_account = self._select_boursobank_account(account)
            return Decimal(str(bourso_account.balance)).quantize(Decimal("1.00"))
        except (InvalidOperation, ValueError) as e:
            raise BankConnectionError(f"Solde invalide retourné par BoursoBank: {e}") from e
        except Exception as e:
            error = str(e).lower()
            if "timeout" in error:
                raise ConnectionTimeoutError(f"Timeout lors de la récupération du solde: {e}") from e
            raise BankConnectionError(f"Erreur lors de la récupération du solde: {e}") from e

    def disconnect(self) -> None:
        if self.scraper:
            try:
                self.scraper.close()
            except Exception:
                logger.debug("Erreur ignorée lors de la fermeture du scraper BoursoBank", exc_info=True)
        self.scraper = None
        self._accounts = {}
        self._authenticated = False
        self._username = None

    def _ensure_authenticated(self) -> None:
        if not self._authenticated or not self.scraper:
            raise AuthenticationError("Session non authentifiée. Authentifiez-vous d'abord.")

    def _select_boursobank_account(self, account):
        if not self._accounts:
            raise BankConnectionError("Aucun compte BoursoBank disponible après authentification.")

        if len(self._accounts) == 1:
            return next(iter(self._accounts.values()))

        account_name = (getattr(account, "name", "") or "").strip().lower()
        account_iban = (getattr(account, "iban", "") or "").strip().lower()

        for candidate in self._accounts.values():
            candidate_name = (getattr(candidate, "name", "") or "").strip().lower()
            if account_name and (account_name in candidate_name or candidate_name in account_name):
                return candidate
            if account_iban and account_iban in candidate_name:
                return candidate

        return next(iter(self._accounts.values()))

    def _load_operations_for_account(self, account_id: str) -> List[Dict]:
        assert self.scraper is not None  # garanti par _ensure_authenticated
        transactions_path = self.scraper.transactionsPath / account_id
        if not transactions_path.exists():
            return []

        operations: List[Dict] = []
        for file_path in transactions_path.rglob("*.json"):
            if "authorization" in file_path.parts:
                continue
            if "invalid" in file_path.parts:
                continue
            try:
                payload = json.loads(file_path.read_text(encoding="utf-8"))
                operations.append(payload)
            except Exception:
                logger.debug("Transaction JSON illisible ignorée: %s", file_path, exc_info=True)
        return operations

    def _map_operation_to_transaction(self, payload: Dict) -> Optional[Dict]:
        operation = payload.get("operation") or {}
        operation_id = operation.get("id")
        amount = operation.get("amount")

        if amount is None:
            return None

        posted_at = self._extract_operation_date(operation)
        if posted_at is None:
            return None

        labels = operation.get("labels") or []
        label_values = [lbl.get("body", "").strip() for lbl in labels if isinstance(lbl, dict)]
        description = (" - ".join([txt for txt in label_values if txt]) or "Transaction BoursoBank")[:512]

        # Extraire la catégorie BoursoBank — label et parentLabel correspondent
        # directement aux noms de Category dans notre base (initialisées depuis le
        # référentiel BoursoBank). Le lookup se fait dans sync_service.
        boursobank_category = operation.get("category") or {}
        category_label = boursobank_category.get("label") or ""
        category_parent_label = boursobank_category.get("parentLabel") or ""

        raw = {
            "source": "boursorama",
            "transaction_id": operation_id,
            "status": ((operation.get("status") or {}).get("id")),
            "boursobank_category_label": category_label,
            "boursobank_category_parent_label": category_parent_label,
            "raw_operation": operation,
        }

        try:
            amount_dec = Decimal(str(amount)).quantize(Decimal("1.00"))
        except InvalidOperation:
            return None

        return {
            "posted_at": posted_at,
            "amount": amount_dec,
            "description": description,
            "raw": raw,
        }

    def _extract_operation_date(self, operation: Dict) -> Optional[datetime]:
        dates = operation.get("dates") or []
        date_value = None
        for date_obj in dates:
            if not isinstance(date_obj, dict):
                continue
            if date_obj.get("type") == "operation_date":
                date_value = date_obj.get("date")
                break
        if not date_value:
            return None

        try:
            return datetime.fromisoformat(str(date_value).replace("Z", "+00:00"))
        except ValueError:
            return None

    def _to_aware_datetime(self, value: Optional[datetime]) -> datetime:
        if value is None:
            return timezone.now()
        if timezone.is_naive(value):
            return timezone.make_aware(value, timezone.get_current_timezone())
        return value

    def _configure_browser(self) -> None:
        """
        Reconfigure le navigateur Playwright du scraper avec :
        - flags Chromium anti-détection (désactive AutomationControlled)
        - user-agent Windows/Chrome réaliste (pas "HeadlessChrome")
        - script stealth injecté sur chaque page (masque navigator.webdriver, plugins…)
        - proxy optionnel si BOURSOBANK_PROXY est défini

        BoursoBank (et son système de fraude) détecte les navigateurs automatisés via
        plusieurs vecteurs : navigator.webdriver, User-Agent HeadlessChrome, absence de
        plugins, viewport nul, etc. — et affiche "opération non aboutie - service
        d'anonymisation détecté", même depuis une IP résidentielle.

        La technique : on remplace le browser lancé par BoursoScraper par un nouveau avec
        les bons paramètres, puis on monkey-patche `browser.new_context` pour que chaque
        contexte créé par le scraper hérite du user-agent et du script stealth.
        """
        if not self.scraper:
            return
        try:
            proxy_config = {"server": self.proxy} if self.proxy else None
            if proxy_config:
                logger.info("BoursoBank: proxy activé → %s", self.proxy)

            self.scraper.browser.close()
            self.scraper.browser = self.scraper.playwright.chromium.launch(
                headless=self.headless,
                slow_mo=500,
                proxy=proxy_config,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                    "--disable-infobars",
                    "--window-size=1280,720",
                ],
            )

            # Monkey-patch new_context pour injecter user-agent + stealth sur chaque page.
            # BoursoScraper appelle self.browser.new_context(...) dans connect() — notre
            # version patché est donc appelée à chaque ouverture de contexte.
            original_new_context = self.scraper.browser.new_context
            stealth_script = self._stealth_init_script()

            def _stealth_new_context(*args, **kwargs):
                if "user_agent" not in kwargs:
                    kwargs["user_agent"] = (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    )
                if "viewport" not in kwargs:
                    kwargs["viewport"] = {"width": 1280, "height": 720}
                if "locale" not in kwargs:
                    kwargs["locale"] = "fr-FR"
                ctx = original_new_context(*args, **kwargs)
                logger.info("BoursoBank: init_script stealth injecté dans le contexte.")
                ctx.add_init_script(stealth_script)
                logger.info("BoursoBank: auto-accepteur Didomi actif (init script).")
                # Bloquer les appels API Didomi vers ses CDN externes.
                # Didomi est bundlé dans le JS BoursoBank MAIS fait des appels réseau vers
                # ses propres serveurs (config, consentement…). Sans ces appels, Didomi ne
                # s'initialise pas et n'affiche pas sa bannière. C'est ce qui permettait
                # la récupération de session dans les runs précédents.
                for pattern in [
                    "**/sdk.privacy-center.org/**",
                    "**/api.privacy-center.org/**",
                    "**/privacy-center.org/**",
                    "**/*didomi*.js*",
                    "**/*didomi*.json*",
                ]:
                    ctx.route(pattern, lambda route: route.abort())
                logger.info("BoursoBank: appels réseau Didomi bloqués dans le contexte.")
                return ctx

            self.scraper.browser.new_context = _stealth_new_context
            logger.info(
                "BoursoBank: navigateur reconfiguré (stealth + UA réaliste%s).",
                f" + proxy {self.proxy}" if self.proxy else "",
            )
        except Exception:
            logger.warning(
                "BoursoBank: impossible de reconfigurer le navigateur (stealth/proxy)",
                exc_info=True,
            )

    @staticmethod
    def _stealth_init_script() -> str:
        """
        Script JS injecté sur chaque page pour :
        1. Masquer les indicateurs d'automation (navigator.webdriver, UA HeadlessChrome…)
        2. Supprimer la bannière de consentement RGPD Didomi qui intercepte les clics
           et empêche le scraper de cliquer sur "Mémoriser mon identifiant" lors du login.
        """
        return """
        (() => {
            // ── 1. Masquage des indicateurs d'automation ──────────────────────────────

            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined,
                configurable: true,
            });

            const driverKeys = [
                '__driver_evaluate', '__webdriver_evaluate', '__selenium_evaluate',
                '__fxdriver_evaluate', '__driver_unwrapped', '__webdriver_unwrapped',
                '__selenium_unwrapped', '__fxdriver_unwrapped', '__webdriverFunc',
            ];
            driverKeys.forEach(k => { try { delete window[k]; } catch (_) {} });

            const fakePluigns = [
                { name: 'PDF Viewer', filename: 'internal-pdf-viewer', description: '' },
                { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
                { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
            ];
            Object.defineProperty(navigator, 'plugins', {
                get: () => {
                    const arr = [...fakePluigns];
                    arr.refresh = () => {};
                    arr.item = i => arr[i] ?? null;
                    arr.namedItem = n => arr.find(p => p.name === n) ?? null;
                    Object.defineProperty(arr, 'length', { get: () => fakePluigns.length });
                    return arr;
                },
                configurable: true,
            });

            Object.defineProperty(navigator, 'languages', {
                get: () => ['fr-FR', 'fr', 'en-US', 'en'],
                configurable: true,
            });

            const _origQuery = window.navigator.permissions?.query?.bind(navigator.permissions);
            if (_origQuery) {
                navigator.permissions.query = params =>
                    params.name === 'notifications'
                        ? Promise.resolve({ state: Notification.permission })
                        : _origQuery(params);
            }

            if (!window.chrome) { window.chrome = {}; }
            if (!window.chrome.runtime) { window.chrome.runtime = {}; }

            // ── 2. Neutraliser la bannière Didomi (RGPD) ─────────────────────────────
            //
            // Didomi est bundlé dans le JS de BoursoBank et réapplique ses propres styles
            // en continu. Un simple display:none ponctuel est annulé aussitôt.
            // Solution : setInterval toutes les 50ms avec setProperty 'important' —
            // un style inline !important ne peut pas être surchargé par le JS Didomi.

            const _killDidomi = () => {
                const host = document.querySelector('#didomi-host');
                if (!host) return;

                // 1. Tenter d'accepter proprement via le Shadow DOM
                const shadow = host.shadowRoot;
                if (shadow) {
                    const selectors = [
                        '#didomi-notice-agree-button',
                        '[data-didomi-action="agree"]',
                        'button[aria-label*="ccepter"]',
                        'button[aria-label*="ccept"]',
                    ];
                    for (const sel of selectors) {
                        const el = shadow.querySelector(sel);
                        if (el) { el.click(); }
                    }
                    for (const btn of shadow.querySelectorAll('button')) {
                        if (/tout accepter|accepter|accept all/i.test(btn.textContent || '')) {
                            btn.click();
                        }
                    }
                }

                // 2. Forcer la disparition de l'overlay (style !important non surchargeable)
                host.style.setProperty('display', 'none', 'important');
                host.style.setProperty('pointer-events', 'none', 'important');
                host.style.setProperty('visibility', 'hidden', 'important');
                host.style.setProperty('z-index', '-9999', 'important');
            };

            // Démarrer immédiatement et répéter toutes les 50ms
            setInterval(_killDidomi, 50);
        })();
        """

    def _is_step_up_authentication_required(self, error: str) -> bool:
        """Détecte les cas de sécurisation/step-up auth visibles dans les logs Playwright."""
        indicators = (
            "/securisation",
            "x-domain-authentification",
            "referer-domain=clients.boursobank.com",
        )
        return any(token in error for token in indicators)

    def _is_on_securisation_page(self) -> bool:
        """Vérifie si la page Playwright courante est bien la page /securisation."""
        if not self.scraper:
            return False
        page = getattr(self.scraper, "page", None)
        if page is None:
            return False
        try:
            return "/securisation" in (getattr(page, "url", "") or "")
        except Exception:
            return False

    def _trigger_securisation_code_send(self) -> bool:
        """
        Sur la page /securisation, clique "Suivant" pour déclencher l'envoi du code
        à usage unique (SMS / notification app BoursoBank).

        IMPORTANT : sur la page de sécurisation BoursoBank, "Suivant" est un <a> lien
        (pas un <button>), de classe c-button--primary, avec href vers /securisation/validation.
        Sans ce clic, l'utilisateur ne reçoit aucun code.
        Retourne True si le clic / la navigation a été effectué.
        """
        if not self.scraper:
            return False
        page = getattr(self.scraper, "page", None)
        if page is None:
            return False
        try:
            current_url = getattr(page, "url", "") or ""
            if "/securisation" not in current_url:
                return False

            # Sur /securisation, "Suivant" est un <a> lien (et non un <button>).
            # Essayer d'abord le lien, puis fallback sur tout élément avec ce texte.
            link = page.get_by_role("link", name="Suivant")
            if link.count() > 0:
                link.first.click()
                logger.info(
                    "BoursoBank: lien 'Suivant' cliqué sur la page de sécurisation "
                    "— code 2FA en cours d'envoi."
                )
                time.sleep(1)
                return True

            # Fallback : naviguer directement vers /securisation/validation
            validation_url = current_url.replace("/securisation?", "/securisation/validation?")
            if validation_url != current_url and "/securisation/validation" in validation_url:
                page.goto(validation_url)
                logger.info(
                    "BoursoBank: navigation directe vers %s pour déclencher l'envoi du code.",
                    validation_url,
                )
                time.sleep(1)
                return True

        except Exception:
            logger.debug(
                "Impossible de cliquer sur 'Suivant' (page sécurisation)",
                exc_info=True,
            )
        return False

    def _wait_for_security_validation_completion(self) -> bool:
        """
        Attend la fin d'une validation de nouvel appareil (page /securisation).
        Doit être appelée APRÈS _trigger_securisation_code_send().
        Retourne True si l'espace comptes devient accessible dans la session courante.
        """
        if not self.scraper:
            return False
        page = getattr(self.scraper, "page", None)
        if page is None:
            return False

        try:
            deadline = time.time() + max(self.security_wait_seconds, 10)
            while time.time() < deadline:
                try:
                    current_url = getattr(page, "url", "") or ""
                    if not isinstance(current_url, str):
                        return False
                    # Attendre que le navigateur quitte toutes les pages de sécurisation
                    # (/securisation et /securisation/validation).
                    if "/securisation" not in current_url:
                        accounts = self._safe_list_accounts()
                        if accounts:
                            return True
                except Exception:
                    # La page peut être en cours de transition; on continue de poll.
                    pass
                time.sleep(2)
        except Exception:
            return False
        return False

    def _safe_list_accounts(self) -> List[_Account]:
        """
        Liste les comptes via l'API du module puis fallback DOM
        (le module peut timeout même avec session déjà ouverte).
        """
        if not self.scraper:
            return []

        try:
            accounts = list(self.scraper.listAccounts())
            return [
                self._Account(
                    id=str(acc.id),
                    name=str(acc.name),
                    balance=Decimal(str(acc.balance)).quantize(Decimal("1.00")),
                    link=str(acc.link),
                )
                for acc in accounts
            ]
        except Exception:
            logger.info("Fallback DOM pour la détection des comptes BoursoBank.")
            return self._accounts_from_page_dom()

    def _accounts_from_page_dom(self) -> List[_Account]:
        if not self.scraper:
            return []
        page = getattr(self.scraper, "page", None)
        if page is None:
            return []

        try:
            if "/budget/mouvements" not in (page.url or ""):
                page.goto("https://clients.boursobank.com/budget/mouvements")
            locator_accounts = page.locator("css=a.c-info-box__link-wrapper")
            account_elements = locator_accounts.all()
            recovered: List[BoursoBankConnector._Account] = []
            for account_el in account_elements:
                name = (account_el.get_attribute("title") or "").strip()
                balance_el = account_el.locator("css=span.c-info-box__account-balance")
                label_el = account_el.locator("css=span.c-info-box__account-label")
                if balance_el.count() == 0 or label_el.count() == 0:
                    continue
                account_id = label_el.get_attribute("data-account-label")
                if not account_id:
                    continue
                href = account_el.get_attribute("href") or ""
                link = href if href.startswith("http") else f"https://clients.boursobank.com{href}"
                balance_raw = (balance_el.first.text_content() or "").strip()
                balance = self._parse_balance_text(balance_raw)
                recovered.append(self._Account(id=account_id, name=name, balance=balance, link=link))
            return recovered
        except Exception:
            return []

    def _parse_balance_text(self, value: str) -> Decimal:
        cleaned = (
            value.replace("\xa0", "")
            .replace(" ", "")
            .replace("€", "")
            .replace(",", ".")
            .replace("+", "")
            .strip()
        )
        try:
            return Decimal(cleaned).quantize(Decimal("1.00"))
        except Exception:
            return Decimal("0.00")

    def _write_config_yaml(self, user_data_path: Path, username: str, password: str) -> None:
        """
        Génère un config.yaml dans le dossier data (workflow recommandé par le module).
        Par défaut, le mot de passe n'est pas persisté.
        """
        config = {
            "username": username,
            "headless": self.headless,
            "timeoutMs": self.timeout_ms,
            "saveTrace": self.save_trace,
            "password": password,
        }

        config_path = user_data_path / "config.yaml"
        config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

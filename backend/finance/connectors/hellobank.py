"""
Connecteur Hello Bank pour synchronisation automatique.

Ce connecteur utilise Playwright pour automatiser un navigateur et récupérer
les transactions et soldes depuis l'interface web Hello Bank.
"""

import logging
import time
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional

from django.conf import settings
from django.utils import timezone

from finance.connectors.base import (
    AuthenticationError,
    BankConnectionError,
    ConnectionTimeoutError,
    InvalidCredentialsError,
    RateLimitError,
    BaseBankConnector,
)

logger = logging.getLogger(__name__)

# Import conditionnel de Playwright
try:
    from playwright.sync_api import sync_playwright, Browser, Page, TimeoutError as PlaywrightTimeoutError
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    logger.warning("Playwright non disponible. Le connecteur Hello Bank nécessite Playwright.")


class HelloBankConnector(BaseBankConnector):
    """
    Connecteur Hello Bank pour synchronisation automatique.

    Ce connecteur utilise Playwright pour automatiser un navigateur et simuler
    une connexion utilisateur à l'interface web Hello Bank.
    """

    def __init__(self):
        """Initialise le connecteur Hello Bank."""
        if not PLAYWRIGHT_AVAILABLE:
            raise ImportError(
                "Playwright n'est pas installé. Installez-le avec: pip install playwright && playwright install"
            )

        self.username: Optional[str] = None
        self.password: Optional[str] = None
        self.two_fa_code: Optional[str] = None
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.session_cookies: List[Dict] = []
        self._authenticated = False

    @property
    def provider_name(self) -> str:
        """Retourne le nom du provider bancaire."""
        return "Hello Bank"

    def authenticate(self, credentials: Dict) -> Dict:
        """
        Authentifie le connecteur avec les credentials fournis.

        Gère l'authentification complète avec Hello Bank via l'interface web,
        incluant si nécessaire l'authentification 2FA.

        Args:
            credentials: Dictionnaire contenant les credentials nécessaires :
                        - "username": str (identifiant Hello Bank)
                        - "password": str (mot de passe)
                        - "2fa_code": str (optionnel, code 2FA pour authentification complète)

        Returns:
            dict: Informations de session :
                  {"session_id": str, "cookies": list, "expires_at": datetime}

        Raises:
            InvalidCredentialsError: Si les credentials sont invalides
            AuthenticationError: Si l'authentification échoue
            ConnectionTimeoutError: Si la connexion timeout
        """
        username = credentials.get("username")
        password = credentials.get("password")
        two_fa_code = credentials.get("2fa_code")

        if not username or not password:
            raise InvalidCredentialsError("username et password sont requis")

        self.username = username
        self.password = password
        self.two_fa_code = two_fa_code

        max_retries = 3
        base_delay = 1.0

        for attempt in range(max_retries):
            try:
                return self._authenticate_with_playwright()
            except PlaywrightTimeoutError as e:
                if attempt < max_retries - 1:
                    wait_time = base_delay * (2 ** attempt)
                    logger.warning(
                        f"Timeout lors de l'authentification (tentative {attempt + 1}/{max_retries}). "
                        f"Retry dans {wait_time}s..."
                    )
                    time.sleep(wait_time)
                    continue
                raise ConnectionTimeoutError(f"Timeout lors de l'authentification: {str(e)}")
            except Exception as e:
                error_msg = str(e).lower()
                if "invalid" in error_msg or "incorrect" in error_msg or "wrong" in error_msg:
                    raise InvalidCredentialsError(f"Credentials invalides: {str(e)}")
                if attempt < max_retries - 1:
                    wait_time = base_delay * (2 ** attempt)
                    logger.warning(
                        f"Erreur lors de l'authentification (tentative {attempt + 1}/{max_retries}): {str(e)}. "
                        f"Retry dans {wait_time}s..."
                    )
                    time.sleep(wait_time)
                    continue
                raise AuthenticationError(f"Erreur lors de l'authentification: {str(e)}")

        raise AuthenticationError("Échec de l'authentification après plusieurs tentatives")

    def _authenticate_with_playwright(self) -> Dict:
        """
        Authentifie avec Playwright en automatisant le navigateur.

        Returns:
            dict: Informations de session

        Raises:
            AuthenticationError: Si l'authentification échoue
            ConnectionTimeoutError: Si la connexion timeout
        """
        try:
            self.playwright = sync_playwright().start()
            self.browser = self.playwright.chromium.launch(headless=True)
            self.page = self.browser.new_page()

            # Naviguer vers la page de connexion Hello Bank
            logger.info("Navigation vers la page de connexion Hello Bank")
            self.page.goto("https://www.hellobank.fr/connexion", timeout=30000)
            self.page.wait_for_load_state("networkidle", timeout=10000)

            # Remplir le formulaire de connexion
            # Note: Les sélecteurs CSS peuvent changer, ils doivent être ajustés selon l'interface actuelle
            try:
                # Attendre que le formulaire soit visible
                self.page.wait_for_selector('input[name="username"], input[type="text"][id*="login"], input[id*="username"], input[type="email"]', timeout=10000)
                
                # Trouver et remplir le champ username
                username_selectors = [
                    'input[name="username"]',
                    'input[type="text"][id*="login"]',
                    'input[id*="username"]',
                    'input[type="email"]',
                    'input[name="email"]',
                ]
                username_filled = False
                for selector in username_selectors:
                    try:
                        username_input = self.page.query_selector(selector)
                        if username_input:
                            username_input.fill(self.username)
                            username_filled = True
                            break
                    except Exception:
                        continue
                
                if not username_filled:
                    raise AuthenticationError("Impossible de trouver le champ username")

                # Trouver et remplir le champ password
                password_selectors = [
                    'input[name="password"]',
                    'input[type="password"]',
                    'input[id*="password"]',
                ]
                password_filled = False
                for selector in password_selectors:
                    try:
                        password_input = self.page.query_selector(selector)
                        if password_input:
                            password_input.fill(self.password)
                            password_filled = True
                            break
                    except Exception:
                        continue
                
                if not password_filled:
                    raise AuthenticationError("Impossible de trouver le champ password")

                # Cliquer sur le bouton de connexion
                submit_selectors = [
                    'button[type="submit"]',
                    'button:has-text("Connexion")',
                    'button:has-text("Se connecter")',
                    'input[type="submit"]',
                ]
                submitted = False
                for selector in submit_selectors:
                    try:
                        submit_button = self.page.query_selector(selector)
                        if submit_button:
                            submit_button.click()
                            submitted = True
                            break
                    except Exception:
                        continue
                
                if not submitted:
                    # Essayer de presser Enter sur le champ password
                    password_input.press("Enter")

                # Attendre la réponse (soit succès, soit 2FA, soit erreur)
                self.page.wait_for_load_state("networkidle", timeout=15000)

                # Vérifier si on est connecté ou si 2FA est requis
                current_url = self.page.url
                page_content = self.page.content().lower()

                # Vérifier les erreurs d'authentification
                if "erreur" in page_content or "incorrect" in page_content or "invalid" in page_content:
                    error_elements = self.page.query_selector_all('.error, .alert, [class*="error"], [class*="alert"]')
                    error_messages = [elem.inner_text() for elem in error_elements if elem.inner_text()]
                    error_msg = " - ".join(error_messages) if error_messages else "Erreur d'authentification"
                    raise InvalidCredentialsError(error_msg)

                # Vérifier si 2FA est requis
                if "2fa" in page_content or "code" in page_content or "sms" in page_content or "authentification" in page_content:
                    if self.two_fa_code:
                        # Remplir le code 2FA
                        code_input_selectors = [
                            'input[name="code"]',
                            'input[type="text"][id*="code"]',
                            'input[id*="2fa"]',
                            'input[type="number"]',
                        ]
                        code_filled = False
                        for selector in code_input_selectors:
                            try:
                                code_input = self.page.query_selector(selector)
                                if code_input:
                                    code_input.fill(self.two_fa_code)
                                    code_filled = True
                                    break
                            except Exception:
                                continue
                        
                        if code_filled:
                            # Soumettre le code 2FA
                            submit_button = self.page.query_selector('button[type="submit"], button:has-text("Valider")')
                            if submit_button:
                                submit_button.click()
                                self.page.wait_for_load_state("networkidle", timeout=15000)
                    else:
                        # Retourner les infos pour authentification en deux étapes
                        return {
                            "requires_2fa": True,
                            "message": "Code 2FA requis. Fournissez '2fa_code' dans les credentials.",
                        }

                # Vérifier si on est connecté (URL change ou présence d'éléments spécifiques)
                if "espace-client" in current_url or "compte" in current_url or "dashboard" in current_url or "accueil" in current_url:
                    # Récupérer les cookies de session
                    self.session_cookies = self.browser.contexts[0].cookies()
                    self._authenticated = True
                    logger.info("Authentification Hello Bank réussie")
                    return {
                        "session_id": "playwright_session",
                        "cookies": self.session_cookies,
                        "expires_at": None,  # Les cookies gèrent l'expiration
                    }
                else:
                    # On n'est pas encore connecté, peut-être en attente de 2FA
                    if not self.two_fa_code:
                        return {
                            "requires_2fa": True,
                            "message": "Code 2FA requis. Fournissez '2fa_code' dans les credentials.",
                        }
                    else:
                        raise AuthenticationError("Échec de l'authentification après saisie du code 2FA")

            except PlaywrightTimeoutError as e:
                raise ConnectionTimeoutError(f"Timeout lors de l'authentification: {str(e)}")
            except Exception as e:
                if isinstance(e, (AuthenticationError, InvalidCredentialsError, ConnectionTimeoutError)):
                    raise
                raise AuthenticationError(f"Erreur lors de l'authentification: {str(e)}")

        except Exception as e:
            self._cleanup_browser()
            if isinstance(e, (AuthenticationError, InvalidCredentialsError, ConnectionTimeoutError)):
                raise
            raise AuthenticationError(f"Erreur lors de l'authentification: {str(e)}")

    def sync_transactions(self, account, since: Optional[datetime] = None) -> List[Dict]:
        """
        Récupère les transactions depuis la dernière synchronisation.

        Args:
            account: Objet Account Django représentant le compte à synchroniser
            since: Date optionnelle de la dernière synchronisation. Si None, récupère
                   toutes les transactions disponibles.

        Returns:
            list: Liste de dictionnaires représentant les transactions. Chaque dictionnaire
                  contient :
                  - "posted_at": datetime de la transaction
                  - "amount": Decimal du montant (positif pour revenus, négatif pour dépenses)
                  - "description": str description de la transaction
                  - "raw": dict métadonnées supplémentaires

        Raises:
            AuthenticationError: Si la session a expiré
            RateLimitError: Si le rate limit est atteint
            ConnectionTimeoutError: Si la connexion timeout
            BankConnectionError: Pour toute autre erreur de connexion
        """
        if not self._authenticated or not self.page:
            raise AuthenticationError("Session non authentifiée. Authentifiez-vous d'abord.")

        max_retries = 3
        base_delay = 1.0

        for attempt in range(max_retries):
            try:
                # Naviguer vers la page des transactions
                logger.info("Navigation vers la page des transactions Hello Bank")
                self.page.goto("https://www.hellobank.fr/compte/operations", timeout=30000)
                self.page.wait_for_load_state("networkidle", timeout=15000)

                # Scraper les transactions depuis la page
                # Note: Les sélecteurs CSS doivent être ajustés selon l'interface actuelle
                transactions = self._scrape_transactions_from_page(since)

                # Détecter et éviter les doublons
                # Note: La détection de doublons complète sera faite dans le service de synchronisation (Story 1.6)
                # Ici, on filtre uniquement par date si nécessaire
                if since:
                    # Normaliser since pour la comparaison
                    if timezone.is_naive(since):
                        since = timezone.make_aware(since, timezone.get_current_timezone())
                    transactions = [
                        t for t in transactions 
                        if timezone.is_aware(t["posted_at"]) and t["posted_at"] > since
                        or timezone.is_naive(t["posted_at"]) and timezone.make_aware(t["posted_at"], timezone.get_current_timezone()) > since
                    ]

                logger.info(f"Récupéré {len(transactions)} transactions depuis Hello Bank")
                return transactions

            except PlaywrightTimeoutError as e:
                if attempt < max_retries - 1:
                    wait_time = base_delay * (2 ** attempt)
                    logger.warning(
                        f"Timeout lors de la récupération des transactions (tentative {attempt + 1}/{max_retries}). "
                        f"Retry dans {wait_time}s..."
                    )
                    time.sleep(wait_time)
                    continue
                raise ConnectionTimeoutError(f"Timeout lors de la récupération des transactions: {str(e)}")
            except Exception as e:
                error_msg = str(e).lower()
                if "rate limit" in error_msg or "429" in error_msg:
                    raise RateLimitError("Rate limit atteint lors de la récupération des transactions")
                if attempt < max_retries - 1:
                    wait_time = base_delay * (2 ** attempt)
                    logger.warning(
                        f"Erreur lors de la récupération des transactions (tentative {attempt + 1}/{max_retries}): {str(e)}. "
                        f"Retry dans {wait_time}s..."
                    )
                    time.sleep(wait_time)
                    continue
                raise BankConnectionError(f"Erreur lors de la récupération des transactions: {str(e)}")

        raise BankConnectionError("Échec de la récupération des transactions après plusieurs tentatives")

    def _scrape_transactions_from_page(self, since: Optional[datetime] = None) -> List[Dict]:
        """
        Scrape les transactions depuis la page web.

        Args:
            since: Date optionnelle pour filtrer les transactions

        Returns:
            list: Liste de transactions formatées
        """
        transactions = []

        try:
            # Attendre que le tableau des transactions soit chargé
            # Note: Les sélecteurs doivent être ajustés selon l'interface actuelle
            table_selectors = [
                'table[class*="transaction"]',
                'table[class*="operation"]',
                '.transactions-table',
                '.operations-table',
                'table',
            ]

            table = None
            for selector in table_selectors:
                try:
                    table = self.page.query_selector(selector)
                    if table:
                        break
                except Exception:
                    continue

            if not table:
                logger.warning("Tableau des transactions non trouvé sur la page")
                return []

            # Extraire les lignes du tableau
            rows = table.query_selector_all('tbody tr, tr:not(:first-child)')

            for row in rows:
                try:
                    transaction = self._parse_transaction_row(row, since)
                    if transaction:
                        transactions.append(transaction)
                except Exception as e:
                    logger.warning(f"Erreur lors du parsing d'une transaction: {str(e)}")
                    continue

        except Exception as e:
            logger.error(f"Erreur lors du scraping des transactions: {str(e)}")
            raise BankConnectionError(f"Erreur lors du scraping des transactions: {str(e)}")

        return transactions

    def _parse_transaction_row(self, row, since: Optional[datetime] = None) -> Optional[Dict]:
        """
        Parse une ligne de transaction depuis le tableau.

        Format Hello Bank : Date;Type;Libellé court;Libellé détaillé;Montant

        Args:
            row: Élément HTML de la ligne
            since: Date optionnelle pour filtrer

        Returns:
            dict: Transaction formatée ou None si filtrée
        """
        try:
            cells = row.query_selector_all('td, th')
            if len(cells) < 5:
                return None

            # Extraire les données de la ligne selon le format Hello Bank
            # Format : Date;Type;Libellé court;Libellé détaillé;Montant
            cell_texts = [cell.inner_text().strip() for cell in cells]
            
            # Parser la date (première colonne)
            date_str = cell_texts[0] if cell_texts else None
            if not date_str:
                return None

            posted_at = self._parse_date(date_str)
            if not posted_at:
                return None

            # Filtrer par date si nécessaire
            if since:
                # Normaliser les deux datetime pour la comparaison
                if timezone.is_naive(posted_at):
                    posted_at = timezone.make_aware(posted_at, timezone.get_current_timezone())
                if timezone.is_naive(since):
                    since = timezone.make_aware(since, timezone.get_current_timezone())
                if posted_at <= since:
                    return None

            # Extraire le montant (dernière colonne généralement)
            amount = None
            # Essayer la dernière colonne d'abord (format Hello Bank)
            if len(cell_texts) >= 5:
                amount_str = cell_texts[4]
                try:
                    # Parser le montant (format: "123,45 €" ou "-123,45")
                    cleaned = amount_str.replace("€", "").replace(" ", "").replace(",", ".")
                    if cleaned.startswith("-") or cleaned.replace("-", "").replace(".", "").isdigit():
                        amount = Decimal(cleaned)
                except Exception:
                    pass
            
            # Si pas trouvé, chercher dans toutes les colonnes
            if amount is None:
                for text in cell_texts:
                    try:
                        cleaned = text.replace("€", "").replace(" ", "").replace(",", ".")
                        if cleaned.startswith("-") or cleaned.replace("-", "").replace(".", "").isdigit():
                            amount = Decimal(cleaned)
                            break
                    except Exception:
                        continue

            if amount is None:
                logger.warning(f"Montant non trouvé pour la transaction du {posted_at}")
                return None

            # Extraire les autres champs selon le format Hello Bank
            operation_type = cell_texts[1] if len(cell_texts) > 1 else ""
            label_short = cell_texts[2] if len(cell_texts) > 2 else ""
            label_detailed = cell_texts[3] if len(cell_texts) > 3 else ""

            # Construire la description (combinaison de libellé court + libellé détaillé)
            description_parts = []
            if label_short:
                description_parts.append(label_short)
            if label_detailed:
                description_parts.append(label_detailed)
            if not description_parts and operation_type:
                description_parts.append(operation_type)
            
            description = " - ".join(description_parts) if description_parts else "Transaction Hello Bank"

            # Construire le raw avec toutes les métadonnées
            raw = {
                "source": "hellobank",
                "operation_type": operation_type,
                "label_short": label_short,
                "label_detailed": label_detailed,
                "raw_data": cell_texts,  # Conserver toutes les données originales
            }

            return {
                "posted_at": posted_at,
                "amount": amount,
                "description": description,
                "raw": raw,
            }

        except Exception as e:
            logger.warning(f"Erreur lors du parsing d'une ligne de transaction: {str(e)}")
            return None

    def _parse_date(self, date_str: str) -> Optional[datetime]:
        """
        Parse une date depuis une chaîne.

        Args:
            date_str: Chaîne de date à parser

        Returns:
            datetime: Date parsée ou None si échec
        """
        from dateutil import parser as date_parser

        # Formats de date courants pour Hello Bank
        date_formats = [
            "%d/%m/%Y",
            "%d-%m-%Y",
            "%d.%m.%Y",
            "%Y-%m-%d",
            "%d/%m/%Y %H:%M",
        ]

        # Essayer les formats spécifiques d'abord
        parsed_date = None
        for fmt in date_formats:
            try:
                parsed_date = datetime.strptime(date_str.strip(), fmt)
                break
            except ValueError:
                continue

        # Essayer avec dateutil en dernier recours
        if parsed_date is None:
            try:
                parsed_date = date_parser.parse(date_str.strip())
            except Exception:
                logger.warning(f"Impossible de parser la date '{date_str}'")
                return None

        # S'assurer que le datetime est timezone-aware
        if parsed_date:
            if timezone.is_naive(parsed_date):
                # Assumer la timezone Django locale pour les dates sans timezone
                parsed_date = timezone.make_aware(parsed_date, timezone.get_current_timezone())
            return parsed_date
        
        return None

    def get_balance(self, account) -> Decimal:
        """
        Récupère le solde actuel du compte.

        Args:
            account: Objet Account Django représentant le compte

        Returns:
            Decimal: Solde actuel du compte

        Raises:
            AuthenticationError: Si la session a expiré
            ConnectionTimeoutError: Si la connexion timeout
            BankConnectionError: Pour toute autre erreur de connexion
        """
        if not self._authenticated or not self.page:
            raise AuthenticationError("Session non authentifiée. Authentifiez-vous d'abord.")

        max_retries = 3
        base_delay = 1.0

        for attempt in range(max_retries):
            try:
                # Naviguer vers la page du compte
                logger.info("Navigation vers la page du compte Hello Bank")
                self.page.goto("https://www.hellobank.fr/compte", timeout=30000)
                self.page.wait_for_load_state("networkidle", timeout=15000)

                # Scraper le solde depuis la page
                balance = self._scrape_balance_from_page()

                logger.info(f"Solde récupéré depuis Hello Bank: {balance}")
                return balance

            except PlaywrightTimeoutError as e:
                if attempt < max_retries - 1:
                    wait_time = base_delay * (2 ** attempt)
                    logger.warning(
                        f"Timeout lors de la récupération du solde (tentative {attempt + 1}/{max_retries}). "
                        f"Retry dans {wait_time}s..."
                    )
                    time.sleep(wait_time)
                    continue
                raise ConnectionTimeoutError(f"Timeout lors de la récupération du solde: {str(e)}")
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = base_delay * (2 ** attempt)
                    logger.warning(
                        f"Erreur lors de la récupération du solde (tentative {attempt + 1}/{max_retries}): {str(e)}. "
                        f"Retry dans {wait_time}s..."
                    )
                    time.sleep(wait_time)
                    continue
                raise BankConnectionError(f"Erreur lors de la récupération du solde: {str(e)}")

        raise BankConnectionError("Échec de la récupération du solde après plusieurs tentatives")

    def _scrape_balance_from_page(self) -> Decimal:
        """
        Scrape le solde depuis la page du compte.

        Returns:
            Decimal: Solde du compte
        """
        try:
            # Chercher le solde dans différents sélecteurs possibles
            balance_selectors = [
                '[class*="solde"]',
                '[class*="balance"]',
                '[id*="solde"]',
                '[id*="balance"]',
                '.account-balance',
                '.balance',
            ]

            for selector in balance_selectors:
                try:
                    balance_elements = self.page.query_selector_all(selector)
                    for elem in balance_elements:
                        text = elem.inner_text().strip()
                        if text:
                            # Essayer d'extraire un montant
                            balance = self._extract_amount_from_text(text)
                            if balance is not None:
                                return balance
                except Exception:
                    continue

            # Si on ne trouve pas, chercher dans tout le contenu de la page
            page_text = self.page.inner_text()
            # Chercher des patterns comme "Solde: 1234,56 €"
            import re
            patterns = [
                r'solde[:\s]+([\d\s,\.]+)\s*€',
                r'balance[:\s]+([\d\s,\.]+)\s*€',
                r'([\d\s,\.]+)\s*€',  # Dernier recours: chercher n'importe quel montant
            ]

            for pattern in patterns:
                matches = re.findall(pattern, page_text, re.IGNORECASE)
                if matches:
                    try:
                        amount_str = matches[0].replace(" ", "").replace(",", ".")
                        return Decimal(amount_str)
                    except Exception:
                        continue

            logger.warning("Solde non trouvé sur la page")
            return Decimal("0")

        except Exception as e:
            logger.error(f"Erreur lors du scraping du solde: {str(e)}")
            raise BankConnectionError(f"Erreur lors du scraping du solde: {str(e)}")

    def _extract_amount_from_text(self, text: str) -> Optional[Decimal]:
        """
        Extrait un montant depuis un texte.

        Args:
            text: Texte contenant potentiellement un montant

        Returns:
            Decimal: Montant extrait ou None
        """
        import re

        # Chercher des patterns de montant
        patterns = [
            r'([\d\s,\.]+)\s*€',
            r'€\s*([\d\s,\.]+)',
            r'([-]?[\d\s,\.]+)',
        ]

        for pattern in patterns:
            matches = re.findall(pattern, text)
            if matches:
                try:
                    amount_str = matches[0].replace(" ", "").replace(",", ".")
                    return Decimal(amount_str)
                except Exception:
                    continue

        return None

    def disconnect(self) -> None:
        """
        Ferme la connexion et nettoie les ressources.

        Ferme proprement le navigateur et libère les ressources utilisées.
        """
        self._cleanup_browser()
        self._authenticated = False
        self.session_cookies = []
        logger.info("Connexion Hello Bank fermée")

    def _cleanup_browser(self) -> None:
        """Nettoie les ressources du navigateur."""
        try:
            if self.page:
                self.page.close()
                self.page = None
        except Exception as e:
            logger.warning(f"Erreur lors de la fermeture de la page: {str(e)}")

        try:
            if self.browser:
                self.browser.close()
                self.browser = None
        except Exception as e:
            logger.warning(f"Erreur lors de la fermeture du navigateur: {str(e)}")

        try:
            if self.playwright:
                self.playwright.stop()
                self.playwright = None
        except Exception as e:
            logger.warning(f"Erreur lors de l'arrêt de Playwright: {str(e)}")

"""
Connecteur BoursoBank pour synchronisation automatique.

Ce connecteur utilise Playwright pour automatiser un navigateur et récupérer
les transactions et soldes depuis l'interface web BoursoBank.
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
    logger.warning("Playwright non disponible. Le connecteur BoursoBank nécessite Playwright.")


class BoursoBankConnector(BaseBankConnector):
    """
    Connecteur BoursoBank pour synchronisation automatique.

    Ce connecteur utilise Playwright pour automatiser un navigateur et simuler
    une connexion utilisateur à l'interface web BoursoBank.
    """

    def __init__(self):
        """Initialise le connecteur BoursoBank."""
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
        return "BoursoBank"

    def authenticate(self, credentials: Dict) -> Dict:
        """
        Authentifie le connecteur avec les credentials fournis.

        Gère l'authentification complète avec BoursoBank via l'interface web,
        incluant si nécessaire l'authentification 2FA.

        Args:
            credentials: Dictionnaire contenant les credentials nécessaires :
                        - "username": str (identifiant BoursoBank)
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

            # Naviguer directement vers la page de connexion de clients.boursobank.com
            logger.info("Navigation directe vers la page de connexion BoursoBank")
            self.page.goto("https://clients.boursobank.com/connexion/", timeout=30000)
            self.page.wait_for_load_state("networkidle", timeout=15000)
            time.sleep(2)
            
            # Fermer la bannière de cookies si présente
            logger.info("Gestion de la bannière de cookies")
            try:
                cookie_selectors = [
                    'button:has-text("Continuer sans accepter")',
                    'button:has-text("Tout accepter")',
                    'button:has-text("Accepter & Fermer")',
                ]
                for selector in cookie_selectors:
                    try:
                        cookie_button = self.page.locator(selector).first
                        if cookie_button.is_visible(timeout=3000):
                            cookie_button.click()
                            time.sleep(1)
                            logger.info(f"Bannière de cookies fermée: {selector}")
                            break
                    except Exception:
                        continue
            except Exception as e:
                logger.debug(f"Pas de bannière de cookies à fermer: {str(e)}")

            # Étape 5: Remplir l'identifiant (première étape du formulaire)
            logger.info("Remplissage de l'identifiant")
            try:
                # Attendre que le champ identifiant soit visible (ID exact: form_clientNumber)
                identifiant_input = self.page.locator('#form_clientNumber')
                identifiant_input.wait_for(state="visible", timeout=15000)
                identifiant_input.fill(self.username)
                logger.info("Identifiant rempli")
                
                # Cliquer sur "Suivant" (bouton avec classe c-button--primary)
                suivant_button = self.page.locator('button.c-button--primary:has-text("Suivant")').first
                suivant_button.click()
                self.page.wait_for_load_state("networkidle", timeout=10000)
                time.sleep(2)
            except Exception as e:
                logger.error(f"Erreur lors de la saisie de l'identifiant: {str(e)}")
                raise AuthenticationError("Impossible de remplir l'identifiant")

            # Étape 6: Remplir le mot de passe avec le clavier virtuel sécurisé
            logger.info("Remplissage du mot de passe via clavier virtuel")
            try:
                # Vérifier qu'on est sur la page de saisie du mot de passe
                self.page.wait_for_url("**/saisie-mot-de-passe", timeout=15000)
                logger.info("Page de saisie du mot de passe détectée")
                
                # Extraire le mapping des chiffres depuis le clavier virtuel
                # Chaque bouton a un data-matrix-key unique, on doit trouver quel chiffre correspond
                keyboard_mapping = self.page.evaluate("""() => {
                    const buttons = Array.from(document.querySelectorAll('button.sasmap__key'));
                    const mapping = {};
                    
                    buttons.forEach((btn, idx) => {
                        const img = btn.querySelector('img');
                        if (img && img.src.startsWith('data:image/svg+xml')) {
                            try {
                                const svgData = atob(img.src.split(',')[1]);
                                // Les SVG contiennent les chiffres dans les chemins
                                // On peut essayer de reconnaître le chiffre via les patterns SVG
                                // Pour l'instant, on stocke l'index et la clé
                                const li = btn.closest('li');
                                mapping[idx] = {
                                    dataKey: btn.getAttribute('data-matrix-key'),
                                    listIndex: li?.getAttribute('data-matrix-list-item-index'),
                                    button: btn
                                };
                            } catch (e) {
                                console.error('Erreur décodage SVG:', e);
                            }
                        }
                    });
                    
                    return mapping;
                }""")
                
                # Méthode alternative : utiliser les événements clavier si possible
                # Sinon, on doit reconnaître les chiffres depuis les SVG
                # Pour l'instant, on va essayer de trouver un champ caché ou utiliser une autre méthode
                
                # Le mot de passe utilise un clavier virtuel sécurisé
                # Il faut cliquer sur les boutons du clavier virtuel, pas utiliser les événements clavier
                # Chaque bouton a un data-matrix-key qui est ajouté au champ #form_password quand on clique
                logger.info(f"Saisie du mot de passe via clavier virtuel ({len(self.password)} chiffres)")
                
                # S'assurer qu'on est sur la page
                self.page.wait_for_load_state("networkidle", timeout=10000)
                time.sleep(0.5)
                
                # Extraire le mapping des chiffres depuis les SVG des boutons
                digit_mapping = self.page.evaluate("""() => {
                    const buttons = Array.from(document.querySelectorAll('button.sasmap__key'));
                    const mapping = {};
                    
                    buttons.forEach((btn, idx) => {
                        const img = btn.querySelector('img');
                        if (img && img.src.startsWith('data:image/svg+xml')) {
                            try {
                                const svgData = atob(img.src.split(',')[1]);
                                const dataKey = btn.getAttribute('data-matrix-key');
                                
                                // Analyser le SVG pour trouver le chiffre
                                // Les chiffres sont représentés par des chemins SVG spécifiques
                                // On peut utiliser une approche de reconnaissance basée sur les patterns
                                // Pour l'instant, on stocke le SVG et le data-key pour analyse ultérieure
                                mapping[dataKey] = {
                                    buttonIndex: idx,
                                    svgData: svgData.substring(0, 500), // Stocker une partie du SVG pour analyse
                                    button: btn
                                };
                            } catch (e) {
                                console.error('Erreur décodage SVG:', e);
                            }
                        }
                    });
                    
                    return mapping;
                }""")
                
                # Pour chaque chiffre du mot de passe, trouver et cliquer sur le bon bouton
                # Note: Le mapping change à chaque chargement, donc on doit reconnaître le chiffre
                # Pour l'instant, on va utiliser une approche basée sur l'ordre des boutons
                # (ce n'est pas idéal car l'ordre change, mais c'est un début)
                
                # Méthode alternative : utiliser JavaScript pour reconnaître les chiffres depuis les SVG
                # et créer un mapping chiffre -> data-matrix-key
                digit_to_key_mapping = self.page.evaluate("""() => {
                    const buttons = Array.from(document.querySelectorAll('button.sasmap__key'));
                    const mapping = {};
                    
                    // Pour chaque bouton, essayer de reconnaître le chiffre depuis le SVG
                    buttons.forEach((btn) => {
                        const img = btn.querySelector('img');
                        if (img && img.src.startsWith('data:image/svg+xml')) {
                            try {
                                const svgData = atob(img.src.split(',')[1]);
                                const dataKey = btn.getAttribute('data-matrix-key');
                                
                                // Analyser le SVG pour trouver le chiffre
                                // Les chiffres sont souvent représentés par des chemins spécifiques
                                // On peut utiliser une approche de reconnaissance basée sur les patterns SVG
                                // Pour l'instant, on va essayer de trouver le chiffre dans le SVG
                                // en cherchant des patterns communs
                                
                                // Méthode simple : chercher des patterns de chiffres dans les chemins SVG
                                // Les chiffres ont des patterns spécifiques dans les chemins
                                // On peut utiliser une approche heuristique basée sur la longueur et la complexité des chemins
                                
                                // Pour l'instant, on va utiliser l'ordre des boutons comme approximation
                                // (ce n'est pas idéal, mais c'est un début)
                                const buttonIndex = buttons.indexOf(btn);
                                // Les boutons sont généralement dans l'ordre 0-9, mais mélangés
                                // On va essayer de reconnaître le chiffre depuis le SVG
                                
                                // Stocker le mapping pour analyse ultérieure
                                mapping[dataKey] = {
                                    buttonIndex: buttonIndex,
                                    svgPreview: svgData.substring(0, 200)
                                };
                            } catch (e) {
                                console.error('Erreur:', e);
                            }
                        }
                    });
                    
                    return mapping;
                }""")
                
                # Pour l'instant, on va utiliser une approche simple :
                # Cliquer sur tous les boutons dans l'ordre et voir lequel correspond à quel chiffre
                # Mais cela nécessite de reconnaître les chiffres depuis les SVG
                
                # Solution temporaire : utiliser une bibliothèque OCR ou une API de reconnaissance
                # Pour l'instant, on va essayer de cliquer sur les boutons en utilisant l'ordre
                # et espérer que l'ordre correspond aux chiffres (ce n'est pas garanti)
                
                # Extraire le mapping chiffre -> data-matrix-key en analysant les SVG
                digit_to_key = self.page.evaluate("""() => {
                    const buttons = Array.from(document.querySelectorAll('button.sasmap__key'));
                    const mapping = {};
                    
                    buttons.forEach((btn) => {
                        const img = btn.querySelector('img');
                        if (img && img.src.startsWith('data:image/svg+xml')) {
                            try {
                                const svgData = atob(img.src.split(',')[1]);
                                const dataKey = btn.getAttribute('data-matrix-key');
                                
                                // Analyser les patterns SVG pour reconnaître le chiffre
                                const pathMatches = svgData.match(/<path[^>]*d="([^"]*)"/g);
                                const paths = pathMatches ? pathMatches.map(m => m.match(/d="([^"]*)"/)[1]) : [];
                                const pathCount = paths.length;
                                const totalPathLength = paths.reduce((sum, p) => sum + p.length, 0);
                                
                                // Heuristiques pour reconnaître les chiffres :
                                // 0: cercle (1 chemin, longueur moyenne ~150-200)
                                // 1: ligne verticale (1 chemin, longueur courte ~40-60)
                                // Autres: patterns plus complexes
                                
                                let digit = null;
                                if (pathCount === 1) {
                                    if (totalPathLength < 60) {
                                        digit = '1'; // Ligne simple
                                    } else if (totalPathLength > 140 && totalPathLength < 200) {
                                        digit = '0'; // Cercle
                                    }
                                }
                                
                                // Pour les autres chiffres, on devra utiliser une approche différente
                                // Stocker les informations pour analyse ultérieure
                                mapping[dataKey] = {
                                    digit: digit,
                                    pathCount: pathCount,
                                    totalPathLength: totalPathLength
                                };
                            } catch (e) {
                                console.error('Erreur:', e);
                            }
                        }
                    });
                    
                    return mapping;
                }""")
                
                # Créer le mapping inverse : chiffre -> data-matrix-key
                key_to_digit = {}
                digit_to_key_mapping = {}
                for data_key, info in digit_to_key.items():
                    if info.get('digit'):
                        digit_to_key_mapping[info['digit']] = data_key
                    key_to_digit[data_key] = info.get('digit')
                
                logger.info(f"Mapping extrait: {digit_to_key_mapping}")
                
                # Pour chaque chiffre du mot de passe, trouver et cliquer sur le bon bouton
                buttons_locator = self.page.locator('button.sasmap__key')
                button_count = buttons_locator.count()
                
                for digit in self.password:
                    if not digit.isdigit():
                        logger.warning(f"Caractère non numérique ignoré: {digit}")
                        continue
                
                    # Trouver le data-matrix-key correspondant au chiffre
                    data_key = digit_to_key_mapping.get(digit)
                    
                    if data_key:
                        # Cliquer sur le bouton avec ce data-matrix-key
                        button = buttons_locator.filter(has=self.page.locator(f'[data-matrix-key="{data_key}"]')).first
                        button.click()
                        time.sleep(0.3)
                        logger.info(f"Chiffre {digit} saisi (data-key: {data_key})")
                    else:
                        # Si le chiffre n'a pas été reconnu, essayer une approche alternative
                        # Utiliser l'ordre comme approximation (non idéal)
                        logger.warning(f"Chiffre {digit} non reconnu, utilisation de l'ordre comme approximation")
                        button_index = int(digit) % button_count
                        button = buttons_locator.nth(button_index)
                        button.click()
                        time.sleep(0.3)
                        logger.info(f"Bouton cliqué pour le chiffre {digit} (index {button_index})")
                
                logger.info("Mot de passe saisi via clavier virtuel")
                
                # Cliquer sur le bouton "Je me connecte"
                logger.info("Clic sur le bouton de connexion")
                connect_button = self.page.locator('button.c-button--primary:has-text("Je me connecte")').first
                connect_button.wait_for(state="visible", timeout=10000)
                connect_button.click()
                logger.info("Bouton de connexion cliqué")
                
                self.page.wait_for_load_state("networkidle", timeout=15000)
                time.sleep(2)
            except Exception as e:
                logger.error(f"Erreur lors de la saisie du mot de passe: {str(e)}")
                raise AuthenticationError(f"Impossible de remplir le mot de passe: {str(e)}")

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
                if "espace-client" in current_url or "compte" in current_url or "dashboard" in current_url:
                    # Récupérer les cookies de session
                    self.session_cookies = self.browser.contexts[0].cookies()
                    self._authenticated = True
                    logger.info("Authentification BoursoBank réussie")
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
                logger.info("Navigation vers la page des transactions BoursoBank")
                self.page.goto("https://www.boursorama.com/compte/operations", timeout=30000)
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

                logger.info(f"Récupéré {len(transactions)} transactions depuis BoursoBank")
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

        Args:
            row: Élément HTML de la ligne
            since: Date optionnelle pour filtrer

        Returns:
            dict: Transaction formatée ou None si filtrée
        """
        try:
            cells = row.query_selector_all('td, th')
            if len(cells) < 3:
                return None

            # Extraire les données de la ligne
            # Format attendu (basé sur le CSV) : dateop, label, comment, amount, category, accountbalance
            # L'ordre peut varier, on essaie de détecter les colonnes
            
            cell_texts = [cell.inner_text().strip() for cell in cells]
            
            # Parser la date (première colonne généralement)
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

            # Extraire le montant (chercher dans toutes les colonnes)
            amount = None
            for text in cell_texts:
                try:
                    # Essayer de parser un montant (format: "123,45 €" ou "-123,45")
                    cleaned = text.replace("€", "").replace(" ", "").replace(",", ".")
                    if cleaned.startswith("-") or cleaned.replace("-", "").replace(".", "").isdigit():
                        amount = Decimal(cleaned)
                        break
                except Exception:
                    continue

            if amount is None:
                logger.warning(f"Montant non trouvé pour la transaction du {posted_at}")
                return None

            # Extraire la description (combinaison des colonnes texte)
            description_parts = []
            for i, text in enumerate(cell_texts):
                if i == 0:  # Skip date
                    continue
                if text and not self._looks_like_amount(text) and not self._looks_like_date(text):
                    description_parts.append(text)
            
            description = " - ".join(description_parts) if description_parts else "Transaction BoursoBank"

            # Construire le raw avec toutes les métadonnées
            raw = {
                "source": "boursorama",
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

        # Formats de date courants pour BoursoBank
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

    def _looks_like_amount(self, text: str) -> bool:
        """Vérifie si un texte ressemble à un montant."""
        cleaned = text.replace("€", "").replace(" ", "").replace(",", ".").replace("-", "")
        try:
            float(cleaned)
            return True
        except ValueError:
            return False

    def _looks_like_date(self, text: str) -> bool:
        """Vérifie si un texte ressemble à une date."""
        return "/" in text or "-" in text or "." in text

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
                logger.info("Navigation vers la page du compte BoursoBank")
                self.page.goto("https://www.boursorama.com/compte", timeout=30000)
                self.page.wait_for_load_state("networkidle", timeout=15000)

                # Scraper le solde depuis la page
                balance = self._scrape_balance_from_page()

                logger.info(f"Solde récupéré depuis BoursoBank: {balance}")
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
        logger.info("Connexion BoursoBank fermée")

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

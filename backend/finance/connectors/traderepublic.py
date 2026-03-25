"""
Connecteur Trade Republic pour synchronisation automatique.

Ce connecteur refactorise le code existant de traderepublic_scraper.py
pour l'intégrer dans l'architecture modulaire de connecteurs bancaires.
"""

import asyncio
import json
import logging
import time
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional

import requests
import websockets

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

# Réutiliser les fonctions async depuis le module existant
# On les importe directement pour éviter la duplication
from finance.importers.traderepublic_scraper import (
    apply_cookies_to_http_session,
    connect_to_websocket,
    create_tr_requests_session,
    fetch_all_transactions,
    fetch_available_cash,
    fetch_portfolio,
    generate_tr_device_info,
    headers_to_dict,
    normalize_phone_number_for_tr,
    traderepublic_error_message_for_failed_response,
    tr_countdown_from_login_payload,
    tr_merged_auth_headers,
    tr_warmup_login_page,
)


class TradeRepublicConnector(BaseBankConnector):
    """
    Connecteur Trade Republic pour synchronisation automatique.

    Ce connecteur utilise l'API REST pour l'authentification et WebSocket
    pour récupérer les transactions, soldes et portefeuilles.
    """

    def __init__(self):
        """Initialise le connecteur Trade Republic."""
        self.phone_number: Optional[str] = None
        self.pin: Optional[str] = None
        self.process_id: Optional[str] = None
        self.countdown: Optional[int] = None
        self.token: Optional[str] = None
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.session = create_tr_requests_session()
        self._waf_token: str = ""
        self._device_info: str = ""

    @property
    def provider_name(self) -> str:
        """Retourne le nom du provider bancaire."""
        return "Trade Republic"

    def authenticate(self, credentials: Dict) -> Dict:
        """
        Authentifie le connecteur avec les credentials fournis.

        Gère l'authentification complète avec Trade Republic, incluant
        l'authentification 2FA si nécessaire.

        Args:
            credentials: Dictionnaire contenant les credentials nécessaires :
                        - "phone_number": str (numéro de téléphone)
                        - "pin": str (code PIN)
                        - "2fa_code": str (optionnel, code 2FA pour authentification complète)

        Returns:
            dict: Informations de session :
                  - Si 2FA complète : {"token": str, "expires_at": datetime}
                  - Si 2FA requise : {"process_id": str, "countdown": int, "requires_2fa": True}

        Raises:
            InvalidCredentialsError: Si les credentials sont invalides
            AuthenticationError: Si l'authentification échoue
            ConnectionTimeoutError: Si la connexion timeout
        """
        phone_number = credentials.get("phone_number")
        pin = credentials.get("pin")
        two_fa_code = credentials.get("2fa_code")

        if not phone_number or not pin:
            raise InvalidCredentialsError("phone_number et pin sont requis")

        try:
            self.phone_number = normalize_phone_number_for_tr(phone_number)
        except ValueError as e:
            raise InvalidCredentialsError(str(e))
        self.pin = pin

        try:
            # Initier la connexion
            login_info = self._initiate_login()

            # Si le code 2FA est fourni, compléter l'authentification
            if two_fa_code:
                token = self._verify_2fa(two_fa_code)
                self.token = token
                logger.info("Authentification Trade Republic réussie avec 2FA")
                return {"token": token, "expires_at": None}  # Trade Republic ne fournit pas d'expiration
            else:
                # Retourner les infos pour authentification en deux étapes
                return {
                    "process_id": login_info["process_id"],
                    "countdown": login_info["countdown"],
                    "requires_2fa": True,
                }
        except requests.exceptions.Timeout as e:
            raise ConnectionTimeoutError(f"Timeout lors de l'authentification: {str(e)}")
        except requests.exceptions.RequestException as e:
            raise AuthenticationError(f"Erreur lors de l'authentification: {str(e)}")
        except ValueError as e:
            # Les erreurs ValueError du code existant sont converties en exceptions appropriées
            error_msg = str(e)
            if "numéro de téléphone" in error_msg.lower() or "pin" in error_msg.lower():
                raise InvalidCredentialsError(error_msg)
            raise AuthenticationError(error_msg)

    def _initiate_login(self) -> Dict:
        """
        Initie la connexion et retourne le process_id et countdown.

        Returns:
            dict: {"process_id": str, "countdown": int}

        Raises:
            InvalidCredentialsError: Si les credentials sont invalides
            ConnectionTimeoutError: Si la connexion timeout
        """
        try:
            try:
                from finance.importers.traderepublic_playwright import fetch_tr_waf_context_playwright

                waf_token, extra_cookies = fetch_tr_waf_context_playwright()
                self._waf_token = waf_token
                apply_cookies_to_http_session(self.session, extra_cookies)
            except Exception as e:
                logger.debug("Trade Republic: jeton WAF (Playwright) non récupéré: %s", e)
                self._waf_token = ""
            self._device_info = generate_tr_device_info()
            self.session.headers.update(tr_merged_auth_headers(self._waf_token, self._device_info))

            tr_warmup_login_page(self.session)
            response = self.session.post(
                "https://api.traderepublic.com/api/v1/auth/web/login",
                json={"phoneNumber": self.phone_number, "pin": self.pin},
                timeout=30.0,
            )

            if response.status_code != 200:
                msg = traderepublic_error_message_for_failed_response(response)
                if response.status_code == 403:
                    raise AuthenticationError(msg)
                raise InvalidCredentialsError(
                    f"Échec de l'authentification ({response.status_code}): {msg}"
                )

            try:
                data = response.json()
            except json.JSONDecodeError:
                raise InvalidCredentialsError(
                    "Réponse Trade Republic invalide (non JSON). Vérifiez le réseau ou réessayez plus tard."
                )
            self.process_id = data.get("processId")
            self.countdown = tr_countdown_from_login_payload(data)

            if not self.process_id:
                raise InvalidCredentialsError(
                    "Échec de l'initialisation de la connexion. Vérifiez votre numéro de téléphone et PIN."
                )

            return {
                "process_id": self.process_id,
                "countdown": self.countdown,
            }
        except requests.exceptions.Timeout:
            raise ConnectionTimeoutError("Timeout lors de l'initiation de la connexion")
        except requests.exceptions.RequestException as e:
            raise AuthenticationError(f"Erreur lors de l'initiation de la connexion: {str(e)}")

    def resend_2fa(self) -> None:
        """
        Renvoie le code 2FA par SMS.

        Raises:
            ValueError: Si process_id n'est pas défini
            AuthenticationError: Si l'envoi échoue
        """
        if not self.process_id:
            raise ValueError("Process ID manquant. Initiez d'abord la connexion.")

        try:
            response = self.session.post(
                f"https://api.traderepublic.com/api/v1/auth/web/login/{self.process_id}/resend",
                timeout=30.0,
            )
            if response.status_code != 200:
                raise AuthenticationError(f"Échec de l'envoi du code 2FA (Status: {response.status_code})")
        except requests.exceptions.Timeout:
            raise ConnectionTimeoutError("Timeout lors de l'envoi du code 2FA")
        except requests.exceptions.RequestException as e:
            raise AuthenticationError(f"Erreur lors de l'envoi du code 2FA: {str(e)}")

    def _verify_2fa(self, code: str) -> str:
        """
        Vérifie le code 2FA et retourne le token de session.

        Args:
            code: Code 2FA reçu par SMS

        Returns:
            str: Token de session

        Raises:
            InvalidCredentialsError: Si le code 2FA est invalide
            AuthenticationError: Si la vérification échoue
            ConnectionTimeoutError: Si la connexion timeout
        """
        if not self.process_id:
            raise ValueError("Process ID manquant. Initiez d'abord la connexion.")

        if not code or not code.strip():
            raise InvalidCredentialsError("Code 2FA vide.")

        code = code.strip()

        try:
            response = self.session.post(
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
                raise InvalidCredentialsError(f"{error_msg} (Status: {response.status_code})")

            # Extraire le token depuis les cookies
            session_token = response.cookies.get("tr_session")
            if not session_token:
                # Essayer avec headers_to_dict en fallback
                response_headers = headers_to_dict(response)
                session_token = response_headers.get("Set-Cookie", {}).get("tr_session")

            if not session_token:
                raise AuthenticationError("Token de connexion introuvable dans les cookies.")

            return session_token
        except requests.exceptions.Timeout:
            raise ConnectionTimeoutError("Timeout lors de la vérification 2FA")
        except requests.exceptions.RequestException as e:
            raise AuthenticationError(f"Erreur lors de la vérification 2FA: {str(e)}")

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
            RateLimitError: Si le rate limit de l'API est atteint
            ConnectionTimeoutError: Si la connexion timeout
            BankConnectionError: Pour toute autre erreur de connexion
        """
        if not self.token:
            raise AuthenticationError("Token de session manquant. Authentifiez-vous d'abord.")

        max_retries = 3
        base_delay = 1.0

        for attempt in range(max_retries):
            try:
                # Récupérer toutes les transactions via WebSocket
                all_transactions = asyncio.run(fetch_all_transactions(self.token, extract_details=False))

                # Transformer les transactions au format standard
                formatted_transactions = []
                for transaction in all_transactions:
                    formatted = self._format_transaction(transaction, since)
                    if formatted:
                        formatted_transactions.append(formatted)

                logger.info(f"Récupéré {len(formatted_transactions)} transactions depuis Trade Republic")
                return formatted_transactions

            except asyncio.TimeoutError as e:
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

    def _format_transaction(self, transaction: Dict, since: Optional[datetime] = None) -> Optional[Dict]:
        """
        Formate une transaction Trade Republic au format standard.

        Args:
            transaction: Transaction brute depuis l'API Trade Republic
            since: Date optionnelle pour filtrer les transactions

        Returns:
            dict: Transaction formatée ou None si filtrée
        """
        # Parser le timestamp
        timestamp_str = transaction.get("timestamp")
        if not timestamp_str:
            logger.warning("Transaction sans timestamp ignorée")
            return None

        try:
            # Le timestamp Trade Republic est en format ISO 8601
            # Gérer différents formats possibles
            timestamp_clean = timestamp_str.replace("Z", "+00:00")
            if timestamp_clean.endswith("+00:00"):
                posted_at = datetime.fromisoformat(timestamp_clean)
            else:
                # Essayer de parser directement
                posted_at = datetime.fromisoformat(timestamp_str)
            
            # S'assurer que le datetime est timezone-aware
            # Si le timestamp est en UTC mais sans timezone, l'ajouter
            if posted_at.tzinfo is None:
                # Assumer UTC si pas de timezone spécifiée
                posted_at = posted_at.replace(tzinfo=timezone.utc)
            
            # Convertir en timezone Django si nécessaire
            if settings.USE_TZ:
                # Convertir UTC vers la timezone Django locale
                posted_at = posted_at.astimezone(timezone.get_current_timezone())
        except (ValueError, AttributeError) as e:
            logger.warning(f"Impossible de parser le timestamp '{timestamp_str}': {str(e)}")
            return None

        # Filtrer par date si nécessaire
        # Normaliser les deux datetime pour la comparaison
        if since:
            # S'assurer que since est timezone-aware pour la comparaison
            if timezone.is_naive(since):
                since = timezone.make_aware(since, timezone.get_current_timezone())
            if posted_at <= since:
                return None

        # Extraire le montant
        amount_value = None
        if "amount" in transaction and isinstance(transaction["amount"], dict):
            amount_value = transaction["amount"].get("value")
        elif "value" in transaction:
            amount_value = transaction["value"]

        if amount_value is None:
            logger.warning(f"Transaction sans montant ignorée: {transaction.get('id')}")
            return None

        try:
            amount = Decimal(str(amount_value))
        except (ValueError, TypeError):
            logger.warning(f"Impossible de convertir le montant '{amount_value}' en Decimal")
            return None

        # Déterminer si c'est une dépense ou un revenu
        # Trade Republic utilise des montants positifs/négatifs selon le type de transaction
        # On garde le signe tel quel

        # Construire la description
        description_parts = []
        if transaction.get("type"):
            description_parts.append(transaction["type"])
        if transaction.get("title"):
            description_parts.append(transaction["title"])
        if transaction.get("description"):
            description_parts.append(transaction["description"])

        description = " - ".join(description_parts) if description_parts else "Transaction Trade Republic"

        # Construire le raw avec toutes les métadonnées
        raw = {
            "source": "traderepublic",
            "transaction_id": transaction.get("id"),
            "type": transaction.get("type"),
            "instrument": transaction.get("instrument"),
            "isin": transaction.get("isin"),
            "raw_data": transaction,  # Conserver toutes les données originales
        }

        return {
            "posted_at": posted_at,
            "amount": amount,
            "description": description,
            "raw": raw,
        }

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
        if not self.token:
            raise AuthenticationError("Token de session manquant. Authentifiez-vous d'abord.")

        max_retries = 3
        base_delay = 1.0

        for attempt in range(max_retries):
            try:
                cash_data = asyncio.run(fetch_available_cash(self.token))
                if cash_data and isinstance(cash_data, list) and len(cash_data) > 0:
                    balance_value = cash_data[0].get("value") or cash_data[0].get("amount")
                    if balance_value is not None:
                        return Decimal(str(balance_value))
                    else:
                        logger.warning("Données de solde sans valeur disponible")
                        return Decimal("0")
                else:
                    logger.warning("Aucune donnée de solde disponible")
                    return Decimal("0")
            except asyncio.TimeoutError as e:
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

    def sync_portfolio_valuations(self, account) -> Dict[str, Decimal]:
        """
        Synchronise les valorisations de portefeuille (PEA/CTO/CRYPTO).

        Args:
            account: Objet Account Django représentant le compte

        Returns:
            dict: Valorisations par type de portefeuille :
                  {"PEA": Decimal(...), "CTO": Decimal(...), "CRYPTO": Decimal(...)}

        Raises:
            AuthenticationError: Si la session a expiré
            ConnectionTimeoutError: Si la connexion timeout
            BankConnectionError: Pour toute autre erreur de connexion
        """
        if not self.token:
            raise AuthenticationError("Token de session manquant. Authentifiez-vous d'abord.")

        max_retries = 3
        base_delay = 1.0

        for attempt in range(max_retries):
            try:
                portfolio_data = asyncio.run(fetch_portfolio(self.token))
                valuations = {}

                # Parser les données de portefeuille
                # Le format exact dépend de la réponse de l'API Trade Republic
                # On essaie de détecter les différents types de portefeuille
                if portfolio_data:
                    for portfolio_type, data in portfolio_data.items():
                        if isinstance(data, dict):
                            # Chercher les valorisations dans les données
                            # Trade Republic peut retourner différentes structures
                            # On cherche les champs communs : "value", "totalValue", "valuation", etc.
                            value = None
                            for key in ["totalValue", "value", "valuation", "total"]:
                                if key in data:
                                    value = data[key]
                                    break

                            # Si on trouve une valeur, essayer de déterminer le type
                            if value is not None:
                                # Essayer de déterminer le type depuis les données
                                portfolio_type_name = self._detect_portfolio_type(data, portfolio_type)
                                if portfolio_type_name:
                                    try:
                                        valuations[portfolio_type_name] = Decimal(str(value))
                                    except (ValueError, TypeError):
                                        logger.warning(f"Impossible de convertir la valorisation '{value}' en Decimal")
                        elif isinstance(data, list):
                            # Si c'est une liste, traiter chaque élément
                            for item in data:
                                if isinstance(item, dict):
                                    value = item.get("value") or item.get("totalValue")
                                    portfolio_type_name = self._detect_portfolio_type(item, portfolio_type)
                                    if value is not None and portfolio_type_name:
                                        try:
                                            current_value = valuations.get(portfolio_type_name, Decimal("0"))
                                            valuations[portfolio_type_name] = current_value + Decimal(str(value))
                                        except (ValueError, TypeError):
                                            logger.warning(f"Impossible de convertir la valorisation '{value}' en Decimal")

                logger.info(f"Valorisations récupérées: {valuations}")
                return valuations

            except asyncio.TimeoutError as e:
                if attempt < max_retries - 1:
                    wait_time = base_delay * (2 ** attempt)
                    logger.warning(
                        f"Timeout lors de la récupération du portefeuille (tentative {attempt + 1}/{max_retries}). "
                        f"Retry dans {wait_time}s..."
                    )
                    time.sleep(wait_time)
                    continue
                raise ConnectionTimeoutError(f"Timeout lors de la récupération du portefeuille: {str(e)}")
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = base_delay * (2 ** attempt)
                    logger.warning(
                        f"Erreur lors de la récupération du portefeuille (tentative {attempt + 1}/{max_retries}): {str(e)}. "
                        f"Retry dans {wait_time}s..."
                    )
                    time.sleep(wait_time)
                    continue
                raise BankConnectionError(f"Erreur lors de la récupération du portefeuille: {str(e)}")

        raise BankConnectionError("Échec de la récupération du portefeuille après plusieurs tentatives")

    def _detect_portfolio_type(self, data: Dict, default_type: str) -> Optional[str]:
        """
        Détecte le type de portefeuille depuis les données.

        Args:
            data: Données du portefeuille
            default_type: Type par défaut depuis la clé du dict

        Returns:
            str: Type de portefeuille (PEA, CTO, CRYPTO) ou None
        """
        # Chercher des indices dans les données
        type_str = str(data.get("type", "")).upper()
        name_str = str(data.get("name", "")).upper()

        if "PEA" in type_str or "PEA" in name_str:
            return "PEA"
        elif "CTO" in type_str or "CTO" in name_str or "COMPTE-TITRES" in name_str:
            return "CTO"
        elif "CRYPTO" in type_str or "CRYPTO" in name_str:
            return "CRYPTO"

        # Si on ne trouve rien, retourner None (on ne peut pas déterminer)
        return None

    def disconnect(self) -> None:
        """
        Ferme la connexion et nettoie les ressources.

        Ferme proprement toutes les connexions ouvertes et libère les ressources utilisées.
        """
        if self.websocket:
            try:
                # Vérifier si c'est une coroutine ou une méthode
                if asyncio.iscoroutinefunction(self.websocket.close):
                    asyncio.run(self.websocket.close())
                elif callable(self.websocket.close):
                    # Si c'est une méthode normale, l'appeler directement
                    self.websocket.close()
            except Exception as e:
                logger.warning(f"Erreur lors de la fermeture du WebSocket: {str(e)}")
            finally:
                self.websocket = None

        # Nettoyer les ressources
        self.token = None
        self.process_id = None
        self.countdown = None
        logger.info("Connexion Trade Republic fermée")

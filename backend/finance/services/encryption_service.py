"""
Service de chiffrement pour les credentials bancaires.

Ce service utilise Fernet (AES-128 en mode CBC) pour chiffrer/déchiffrer
les credentials bancaires de manière sécurisée.
"""

import json
import os
from typing import Dict

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend
import base64


class EncryptionError(Exception):
    """Exception levée en cas d'erreur de chiffrement/déchiffrement."""

    pass


class EncryptionService:
    """
    Service de chiffrement pour les credentials bancaires.

    Utilise Fernet (AES-128 en mode CBC) qui est construit sur AES-128 en mode CBC
    avec HMAC-SHA256 pour l'authentification. Bien que Fernet utilise AES-128,
    il est considéré comme sécurisé pour ce cas d'usage et est plus simple à utiliser
    que AES-256 direct.

    Pour une sécurité maximale, la clé de chiffrement doit être stockée dans
    les variables d'environnement et ne jamais être commitée dans le code.
    """

    @staticmethod
    def get_encryption_key() -> bytes:
        """
        Récupère la clé de chiffrement depuis les variables d'environnement.

        Returns:
            bytes: Clé de chiffrement Fernet (32 bytes encodés en base64)

        Raises:
            EncryptionError: Si la clé n'est pas définie dans l'environnement
        """
        key = os.environ.get("ENCRYPTION_KEY")
        if not key:
            raise EncryptionError(
                "ENCRYPTION_KEY not set in environment variables. "
                "Please set ENCRYPTION_KEY in your .env file."
            )

        # Si la clé est une string, la convertir en bytes
        if isinstance(key, str):
            # Si c'est déjà une clé Fernet valide (base64), la décoder
            try:
                return key.encode()
            except Exception:
                raise EncryptionError(
                    "ENCRYPTION_KEY must be a valid Fernet key (base64-encoded 32 bytes)"
                )

        return key

    @staticmethod
    def _get_fernet_instance() -> Fernet:
        """
        Crée une instance Fernet avec la clé de chiffrement.

        Returns:
            Fernet: Instance Fernet configurée

        Raises:
            EncryptionError: Si la clé est invalide
        """
        try:
            key = EncryptionService.get_encryption_key()
            return Fernet(key)
        except Exception as e:
            raise EncryptionError(f"Failed to initialize Fernet: {str(e)}")

    @staticmethod
    def encrypt_credentials(credentials: Dict) -> str:
        """
        Chiffre les credentials bancaires avec Fernet (AES-128).

        Args:
            credentials: Dictionnaire contenant les credentials à chiffrer
                        (ex: {"username": "...", "password": "...", "2fa_code": "..."})

        Returns:
            str: Credentials chiffrés encodés en base64

        Raises:
            EncryptionError: Si le chiffrement échoue
        """
        if not isinstance(credentials, dict):
            raise EncryptionError("Credentials must be a dictionary")

        try:
            # Convertir le dictionnaire en JSON puis en bytes
            credentials_json = json.dumps(credentials)
            credentials_bytes = credentials_json.encode("utf-8")

            # Chiffrer avec Fernet
            fernet = EncryptionService._get_fernet_instance()
            encrypted_bytes = fernet.encrypt(credentials_bytes)

            # Encoder en base64 pour stockage en string
            encrypted_str = base64.b64encode(encrypted_bytes).decode("utf-8")

            return encrypted_str
        except InvalidToken:
            raise EncryptionError("Invalid encryption key")
        except Exception as e:
            raise EncryptionError(f"Failed to encrypt credentials: {str(e)}")

    @staticmethod
    def decrypt_credentials(encrypted_data: str) -> Dict:
        """
        Déchiffre les credentials bancaires.

        Args:
            encrypted_data: Credentials chiffrés encodés en base64

        Returns:
            dict: Dictionnaire contenant les credentials déchiffrés

        Raises:
            EncryptionError: Si le déchiffrement échoue
        """
        if not isinstance(encrypted_data, str):
            raise EncryptionError("Encrypted data must be a string")

        try:
            # Décoder depuis base64
            encrypted_bytes = base64.b64decode(encrypted_data.encode("utf-8"))

            # Déchiffrer avec Fernet
            fernet = EncryptionService._get_fernet_instance()
            decrypted_bytes = fernet.decrypt(encrypted_bytes)

            # Convertir en JSON puis en dictionnaire
            credentials_json = decrypted_bytes.decode("utf-8")
            credentials = json.loads(credentials_json)

            return credentials
        except InvalidToken:
            raise EncryptionError("Invalid encrypted data or encryption key")
        except json.JSONDecodeError:
            raise EncryptionError("Decrypted data is not valid JSON")
        except Exception as e:
            raise EncryptionError(f"Failed to decrypt credentials: {str(e)}")

    @staticmethod
    def generate_key() -> str:
        """
        Génère une nouvelle clé de chiffrement Fernet.

        Cette méthode est utile pour générer une clé lors de la configuration initiale.
        La clé générée doit être stockée dans les variables d'environnement.

        Returns:
            str: Clé Fernet encodée en base64 (prête à être stockée dans ENCRYPTION_KEY)
        """
        key = Fernet.generate_key()
        return key.decode("utf-8")

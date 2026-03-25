"""
Tests unitaires pour le service de chiffrement des credentials bancaires.
"""

import os
import unittest
from unittest.mock import patch

from django.test import TestCase

from finance.services.encryption_service import (
    EncryptionService,
    EncryptionError,
)


class TestEncryptionService(TestCase):
    """Tests pour le service de chiffrement."""

    def setUp(self):
        """Configure l'environnement de test avec une clé de chiffrement."""
        # Générer une clé de test
        self.test_key = EncryptionService.generate_key()
        os.environ["ENCRYPTION_KEY"] = self.test_key

    def tearDown(self):
        """Nettoie l'environnement de test."""
        if "ENCRYPTION_KEY" in os.environ:
            del os.environ["ENCRYPTION_KEY"]

    def test_generate_key(self):
        """Test génération d'une clé de chiffrement."""
        key = EncryptionService.generate_key()
        self.assertIsInstance(key, str)
        self.assertGreater(len(key), 0)

    def test_get_encryption_key_success(self):
        """Test récupération de la clé depuis l'environnement."""
        key = EncryptionService.get_encryption_key()
        self.assertIsInstance(key, bytes)
        self.assertEqual(key.decode("utf-8"), self.test_key)

    def test_get_encryption_key_missing(self):
        """Test erreur si la clé n'est pas définie."""
        del os.environ["ENCRYPTION_KEY"]
        with self.assertRaises(EncryptionError) as context:
            EncryptionService.get_encryption_key()
        self.assertIn("ENCRYPTION_KEY not set", str(context.exception))

    def test_encrypt_credentials_success(self):
        """Test chiffrement réussi de credentials."""
        credentials = {"username": "test_user", "password": "secret123", "2fa_code": "123456"}

        encrypted = EncryptionService.encrypt_credentials(credentials)

        self.assertIsInstance(encrypted, str)
        self.assertGreater(len(encrypted), 0)
        # Vérifier que les données chiffrées sont différentes des données originales
        self.assertNotIn("test_user", encrypted)
        self.assertNotIn("secret123", encrypted)

    def test_encrypt_credentials_invalid_input(self):
        """Test erreur si les credentials ne sont pas un dictionnaire."""
        with self.assertRaises(EncryptionError) as context:
            EncryptionService.encrypt_credentials("not a dict")
        self.assertIn("must be a dictionary", str(context.exception))

    def test_decrypt_credentials_success(self):
        """Test déchiffrement réussi de credentials."""
        original_credentials = {"username": "test_user", "password": "secret123"}

        encrypted = EncryptionService.encrypt_credentials(original_credentials)
        decrypted = EncryptionService.decrypt_credentials(encrypted)

        self.assertEqual(decrypted, original_credentials)
        self.assertEqual(decrypted["username"], "test_user")
        self.assertEqual(decrypted["password"], "secret123")

    def test_decrypt_credentials_invalid_data(self):
        """Test erreur si les données chiffrées sont invalides."""
        with self.assertRaises(EncryptionError) as context:
            EncryptionService.decrypt_credentials("invalid_encrypted_data")
        self.assertIn("Invalid encrypted data", str(context.exception))

    def test_decrypt_credentials_wrong_key(self):
        """Test erreur si la clé de déchiffrement est incorrecte."""
        credentials = {"username": "test_user", "password": "secret123"}
        encrypted = EncryptionService.encrypt_credentials(credentials)

        # Changer la clé
        os.environ["ENCRYPTION_KEY"] = EncryptionService.generate_key()

        with self.assertRaises(EncryptionError) as context:
            EncryptionService.decrypt_credentials(encrypted)
        self.assertIn("Invalid encrypted data or encryption key", str(context.exception))

    def test_encrypt_decrypt_round_trip(self):
        """Test que le chiffrement/déchiffrement fonctionne en boucle."""
        test_cases = [
            {"username": "user1", "password": "pass1"},
            {"username": "user2", "password": "pass2", "2fa_code": "123456"},
            {"phone": "+33123456789", "pin": "1234"},
            {"token": "very_long_token_string_123456789"},
        ]

        for credentials in test_cases:
            with self.subTest(credentials=credentials):
                encrypted = EncryptionService.encrypt_credentials(credentials)
                decrypted = EncryptionService.decrypt_credentials(encrypted)
                self.assertEqual(decrypted, credentials)

    def test_encrypt_empty_dict(self):
        """Test chiffrement d'un dictionnaire vide."""
        credentials = {}
        encrypted = EncryptionService.encrypt_credentials(credentials)
        decrypted = EncryptionService.decrypt_credentials(encrypted)
        self.assertEqual(decrypted, {})

    def test_encrypt_special_characters(self):
        """Test chiffrement avec caractères spéciaux."""
        credentials = {
            "username": "user@example.com",
            "password": "p@ssw0rd!$%^&*()",
            "note": "Français avec accents: éèàùç",
        }

        encrypted = EncryptionService.encrypt_credentials(credentials)
        decrypted = EncryptionService.decrypt_credentials(encrypted)

        self.assertEqual(decrypted, credentials)

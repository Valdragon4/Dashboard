"""
Tests unitaires pour la classe abstraite BaseBankConnector et les exceptions.

Ces tests vérifient que:
- La classe abstraite ne peut pas être instanciée directement
- Les classes héritant doivent implémenter toutes les méthodes abstraites
- Les exceptions héritent correctement
- La méthode sync_portfolio_valuations retourne un dict vide par défaut
"""

from datetime import datetime
from decimal import Decimal
from django.test import TestCase

from finance.connectors.base import (
    BaseBankConnector,
    BankConnectionError,
    AuthenticationError,
    InvalidCredentialsError,
    RateLimitError,
    ConnectionTimeoutError,
)


class TestBaseBankConnector(TestCase):
    """Tests pour la classe abstraite BaseBankConnector."""

    def test_cannot_instantiate_abstract_class(self):
        """Test qu'on ne peut pas instancier BaseBankConnector directement."""
        with self.assertRaises(TypeError):
            BaseBankConnector()

    def test_must_implement_abstract_methods(self):
        """Test qu'une classe héritant doit implémenter toutes les méthodes abstraites."""
        # Classe incomplète sans implémentation des méthodes abstraites
        class IncompleteConnector(BaseBankConnector):
            pass

        with self.assertRaises(TypeError):
            IncompleteConnector()

    def test_must_implement_provider_name(self):
        """Test qu'une classe héritant doit implémenter provider_name."""
        class ConnectorWithoutProviderName(BaseBankConnector):
            def authenticate(self, credentials):
                return {}

            def sync_transactions(self, account, since=None):
                return []

            def get_balance(self, account):
                return Decimal("0")

            def disconnect(self):
                pass

        with self.assertRaises(TypeError):
            ConnectorWithoutProviderName()

    def test_must_implement_all_abstract_methods(self):
        """Test qu'une classe héritant doit implémenter toutes les méthodes abstraites."""
        # Classe avec seulement provider_name
        class PartialConnector(BaseBankConnector):
            @property
            def provider_name(self):
                return "TestProvider"

        with self.assertRaises(TypeError):
            PartialConnector()

    def test_complete_implementation_can_be_instantiated(self):
        """Test qu'une classe avec toutes les méthodes implémentées peut être instanciée."""
        class CompleteConnector(BaseBankConnector):
            @property
            def provider_name(self):
                return "TestProvider"

            def authenticate(self, credentials):
                return {"token": "test_token"}

            def sync_transactions(self, account, since=None):
                return []

            def get_balance(self, account):
                return Decimal("0")

            def disconnect(self):
                pass

        connector = CompleteConnector()
        self.assertIsInstance(connector, BaseBankConnector)
        self.assertEqual(connector.provider_name, "TestProvider")

    def test_sync_portfolio_valuations_default_returns_empty_dict(self):
        """Test que sync_portfolio_valuations retourne un dict vide par défaut."""
        class CompleteConnector(BaseBankConnector):
            @property
            def provider_name(self):
                return "TestProvider"

            def authenticate(self, credentials):
                return {}

            def sync_transactions(self, account, since=None):
                return []

            def get_balance(self, account):
                return Decimal("0")

            def disconnect(self):
                pass

        connector = CompleteConnector()
        # Créer un mock account (on n'a pas besoin d'un vrai objet Django pour ce test)
        result = connector.sync_portfolio_valuations(None)
        self.assertIsInstance(result, dict)
        self.assertEqual(result, {})


class TestExceptionHierarchy(TestCase):
    """Tests pour la hiérarchie des exceptions."""

    def test_bank_connection_error_is_base_exception(self):
        """Test que BankConnectionError hérite de Exception."""
        error = BankConnectionError("Test error")
        self.assertIsInstance(error, Exception)

    def test_authentication_error_inherits_from_bank_connection_error(self):
        """Test que AuthenticationError hérite de BankConnectionError."""
        error = AuthenticationError("Auth failed")
        self.assertIsInstance(error, BankConnectionError)
        self.assertIsInstance(error, Exception)

    def test_invalid_credentials_error_inherits_from_authentication_error(self):
        """Test que InvalidCredentialsError hérite de AuthenticationError."""
        error = InvalidCredentialsError("Invalid credentials")
        self.assertIsInstance(error, AuthenticationError)
        self.assertIsInstance(error, BankConnectionError)
        self.assertIsInstance(error, Exception)

    def test_rate_limit_error_inherits_from_bank_connection_error(self):
        """Test que RateLimitError hérite de BankConnectionError."""
        error = RateLimitError("Rate limit exceeded")
        self.assertIsInstance(error, BankConnectionError)
        self.assertIsInstance(error, Exception)

    def test_connection_timeout_error_inherits_from_bank_connection_error(self):
        """Test que ConnectionTimeoutError hérite de BankConnectionError."""
        error = ConnectionTimeoutError("Connection timeout")
        self.assertIsInstance(error, BankConnectionError)
        self.assertIsInstance(error, Exception)

    def test_can_catch_specific_exceptions_by_parent_class(self):
        """Test qu'on peut attraper les exceptions spécifiques par leur classe parente."""
        # Test avec AuthenticationError
        try:
            raise AuthenticationError("Auth failed")
        except BankConnectionError:
            pass  # Doit être attrapée par BankConnectionError
        else:
            self.fail("AuthenticationError should be caught by BankConnectionError")

        # Test avec InvalidCredentialsError
        try:
            raise InvalidCredentialsError("Invalid credentials")
        except AuthenticationError:
            pass  # Doit être attrapée par AuthenticationError
        except BankConnectionError:
            pass  # Doit aussi être attrapée par BankConnectionError
        else:
            self.fail("InvalidCredentialsError should be caught by parent classes")

        # Test avec RateLimitError
        try:
            raise RateLimitError("Rate limit exceeded")
        except BankConnectionError:
            pass  # Doit être attrapée par BankConnectionError
        else:
            self.fail("RateLimitError should be caught by BankConnectionError")

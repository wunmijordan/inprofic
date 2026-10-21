from django.test import SimpleTestCase

from .ledger import validate_registry


class LedgerRegistryTests(SimpleTestCase):
    def test_every_registered_model_has_a_date_field(self):
        self.assertTrue(validate_registry())

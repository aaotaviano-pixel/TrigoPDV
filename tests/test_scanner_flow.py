from __future__ import annotations

import unittest
from types import SimpleNamespace

from db.database import Database
from services.errors import ValidationError
from services.products import ProductService
from tests.support import provision_test_admin


class ScannerResolutionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.database = Database(":memory:")
        self.admin = provision_test_admin(self.database)

    def tearDown(self) -> None:
        self.database.close()

    def test_registered_plu_is_resolved_locally_without_network(self) -> None:
        external = SimpleNamespace(lookup=lambda _code: self.fail("PLU não pode consultar a internet"))
        products = ProductService(self.database, external_client=external)
        products.create_product(
            "PLU-PAO-FRANCES",
            "Pão francês",
            1.20,
            unidade="UN",
            tipo_codigo="PLU",
            actor_id=self.admin["id"],
        )

        result = products.lookup_external("PLU-PAO-FRANCES", actor_id=self.admin["id"])

        self.assertEqual(result["status"], "FOUND")
        self.assertEqual(result["source"], "local")
        self.assertEqual(result["product"]["tipo_codigo"], "PLU")

    def test_unknown_plu_goes_to_manual_flow_without_network(self) -> None:
        calls: list[str] = []
        products = ProductService(
            self.database,
            external_client=SimpleNamespace(lookup=lambda code: calls.append(code)),
        )

        result = products.lookup_external("BALCAO-99", actor_id=self.admin["id"])

        self.assertEqual(result["status"], "MANUAL_ENTRY_REQUIRED")
        self.assertEqual(calls, [])

    def test_incomplete_or_hostile_reader_input_is_rejected(self) -> None:
        products = ProductService(self.database)
        for code in ("7", "789123", "ABC/../123", "A" * 51):
            with self.subTest(code=code), self.assertRaises(ValidationError):
                products.lookup_external(code, actor_id=self.admin["id"])


if __name__ == "__main__":
    unittest.main()

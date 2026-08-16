from __future__ import annotations

import tempfile
import unittest
import sqlite3
import time
from pathlib import Path

from db.database import Database
from integrations.cosmos import CosmosProduct
from db.schema import SCHEMA_SQL
from integrations.open_food_facts import OpenFoodFactsProduct
from services.auth import AuthService
from services.backup import BackupService
from services.cash import CashService
from services.errors import AuthorizationError, ExternalLookupError, ValidationError
from services.pix import build_pix_payload, crc16_ccitt
from services.products import ProductService
from services.sales import SaleService
from services.pdv_service import PDVService
from integrations.open_food_facts import normalize_gtin
from tests.support import (
    TEST_ADMIN_LOGIN,
    TEST_ADMIN_PASSWORD,
    TEST_RECOVERY_CODE,
    provision_test_admin,
    provision_test_pdv,
)


class FakeOpenFoodFacts:
    def lookup(self, gtin: str):
        return OpenFoodFactsProduct(gtin=gtin, name="Biscoito de teste", brand="Trigo")


class OfflineThenOnlineOpenFoodFacts:
    """Simula o cabo desconectado e a próxima leitura após reconectar."""

    def __init__(self) -> None:
        self.calls = 0

    def lookup(self, gtin: str):
        self.calls += 1
        if self.calls == 1:
            raise ExternalLookupError(
                "Sem conexão com a internet. Você pode cadastrar o produto manualmente e continuar a venda."
            )
        return OpenFoodFactsProduct(gtin=gtin, name="Produto após reconexão", brand="Trigo")


class MissingOpenFoodFacts:
    def lookup(self, gtin: str):
        return None


class CountingMissingOpenFoodFacts:
    def __init__(self) -> None:
        self.calls = 0

    def lookup(self, gtin: str):
        self.calls += 1
        return None


class FakeCosmos:
    enabled = True

    def lookup(self, gtin: str):
        return CosmosProduct(gtin=gtin, name="Produto encontrado na Cosmos", brand="Marca Cosmos")


class BackendTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tempdir.name) / "pdv.sqlite3")
        self.database.initialize()
        self.auth = AuthService(self.database)
        self.admin = provision_test_admin(self.database)
        self.cashier = self.auth.create_user("Operador Caixa", "caixa", "caixa123", "caixa", actor_id=self.admin["id"])
        self.cashier = self.auth.change_password(
            self.cashier["id"], "caixa123", "CaixaPronta9", actor_id=self.cashier["id"]
        )
        self.products = ProductService(self.database, external_client=FakeOpenFoodFacts())
        self.backup = BackupService(self.database, Path(self.tempdir.name) / "backups")
        self.cash = CashService(self.database, backup_service=self.backup)
        self.sales = SaleService(self.database)

    def tearDown(self) -> None:
        self.cash.shutdown()
        self.database.close()
        self.tempdir.cleanup()

    def test_gtin_cache_and_sale_decrements_stock_transactionally(self) -> None:
        product = self.products.create_product(
            "7891234567895",
            "Pão de queijo",
            "12,50",
            estoque="3,000",
            unidade="KG",
            actor_id=self.admin["id"],
        )
        self.assertEqual(product["preco"], 12.5)
        cash = self.cash.open_cash(self.cashier["id"], "10,00", actor_id=self.cashier["id"])
        sale = self.sales.finalize(
            cash["id"],
            [{"gtin": product["gtin"], "quantidade": "0,250", "preco_unitario": "12,50"}],
            "Dinheiro",
            "5,00",
            operador_id=self.cashier["id"],
            chave_idempotencia="11111111-2222-3333-4444-555555555555",
        )
        self.assertEqual(sale["total"], 3.13)
        self.assertEqual(sale["troco"], 1.87)
        self.assertEqual(self.products.get_product(product["gtin"])["estoque"], 2.75)

        # A mesma chave não produz uma segunda baixa de estoque.
        same_sale = self.sales.finalize(
            cash["id"], [{"gtin": product["gtin"], "quantidade": 0.25}], "Dinheiro", 5,
            operador_id=self.cashier["id"], chave_idempotencia="11111111-2222-3333-4444-555555555555",
        )
        self.assertEqual(same_sale["id"], sale["id"])
        self.assertEqual(self.products.get_product(product["gtin"])["estoque"], 2.75)

    def test_gtin_normalization_validates_supported_lengths_and_check_digit(self) -> None:
        self.assertEqual(normalize_gtin(" 7894900011517 "), "7894900011517")
        self.assertEqual(normalize_gtin("00012348"), "00012348")
        with self.assertRaises(ValidationError):
            normalize_gtin("7894900011516")
        with self.assertRaises(ValidationError):
            normalize_gtin("123456789013")

    def test_product_search_prioritizes_exact_gtin_and_name(self) -> None:
        self.products.create_product("7891234567895", "Café Trigo", "10", actor_id=self.admin["id"])
        self.products.create_product("7894900011517", "Coca-Cola Original", "10", actor_id=self.admin["id"])
        self.products.create_product("7894900020021", "Coca-Cola Zero", "10", actor_id=self.admin["id"])
        by_gtin = self.products.search("7894900011517")
        self.assertEqual(by_gtin[0]["gtin"], "7894900011517")
        by_name = self.products.search("Coca-Cola")
        self.assertEqual([row["nome"] for row in by_name[:2]], ["Coca-Cola Original", "Coca-Cola Zero"])

    def test_blind_close_requires_justification_and_hides_expected(self) -> None:
        cash = self.cash.open_cash(self.cashier["id"], 10, actor_id=self.cashier["id"])
        with self.assertRaises(ValidationError):
            self.cash.close_cash(cash["id"], 9, actor_id=self.cashier["id"])
        closed = self.cash.close_cash(cash["id"], 9, "Conferência recontada", actor_id=self.cashier["id"])
        self.assertEqual(closed["status"], "FECHADO")
        self.assertNotIn("valor_esperado", closed)
        self.assertEqual(closed["backup_status"], "PENDENTE")
        deadline = time.monotonic() + 3
        backup_job = None
        while time.monotonic() < deadline:
            backup_job = self.database.fetch_one(
                "SELECT status, arquivo FROM backups_caixa WHERE caixa_id = ?", (cash["id"],)
            )
            if backup_job and backup_job["status"] == "CONCLUIDO":
                break
            time.sleep(0.01)
        self.assertIsNotNone(backup_job)
        self.assertEqual(backup_job["status"], "CONCLUIDO")
        self.assertTrue(Path(backup_job["arquivo"]).exists())
        summary = self.cash.get_cash_summary(cash["id"], actor_id=self.cashier["id"])
        self.assertNotIn("valor_esperado", summary)
        self.assertNotIn("vendas_dinheiro", summary)

    def test_admin_can_see_current_cash_balance(self) -> None:
        cash = self.cash.open_cash(self.cashier["id"], 10, actor_id=self.cashier["id"])
        self.cash.add_movement(cash["id"], "SUPRIMENTO", 5, "Troco", actor_id=self.cashier["id"])
        summary = self.cash.get_cash_summary(cash["id"], actor_id=self.admin["id"])
        self.assertEqual(summary["valor_em_caixa"], 15.0)

    def test_external_product_is_cached_without_price(self) -> None:
        result = self.products.lookup_external("7891234567895", actor_id=self.cashier["id"])
        self.assertEqual(result["status"], "PRICE_REQUIRED")
        self.assertEqual(result["product"]["preco"], 0.0)
        self.assertEqual(result["product"]["origem"], "open_food_facts")
        self.assertFalse(result["product"]["estoque_controlado"])
        self.products.set_price(result["product"]["gtin"], 5, actor_id=self.admin["id"])
        cash = self.cash.open_cash(self.cashier["id"], 0, actor_id=self.cashier["id"])
        sale = self.sales.finalize(
            cash["id"],
            [{"gtin": result["product"]["gtin"], "quantidade": 1, "preco_unitario": 5}],
            "Cartão",
            operador_id=self.cashier["id"],
        )
        self.assertEqual(sale["total"], 5.0)

    def test_unknown_gtin_uses_negative_cache_without_repeating_external_call(self) -> None:
        lookup = CountingMissingOpenFoodFacts()
        products = ProductService(self.database, external_client=lookup)
        first = products.lookup_external("7891234567895", actor_id=self.cashier["id"])
        second = products.lookup_external("7891234567895", actor_id=self.cashier["id"])
        self.assertEqual(first["status"], "MANUAL_ENTRY_REQUIRED")
        self.assertEqual(second["status"], "MANUAL_ENTRY_REQUIRED")
        self.assertEqual(lookup.calls, 1)
        with self.database.transaction() as connection:
            self.assertEqual(connection.execute("SELECT status FROM cache_gtin WHERE gtin = '7891234567895'").fetchone()[0], "NAO_ENCONTRADO")

    def test_internal_plu_is_explicitly_separate_from_gtin(self) -> None:
        product = self.products.create_product(
            "PLU-PAO-FRANCES", "Pão francês", 1.0, unidade="UN", categoria="Pães",
            item_balcao=True, estoque_controlado=False, actor_id=self.admin["id"],
        )
        self.assertEqual(product["tipo_codigo"], "PLU")
        self.assertEqual(product["validacao_codigo"], "VALIDO_INTERNO")
        self.assertEqual(product["categoria"], "Pães")

    def test_gtin_network_failure_is_clear_and_next_scan_recovers(self) -> None:
        lookup = OfflineThenOnlineOpenFoodFacts()
        products = ProductService(self.database, external_client=lookup)
        first = products.lookup_external("7891234567895", actor_id=self.cashier["id"])
        self.assertEqual(first["status"], "OFFLINE")
        self.assertEqual(first["source"], "offline")
        self.assertIn("Sem conexão", first["message"])
        self.assertIsNone(first["product"])

        # Ao voltar a rede não há IP, proxy ou reinício a configurar: a próxima
        # leitura usa a mesma integração e retorna o produto automaticamente.
        recovered = products.lookup_external("7891234567895", actor_id=self.cashier["id"])
        self.assertEqual(recovered["status"], "PRICE_REQUIRED")
        self.assertEqual(recovered["source"], "open_food_facts")
        self.assertEqual(recovered["product"]["nome"], "Produto após reconexão")

    def test_cosmos_fallback_autofills_product_and_requires_only_price(self) -> None:
        products = ProductService(
            self.database,
            external_client=MissingOpenFoodFacts(),
            cosmos_client=FakeCosmos(),
        )
        result = products.lookup_external("7898341430258", actor_id=self.cashier["id"])
        self.assertEqual(result["status"], "PRICE_REQUIRED")
        self.assertEqual(result["source"], "cosmos")
        self.assertEqual(result["product"]["nome"], "Produto encontrado na Cosmos")
        self.assertEqual(result["product"]["marca"], "Marca Cosmos")
        self.assertEqual(result["product"]["preco"], 0.0)
        self.assertEqual(result["product"]["origem"], "open_food_facts")

    def test_admin_can_create_user_through_pdv_without_exposing_hash(self) -> None:
        pdv = PDVService(database=Database(":memory:"))
        admin = provision_test_pdv(pdv)
        created = pdv.create_user_admin("Nova Operadora", "nova.caixa", "SenhaSegura8", "caixa", admin["id"])
        self.assertEqual(created["login"], "nova.caixa")
        self.assertTrue(created["deve_trocar_senha"])
        self.assertNotIn("senha", created)
        self.assertNotIn("senha_hash", created)

        with pdv.database.transaction() as connection:
            stored = connection.execute("SELECT senha_hash FROM usuarios WHERE id = ?", (created["id"],)).fetchone()["senha_hash"]
        self.assertNotEqual(stored, "SenhaSegura8")
        self.assertTrue(stored.startswith("$2") or stored.startswith("scrypt$"))
        self.assertIsNotNone(pdv.authenticate("nova.caixa", "SenhaSegura8"))

    def test_admin_password_reset_requires_change_on_next_login(self) -> None:
        user = self.auth.create_user("Operador Esqueceu", "operador.esqueceu", "SenhaInicial8", "caixa", actor_id=self.admin["id"])
        reset = self.auth.reset_user_password(user["id"], "SenhaTemporaria9", actor_id=self.admin["id"])
        self.assertTrue(reset["deve_trocar_senha"])
        self.assertIsNone(self.auth.authenticate("operador.esqueceu", "SenhaInicial8"))
        authenticated = self.auth.authenticate("operador.esqueceu", "SenhaTemporaria9")
        assert authenticated is not None
        self.assertTrue(authenticated["deve_trocar_senha"])
        with self.database.transaction() as connection:
            stored = connection.execute("SELECT senha_hash FROM usuarios WHERE id = ?", (user["id"],)).fetchone()["senha_hash"]
            action = connection.execute("SELECT acao FROM logs_auditoria WHERE entidade_id = ? ORDER BY id DESC LIMIT 1", (user["id"],)).fetchone()["acao"]
        self.assertNotEqual(stored, "SenhaTemporaria9")
        self.assertEqual(action, "LOGIN_SUCESSO")
        with self.database.transaction() as connection:
            reset_action = connection.execute("SELECT COUNT(*) FROM logs_auditoria WHERE entidade_id = ? AND acao = 'SENHA_REDEFINIDA_ADMIN'", (user["id"],)).fetchone()[0]
        self.assertEqual(reset_action, 1)

    def test_admin_recovers_password_only_after_five_failures_with_rotated_code(self) -> None:
        self.assertFalse(self.auth.password_recovery_available(TEST_ADMIN_LOGIN))
        for _ in range(5):
            self.assertIsNone(self.auth.authenticate(TEST_ADMIN_LOGIN, "senha-incorreta"))
        self.assertTrue(self.auth.password_recovery_available(TEST_ADMIN_LOGIN))
        self.assertFalse(self.auth.password_recovery_available("caixa"))
        with self.assertRaises(ValidationError):
            self.auth.recover_password_with_code(
                TEST_ADMIN_LOGIN, "codigo-incorreto-seguro", "NovaSenhaSegura9", "novo-codigo-seguro"
            )
        recovered = self.auth.recover_password_with_code(
            TEST_ADMIN_LOGIN, TEST_RECOVERY_CODE, "NovaSenhaSegura9", "novo-codigo-seguro"
        )
        self.assertEqual(recovered["perfil"], "admin")
        self.assertFalse(self.auth.password_recovery_available(TEST_ADMIN_LOGIN))
        self.assertIsNone(self.auth.authenticate(TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD))
        self.assertIsNotNone(self.auth.authenticate(TEST_ADMIN_LOGIN, "NovaSenhaSegura9"))
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT codigo_recuperacao_hash, tentativas_login_falhas FROM usuarios WHERE login = ?",
                (TEST_ADMIN_LOGIN,),
            ).fetchone()
            actions = {
                entry["acao"]
                for entry in connection.execute("SELECT acao FROM logs_auditoria WHERE entidade = 'USUARIO'").fetchall()
            }
        self.assertNotEqual(row["codigo_recuperacao_hash"], "novo-codigo-seguro")
        self.assertEqual(row["tentativas_login_falhas"], 0)
        self.assertIn("SENHA_RECUPERADA_CODIGO", actions)

    def test_backup_and_pix_payload(self) -> None:
        backup = BackupService(self.database, Path(self.tempdir.name) / "backups")
        path = backup.backup_database()
        self.assertTrue(path.exists())
        payload = build_pix_payload("teste@example.com", "Trigo de Minas", "Belo Horizonte", "10,00")
        self.assertEqual(payload[-4:], crc16_ccitt(payload[:-4]))

    def test_pdv_service_matches_checkout_controller_contract(self) -> None:
        pdv = PDVService(database=Database(":memory:"))
        admin = provision_test_pdv(pdv)
        product = pdv.create_product(
            {"gtin": "7891234567895", "nome": "Café", "preco": 8, "estoque": 3}, admin["id"]
        )
        cash = pdv.open_cash(admin["id"], 5)
        sale = pdv.finalize_sale(
            cash["id"], admin["id"], [{"gtin": product["gtin"], "quantidade": 1, "preco_unitario": 8}],
            "Cartão", None,
        )
        self.assertEqual(sale["total"], 8.0)
        self.assertEqual(pdv.admin_dashboard()["vendas_hoje"], 1)

    def test_cashier_cannot_nominate_admin_id_to_cancel_cart_item(self) -> None:
        pdv = PDVService(database=Database(":memory:"))
        admin = provision_test_pdv(pdv)
        cashier = pdv.auth.create_user("Caixa", "caixa-ui", "caixa123", "caixa", actor_id=admin["id"])
        pdv.authenticate("caixa-ui", "caixa123")
        with self.assertRaises(AuthorizationError):
            pdv.authorize_item_cancel(
                cashier["id"],
                {"gtin": "7891234567895", "nome": "Item em carrinho"},
                admin_user_id=admin["id"],
            )

    def test_schema_migration_adds_sale_idempotency_to_existing_database(self) -> None:
        legacy_path = Path(self.tempdir.name) / "legacy.sqlite3"
        legacy_schema = SCHEMA_SQL.replace(
            "    chave_idempotencia TEXT,\n    total_manual",
            "    total_manual",
        ).replace(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_vendas_chave_idempotencia\n"
            "    ON vendas(chave_idempotencia) WHERE chave_idempotencia IS NOT NULL;\n",
            "",
        )
        connection = sqlite3.connect(legacy_path)
        try:
            connection.executescript(legacy_schema)
            connection.commit()
        finally:
            connection.close()
        migrated = Database(legacy_path)
        migrated.initialize()
        with migrated.transaction() as connection:
            names = {row["name"] for row in connection.execute("PRAGMA table_info(vendas)").fetchall()}
        self.assertIn("chave_idempotencia", names)

    def test_schema_migration_adds_recovery_columns_to_existing_database(self) -> None:
        legacy_path = Path(self.tempdir.name) / "legacy_users.sqlite3"
        legacy_schema = SCHEMA_SQL.replace(",\n    codigo_recuperacao_hash TEXT", "").replace(
            ",\n    tentativas_login_falhas INTEGER NOT NULL DEFAULT 0", ""
        )
        connection = sqlite3.connect(legacy_path)
        try:
            connection.executescript(legacy_schema)
            connection.commit()
        finally:
            connection.close()
        migrated = Database(legacy_path)
        migrated.initialize()
        with migrated.transaction() as connection:
            names = {row["name"] for row in connection.execute("PRAGMA table_info(usuarios)").fetchall()}
        self.assertTrue({"codigo_recuperacao_hash", "tentativas_login_falhas"}.issubset(names))

    def test_schema_migration_adds_catalog_fields_and_gtin_cache(self) -> None:
        with self.database.transaction() as connection:
            names = {row["name"] for row in connection.execute("PRAGMA table_info(produtos)").fetchall()}
            self.assertTrue({"tipo_codigo", "categoria", "validacao_codigo", "detalhes_embalagem"}.issubset(names))
            self.assertIsNotNone(connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='cache_gtin'").fetchone())


class InstallerCatalogBoundaryTestCase(unittest.TestCase):
    """O pacote distribui catálogo, nunca uma cópia de banco operacional."""

    def test_installer_has_no_operational_seed(self) -> None:
        data = Path(__file__).resolve().parents[1] / "TrigoPDV_Instalacao_PenDrive" / "dados-iniciais"
        self.assertFalse((data / "trigo_de_minas.sqlite3").exists())
        self.assertTrue((data / "catalogo-produtos.sqlite3").is_file())
        self.assertTrue((data / "catalogo-produtos.manifest.json").is_file())


if __name__ == "__main__":
    unittest.main()

"""Contratos de primeiro uso e provisionamento seguro."""

from __future__ import annotations

import ast
import dataclasses
import importlib
import importlib.util
import json
import re
import tempfile
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from db.database import Database, DatabaseError
from db.schema import SCHEMA_VERSION
from desktop_controller import DesktopController
from services.auth import AuthService
from services.errors import ConflictError, ValidationError
from services.passwords import verify_password
from services.pdv_service import PDVService
from tests.support import (
    TEST_ADMIN_LOGIN,
    TEST_ADMIN_NAME,
    TEST_ADMIN_PASSWORD,
    TEST_RECOVERY_CODE,
)


def _provisioning_module(testcase: unittest.TestCase):
    testcase.assertIsNotNone(importlib.util.find_spec("services.provisioning"))
    return importlib.import_module("services.provisioning")


class InstallationStateMigrationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _database(self, name: str = "pdv.sqlite3") -> Database:
        return Database(self.root / name)

    def _prepare_v6(self, database: Database, *, with_user: bool) -> None:
        database.initialize()
        with database.transaction(write=True) as connection:
            connection.execute("DROP TABLE IF EXISTS installation_state")
            connection.execute("UPDATE schema_meta SET valor = '6' WHERE chave = 'schema_version'")
            if with_user:
                connection.execute(
                    "INSERT INTO usuarios(nome, login, senha_hash, perfil, ativo, deve_trocar_senha) "
                    "VALUES ('Pessoa Legada', 'pessoa.legada', 'hash-legado-de-fixture', 'caixa', 1, 0)"
                )
                connection.execute(
                    "INSERT INTO produtos(gtin, nome, preco) VALUES ('7891234567895', 'Produto legado', 4.5)"
                )

    def test_fresh_database_reaches_current_schema_uninitialized_with_zero_users(self) -> None:
        database = self._database()
        database.initialize()
        with database.transaction() as connection:
            version = int(connection.execute(
                "SELECT valor FROM schema_meta WHERE chave = 'schema_version'"
            ).fetchone()[0])
            users = connection.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0]
            table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'installation_state'"
            ).fetchone()
            self.assertIsNotNone(table)
            state = connection.execute(
                "SELECT installation_id, state, created_at, provisioned_at FROM installation_state"
            ).fetchone()
        self.assertEqual(version, SCHEMA_VERSION)
        self.assertEqual(users, 0)
        self.assertIsNotNone(state)
        uuid.UUID(state["installation_id"])
        self.assertEqual(state["state"], "UNINITIALIZED")
        self.assertTrue(state["created_at"])
        self.assertIsNone(state["provisioned_at"])
        self.assertIsNone(AuthService(database).authenticate(TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD))

    def test_fresh_installation_identifiers_are_random_and_singleton_is_enforced(self) -> None:
        first = self._database("first.sqlite3")
        second = self._database("second.sqlite3")
        first.initialize()
        second.initialize()
        self.assertIsNotNone(first.fetch_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='installation_state'"
        ))
        first_id = first.fetch_one("SELECT installation_id FROM installation_state")["installation_id"]
        second_id = second.fetch_one("SELECT installation_id FROM installation_state")["installation_id"]
        self.assertNotEqual(first_id, second_id)
        with self.assertRaises(DatabaseError):
            with first.transaction(write=True) as connection:
                connection.execute(
                    "INSERT INTO installation_state(singleton, installation_id, state, created_at) "
                    "VALUES (2, '00000000-0000-0000-0000-000000000002', "
                    "'UNINITIALIZED', CURRENT_TIMESTAMP)"
                )

    def test_v6_preexisting_ready_state_without_users_is_never_reopened(self) -> None:
        database = self._database()
        database.initialize()
        original_provisioned_at = "2026-08-15T12:00:00-03:00"
        with database.transaction(write=True) as connection:
            connection.execute(
                "UPDATE installation_state SET state = 'READY', provisioned_at = ? WHERE singleton = 1",
                (original_provisioned_at,),
            )
            connection.execute("UPDATE schema_meta SET valor = '6' WHERE chave = 'schema_version'")

        result = database.initialize(backup_dir=self.root / "backups")

        self.assertEqual(result.applied_versions, (7, 8, 9))
        state = database.fetch_one(
            "SELECT state, provisioned_at FROM installation_state WHERE singleton = 1"
        )
        self.assertEqual(state["state"], "READY")
        self.assertEqual(state["provisioned_at"], original_provisioned_at)
        self.assertEqual(database.fetch_one("SELECT COUNT(*) AS total FROM usuarios")["total"], 0)
        with self.assertRaises(ConflictError):
            _provisioning_module(self).ProvisioningService(database).provision_initial_admin(
                TEST_ADMIN_NAME,
                TEST_ADMIN_LOGIN,
                TEST_ADMIN_PASSWORD,
                TEST_RECOVERY_CODE,
            )

    def test_v6_without_users_is_backed_up_and_migrates_to_uninitialized(self) -> None:
        database = self._database()
        self._prepare_v6(database, with_user=False)
        result = database.initialize(backup_dir=self.root / "backups")
        self.assertEqual(result.from_version, 6)
        self.assertEqual(result.to_version, SCHEMA_VERSION)
        self.assertEqual(result.applied_versions, (7, 8, 9))
        self.assertIsNotNone(result.backup_path)
        assert result.backup_path is not None
        self.assertTrue(result.backup_path.exists())
        with database.transaction() as connection:
            state = connection.execute("SELECT state, provisioned_at FROM installation_state").fetchone()
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0], 0)
        self.assertEqual(state["state"], "UNINITIALIZED")
        self.assertIsNone(state["provisioned_at"])

    def test_v6_with_any_user_is_backed_up_ready_and_preserves_data(self) -> None:
        database = self._database()
        self._prepare_v6(database, with_user=True)
        result = database.initialize(backup_dir=self.root / "backups")
        self.assertEqual(result.applied_versions, (7, 8, 9))
        self.assertIsNotNone(result.backup_path)
        assert result.backup_path is not None
        backup = Database(result.backup_path)
        with backup.transaction() as connection:
            self.assertEqual(connection.execute(
                "SELECT valor FROM schema_meta WHERE chave = 'schema_version'"
            ).fetchone()[0], "6")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0], 1)
            self.assertIsNone(connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='installation_state'"
            ).fetchone())
        with database.transaction() as connection:
            state = connection.execute("SELECT state, provisioned_at FROM installation_state").fetchone()
            user = connection.execute("SELECT nome, login, senha_hash FROM usuarios").fetchone()
            product = connection.execute("SELECT nome, preco FROM produtos").fetchone()
        self.assertEqual(state["state"], "READY")
        self.assertTrue(state["provisioned_at"])
        self.assertEqual(tuple(user), ("Pessoa Legada", "pessoa.legada", "hash-legado-de-fixture"))
        self.assertEqual(tuple(product), ("Produto legado", 4.5))


class ProvisioningServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.database = Database(self.root / "pdv.sqlite3")
        self.database.initialize()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _service(self, database: Database | None = None):
        return _provisioning_module(self).ProvisioningService(database or self.database)

    def _provision(self, service=None) -> dict:
        return (service or self._service()).provision_initial_admin(
            TEST_ADMIN_NAME, TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD, TEST_RECOVERY_CODE
        )

    def test_status_is_an_immutable_public_value_without_secret_fields(self) -> None:
        status = self._service().status()
        self.assertTrue(dataclasses.is_dataclass(status))
        self.assertEqual(set(dataclasses.asdict(status)), {"installation_id", "state", "requires_provisioning"})
        self.assertEqual(status.state, "UNINITIALIZED")
        self.assertTrue(status.requires_provisioning)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            status.state = "READY"

    def test_generated_recovery_codes_are_distinct_accepted_format_and_write_nothing(self) -> None:
        service = self._service()
        before = self.database.fetch_one(
            "SELECT (SELECT COUNT(*) FROM usuarios) AS users, "
            "(SELECT COUNT(*) FROM logs_auditoria) AS audit, state FROM installation_state"
        )
        first = service.generate_recovery_code()
        second = service.generate_recovery_code()
        after = self.database.fetch_one(
            "SELECT (SELECT COUNT(*) FROM usuarios) AS users, "
            "(SELECT COUNT(*) FROM logs_auditoria) AS audit, state FROM installation_state"
        )
        self.assertNotEqual(first, second)
        self.assertRegex(first, re.compile(r"^[A-Za-z0-9_-]{20,180}$"))
        self.assertRegex(second, re.compile(r"^[A-Za-z0-9_-]{20,180}$"))
        self.assertEqual(after, before)

    def test_all_four_fields_are_validated_before_any_write(self) -> None:
        cases = (
            ("", TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD, TEST_RECOVERY_CODE),
            (TEST_ADMIN_NAME, " ", TEST_ADMIN_PASSWORD, TEST_RECOVERY_CODE),
            (TEST_ADMIN_NAME, TEST_ADMIN_LOGIN, "curta", TEST_RECOVERY_CODE),
            (TEST_ADMIN_NAME, TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD, "curto"),
        )
        for index, values in enumerate(cases):
            with self.subTest(field=index):
                database = Database(self.root / f"invalid-{index}.sqlite3")
                database.initialize()
                with self.assertRaises(ValidationError):
                    self._service(database).provision_initial_admin(*values)
                self.assertEqual(database.fetch_one("SELECT COUNT(*) AS total FROM usuarios")["total"], 0)
                self.assertEqual(database.fetch_one("SELECT state FROM installation_state")["state"], "UNINITIALIZED")

    def test_provisioning_stores_verifiable_hashes_and_returns_only_public_user(self) -> None:
        created = self._provision()
        row = self.database.fetch_one(
            "SELECT senha_hash, codigo_recuperacao_hash FROM usuarios WHERE id = ?", (created["id"],)
        )
        self.assertTrue(verify_password(TEST_ADMIN_PASSWORD, row["senha_hash"]))
        self.assertTrue(verify_password(TEST_RECOVERY_CODE, row["codigo_recuperacao_hash"]))
        self.assertNotEqual(row["senha_hash"], TEST_ADMIN_PASSWORD)
        self.assertNotEqual(row["codigo_recuperacao_hash"], TEST_RECOVERY_CODE)
        self.assertEqual(created["perfil"], "admin")
        self.assertFalse(created["deve_trocar_senha"])
        self.assertTrue({"senha", "senha_hash", "codigo_recuperacao", "codigo_recuperacao_hash"}.isdisjoint(created))
        status = self._service().status()
        self.assertEqual(status.state, "READY")
        self.assertFalse(status.requires_provisioning)
        self.assertIsNotNone(AuthService(self.database).authenticate(TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD))

    def test_provisioning_audit_contains_no_password_code_hash_or_derived_secret_material(self) -> None:
        created = self._provision()
        stored = self.database.fetch_one(
            "SELECT senha_hash, codigo_recuperacao_hash FROM usuarios WHERE id = ?", (created["id"],)
        )
        rows = self.database.fetch_all("SELECT * FROM logs_auditoria ORDER BY id")
        serialized = json.dumps(rows, ensure_ascii=False, sort_keys=True)
        self.assertEqual([row["acao"] for row in rows], ["INSTALACAO_PROVISIONADA"])
        for forbidden in (TEST_ADMIN_PASSWORD, TEST_RECOVERY_CODE, stored["senha_hash"], stored["codigo_recuperacao_hash"]):
            self.assertNotIn(forbidden, serialized)
        details = json.loads(rows[0]["detalhes"])
        self.assertFalse(any(
            token in key.casefold()
            for key in details
            for token in ("senha", "password", "codigo", "recovery", "hash", "secret")
        ))

    def test_second_sequential_attempt_is_a_clear_conflict_and_creates_no_extra_user(self) -> None:
        service = self._service()
        self._provision(service)
        with self.assertRaises(ConflictError) as captured:
            self._provision(service)
        self.assertIn("provision", str(captured.exception).casefold())
        self.assertEqual(self.database.fetch_one("SELECT COUNT(*) AS total FROM usuarios")["total"], 1)

    def test_two_concurrent_attempts_create_exactly_one_admin(self) -> None:
        module = _provisioning_module(self)
        barrier = threading.Barrier(2)

        def provision(suffix: str):
            service = module.ProvisioningService(Database(self.database.path))
            barrier.wait(timeout=5)
            try:
                return service.provision_initial_admin(
                    f"Admin {suffix}", f"admin.{suffix}", f"SenhaConcorrente{suffix}8",
                    f"codigo-recuperacao-concorrente-{suffix}",
                )
            except BaseException as exc:
                return exc

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(provision, ("um", "dois")))
        self.assertEqual(sum(isinstance(item, dict) for item in outcomes), 1)
        conflicts = [item for item in outcomes if isinstance(item, ConflictError)]
        self.assertEqual(len(conflicts), 1, outcomes)
        self.assertIn("provision", str(conflicts[0]).casefold())
        with self.database.transaction() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0], 1)
            state = connection.execute("SELECT state FROM installation_state").fetchone()[0]
        self.assertEqual(state, "READY")

    def test_failure_between_admin_insert_and_ready_update_rolls_back_both(self) -> None:
        service = self._service()
        with self.database.transaction(write=True) as connection:
            connection.execute(
                "CREATE TRIGGER fail_ready_update BEFORE UPDATE OF state ON installation_state "
                "WHEN NEW.state = 'READY' BEGIN SELECT RAISE(ABORT, 'falha injetada'); END"
            )
        with self.assertRaises((DatabaseError, ConflictError)):
            self._provision(service)
        with self.database.transaction() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM logs_auditoria").fetchone()[0], 0)
            state = connection.execute("SELECT state, provisioned_at FROM installation_state").fetchone()
        self.assertEqual(state["state"], "UNINITIALIZED")
        self.assertIsNone(state["provisioned_at"])

    def test_uninitialized_state_with_existing_user_refuses_provisioning(self) -> None:
        with self.database.transaction(write=True) as connection:
            connection.execute(
                "INSERT INTO usuarios(nome, login, senha_hash, perfil) "
                "VALUES ('Usuário inesperado', 'inesperado', 'hash-fixture', 'caixa')"
            )
        with self.assertRaises(ConflictError):
            self._provision()
        self.assertEqual(self.database.fetch_one("SELECT COUNT(*) AS total FROM usuarios")["total"], 1)
        self.assertEqual(self.database.fetch_one("SELECT state FROM installation_state")["state"], "UNINITIALIZED")

    def test_ready_installation_cannot_be_reprovisioned_even_after_users_are_removed(self) -> None:
        self._provision()
        with self.database.transaction(write=True) as connection:
            connection.execute("DELETE FROM logs_auditoria")
            connection.execute("DELETE FROM usuarios")
        with self.assertRaises(ConflictError):
            self._provision()
        self.assertEqual(self.database.fetch_one("SELECT COUNT(*) AS total FROM usuarios")["total"], 0)
        self.assertEqual(self.database.fetch_one("SELECT state FROM installation_state")["state"], "READY")


class ProvisioningIntegrationTestCase(unittest.TestCase):
    def test_constructing_pdv_service_does_not_create_a_user_and_exposes_apis(self) -> None:
        service = PDVService(database=Database(":memory:"))
        with service.database.transaction() as connection:
            users = connection.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0]
        self.assertEqual(users, 0)
        self.assertTrue(callable(getattr(service, "installation_status", None)))
        self.assertTrue(callable(getattr(service, "generate_recovery_code", None)))
        self.assertTrue(callable(getattr(service, "provision_initial_admin", None)))
        self.assertTrue(service.installation_status().requires_provisioning)

    def test_three_provisioning_apis_traverse_desktop_controller(self) -> None:
        service = PDVService(database=Database(":memory:"))
        controller = DesktopController(service)
        self.assertTrue(callable(getattr(controller, "installation_status", None)))
        self.assertTrue(callable(getattr(controller, "generate_recovery_code", None)))
        self.assertTrue(callable(getattr(controller, "provision_initial_admin", None)))
        before = controller.installation_status()
        recovery_code = controller.generate_recovery_code()
        created = controller.provision_initial_admin(TEST_ADMIN_NAME, TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD, recovery_code)
        after = controller.installation_status()
        self.assertTrue(before.requires_provisioning)
        self.assertFalse(after.requires_provisioning)
        self.assertEqual(created["login"], TEST_ADMIN_LOGIN)
        self.assertNotIn("senha_hash", created)

    def test_production_has_no_default_bootstrap_or_unproved_local_recovery_path(self) -> None:
        root = Path(__file__).resolve().parents[1]
        files = tuple((root / "db").glob("*.py")) + tuple((root / "services").glob("*.py")) + tuple(
            (root / "ui").glob("*.py")
        ) + (root / "desktop_controller.py", root / "main.py", root / "init_db.py")
        forbidden_names = {
            "DEFAULT_ADMIN_LOGIN",
            "DEFAULT_ADMIN_PASSWORD",
            "bootstrap_default_admin",
            "bootstrap_single_admin_recovery",
            "launch_single_admin_recovery",
        }
        found_names: set[str] = set()
        found_arguments: set[str] = set()
        found_credentials: set[str] = set()
        for path in files:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in forbidden_names:
                    found_names.add(node.name)
                elif isinstance(node, ast.Name) and node.id in forbidden_names:
                    found_names.add(node.id)
                elif isinstance(node, ast.Attribute) and node.attr in forbidden_names:
                    found_names.add(node.attr)
                elif isinstance(node, ast.Constant) and node.value == "--recuperar-admin-local":
                    found_arguments.add(node.value)
                elif isinstance(node, ast.Constant) and node.value == "admin123":
                    found_credentials.add(node.value)
        self.assertEqual(found_names, set())
        self.assertEqual(found_arguments, set())
        self.assertEqual(found_credentials, set())


if __name__ == "__main__":
    unittest.main()

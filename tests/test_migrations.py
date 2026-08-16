"""Contratos do motor de migrações em bancos SQLite temporários."""

from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
import re
from pathlib import Path
from unittest.mock import patch

from db.database import Database, DatabaseError
from db.migrations import MigrationManager, default_migrations
from db.schema import SCHEMA_SQL, SCHEMA_VERSION
from services.backup import BackupService
from services.errors import BackupError


class MigrationManagerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.path = self.root / "pdv.sqlite3"
        self.database = Database(self.path)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _set_version(self, version: int) -> None:
        with self.database.transaction(write=True) as connection:
            connection.execute(
                "UPDATE schema_meta SET valor = ? WHERE chave = 'schema_version'", (str(version),)
            )

    def _version(self) -> int:
        with self.database.transaction() as connection:
            return int(connection.execute(
                "SELECT valor FROM schema_meta WHERE chave = 'schema_version'"
            ).fetchone()[0])

    def _set_raw_version_and_journal(self, version: int, journal_mode: str = "DELETE") -> None:
        connection = sqlite3.connect(self.path)
        try:
            connection.execute(
                "UPDATE schema_meta SET valor = ? WHERE chave = 'schema_version'", (str(version),)
            )
            connection.commit()
            self.assertEqual(connection.execute(f"PRAGMA journal_mode = {journal_mode}").fetchone()[0], journal_mode.lower())
        finally:
            connection.close()

    def _raw_journal_mode(self) -> str:
        connection = sqlite3.connect(self.path)
        try:
            return str(connection.execute("PRAGMA journal_mode").fetchone()[0])
        finally:
            connection.close()

    def test_empty_database_initializes_without_backup(self) -> None:
        result = self.database.initialize(backup_dir=self.root / "backups")

        self.assertEqual(result.from_version, 0)
        self.assertEqual(result.to_version, SCHEMA_VERSION)
        self.assertEqual(result.applied_versions, ())
        self.assertEqual(result.backup_path, None)
        self.assertFalse((self.root / "backups").exists())
        self.assertEqual(self._version(), SCHEMA_VERSION)

    def test_existing_v4_database_is_backed_up_before_migration(self) -> None:
        self.database.initialize()
        self._set_version(4)
        backup_dir = self.root / "backups"

        result = self.database.initialize(backup_dir=backup_dir)

        self.assertEqual(result.from_version, 4)
        self.assertEqual(result.to_version, SCHEMA_VERSION)
        self.assertEqual(result.applied_versions, (5, 6, 7, 8, 9))
        self.assertIsNotNone(result.backup_path)
        assert result.backup_path is not None
        backup_connection = sqlite3.connect(result.backup_path)
        try:
            self.assertEqual(
                backup_connection.execute(
                    "SELECT valor FROM schema_meta WHERE chave = 'schema_version'"
                ).fetchone()[0],
                "4",
            )
        finally:
            backup_connection.close()
        self.assertEqual(self._version(), SCHEMA_VERSION)

    def test_backup_failure_aborts_without_schema_change(self) -> None:
        self.database.initialize()
        self._set_version(4)
        before_names = self.database.fetch_all("SELECT name FROM sqlite_master ORDER BY name")

        with patch.object(self.database, "backup_to", side_effect=DatabaseError("disco indisponível")):
            with self.assertRaises(DatabaseError):
                self.database.initialize(backup_dir=self.root / "backups")

        self.assertEqual(self._version(), 4)
        self.assertEqual(self.database.fetch_all("SELECT name FROM sqlite_master ORDER BY name"), before_names)

    def test_backup_failure_does_not_change_source_journal_mode(self) -> None:
        self.database.initialize()
        self._set_raw_version_and_journal(4)

        with patch.object(self.database, "backup_to", side_effect=DatabaseError("disco indisponível")):
            with self.assertRaises(DatabaseError):
                self.database.initialize(backup_dir=self.root / "backups")

        self.assertEqual(self._raw_journal_mode(), "delete")
        self.assertEqual(self._version(), 4)

    def test_failure_after_first_ddl_rolls_back_schema_and_version(self) -> None:
        self.database.initialize()
        self._set_version(4)

        def create_temporary_table(connection: sqlite3.Connection) -> None:
            connection.execute("CREATE TABLE migration_must_rollback (id INTEGER PRIMARY KEY)")
            raise sqlite3.OperationalError("falha simulada após DDL")

        manager = MigrationManager(self.database, 5, {5: create_temporary_table})
        with self.assertRaises(DatabaseError):
            manager.migrate(backup_dir=self.root / "backups")

        with self.database.transaction() as connection:
            self.assertIsNone(connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'migration_must_rollback'"
            ).fetchone())
        self.assertEqual(self._version(), 4)

    def test_newer_schema_is_rejected_without_write(self) -> None:
        self.database.initialize()
        self._set_version(SCHEMA_VERSION + 1)

        with self.assertRaises(DatabaseError):
            self.database.initialize(backup_dir=self.root / "backups")

        self.assertEqual(self._version(), SCHEMA_VERSION + 1)
        self.assertFalse((self.root / "backups").exists())

    def test_newer_schema_is_rejected_without_changing_journal_mode(self) -> None:
        self.database.initialize()
        self._set_raw_version_and_journal(SCHEMA_VERSION + 1)

        with self.assertRaises(DatabaseError):
            self.database.initialize(backup_dir=self.root / "backups")

        self.assertEqual(self._raw_journal_mode(), "delete")
        self.assertEqual(self._version(), SCHEMA_VERSION + 1)

    def test_nonempty_database_marked_version_zero_is_rejected_without_ddl(self) -> None:
        self.database.initialize()
        self._set_version(0)
        with self.database.transaction(write=True) as connection:
            connection.execute("DROP INDEX idx_produtos_classificacao")

        with self.assertRaises(DatabaseError):
            self.database.initialize(backup_dir=self.root / "backups")

        self.assertEqual(self._version(), 0)
        with self.database.transaction() as connection:
            self.assertIsNone(connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND name = 'idx_produtos_classificacao'"
            ).fetchone())
        self.assertFalse((self.root / "backups").exists())

    def test_legacy_migration_enforces_fresh_schema_code_constraints(self) -> None:
        connection = sqlite3.connect(self.path)
        try:
            connection.executescript(
                "CREATE TABLE schema_meta (chave TEXT PRIMARY KEY, valor TEXT NOT NULL);"
                "INSERT INTO schema_meta VALUES ('schema_version', '4');"
                "CREATE TABLE produtos ("
                "gtin TEXT PRIMARY KEY, nome TEXT NOT NULL, tipo_codigo TEXT NOT NULL DEFAULT 'GTIN', "
                "categoria TEXT NOT NULL DEFAULT 'Outros', validacao_codigo TEXT NOT NULL DEFAULT 'PENDENTE', "
                "ativo INTEGER NOT NULL DEFAULT 1);"
                "CREATE TABLE cache_gtin ("
                "gtin TEXT PRIMARY KEY, status TEXT NOT NULL, fonte TEXT NOT NULL, expira_em TEXT NOT NULL, "
                "tentativas INTEGER NOT NULL DEFAULT 1);"
                "CREATE TABLE usuarios (id INTEGER PRIMARY KEY, nome TEXT NOT NULL, login TEXT NOT NULL, "
                "senha_hash TEXT NOT NULL, perfil TEXT NOT NULL, ativo INTEGER NOT NULL DEFAULT 1, "
                "deve_trocar_senha INTEGER NOT NULL DEFAULT 0, codigo_recuperacao_hash TEXT, "
                "tentativas_login_falhas INTEGER NOT NULL DEFAULT 0, criado_em TEXT, atualizado_em TEXT);"
            )
            connection.commit()
        finally:
            connection.close()

        result = MigrationManager(
            self.database, 8, default_migrations()
        ).migrate(backup_dir=self.root / "backups")

        self.assertEqual(result.applied_versions, (5, 6, 7, 8))
        with self.database.transaction(write=True) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO produtos (gtin, nome, tipo_codigo) VALUES ('1', 'Teste', 'INVALIDO')")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO produtos (gtin, nome, validacao_codigo) VALUES ('2', 'Teste', 'INVALIDA')")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO cache_gtin (gtin, status, fonte, expira_em) VALUES ('3', 'INVALIDO', 'teste', '2030-01-01')"
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO cache_gtin (gtin, status, fonte, expira_em, tentativas) "
                    "VALUES ('4', 'ENCONTRADO', 'teste', '2030-01-01', 0)"
                )

    def test_defective_v5_is_backed_up_and_repaired_by_v6(self) -> None:
        connection = sqlite3.connect(self.path)
        try:
            connection.executescript(
                "CREATE TABLE schema_meta (chave TEXT PRIMARY KEY, valor TEXT NOT NULL);"
                "INSERT INTO schema_meta VALUES ('schema_version', '5');"
                "CREATE TABLE produtos ("
                "gtin TEXT PRIMARY KEY, nome TEXT NOT NULL, tipo_codigo TEXT NOT NULL DEFAULT 'GTIN', "
                "categoria TEXT NOT NULL DEFAULT 'Outros', validacao_codigo TEXT NOT NULL DEFAULT 'PENDENTE', "
                "ativo INTEGER NOT NULL DEFAULT 1);"
                "CREATE TABLE cache_gtin ("
                "gtin TEXT PRIMARY KEY, status TEXT NOT NULL, fonte TEXT NOT NULL, expira_em TEXT NOT NULL, "
                "tentativas INTEGER NOT NULL DEFAULT 1);"
                "CREATE TABLE usuarios (id INTEGER PRIMARY KEY, nome TEXT NOT NULL, login TEXT NOT NULL, "
                "senha_hash TEXT NOT NULL, perfil TEXT NOT NULL, ativo INTEGER NOT NULL DEFAULT 1, "
                "deve_trocar_senha INTEGER NOT NULL DEFAULT 0, codigo_recuperacao_hash TEXT, "
                "tentativas_login_falhas INTEGER NOT NULL DEFAULT 0, criado_em TEXT, atualizado_em TEXT);"
            )
            connection.commit()
        finally:
            connection.close()

        result = MigrationManager(
            self.database, 8, default_migrations()
        ).migrate(backup_dir=self.root / "backups")

        self.assertEqual(result.from_version, 5)
        self.assertEqual(result.to_version, 8)
        self.assertEqual(result.applied_versions, (6, 7, 8))
        self.assertIsNotNone(result.backup_path)
        assert result.backup_path is not None
        backup_connection = sqlite3.connect(result.backup_path)
        try:
            self.assertEqual(backup_connection.execute(
                "SELECT valor FROM schema_meta WHERE chave = 'schema_version'"
            ).fetchone()[0], "5")
        finally:
            backup_connection.close()
        with self.database.transaction(write=True) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO produtos (gtin, nome, tipo_codigo) VALUES ('v5', 'Teste', 'INVALIDO')")
        self.assertEqual(self._version(), 8)

    def test_initialize_is_idempotent(self) -> None:
        self.database.initialize()

        result = self.database.initialize(backup_dir=self.root / "backups")

        self.assertEqual(result.from_version, SCHEMA_VERSION)
        self.assertEqual(result.to_version, SCHEMA_VERSION)
        self.assertEqual(result.applied_versions, ())
        self.assertIsNone(result.backup_path)
        self.assertFalse((self.root / "backups").exists())

    def test_concurrent_initializations_converge_after_version_reread(self) -> None:
        self.database.initialize()
        self._set_version(4)
        start = threading.Barrier(2)
        results: list[object] = []
        errors: list[BaseException] = []

        def initialize() -> None:
            try:
                start.wait(timeout=5)
                results.append(self.database.initialize(backup_dir=self.root / "backups"))
            except BaseException as exc:  # A thread deve devolver qualquer falha ao teste principal.
                errors.append(exc)

        threads = [threading.Thread(target=initialize), threading.Thread(target=initialize)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(self._version(), SCHEMA_VERSION)
        self.assertEqual(sum(result.applied_versions == (5, 6, 7, 8, 9) for result in results), 1)

    @staticmethod
    def _v7_schema_sql() -> str:
        rate_columns = {
            "login_falhas_janela_inicio TEXT,",
            "login_bloqueado_ate TEXT,",
            "recuperacao_falhas INTEGER NOT NULL DEFAULT 0 CHECK (recuperacao_falhas >= 0),",
            "recuperacao_janela_inicio TEXT,",
            "recuperacao_bloqueado_ate TEXT,",
        }
        lines = [line for line in SCHEMA_SQL.splitlines() if line.strip() not in rate_columns]
        schema = "\n".join(lines)
        return re.sub(
            r"CREATE TRIGGER IF NOT EXISTS trg_usuarios_rate_limit_[\s\S]*?END;\s*",
            "",
            schema,
        )

    def _create_v7_database(self) -> None:
        connection = sqlite3.connect(self.path)
        try:
            connection.executescript(self._v7_schema_sql())
            connection.execute(
                "INSERT INTO schema_meta(chave, valor) VALUES ('schema_version', '7') "
                "ON CONFLICT(chave) DO UPDATE SET valor = '7'"
            )
            connection.execute(
                "INSERT INTO usuarios(nome, login, senha_hash, perfil, codigo_recuperacao_hash) "
                "VALUES ('Admin preservado', 'admin.preservado', 'hash-senha-preservado', 'admin', 'hash-codigo-preservado')"
            )
            connection.execute(
                "UPDATE installation_state SET state = 'READY', provisioned_at = CURRENT_TIMESTAMP WHERE singleton = 1"
            )
            connection.commit()
        finally:
            connection.close()

    def test_v7_to_v8_is_backed_up_and_preserves_identity_data(self) -> None:
        self._create_v7_database()

        result = MigrationManager(
            self.database, 8, default_migrations()
        ).migrate(backup_dir=self.root / "backups")

        self.assertEqual(result.from_version, 7)
        self.assertEqual(result.to_version, 8)
        self.assertEqual(result.applied_versions, (8,))
        self.assertIsNotNone(result.backup_path)
        assert result.backup_path is not None
        backup = sqlite3.connect(result.backup_path)
        try:
            self.assertEqual(
                backup.execute(
                    "SELECT valor FROM schema_meta WHERE chave = 'schema_version'"
                ).fetchone()[0],
                "7",
            )
        finally:
            backup.close()
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM usuarios WHERE login = 'admin.preservado'"
            ).fetchone()
            self.assertEqual(row["senha_hash"], "hash-senha-preservado")
            self.assertEqual(row["codigo_recuperacao_hash"], "hash-codigo-preservado")
            self.assertEqual(row["tentativas_login_falhas"], 0)
            self.assertEqual(row["recuperacao_falhas"], 0)
            self.assertIsNone(row["login_falhas_janela_inicio"])
            self.assertIsNone(row["login_bloqueado_ate"])
            self.assertIsNone(row["recuperacao_janela_inicio"])
            self.assertIsNone(row["recuperacao_bloqueado_ate"])

    def test_v8_failure_after_first_alter_rolls_back_columns_and_version(self) -> None:
        self._create_v7_database()

        def fail_after_first_alter(connection: sqlite3.Connection) -> None:
            connection.execute("ALTER TABLE usuarios ADD COLUMN login_falhas_janela_inicio TEXT")
            raise sqlite3.OperationalError("falha v8 simulada")

        manager = MigrationManager(self.database, 8, {8: fail_after_first_alter})
        with self.assertRaises(DatabaseError):
            manager.migrate(backup_dir=self.root / "backups")

        with self.database.transaction() as connection:
            names = {row["name"] for row in connection.execute("PRAGMA table_info(usuarios)")}
        self.assertNotIn("login_falhas_janela_inicio", names)
        self.assertEqual(self._version(), 7)

    def test_fresh_v8_has_rate_limit_defaults_and_constraints(self) -> None:
        self.database.initialize()
        with self.database.transaction() as connection:
            columns = {
                row["name"]: row
                for row in connection.execute("PRAGMA table_info(usuarios)").fetchall()
            }
        self.assertTrue(
            {
                "login_falhas_janela_inicio",
                "login_bloqueado_ate",
                "recuperacao_falhas",
                "recuperacao_janela_inicio",
                "recuperacao_bloqueado_ate",
            }.issubset(columns)
        )
        self.assertEqual(str(columns["recuperacao_falhas"]["dflt_value"]), "0")
        with self.database.transaction(write=True) as connection:
            connection.execute(
                "INSERT INTO usuarios(nome, login, senha_hash, perfil) VALUES ('Teste', 'teste', 'hash', 'caixa')"
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE usuarios SET tentativas_login_falhas = -1 WHERE login = 'teste'"
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE usuarios SET recuperacao_falhas = -1 WHERE login = 'teste'"
                )


class BackupServiceTestCase(unittest.TestCase):
    def test_backup_partial_is_closed_and_removed_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database = Database(root / "pdv.sqlite3")
            database.initialize()
            service = BackupService(database, root / "backups")
            target_path = root / "backups" / "partial.sqlite3"

            class Target:
                closed = False

                def close(self) -> None:
                    self.closed = True

            class Source:
                def backup(self, target: Target) -> None:
                    target_path.touch()
                    raise sqlite3.OperationalError("cópia interrompida")

                def close(self) -> None:
                    pass

            target = Target()
            original_unlink = Path.unlink

            def unlink_after_close(path: Path, *args: object, **kwargs: object) -> None:
                self.assertTrue(target.closed)
                original_unlink(path, *args, **kwargs)

            with patch.object(service, "_backup_name", return_value="partial.sqlite3"), \
                 patch.object(database, "_connect", return_value=Source()), \
                 patch("services.backup.sqlite3.connect", return_value=target), \
                 patch("services.backup.Path.unlink", new=unlink_after_close):
                with self.assertRaises(BackupError):
                    service.backup_database()

            self.assertTrue(target.closed)
            self.assertFalse(target_path.exists())


class DatabaseBackupTestCase(unittest.TestCase):
    def test_backup_to_closes_and_removes_partial_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database = Database(root / "pdv.sqlite3")
            database.initialize()
            target_path = root / "backups" / "partial.sqlite3"

            class Target:
                closed = False

                def close(self) -> None:
                    self.closed = True

            class Source:
                def backup(self, target: Target) -> None:
                    raise sqlite3.OperationalError("cópia interrompida")

                def close(self) -> None:
                    pass

            target = Target()
            original_unlink = Path.unlink

            def unlink_after_close(path: Path, *args: object, **kwargs: object) -> None:
                self.assertTrue(target.closed)
                original_unlink(path, *args, **kwargs)

            def create_target(path: str, *args: object, **kwargs: object) -> Target:
                Path(path).touch()
                return target

            with patch.object(database, "_connect_inspection", return_value=Source()), \
                 patch("db.database.sqlite3.connect", side_effect=create_target), \
                 patch("db.database.Path.unlink", new=unlink_after_close):
                with self.assertRaises(DatabaseError):
                    database.backup_to(target_path)

            self.assertTrue(target.closed)
            self.assertFalse(target_path.exists())
            self.assertEqual(list(target_path.parent.glob(f".{target_path.name}.*.tmp")), [])

    def test_backup_to_source_open_failure_preserves_existing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database = Database(root / "pdv.sqlite3")
            destination = root / "backups" / "preexisting.sqlite3"
            destination.parent.mkdir()
            expected_contents = b"backup-validado-anterior"
            destination.write_bytes(expected_contents)

            with patch.object(database, "_connect_inspection", side_effect=DatabaseError("fonte indisponível")):
                with self.assertRaises(DatabaseError):
                    database.backup_to(destination)

            self.assertEqual(destination.read_bytes(), expected_contents)


if __name__ == "__main__":
    unittest.main()

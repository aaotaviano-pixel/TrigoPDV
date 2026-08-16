"""Regressões de isolamento e ciclo de vida para bancos SQLite em memória."""

from __future__ import annotations

import gc
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from db.database import Database, DatabaseError
from db.schema import SCHEMA_VERSION


class MemoryDatabaseIsolationTestCase(unittest.TestCase):
    @staticmethod
    def _assert_fresh(database: Database) -> None:
        version = database.fetch_one(
            "SELECT valor FROM schema_meta WHERE chave = 'schema_version'"
        )
        state = database.fetch_one(
            "SELECT state FROM installation_state WHERE singleton = 1"
        )
        users = database.fetch_one("SELECT COUNT(*) AS total FROM usuarios")

        assert version is not None
        assert state is not None
        assert users is not None
        if int(version["valor"]) != SCHEMA_VERSION:
            raise AssertionError(version)
        if state["state"] != "UNINITIALIZED":
            raise AssertionError(state)
        if users["total"] != 0:
            raise AssertionError(users)

    @staticmethod
    def _mark_ready(database: Database, marker: str) -> None:
        with database.transaction(write=True) as connection:
            connection.execute(
                "INSERT INTO usuarios(nome, login, senha_hash, perfil) VALUES (?, ?, ?, 'admin')",
                (f"Administrador {marker}", f"admin.{marker}", f"hash-{marker}"),
            )
            connection.execute(
                "UPDATE installation_state "
                "SET state = 'READY', provisioned_at = CURRENT_TIMESTAMP "
                "WHERE singleton = 1"
            )

    def test_sequential_instances_survive_gc_and_reused_identity_without_sharing_state(self) -> None:
        """Uma conexão sobrevivente não pode contaminar uma nova Database.

        O ``id`` fixo reproduz de forma determinística a reutilização do nome
        que antes dependia do endereço do objeto Python. As conexões mantidas
        abertas simulam uma conexão temporária que sobreviveu à âncora.
        """

        surviving_connections = []
        try:
            with patch("db.database.id", return_value=7, create=True):
                for index in range(12):
                    database = Database(":memory:")
                    database.initialize()
                    self._assert_fresh(database)
                    self._mark_ready(database, str(index))

                    surviving_connections.append(database._connect())
                    del database
                    gc.collect()
        finally:
            for connection in surviving_connections:
                connection.close()

    def test_many_simultaneous_memory_databases_are_isolated(self) -> None:
        workers = 8
        ready = threading.Barrier(workers)

        def exercise(index: int) -> tuple[str, int]:
            database = Database(":memory:")
            try:
                database.initialize()
                self._assert_fresh(database)
                marker = str(index)
                self._mark_ready(database, marker)
                ready.wait(timeout=15)
                state = database.fetch_one(
                    "SELECT state FROM installation_state WHERE singleton = 1"
                )
                users = database.fetch_one("SELECT login FROM usuarios ORDER BY id")
                assert state is not None
                assert users is not None
                return state["state"], int(users["login"] == f"admin.{marker}")
            finally:
                database.close()

        with ThreadPoolExecutor(max_workers=workers) as executor:
            results = list(executor.map(exercise, range(workers)))

        self.assertEqual(results, [("READY", 1)] * workers)

    def test_connections_from_same_database_share_until_close(self) -> None:
        database = Database(":memory:")
        database.initialize()
        self._mark_ready(database, "shared")

        self.assertEqual(
            database.fetch_one("SELECT login FROM usuarios")["login"],
            "admin.shared",
        )

        database.close()
        database.close()
        with self.assertRaises(DatabaseError):
            database.fetch_one("SELECT 1")

        replacement = Database(":memory:")
        try:
            replacement.initialize()
            self._assert_fresh(replacement)
        finally:
            replacement.close()


if __name__ == "__main__":
    unittest.main()

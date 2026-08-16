"""Migrações SQLite aditivas, transacionais e com cópia prévia verificada."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Mapping

from .database import DatabaseError
from .schema import fresh_schema_statements

if TYPE_CHECKING:
    from .database import Database


@dataclass(frozen=True)
class MigrationResult:
    from_version: int
    to_version: int
    applied_versions: tuple[int, ...]
    backup_path: Path | None


Migration = Callable[[sqlite3.Connection], None]


class MigrationManager:
    """Aplica migrações somente para frente, em uma única transação de escrita."""

    def __init__(
        self,
        database: Database,
        target_version: int,
        migrations: Mapping[int, Migration],
    ) -> None:
        self.database = database
        self.target_version = target_version
        self.migrations = dict(migrations)

    @staticmethod
    def _table_names(connection: sqlite3.Connection) -> set[str]:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }

    def _read_version(self, connection: sqlite3.Connection) -> int:
        tables = self._table_names(connection)
        if not tables:
            return 0
        if "schema_meta" in tables:
            row = connection.execute(
                "SELECT valor FROM schema_meta WHERE chave = 'schema_version'"
            ).fetchone()
            if row is not None:
                try:
                    version = int(row[0])
                except (TypeError, ValueError) as exc:
                    raise DatabaseError("A versão do schema é inválida.") from exc
                if version == 0:
                    raise DatabaseError("Um banco não vazio não pode declarar schema_version 0.")
                return version
        recognizable = {"produtos", "usuarios", "caixas", "vendas", "itens_venda"}
        if recognizable.issubset(tables):
            return 1
        raise DatabaseError("O banco existente não possui um schema TrigoPDV reconhecível.")

    @staticmethod
    def _set_version(connection: sqlite3.Connection, version: int) -> None:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_meta (chave TEXT PRIMARY KEY, valor TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_meta(chave, valor) VALUES ('schema_version', ?) "
            "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor",
            (str(version),),
        )

    @staticmethod
    def _assert_integrity(connection: sqlite3.Connection) -> None:
        check = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if check != "ok":
            raise DatabaseError(f"A verificação de integridade do banco falhou: {check}")

    def _apply_fresh_schema(self, connection: sqlite3.Connection) -> tuple[int, ...]:
        for statement in fresh_schema_statements():
            connection.execute(statement)
        self._set_version(connection, self.target_version)
        self._assert_integrity(connection)
        return ()

    def _backup_destination(self, backup_dir: Path | None) -> Path:
        if backup_dir is None:
            if self.database._memory:
                raise DatabaseError("Uma base em memória precisa de uma pasta de backup explícita.")
            backup_dir = self.database.path.parent / "backups"
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        return backup_dir / f"pre-migracao-{timestamp}.sqlite3"

    def migrate(self, *, backup_dir: Path | None) -> MigrationResult:
        """Faz backup antes de DDL e relê a versão após adquirir o lock final."""

        inspection = self.database._connect_inspection()
        try:
            observed_version = self._read_version(inspection)
        finally:
            inspection.close()

        if observed_version == 0:
            with self.database.transaction(write=True) as connection:
                current_version = self._read_version(connection)
                if current_version == 0:
                    applied = self._apply_fresh_schema(connection)
                    return MigrationResult(0, self.target_version, applied, None)
                if current_version > self.target_version:
                    raise DatabaseError("O banco foi criado por uma versão mais nova do TrigoPDV.")
                if current_version == self.target_version:
                    return MigrationResult(current_version, current_version, (), None)
                observed_version = current_version

        if observed_version > self.target_version:
            raise DatabaseError("O banco foi criado por uma versão mais nova do TrigoPDV.")
        if observed_version == self.target_version:
            return MigrationResult(observed_version, observed_version, (), None)

        backup_path = self.database.backup_to(self._backup_destination(backup_dir))

        with self.database.transaction(write=True) as connection:
            current_version = self._read_version(connection)
            if current_version > self.target_version:
                raise DatabaseError("O banco foi criado por uma versão mais nova do TrigoPDV.")
            if current_version == self.target_version:
                return MigrationResult(current_version, current_version, (), backup_path)

            applied: list[int] = []
            for version in range(current_version + 1, self.target_version + 1):
                migration = self.migrations.get(version)
                if migration is None:
                    raise DatabaseError(f"Não há migração disponível para a versão {version}.")
                migration(connection)
                self._set_version(connection, version)
                applied.append(version)
            self._assert_integrity(connection)
            return MigrationResult(current_version, self.target_version, tuple(applied), backup_path)


def _column_names(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}


def _migration_2(connection: sqlite3.Connection) -> None:
    if "chave_idempotencia" not in _column_names(connection, "vendas"):
        connection.execute("ALTER TABLE vendas ADD COLUMN chave_idempotencia TEXT")
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_vendas_chave_idempotencia "
        "ON vendas(chave_idempotencia) WHERE chave_idempotencia IS NOT NULL"
    )


def _migration_3(connection: sqlite3.Connection) -> None:
    columns = _column_names(connection, "usuarios")
    if "codigo_recuperacao_hash" not in columns:
        connection.execute("ALTER TABLE usuarios ADD COLUMN codigo_recuperacao_hash TEXT")
    if "tentativas_login_falhas" not in columns:
        connection.execute(
            "ALTER TABLE usuarios ADD COLUMN tentativas_login_falhas INTEGER NOT NULL DEFAULT 0"
        )


def _migration_4(connection: sqlite3.Connection) -> None:
    columns = _column_names(connection, "produtos")
    additions = (
        ("tipo_codigo", "TEXT NOT NULL DEFAULT 'GTIN' CHECK (tipo_codigo IN ('GTIN', 'PLU'))"),
        ("categoria", "TEXT NOT NULL DEFAULT 'Outros'"),
        ("subcategoria", "TEXT"),
        ("detalhes_embalagem", "TEXT"),
        (
            "validacao_codigo",
            "TEXT NOT NULL DEFAULT 'PENDENTE' CHECK (validacao_codigo IN "
            "('PENDENTE', 'VALIDO_ESTRUTURAL', 'CONFIRMADO', 'INCOMPATIVEL', 'VALIDO_INTERNO'))",
        ),
        ("fonte_validacao", "TEXT"),
        ("validado_em", "TEXT"),
    )
    for name, definition in additions:
        if name not in columns:
            connection.execute(f"ALTER TABLE produtos ADD COLUMN {name} {definition}")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS cache_gtin ("
        "gtin TEXT PRIMARY KEY, status TEXT NOT NULL CHECK (status IN "
        "('ENCONTRADO', 'NAO_ENCONTRADO', 'INDISPONIVEL')), fonte TEXT NOT NULL, nome TEXT, marca TEXT, "
        "categoria TEXT, detalhes_embalagem TEXT, consultado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "expira_em TEXT NOT NULL, tentativas INTEGER NOT NULL DEFAULT 1 CHECK (tentativas > 0))"
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_produtos_categoria ON produtos(categoria, subcategoria, ativo)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_produtos_tipo_codigo ON produtos(tipo_codigo, ativo)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_produtos_validacao_codigo ON produtos(validacao_codigo, ativo)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_cache_gtin_expira ON cache_gtin(expira_em)")


def _migration_5(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_produtos_classificacao "
        "ON produtos(categoria, tipo_codigo, ativo)"
    )
    connection.execute(
        "CREATE TRIGGER IF NOT EXISTS trg_produtos_codigos_validos_insert "
        "BEFORE INSERT ON produtos FOR EACH ROW WHEN NEW.tipo_codigo IS NULL "
        "OR NEW.tipo_codigo NOT IN ('GTIN', 'PLU') OR NEW.validacao_codigo IS NULL "
        "OR NEW.validacao_codigo NOT IN ('PENDENTE', 'VALIDO_ESTRUTURAL', 'CONFIRMADO', "
        "'INCOMPATIVEL', 'VALIDO_INTERNO') BEGIN SELECT RAISE(ABORT, 'codigo de produto inválido'); END"
    )
    connection.execute(
        "CREATE TRIGGER IF NOT EXISTS trg_produtos_codigos_validos_update "
        "BEFORE UPDATE OF tipo_codigo, validacao_codigo ON produtos FOR EACH ROW "
        "WHEN NEW.tipo_codigo IS NULL OR NEW.tipo_codigo NOT IN ('GTIN', 'PLU') "
        "OR NEW.validacao_codigo IS NULL OR NEW.validacao_codigo NOT IN "
        "('PENDENTE', 'VALIDO_ESTRUTURAL', 'CONFIRMADO', 'INCOMPATIVEL', 'VALIDO_INTERNO') "
        "BEGIN SELECT RAISE(ABORT, 'codigo de produto inválido'); END"
    )
    connection.execute(
        "CREATE TRIGGER IF NOT EXISTS trg_cache_gtin_valido_insert "
        "BEFORE INSERT ON cache_gtin FOR EACH ROW WHEN NEW.status IS NULL "
        "OR NEW.status NOT IN ('ENCONTRADO', 'NAO_ENCONTRADO', 'INDISPONIVEL') "
        "OR NEW.tentativas IS NULL OR NEW.tentativas <= 0 "
        "BEGIN SELECT RAISE(ABORT, 'cache GTIN inválido'); END"
    )
    connection.execute(
        "CREATE TRIGGER IF NOT EXISTS trg_cache_gtin_valido_update "
        "BEFORE UPDATE OF status, tentativas ON cache_gtin FOR EACH ROW WHEN NEW.status IS NULL "
        "OR NEW.status NOT IN ('ENCONTRADO', 'NAO_ENCONTRADO', 'INDISPONIVEL') "
        "OR NEW.tentativas IS NULL OR NEW.tentativas <= 0 "
        "BEGIN SELECT RAISE(ABORT, 'cache GTIN inválido'); END"
    )


def _migration_6(connection: sqlite3.Connection) -> None:
    """Repara bases v5 criadas antes das invariantes aditivas do round 1."""

    connection.execute(
        "CREATE TRIGGER IF NOT EXISTS trg_produtos_codigos_validos_insert "
        "BEFORE INSERT ON produtos FOR EACH ROW WHEN NEW.tipo_codigo IS NULL "
        "OR NEW.tipo_codigo NOT IN ('GTIN', 'PLU') OR NEW.validacao_codigo IS NULL "
        "OR NEW.validacao_codigo NOT IN ('PENDENTE', 'VALIDO_ESTRUTURAL', 'CONFIRMADO', "
        "'INCOMPATIVEL', 'VALIDO_INTERNO') BEGIN SELECT RAISE(ABORT, 'codigo de produto inválido'); END"
    )
    connection.execute(
        "CREATE TRIGGER IF NOT EXISTS trg_produtos_codigos_validos_update "
        "BEFORE UPDATE OF tipo_codigo, validacao_codigo ON produtos FOR EACH ROW "
        "WHEN NEW.tipo_codigo IS NULL OR NEW.tipo_codigo NOT IN ('GTIN', 'PLU') "
        "OR NEW.validacao_codigo IS NULL OR NEW.validacao_codigo NOT IN "
        "('PENDENTE', 'VALIDO_ESTRUTURAL', 'CONFIRMADO', 'INCOMPATIVEL', 'VALIDO_INTERNO') "
        "BEGIN SELECT RAISE(ABORT, 'codigo de produto inválido'); END"
    )
    connection.execute(
        "CREATE TRIGGER IF NOT EXISTS trg_cache_gtin_valido_insert "
        "BEFORE INSERT ON cache_gtin FOR EACH ROW WHEN NEW.status IS NULL "
        "OR NEW.status NOT IN ('ENCONTRADO', 'NAO_ENCONTRADO', 'INDISPONIVEL') "
        "OR NEW.tentativas IS NULL OR NEW.tentativas <= 0 "
        "BEGIN SELECT RAISE(ABORT, 'cache GTIN inválido'); END"
    )
    connection.execute(
        "CREATE TRIGGER IF NOT EXISTS trg_cache_gtin_valido_update "
        "BEFORE UPDATE OF status, tentativas ON cache_gtin FOR EACH ROW WHEN NEW.status IS NULL "
        "OR NEW.status NOT IN ('ENCONTRADO', 'NAO_ENCONTRADO', 'INDISPONIVEL') "
        "OR NEW.tentativas IS NULL OR NEW.tentativas <= 0 "
        "BEGIN SELECT RAISE(ABORT, 'cache GTIN inválido'); END"
    )


def _migration_7(connection: sqlite3.Connection) -> None:
    has_users_table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'usuarios'"
    ).fetchone() is not None
    has_users = bool(
        has_users_table
        and connection.execute("SELECT 1 FROM usuarios LIMIT 1").fetchone() is not None
    )
    state = "READY" if has_users else "UNINITIALIZED"
    provisioned_at = datetime.now().astimezone().isoformat(timespec="seconds") if has_users else None
    connection.execute(
        "CREATE TABLE IF NOT EXISTS installation_state ("
        "singleton INTEGER PRIMARY KEY CHECK (singleton = 1), "
        "installation_id TEXT NOT NULL UNIQUE CHECK (length(installation_id) = 36), "
        "state TEXT NOT NULL CHECK (state IN ('UNINITIALIZED', 'READY')), "
        "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, provisioned_at TEXT)"
    )
    connection.execute(
        "INSERT INTO installation_state(singleton, installation_id, state, created_at, provisioned_at) "
        "VALUES (1, "
        "lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-' || "
        "lower(hex(randomblob(2))) || '-' || lower(hex(randomblob(2))) || '-' || lower(hex(randomblob(6))), "
        "?, CURRENT_TIMESTAMP, ?) ON CONFLICT(singleton) DO NOTHING",
        (state, provisioned_at),
    )
    if has_users:
        connection.execute(
            "UPDATE installation_state SET state = 'READY', "
            "provisioned_at = COALESCE(provisioned_at, ?) "
            "WHERE singleton = 1 AND state = 'UNINITIALIZED'",
            (provisioned_at,),
        )


def _migration_8(connection: sqlite3.Connection) -> None:
    columns = _column_names(connection, "usuarios")
    additions = (
        ("login_falhas_janela_inicio", "TEXT"),
        ("login_bloqueado_ate", "TEXT"),
        (
            "recuperacao_falhas",
            "INTEGER NOT NULL DEFAULT 0 CHECK (recuperacao_falhas >= 0)",
        ),
        ("recuperacao_janela_inicio", "TEXT"),
        ("recuperacao_bloqueado_ate", "TEXT"),
    )
    for name, definition in additions:
        if name not in columns:
            connection.execute(f"ALTER TABLE usuarios ADD COLUMN {name} {definition}")
    connection.execute(
        "CREATE TRIGGER IF NOT EXISTS trg_usuarios_rate_limit_insert "
        "BEFORE INSERT ON usuarios FOR EACH ROW "
        "WHEN NEW.tentativas_login_falhas < 0 OR NEW.recuperacao_falhas < 0 "
        "BEGIN SELECT RAISE(ABORT, 'contador de autenticacao invalido'); END"
    )
    connection.execute(
        "CREATE TRIGGER IF NOT EXISTS trg_usuarios_rate_limit_update "
        "BEFORE UPDATE OF tentativas_login_falhas, recuperacao_falhas ON usuarios FOR EACH ROW "
        "WHEN NEW.tentativas_login_falhas < 0 OR NEW.recuperacao_falhas < 0 "
        "BEGIN SELECT RAISE(ABORT, 'contador de autenticacao invalido'); END"
    )


def _migration_9(connection: sqlite3.Connection) -> None:
    """Unifica checkout, caixa único, cancelamento, backup e impressão durável."""

    open_cash_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM caixas WHERE status = 'ABERTO'"
        ).fetchone()[0]
    )
    if open_cash_count > 1:
        raise DatabaseError(
            "A atualização encontrou mais de um caixa aberto. "
            "Feche os caixas excedentes na versão atual antes de tentar novamente."
        )

    sale_columns = _column_names(connection, "vendas")
    sale_additions = (
        ("total_manual", "REAL NOT NULL DEFAULT 0 CHECK (total_manual >= 0)"),
        ("autorizador_excecao_id", "INTEGER REFERENCES usuarios(id)"),
        ("motivo_excecao", "TEXT"),
        ("fingerprint", "TEXT"),
    )
    for name, definition in sale_additions:
        if name not in sale_columns:
            connection.execute(f"ALTER TABLE vendas ADD COLUMN {name} {definition}")

    item_columns = _column_names(connection, "itens_venda")
    item_additions = (
        (
            "tipo_lancamento",
            "TEXT NOT NULL DEFAULT 'CATALOGO' "
            "CHECK (tipo_lancamento IN ('CATALOGO', 'MANUAL'))",
        ),
        ("codigo_informado", "TEXT"),
        ("preco_original", "REAL CHECK (preco_original IS NULL OR preco_original >= 0)"),
    )
    for name, definition in item_additions:
        if name not in item_columns:
            connection.execute(f"ALTER TABLE itens_venda ADD COLUMN {name} {definition}")

    movement_columns = _column_names(connection, "movimentacoes_caixa")
    for name in ("chave_idempotencia", "fingerprint"):
        if name not in movement_columns:
            connection.execute(f"ALTER TABLE movimentacoes_caixa ADD COLUMN {name} TEXT")

    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_caixa_aberto_global "
        "ON caixas(status) WHERE status = 'ABERTO'"
    )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_vendas_chave_idempotencia "
        "ON vendas(chave_idempotencia) WHERE chave_idempotencia IS NOT NULL"
    )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_movimentacoes_chave_idempotencia "
        "ON movimentacoes_caixa(chave_idempotencia) WHERE chave_idempotencia IS NOT NULL"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS cancelamentos_venda ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "venda_id INTEGER NOT NULL UNIQUE REFERENCES vendas(id), "
        "operador_id INTEGER NOT NULL REFERENCES usuarios(id), "
        "autorizador_id INTEGER NOT NULL REFERENCES usuarios(id), "
        "motivo TEXT NOT NULL CHECK (length(trim(motivo)) BETWEEN 8 AND 250), "
        "chave_idempotencia TEXT NOT NULL UNIQUE, fingerprint TEXT NOT NULL, "
        "data_cancelamento TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS backups_caixa ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "caixa_id INTEGER NOT NULL UNIQUE REFERENCES caixas(id), "
        "solicitado_por INTEGER REFERENCES usuarios(id), "
        "status TEXT NOT NULL DEFAULT 'PENDENTE' "
        "CHECK (status IN ('PENDENTE', 'CONCLUIDO', 'FALHOU')), "
        "tentativas INTEGER NOT NULL DEFAULT 0 CHECK (tentativas >= 0), "
        "ultimo_erro TEXT, arquivo TEXT, solicitado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, concluido_em TEXT)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS impressao_outbox ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, venda_id INTEGER NOT NULL REFERENCES vendas(id), "
        "tipo TEXT NOT NULL CHECK (tipo IN ('ORIGINAL', 'SEGUNDA_VIA')), "
        "solicitado_por INTEGER REFERENCES usuarios(id), "
        "chave_idempotencia TEXT NOT NULL UNIQUE, fingerprint TEXT NOT NULL, "
        "status TEXT NOT NULL DEFAULT 'PENDENTE' "
        "CHECK (status IN ('PENDENTE', 'IMPRESSO', 'FALHOU')), "
        "payload TEXT NOT NULL, tentativas INTEGER NOT NULL DEFAULT 0 CHECK (tentativas >= 0), "
        "ultimo_erro TEXT, criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, impresso_em TEXT)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_cancelamentos_venda_data "
        "ON cancelamentos_venda(data_cancelamento)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_backups_caixa_status "
        "ON backups_caixa(status, solicitado_em)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_impressao_outbox_status "
        "ON impressao_outbox(status, criado_em)"
    )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_impressao_original_por_venda "
        "ON impressao_outbox(venda_id) WHERE tipo = 'ORIGINAL'"
    )


def default_migrations() -> dict[int, Migration]:
    return {
        2: _migration_2,
        3: _migration_3,
        4: _migration_4,
        5: _migration_5,
        6: _migration_6,
        7: _migration_7,
        8: _migration_8,
        9: _migration_9,
    }

"""Fachada de aplicação consumida pela interface do PDV.

Ela mantém o usuário autenticado do aplicativo desktop e sempre injeta seu
``actor_id`` nos serviços de domínio, reduzindo o risco de uma tela esquecer
uma verificação de perfil.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from config.settings import Settings, load_settings
from db.database import Database
from integrations.cosmos import CosmosClient
from integrations.open_food_facts import OpenFoodFactsClient

from .audit import AuditService
from .auth import AuthService
from .backup import BackupService
from .cash import CashService
from .errors import AuthenticationError, AuthorizationError, ValidationError
from .pix import PixService
from .products import ProductService
from .production import ProductionPreparationService
from .provisioning import ProvisioningService, ProvisioningStatus
from .sales import SaleService
from .security import get_active_user, require_admin


def _local_default_settings(database: Database) -> Settings:
    root = database.path.parent.resolve() if str(database.path) != ":memory:" else Path.cwd()
    return Settings(
        project_root=root,
        database_path=database.path,
        backup_path=root / "backups",
        establishment_name="TRIGO DE MINAS",
        establishment_document="",
        receipt_header="PDV TRIGO DE MINAS",
        pix_key="",
        pix_receiver_name="TRIGO DE MINAS",
        pix_city="SAO PAULO",
        printer_name="",
        printer_port="9100",
        open_food_facts_timeout=3.0,
        cosmos_api_token="",
        cosmos_user_agent="",
        printer_enabled=False,
        printer_driver="win32raw",
        printer_host="",
        printer_queue_dir=root / "data" / "print_queue",
        printer_uri="",
        cut_paper=True,
        config_path=root / "config.ini",
    )


class PDVService:
    """Ponto de entrada único para a UI, sem depender de SQL ou da rede."""

    def __init__(self, *, settings: Optional[Settings] = None, database: Optional[Database] = None):
        self.settings = settings or (load_settings() if database is None else _local_default_settings(database))
        self.database = database or Database(self.settings.database_path)
        self.database.initialize()
        self.audit = AuditService(self.database)
        self.auth = AuthService(self.database, self.audit)
        self.provisioning = ProvisioningService(self.database)
        self.backup = BackupService(self.database, self.settings.backup_path, audit=self.audit)
        self.production = ProductionPreparationService(self.database, self.backup)
        self.products = ProductService(
            self.database,
            external_client=OpenFoodFactsClient(timeout=self.settings.open_food_facts_timeout),
            cosmos_client=CosmosClient(
                token=self.settings.cosmos_api_token,
                user_agent=self.settings.cosmos_user_agent,
                timeout=self.settings.open_food_facts_timeout,
            ),
            audit=self.audit,
        )
        self.cash = CashService(self.database, audit=self.audit, backup_service=self.backup)
        self.sales = SaleService(self.database, audit=self.audit, auth=self.auth)
        self.pix = PixService(
            pix_key=self.settings.pix_key,
            receiver_name=self.settings.pix_receiver_name,
            city=self.settings.pix_city,
            description=self.settings.establishment_name,
        )
        self._current_user: Optional[dict] = None

    # -- sessão local -------------------------------------------------
    def authenticate(self, login: str, senha: str) -> Optional[dict]:
        user = self.auth.authenticate(login, senha)
        self._current_user = user
        return user

    login = authenticate

    def installation_status(self) -> ProvisioningStatus:
        return self.provisioning.status()

    def generate_recovery_code(self) -> str:
        return self.provisioning.generate_recovery_code()

    def provision_initial_admin(
        self, name: str, login: str, password: str, recovery_code: str
    ) -> dict:
        return self.provisioning.provision_initial_admin(name, login, password, recovery_code)

    def password_recovery_available(self, login: str) -> bool:
        return self.auth.password_recovery_available(login)

    def recover_password_with_code(
        self, login: str, recovery_code: str, new_password: str, new_recovery_code: str
    ) -> dict:
        return self.auth.recover_password_with_code(login, recovery_code, new_password, new_recovery_code)

    def prepare_own_recovery_code(self) -> str:
        """Gera um codigo novo somente para a sessao administrativa ativa."""

        self._admin_actor()
        return self.provisioning.generate_recovery_code()

    def configure_own_recovery_code(
        self, current_password: str, recovery_code: str
    ) -> dict:
        """Configura ou rotaciona a recuperacao sem aceitar id de terceiro."""

        actor_id = self._admin_actor()
        updated = self.auth.configure_own_recovery_code(
            current_password,
            recovery_code,
            actor_id=actor_id,
        )
        self._current_user = updated
        return updated

    def logout(self) -> None:
        self._current_user = None

    def shutdown(self) -> None:
        """Encerra workers cooperativamente antes de fechar o processo."""

        self.cash.shutdown(timeout=2.0)

    def current_user(self) -> Optional[dict]:
        return dict(self._current_user) if self._current_user else None

    def _session_user_id(self) -> int:
        if not self._current_user:
            raise AuthenticationError("Faça login para continuar.")
        return int(self._current_user["id"])

    def active_user(self) -> dict:
        """Revalida no banco o ator da sessão antes de qualquer operação."""

        actor_id = self._session_user_id()
        with self.database.transaction() as connection:
            return get_active_user(connection, actor_id)

    def _actor_id(self) -> int:
        return int(self.active_user()["id"])

    def _session_actor(self, supplied_actor_id: Any) -> int:
        """Vincula parâmetros da UI à sessão local, impedindo personificação."""

        actor_id = self._actor_id()
        try:
            supplied = int(supplied_actor_id)
        except (TypeError, ValueError) as exc:
            raise AuthorizationError("Operador inválido para a sessão atual.") from exc
        if supplied != actor_id:
            raise AuthorizationError("O operador informado não corresponde à sessão atual.")
        return actor_id

    def _admin_actor(self) -> int:
        actor_id = self._actor_id()
        with self.database.transaction() as connection:
            require_admin(connection, actor_id)
        return actor_id

    def change_password(self, user_id: int, current_password: str, new_password: str) -> None:
        actor_id = self._session_user_id()
        self._current_user = self.auth.change_password(
            user_id,
            current_password,
            new_password,
            actor_id=actor_id,
        )

    # -- usuários ------------------------------------------------------
    def admin_users(self) -> list[dict]:
        """Lista contas sem nunca expor hashes de senha."""

        return self.auth.list_users(actor_id=self._admin_actor())

    def create_user_admin(
        self,
        nome: str,
        login: str,
        senha: str,
        perfil: str,
        actor_id: Optional[int] = None,
    ) -> dict:
        """Cria uma conta somente pela sessão administrativa vigente."""

        actor = self._actor_id() if actor_id is None else self._session_actor(actor_id)
        self._admin_actor()
        return self.auth.create_user(nome, login, senha, perfil, actor_id=actor)

    def reset_user_password_admin(
        self, user_id: int, temporary_password: str, actor_id: Optional[int] = None
    ) -> dict:
        """Permite que outro administrador restaure o acesso de uma conta."""

        actor = self._actor_id() if actor_id is None else self._session_actor(actor_id)
        self._admin_actor()
        return self.auth.reset_user_password(user_id, temporary_password, actor_id=actor)

    # -- produto / GTIN ------------------------------------------------
    def find_by_gtin(self, gtin: str) -> Optional[dict]:
        self._actor_id()
        return self.products.find_by_gtin(gtin)

    def resolve_gtin(self, gtin: str, actor_id: Optional[int] = None) -> dict:
        actor = self._actor_id() if actor_id is None else self._session_actor(actor_id)
        return self.products.lookup_external(gtin, actor_id=actor)

    lookup_external = resolve_gtin

    def scan_product(self, gtin: str, actor_id: int) -> dict:
        return self.resolve_gtin(gtin, actor_id=actor_id)

    def search_products(self, query: str = "", *, include_inactive: bool = False, limit: int = 100) -> list[dict]:
        self._actor_id()
        return self.products.search(query, include_inactive=include_inactive, limit=limit)

    search = search_products

    def list_counter_products(self) -> list[dict]:
        self._actor_id()
        return self.products.list_counter_products()

    get_counter_products = list_counter_products

    def expiring_products(self, *, days: int = 7) -> list[dict]:
        self._actor_id()
        return self.products.expiring_products(days=days)

    def create_product(self, product: Optional[dict] = None, actor_id: Optional[int] = None, **payload: Any) -> dict:
        """Aceita tanto payload UI posicional quanto argumentos nomeados da fachada."""

        actor = self._actor_id() if actor_id is None else self._session_actor(actor_id)
        values: dict[str, Any] = {}
        if product is not None:
            if not isinstance(product, dict):
                raise ValidationError("Dados do produto inválidos.")
            values.update(product)
        values.update(payload)
        values.pop("actor_id", None)
        return self.products.create_product(actor_id=actor, **values)

    def update_product(self, gtin: str, **changes: Any) -> dict:
        return self.products.update_product(gtin, actor_id=self._actor_id(), **changes)

    def set_price(self, gtin: str, preco: Any) -> dict:
        return self.products.set_price(gtin, preco, actor_id=self._actor_id())

    def save_price(self, gtin: str, price: Any, actor_id: int) -> dict:
        return self.products.set_price(gtin, price, actor_id=self._session_actor(actor_id))

    def adjust_stock(self, gtin: str, delta: Any, observacao: str = "") -> dict:
        return self.products.adjust_stock(gtin, delta, actor_id=self._actor_id(), observacao=observacao)

    def delete_product(self, gtin: str) -> None:
        self.products.delete_product(gtin, actor_id=self._actor_id())

    def _admin_approval(self, operator_id: int, admin_login: str, admin_senha: str) -> dict:
        actor_id = self._actor_id()
        if int(operator_id) != actor_id:
            raise AuthorizationError("O operador informado não corresponde à sessão atual.")
        admin = self.auth.verify_admin_credentials(admin_login, admin_senha, requested_by=actor_id)
        if admin is None:
            raise AuthorizationError("Credencial de administrador inválida.")
        return admin

    def admin_approve_and_price(
        self, operator_id: int, gtin: str, price: Any, admin_login: str, admin_password: str
    ) -> dict:
        admin = self._admin_approval(operator_id, admin_login, admin_password)
        return self.products.set_price(gtin, price, actor_id=admin["id"])

    def admin_approve_and_create(
        self, operator_id: int, payload: dict, admin_login: str, admin_password: str
    ) -> dict:
        if not isinstance(payload, dict):
            raise ValidationError("Dados do produto inválidos.")
        admin = self._admin_approval(operator_id, admin_login, admin_password)
        clean_payload = dict(payload)
        clean_payload.pop("actor_id", None)
        return self.products.create_product(actor_id=admin["id"], **clean_payload)

    # -- caixa ---------------------------------------------------------
    def get_open_cash(self, user_id: Optional[int] = None) -> Optional[dict]:
        actor_id = self._actor_id() if user_id is None else self._session_actor(user_id)
        return self.cash.get_open_cash(actor_id, actor_id=actor_id)

    def get_global_open_cash(self) -> Optional[dict]:
        return self.cash.get_global_open_cash(actor_id=self._actor_id())

    def resume_open_cash(self, cash_id: int, reason: str) -> dict:
        return self.cash.resume_open_cash(
            cash_id, actor_id=self._admin_actor(), reason=reason
        )

    def open_cash(self, user_id: int, opening_float: Any = None) -> dict:
        # A assinatura de duas posições é a usada pela UI. A forma de uma só
        # posição continua útil em scripts autenticados.
        if opening_float is None:
            actor_id = self._actor_id()
            opening_float = user_id
        else:
            actor_id = self._session_actor(user_id)
        return self.cash.open_cash(actor_id, opening_float, actor_id=actor_id)

    def add_movement(self, caixa_id: int, tipo: str, valor: Any, observacao: str = "") -> dict:
        return self.cash.add_movement(caixa_id, tipo, valor, observacao, actor_id=self._actor_id())

    def record_cash_movement(
        self,
        cash_id: int,
        operator_id: int,
        movement_type: str,
        amount: Any,
        observation: str = "",
        *,
        chave_idempotencia: Optional[str] = None,
    ) -> dict:
        return self.cash.add_movement(
            cash_id,
            movement_type,
            amount,
            observation,
            actor_id=self._session_actor(operator_id),
            chave_idempotencia=chave_idempotencia,
        )

    def close_cash(self, cash_id: int, operator_id: int, counted_cash: Any, justification: str = "") -> dict:
        return self.cash.close_cash(
            cash_id,
            counted_cash,
            justification,
            actor_id=self._session_actor(operator_id),
            reveal_expected=self.settings.show_expected_to_operator,
        )

    def cash_summary(self, caixa_id: int, *, reveal_expected: bool = False) -> dict:
        actor = self._actor_id()
        with self.database.transaction() as connection:
            current = connection.execute("SELECT perfil FROM usuarios WHERE id = ? AND ativo = 1", (actor,)).fetchone()
        allowed = bool(current and (current["perfil"] == "admin" or self.settings.show_expected_to_operator))
        return self.cash.get_cash_summary(caixa_id, actor_id=actor, reveal_expected=bool(reveal_expected and allowed))

    # -- venda / pagamento --------------------------------------------
    def quote_sale(self, operator_id: int, items: list[dict]) -> dict:
        actor = self._session_actor(operator_id)
        return self.sales.quote(items, actor).to_payload()

    def finalize_sale(
        self,
        caixa_id: int,
        operador_id: int,
        itens: list[dict],
        forma_pagamento: str,
        valor_recebido: Any = None,
        *,
        chave_idempotencia: Optional[str] = None,
        manual_authorization: Optional[dict] = None,
        receipt_context: Optional[dict] = None,
    ) -> dict:
        actor = self._session_actor(operador_id)
        authorizer_id: Optional[int] = None
        exception_reason = ""
        if manual_authorization is not None:
            if not isinstance(manual_authorization, dict):
                raise ValidationError("Os dados da autorização são inválidos.")
            exception_reason = str(
                manual_authorization.get(
                    "reason",
                    manual_authorization.get(
                        "motivo", manual_authorization.get("justificativa", "")
                    ),
                )
                or ""
            )
            login = str(manual_authorization.get("login") or "").strip()
            password = manual_authorization.get(
                "password", manual_authorization.get("senha")
            )
            current = self.active_user()
            if current.get("perfil") == "admin" and not login and password in (None, ""):
                authorizer_id = actor
            else:
                approved = self._admin_approval(actor, login, str(password or ""))
                authorizer_id = int(approved["id"])
        return self.sales.finalize(
            caixa_id,
            itens,
            forma_pagamento,
            0 if valor_recebido is None else valor_recebido,
            operador_id=actor,
            chave_idempotencia=chave_idempotencia,
            exception_authorizer_id=authorizer_id,
            exception_reason=exception_reason,
            receipt_context=receipt_context,
        )

    finalize = finalize_sale

    def queue_receipt_copy(
        self, sale_id: int, idempotency_key: str, receipt_context: dict
    ) -> dict:
        return self.sales.queue_receipt_copy(
            sale_id,
            actor_id=self._actor_id(),
            idempotency_key=idempotency_key,
            receipt_context=receipt_context,
        )

    def authorize_item_cancellation(self, item: Any, *, admin_login: str, admin_senha: str) -> dict:
        return self.sales.authorize_item_cancellation(
            item,
            operador_id=self._actor_id(),
            admin_login=admin_login,
            admin_senha=admin_senha,
        )

    cancel_pending_item = authorize_item_cancellation

    def cancel_sale_admin(
        self,
        sale_id: int,
        reason: str,
        idempotency_key: str,
        *,
        admin_login: str,
        admin_password: str,
    ) -> dict:
        actor = self._actor_id()
        admin = self._admin_approval(actor, admin_login, admin_password)
        return self.sales.cancel_sale(
            sale_id,
            operator_id=actor,
            authorizer_id=int(admin["id"]),
            reason=reason,
            idempotency_key=idempotency_key,
        )

    def authorize_item_cancel(
        self,
        operator_id: int,
        item: Any,
        *,
        admin_login: Optional[str] = None,
        admin_password: Optional[str] = None,
        admin_user_id: Optional[int] = None,
    ) -> bool:
        actor = self._session_actor(operator_id)
        if admin_user_id is not None:
            # A logged-in cashier must not be able to nominate the numeric id
            # of an administrator and bypass the credential prompt.  The
            # shortcut used by an admin passes their own active id only.
            try:
                approved_by = int(admin_user_id)
            except (TypeError, ValueError) as exc:
                raise AuthorizationError("Autorizador inválido.") from exc
            if approved_by != actor:
                raise AuthorizationError("O administrador autorizador não corresponde à sessão atual.")
            self._admin_actor()
        result = self.sales.authorize_item_cancellation(
            item,
            operador_id=actor,
            admin_login=admin_login,
            admin_senha=admin_password,
            admin_user_id=admin_user_id,
        )
        return bool(result.get("authorized"))

    def pix_charge(self, total: Any, *, txid: str = "***") -> dict:
        self._actor_id()
        return self.pix.create_charge(total, txid=txid)

    def pix_qr_code(self, total: Any, *, txid: str = "***") -> bytes:
        self._actor_id()
        return self.pix.qr_code(total, txid=txid)

    def get_pix_payload(self, total: Any) -> str:
        return str(self.pix_charge(total)["payload"])

    # -- admin / relatórios / manutenção ------------------------------
    def dashboard_summary(self) -> dict:
        return self.sales.dashboard_summary(actor_id=self._actor_id())

    def sales_report(self, data_inicial: Any, data_final: Any) -> list[dict]:
        return self.sales.sales_report(data_inicial, data_final, actor_id=self._actor_id())

    def payment_totals_by_period(self, data_inicial: Any, data_final: Any) -> list[dict]:
        return self.sales.payment_totals_by_period(data_inicial, data_final, actor_id=self._actor_id())

    def top_products_by_period(self, data_inicial: Any, data_final: Any, *, limit: int = 15) -> list[dict]:
        return self.sales.top_products_by_period(data_inicial, data_final, actor_id=self._actor_id(), limit=limit)

    def list_audit_log(self, *, limit: int = 200, entity: Optional[str] = None, entity_id: Optional[str] = None) -> list[dict]:
        return self.audit.list_events(self._actor_id(), limit=limit, entity=entity, entity_id=entity_id)

    def cash_history(self, *, limit: int = 100) -> list[dict]:
        return self.cash.list_cash_history(actor_id=self._actor_id(), limit=limit)

    def create_backup(self) -> str:
        return str(self.backup.create_backup(actor_id=self._actor_id()))

    def vacuum(self) -> dict:
        return self.backup.vacuum(actor_id=self._actor_id())

    def reindex(self) -> dict:
        return self.backup.reindex(actor_id=self._actor_id())

    def integrity_check(self) -> str:
        return self.backup.integrity_check(actor_id=self._actor_id())

    def prepare_for_production(self, confirmation: str, actor_id: int) -> dict:
        actor = self._session_actor(actor_id)
        self._admin_actor()
        return self.production.prepare(actor_id=actor, confirmation=confirmation)

    def production_preparation_status(self) -> dict:
        actor = self._admin_actor()
        return self.production.status(actor_id=actor)

    # Métodos abaixo espelham o contrato do painel administrativo Tkinter.
    def admin_dashboard(self) -> dict:
        return self.sales.dashboard_summary(actor_id=self._admin_actor())

    def admin_products(self, query: str = "") -> list[dict]:
        self._admin_actor()
        return self.products.search(query, include_inactive=True, limit=500)

    def save_product_admin(self, product: dict, actor_id: int) -> dict:
        actor = self._session_actor(actor_id)
        self._admin_actor()
        if not isinstance(product, dict):
            raise ValidationError("Dados do produto inválidos.")
        payload = dict(product)
        gtin = payload.pop("gtin", None)
        if not gtin:
            raise ValidationError("Informe o GTIN do produto.")
        try:
            self.products.get_product(gtin, include_inactive=True)
        except Exception as exc:
            # Apenas a ausência permite criação; outros erros devem aparecer.
            from .errors import NotFoundError
            if not isinstance(exc, NotFoundError):
                raise
            return self.products.create_product(gtin=gtin, actor_id=actor, **payload)
        return self.products.update_product(gtin, actor_id=actor, **payload)

    def admin_cash_closures(self) -> list[dict]:
        return self.cash.list_cash_history(actor_id=self._admin_actor(), limit=200)

    def admin_financial_report(self, start_date: Any, end_date: Any) -> dict:
        actor = self._admin_actor()
        sales = self.sales.sales_report(start_date, end_date, actor_id=actor)
        return {
            "quantidade_vendas": len(sales),
            "total_vendido": round(sum(float(item["total"]) for item in sales), 2),
            "por_forma_pagamento": self.sales.payment_totals_by_period(start_date, end_date, actor_id=actor),
            "top_produtos": self.sales.top_products_by_period(start_date, end_date, actor_id=actor),
        }

    def admin_audit_logs(self, limit: int = 200) -> list[dict]:
        return self.audit.list_events(self._admin_actor(), limit=limit)

    def run_maintenance(self, operation: str, actor_id: int) -> dict:
        actor = self._session_actor(actor_id)
        command = str(operation or "").strip().upper()
        if command == "VACUUM":
            return self.backup.vacuum(actor_id=actor)
        if command == "REINDEX":
            return self.backup.reindex(actor_id=actor)
        raise ValidationError("Operação de manutenção inválida.")


def build_pdv_service(config_path: str | Path | None = None) -> PDVService:
    """Constrói a fachada padrão usada pelo inicializador desktop."""

    settings = load_settings(config_path) if config_path is not None else load_settings()
    return PDVService(settings=settings)

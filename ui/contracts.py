"""Boundary between the Tk interface and the PDV business layer.

Views deliberately know no SQLite details.  The application bootstrap supplies a
``controller`` which follows :class:`PdvController`.  Keeping this contract here
makes the desktop interface easy to test and lets the service layer evolve
without importing Tkinter.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol, TypedDict, runtime_checkable


class ProductData(TypedDict, total=False):
    gtin: str
    nome: str
    marca: str
    preco: float
    estoque: float
    data_validade: str | None
    unidade: str
    categoria: str
    subcategoria: str
    detalhes_embalagem: str
    item_balcao: bool
    estoque_controlado: bool


class UserData(TypedDict, total=False):
    id: int
    nome: str
    login: str
    perfil: str
    ativo: bool
    deve_trocar_senha: bool
    recovery_configured: bool


class CashData(TypedDict, total=False):
    id: int
    usuario_id: int
    fundo_inicial: float
    status: str
    data_abertura: str


class CartPayload(TypedDict, total=False):
    gtin: str | None
    quantidade: float | str
    preco_unitario: float | str
    nome: str
    descricao: str
    unidade: str
    tipo_lancamento: str
    codigo_informado: str | None


class ManualAuthorizationPayload(TypedDict, total=False):
    login: str
    password: str
    reason: str


@runtime_checkable
class PdvController(Protocol):
    """Methods called by the desktop UI.

    All methods return dictionaries/lists of dictionaries and may raise a
    domain exception with a useful human-readable message.  Numeric amounts are
    sent as ``float`` in reais.  This is intentionally a UI-facing adapter, not
    a requirement for individual services.
    """

    def authenticate(self, login: str, password: str) -> UserData: ...

    def installation_status(self) -> Mapping[str, Any] | object: ...

    def generate_recovery_code(self) -> str: ...

    def provision_initial_admin(
        self, name: str, login: str, password: str, recovery_code: str
    ) -> UserData: ...

    def password_recovery_available(self, login: str) -> bool: ...

    def recover_password_with_code(
        self, login: str, recovery_code: str, new_password: str, new_recovery_code: str
    ) -> UserData: ...

    def prepare_own_recovery_code(self) -> str: ...

    def configure_own_recovery_code(
        self, current_password: str, recovery_code: str
    ) -> UserData: ...

    def get_open_cash(self, user_id: int) -> CashData | None: ...

    def open_cash(self, user_id: int, opening_float: float) -> CashData: ...

    def scan_product(self, gtin: str, actor_id: int) -> Mapping[str, Any]: ...

    def save_price(self, gtin: str, price: float, actor_id: int) -> ProductData: ...

    def create_product(self, product: ProductData, actor_id: int) -> ProductData: ...

    def search_products(self, query: str) -> Sequence[ProductData]: ...

    def get_counter_products(self) -> Sequence[ProductData]: ...

    def finalize_sale(
        self,
        cash_id: int,
        operator_id: int,
        items: Sequence[CartPayload],
        payment_method: str,
        amount_received: float | None = None,
        *,
        chave_idempotencia: str | None = None,
        manual_authorization: ManualAuthorizationPayload | None = None,
    ) -> Mapping[str, Any]: ...

    def quote_sale(
        self, operator_id: int, items: Sequence[CartPayload]
    ) -> Mapping[str, Any]: ...

    def get_pix_payload(self, total: float) -> str: ...

    def authorize_item_cancel(
        self,
        operator_id: int,
        item: CartPayload,
        *,
        admin_login: str | None = None,
        admin_password: str | None = None,
        admin_user_id: int | None = None,
    ) -> bool: ...

    def change_password(self, user_id: int, current_password: str, new_password: str) -> Mapping[str, Any] | None: ...

    def admin_users(self) -> Sequence[UserData]: ...

    def create_user_admin(self, nome: str, login: str, senha: str, perfil: str, actor_id: int) -> UserData: ...

    def admin_approve_and_price(
        self,
        operator_id: int,
        gtin: str,
        price: float,
        admin_login: str,
        admin_password: str,
    ) -> ProductData: ...

    def admin_approve_and_create(
        self,
        operator_id: int,
        product: ProductData,
        admin_login: str,
        admin_password: str,
    ) -> ProductData: ...

    def record_cash_movement(
        self,
        cash_id: int,
        operator_id: int,
        movement_type: str,
        amount: float,
        observation: str,
        *,
        chave_idempotencia: str | None = None,
    ) -> Mapping[str, Any] | None: ...

    def close_cash(
        self,
        cash_id: int,
        operator_id: int,
        counted_cash: float,
        justification: str,
    ) -> Mapping[str, Any]: ...

    # Administrative operations. The view never offers them to a cashier.
    def admin_dashboard(self) -> Mapping[str, Any]: ...

    def admin_products(self, query: str = "") -> Sequence[ProductData]: ...

    def save_product_admin(self, product: ProductData, actor_id: int) -> ProductData: ...

    def admin_cash_closures(self) -> Sequence[Mapping[str, Any]]: ...

    def admin_financial_report(self, start_date: str, end_date: str) -> Mapping[str, Any]: ...

    def admin_audit_logs(self, limit: int = 200) -> Sequence[Mapping[str, Any]]: ...

    def run_maintenance(self, operation: str, actor_id: int) -> Mapping[str, Any] | None: ...


class ControllerError(RuntimeError):
    """Shown when the desktop bootstrap did not expose a required UI operation."""


def invoke(controller: object, method: str, *args: Any, **kwargs: Any) -> Any:
    """Call one controller operation and produce an actionable integration error.

    UI callbacks should catch ``Exception`` around this call and present its
    message to the operator.  A missing method is a deployment/configuration
    problem, not an obscure ``AttributeError`` at the checkout counter.
    """

    target: Callable[..., Any] | None = getattr(controller, method, None)
    if not callable(target):
        raise ControllerError(
            f"A operação '{method}' não foi configurada no controlador do PDV. "
            "Verifique a integração dos serviços."
        )
    return target(*args, **kwargs)


def field(data: Mapping[str, Any] | object, name: str, default: Any = None) -> Any:
    """Read a field from either backend dictionaries or lightweight objects."""

    if isinstance(data, Mapping):
        return data.get(name, default)
    return getattr(data, name, default)

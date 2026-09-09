"""Login, checkout and administration screens for PDV Trigo de Minas."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP, localcontext
import json
from queue import Empty, Queue
from threading import Thread
from tkinter import ttk
from typing import Any
from uuid import uuid4

from services.money import decimal_value

from .contracts import CartPayload, ProductData, UserData, field, invoke
from .scan_queue import ScanQueue, ScanTicket
from .dialogs import (
    AdminAuthorizationDialog,
    CashActionsDialog,
    CashCloseDialog,
    CashMovementDialog,
    CashOpeningDialog,
    CashResumeDialog,
    ConfirmDialog,
    ManualProductDialog,
    PaymentDialog,
    PricingDialog,
    ProductionPreparationDialog,
    ProductEditorDialog,
    PasswordRecoveryDialog,
    RecoveryCodeSetupDialog,
    SearchDialog,
    UserCreateDialog,
    UserPasswordResetDialog,
    WeightDialog,
)
from .dialogs_checkout import (
    CartItemEditDialog,
    ManualSaleItemDialog,
    SaleAuthorizationDialog,
)
from .theme import Button, Card, Colors, SectionLabel, font, money


@dataclass
class CartLine:
    key: int
    gtin: str | None
    name: str
    price: Decimal | str | float
    quantity: Decimal | str | float = Decimal("1")
    unit: str = "UN"
    line_kind: str = "CATALOGO"
    entered_code: str | None = None
    original_price: Decimal | str | float | None = None

    def __post_init__(self) -> None:
        self.gtin = str(self.gtin).strip() if self.gtin not in (None, "") else None
        self.name = " ".join(str(self.name or "").split())
        self.unit = str(self.unit or "UN").strip().upper()
        self.line_kind = str(self.line_kind or "CATALOGO").strip().upper()
        self.price = decimal_value(self.price, "preço").quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        self.quantity = decimal_value(self.quantity, "quantidade").quantize(
            Decimal("0.001"), rounding=ROUND_HALF_UP
        )
        if self.original_price is not None:
            self.original_price = decimal_value(
                self.original_price, "preço original"
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        elif self.line_kind == "CATALOGO":
            self.original_price = self.price
        if self.entered_code is None:
            self.entered_code = self.gtin

    @property
    def subtotal(self) -> Decimal:
        with localcontext() as context:
            context.prec = max(
                28,
                len(self.price.as_tuple().digits)
                + len(self.quantity.as_tuple().digits)
                + 8,
            )
            return (self.price * self.quantity).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

    def as_payload(self) -> CartPayload:
        quantity_text = (
            f"{self.quantity:.0f}"
            if self.unit == "UN"
            else f"{self.quantity:.3f}"
        )
        payload: CartPayload = {
            "tipo_lancamento": self.line_kind,
            "gtin": self.gtin,
            "codigo_informado": self.entered_code,
            "quantidade": quantity_text,
            "preco_unitario": f"{self.price:.2f}",
            "nome": self.name,
            "unidade": self.unit,
        }
        if self.line_kind == "MANUAL":
            payload["descricao"] = self.name
        return payload


class NoticeBar(tk.Frame):
    """Inline feedback avoids blocking native message boxes during checkout."""

    PALETTES = {
        "success": (Colors.SUCCESS_SOFT, Colors.SUCCESS),
        "danger": (Colors.DANGER_SOFT, Colors.DANGER),
        "info": (Colors.INFO_SOFT, Colors.INFO),
        "warning": (Colors.WARNING_SOFT, Colors.WARNING),
    }

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, bg=Colors.INFO_SOFT, padx=11, pady=8)
        self.message = tk.StringVar()
        self.label = tk.Label(
            self,
            textvariable=self.message,
            bg=Colors.INFO_SOFT,
            fg=Colors.INFO,
            font=font(9),
            anchor="w",
            justify="left",
            wraplength=900,
        )
        self.label.pack(fill="x")
        self._after_id: str | None = None

    def show(self, text: str, kind: str = "info", *, ttl: int | None = 5500) -> None:
        background, foreground = self.PALETTES.get(kind, self.PALETTES["info"])
        self.configure(bg=background)
        self.label.configure(bg=background, fg=foreground)
        self.message.set(text)
        if not self.winfo_manager():
            self.pack(fill="x", pady=(0, 10))
        if self._after_id:
            self.after_cancel(self._after_id)
            self._after_id = None
        if ttl:
            self._after_id = self.after(ttl, self.hide)

    def hide(self) -> None:
        if self.winfo_exists() and self.winfo_manager():
            self.pack_forget()
        self._after_id = None

    def destroy(self) -> None:
        if self._after_id:
            try:
                self.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        super().destroy()


class LoginView(tk.Frame):
    """Minimal full-screen login that keeps password entry and errors local."""

    def __init__(self, master: tk.Misc, controller: object, on_authenticated: Callable[[UserData], None]) -> None:
        super().__init__(master, bg=Colors.CREAM)
        self.controller = controller
        self.on_authenticated = on_authenticated
        self.login_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self._recovery_login: str | None = None
        self._login_trace_id = self.login_var.trace_add("write", self._login_changed)
        self._focus_after_id: str | None = None
        self._build()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        content = tk.Frame(self, bg=Colors.CREAM)
        content.grid(row=0, column=0)
        brand = tk.Frame(content, bg=Colors.FOREST, padx=34, pady=34)
        brand.grid(row=0, column=0, sticky="nsew")
        tk.Label(brand, text="TRIGO", bg=Colors.FOREST, fg="#FFFFFF", font=font(29, "bold"), anchor="w").pack(anchor="w")
        tk.Label(brand, text="DE MINAS", bg=Colors.FOREST, fg="#F3C55D", font=font(14, "bold"), anchor="w").pack(anchor="w")
        tk.Frame(brand, height=2, bg=Colors.GOLD).pack(fill="x", pady=(25, 17))
        tk.Label(brand, text="Ponto de Venda", bg=Colors.FOREST, fg="#D9E3DD", font=font(11), anchor="w").pack(anchor="w")
        tk.Label(brand, text="Operação simples, rápida e segura.", bg=Colors.FOREST, fg="#B5C6BC", font=font(9), anchor="w").pack(anchor="w", pady=(4, 0))

        login_card = tk.Frame(content, bg=Colors.SURFACE, padx=36, pady=34, highlightbackground=Colors.LINE, highlightthickness=1)
        login_card.grid(row=0, column=1, sticky="nsew")
        tk.Label(login_card, text="Acessar o PDV", bg=Colors.SURFACE, fg=Colors.INK, font=font(21, "bold"), anchor="w").pack(fill="x")
        tk.Label(login_card, text="Use seu usuário e senha para iniciar o turno.", bg=Colors.SURFACE,
                 fg=Colors.INK_MUTED, font=font(10), anchor="w").pack(fill="x", pady=(5, 23))
        SectionLabel(login_card, "USUÁRIO").pack(fill="x", pady=(0, 5))
        self.login_entry = ttk.Entry(login_card, textvariable=self.login_var, width=31)
        self.login_entry.pack(fill="x")
        SectionLabel(login_card, "SENHA").pack(fill="x", pady=(16, 5))
        self.password_entry = ttk.Entry(login_card, textvariable=self.password_var, show="●")
        self.password_entry.pack(fill="x")
        self.error_var = tk.StringVar()
        self.error = tk.Label(login_card, textvariable=self.error_var, bg=Colors.DANGER_SOFT, fg=Colors.DANGER,
                              font=font(9), justify="left", anchor="w", padx=10, pady=8, wraplength=300)
        Button(login_card, "Entrar", self.login, variant="accent").pack(fill="x", pady=(24, 0))
        self.enter_hint = tk.Label(login_card, text="Enter para acessar", bg=Colors.SURFACE, fg=Colors.INK_MUTED, font=font(9), anchor="center")
        self.enter_hint.pack(fill="x", pady=(12, 0))
        self.recovery_button = Button(login_card, "Esqueci minha senha", self.open_password_recovery, variant="ghost")
        self.login_entry.bind("<Return>", lambda _event: self.password_entry.focus_set())
        self.password_entry.bind("<Return>", lambda _event: self.login())
        self._focus_after_id = self.after(60, self._focus_login_entry)

    def _focus_login_entry(self) -> None:
        self._focus_after_id = None
        if self.winfo_exists():
            self.login_entry.focus_set()

    def destroy(self) -> None:
        if self._login_trace_id:
            try:
                self.login_var.trace_remove("write", self._login_trace_id)
            except tk.TclError:
                pass
            self._login_trace_id = ""
        if self._focus_after_id is not None:
            try:
                self.after_cancel(self._focus_after_id)
            except tk.TclError:
                pass
            self._focus_after_id = None
        super().destroy()

    def _login_changed(self, *_args: object) -> None:
        current = self.login_var.get().strip().casefold()
        expected = (self._recovery_login or "").casefold()
        if current != expected and self.recovery_button.winfo_manager():
            self.recovery_button.pack_forget()
        if current != expected:
            self._recovery_login = None

    def login(self) -> None:
        self.error_var.set("")
        if self.error.winfo_manager():
            self.error.pack_forget()
        login = self.login_var.get().strip()
        password = self.password_var.get()
        if not login or not password:
            self._show_error("Informe seu usuário e senha.")
            (self.login_entry if not login else self.password_entry).focus_set()
            return
        try:
            user = invoke(self.controller, "authenticate", login, password)
            if not user:
                raise ValueError("Usuário ou senha não conferem.")
        except Exception as exc:
            self.password_var.set("")
            self._show_error(exc)
            try:
                available = bool(invoke(self.controller, "password_recovery_available", login))
            except Exception:
                available = False
            if available:
                self._recovery_login = login
                if not self.recovery_button.winfo_manager():
                    self.recovery_button.pack(fill="x", pady=(10, 0), before=self.enter_hint)
            else:
                self._recovery_login = None
                if self.recovery_button.winfo_manager():
                    self.recovery_button.pack_forget()
            self.password_entry.focus_set()
            return
        self.on_authenticated(user)

    def open_password_recovery(self) -> None:
        login = self.login_var.get().strip()
        try:
            available = bool(
                login and invoke(self.controller, "password_recovery_available", login)
            )
        except Exception:
            available = False
        if not available:
            self._recovery_login = None
            if self.recovery_button.winfo_manager():
                self.recovery_button.pack_forget()
            self._show_error("A recuperação de acesso não está disponível para este usuário.")
            return

        def submit(user_login: str, recovery_code: str, new_password: str, new_recovery_code: str) -> bool:
            invoke(self.controller, "recover_password_with_code", user_login, recovery_code, new_password, new_recovery_code)
            self.password_var.set("")
            self._show_error("Acesso redefinido. Entre com a nova senha.")
            self.recovery_button.pack_forget()
            self._recovery_login = None
            self.password_entry.focus_set()
            return True

        PasswordRecoveryDialog(self, login, submit)

    def _show_error(self, message: object) -> None:
        self.error_var.set(str(message))
        if not self.error.winfo_manager():
            self.error.pack(fill="x", pady=(15, 0))


class SaleView(tk.Frame):
    """High-throughput checkout with GTIN focus and keyboard shortcuts."""

    def __init__(
        self,
        master: tk.Misc,
        controller: object,
        user: Mapping[str, Any],
        cash: Mapping[str, Any] | None,
        *,
        on_logout: Callable[[], None],
        on_open_admin: Callable[[], None] | None = None,
        on_cash_closed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master, bg=Colors.CREAM)
        self.controller = controller
        self.user = user
        self.cash: Mapping[str, Any] | None = cash
        self.on_logout = on_logout
        self.on_open_admin = on_open_admin
        self.on_cash_closed = on_cash_closed
        self.gtin_var = tk.StringVar()
        self.total_var = tk.StringVar(value=money(0))
        self.items_var = tk.StringVar(value="0 itens")
        self.cash_status_var = tk.StringVar()
        self.cash_balance_var = tk.StringVar()
        self.cart: list[CartLine] = []
        self._line_sequence = 0
        self._last_sale_id: int | None = None
        self._last_sale_cancelled = False
        self._busy = False
        self._scan_results: Queue[tuple[ScanTicket, Mapping[str, Any] | Any | None, Exception | None]] = Queue()
        self._scan_queue = ScanQueue(max_pending=100)
        self._scan_inflight: ScanTicket | None = None
        self._scan_start_after_id: str | None = None
        self._poll_after_id: str | None = None
        self._focus_after_id: str | None = None
        self._build()
        self.refresh_cash_status()
        self.load_counter_products()
        self._poll_after_id = self.after(50, self._poll_scan_results)
        self._focus_after_id = self.after(80, self.focus_scan)

    def destroy(self) -> None:
        """Cancel recurring UI work before Tk disposes the view command."""

        self._scan_queue.advance_generation()
        for after_id in (self._poll_after_id, self._focus_after_id, self._scan_start_after_id):
            if after_id:
                try:
                    self.after_cancel(after_id)
                except tk.TclError:
                    pass
        self._poll_after_id = None
        self._focus_after_id = None
        self._scan_start_after_id = None
        super().destroy()

    @property
    def is_admin(self) -> bool:
        return str(field(self.user, "perfil", "")).lower() == "admin"

    def _build(self) -> None:
        self.rowconfigure(2, weight=1)
        self.columnconfigure(0, weight=1)
        self._build_header()
        self._build_scan_area()
        self._build_content()
        self._build_shortcuts()

    def _build_header(self) -> None:
        header = tk.Frame(self, bg=Colors.INK, padx=20, pady=12)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        brand = tk.Frame(header, bg=Colors.INK)
        brand.grid(row=0, column=0, sticky="w")
        tk.Label(brand, text="TRIGO", bg=Colors.INK, fg="#FFFFFF", font=font(18, "bold")).pack(side="left")
        tk.Label(brand, text=" DE MINAS", bg=Colors.INK, fg="#EDC04F", font=font(10, "bold")).pack(side="left", padx=(3, 0), pady=(5, 0))
        user_name = str(field(self.user, "nome", field(self.user, "login", "Operador")))
        tk.Label(header, text=f"Operador: {user_name}", bg=Colors.INK, fg="#D8E2DC", font=font(9)).grid(row=0, column=1, sticky="e", padx=(15, 16))
        self.cash_button = Button(header, "Caixa", self.open_cash_actions, variant="ghost")
        self.cash_button.grid(row=0, column=2, padx=(0, 8))
        if self.is_admin and self.on_open_admin:
            Button(header, "Administração", self.on_open_admin, variant="ghost").grid(row=0, column=3, padx=(0, 8))
        Button(header, "Sair", self.on_logout, variant="ghost").grid(row=0, column=4)

    def _build_scan_area(self) -> None:
        scan = tk.Frame(self, bg=Colors.FOREST, padx=20, pady=15)
        scan.grid(row=1, column=0, sticky="ew")
        scan.columnconfigure(1, weight=1)
        tk.Label(scan, text="BIPAR / GTIN", bg=Colors.FOREST, fg="#D5E1D9", font=font(9, "bold")).grid(row=0, column=0, sticky="w", padx=(0, 13))
        self.scan_entry = ttk.Entry(scan, textvariable=self.gtin_var, font=font(15, "bold"))
        self.scan_entry.grid(row=0, column=1, sticky="ew")
        self.scan_entry.bind("<Return>", self.handle_gtin)
        Button(scan, "Buscar  Ctrl+F11", self.shortcut_search, variant="accent").grid(row=0, column=2, padx=(10, 0))
        self.cash_status = tk.Label(scan, textvariable=self.cash_status_var, bg=Colors.FOREST, fg="#E9C262", font=font(9, "bold"), anchor="e")
        self.cash_status.grid(row=1, column=1, columnspan=2, sticky="e", pady=(7, 0))
        self.cash_balance = tk.Label(scan, textvariable=self.cash_balance_var, bg=Colors.FOREST, fg="#D5E1D9", font=font(9), anchor="e")
        self.cash_balance.grid(row=2, column=1, columnspan=2, sticky="e", pady=(2, 0))

    def _build_content(self) -> None:
        content = tk.Frame(self, bg=Colors.CREAM, padx=20, pady=17)
        content.grid(row=2, column=0, sticky="nsew")
        content.rowconfigure(0, weight=1)
        content.columnconfigure(0, weight=1)
        content.columnconfigure(1, weight=0)
        left = tk.Frame(content, bg=Colors.CREAM)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        left.rowconfigure(2, weight=1)
        left.columnconfigure(0, weight=1)
        self.notice = NoticeBar(left)
        self.notice.grid(row=0, column=0, sticky="ew")
        heading = tk.Frame(left, bg=Colors.CREAM)
        heading.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        tk.Label(heading, text="Itens da venda", bg=Colors.CREAM, fg=Colors.INK, font=font(16, "bold")).pack(side="left")
        tk.Label(heading, text="F5 cancela o item selecionado", bg=Colors.CREAM, fg=Colors.INK_MUTED, font=font(9)).pack(side="right", pady=(5, 0))
        table_wrap = tk.Frame(left, bg=Colors.SURFACE, highlightbackground=Colors.LINE, highlightthickness=1)
        table_wrap.grid(row=2, column=0, sticky="nsew")
        columns = ("item", "gtin", "quantity", "unit", "price", "subtotal")
        self.cart_table = ttk.Treeview(table_wrap, columns=columns, show="headings", selectmode="browse")
        spec = {
            "item": ("Produto", 250, "w"),
            "gtin": ("GTIN", 125, "w"),
            "quantity": ("Qtd.", 70, "e"),
            "unit": ("Un.", 55, "w"),
            "price": ("Unitário", 100, "e"),
            "subtotal": ("Subtotal", 110, "e"),
        }
        for column, (label, width, anchor) in spec.items():
            self.cart_table.heading(column, text=label)
            self.cart_table.column(column, width=width, minwidth=50, anchor=anchor, stretch=column == "item")
        scroll = ttk.Scrollbar(table_wrap, orient="vertical", command=self.cart_table.yview)
        horizontal = ttk.Scrollbar(table_wrap, orient="horizontal", command=self.cart_table.xview)
        self.cart_table.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        self.cart_table.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        horizontal.pack(side="bottom", fill="x")
        self.cart_table.bind("<Delete>", lambda _event: self.shortcut_cancel())
        self.cart_table.bind("<Double-1>", lambda _event: self.focus_scan())

        right = tk.Frame(content, bg=Colors.CREAM, width=315)
        right.grid(row=0, column=1, sticky="ns")
        right.grid_propagate(False)
        total_card = Card(right, padding=18)
        total_card.pack(fill="x")
        tk.Label(total_card, text="TOTAL DA VENDA", bg=Colors.SURFACE, fg=Colors.INK_MUTED, font=font(9, "bold")).pack(anchor="w")
        tk.Label(total_card, textvariable=self.total_var, bg=Colors.SURFACE, fg=Colors.FOREST, font=font(27, "bold")).pack(anchor="w", pady=(3, 0))
        tk.Label(total_card, textvariable=self.items_var, bg=Colors.SURFACE, fg=Colors.INK_MUTED, font=font(9)).pack(anchor="w", pady=(5, 0))
        self.pay_button = Button(right, "Pagamento  F10", self.shortcut_payment, variant="accent")
        self.pay_button.pack(fill="x", pady=(12, 0))
        Button(right, "Item avulso  F2", self.shortcut_manual_item, variant="ghost").pack(fill="x", pady=(7, 0))
        Button(right, "Editar item  F3", self.shortcut_edit, variant="ghost").pack(fill="x", pady=(7, 0))
        Button(right, "Cancelar item  F5", self.shortcut_cancel, variant="ghost").pack(fill="x", pady=(7, 0))
        self.second_copy_button = Button(
            right, "2ª via da última venda", self.request_second_copy, variant="ghost"
        )
        self.second_copy_button.pack(fill="x", pady=(7, 0))
        self.second_copy_button.configure(state="disabled")
        if self.is_admin:
            self.cancel_sale_button = Button(
                right, "Cancelar última venda", self.request_last_sale_cancellation, variant="ghost"
            )
            self.cancel_sale_button.pack(fill="x", pady=(7, 0))
            self.cancel_sale_button.configure(state="disabled")
        else:
            self.cancel_sale_button = None
        quick_card = Card(right, padding=14)
        quick_card.pack(fill="both", expand=True, pady=(16, 0))
        tk.Label(quick_card, text="ITENS DE BALCÃO", bg=Colors.SURFACE, fg=Colors.INK, font=font(10, "bold"), anchor="w").pack(fill="x")
        tk.Label(quick_card, text="Toque para incluir rapidamente", bg=Colors.SURFACE, fg=Colors.INK_MUTED, font=font(8), anchor="w").pack(fill="x", pady=(3, 10))
        self.quick_list = tk.Frame(quick_card, bg=Colors.SURFACE)
        self.quick_list.pack(fill="both", expand=True)

    def _build_shortcuts(self) -> None:
        footer = tk.Frame(self, bg=Colors.SURFACE_ALT, padx=20, pady=8)
        footer.grid(row=3, column=0, sticky="ew")
        tk.Label(footer, text="Ctrl+F11  Consultar GTIN", bg=Colors.SURFACE_ALT, fg=Colors.INK_MUTED, font=font(9)).pack(side="left", padx=(0, 22))
        tk.Label(footer, text="F2  Item avulso", bg=Colors.SURFACE_ALT, fg=Colors.INK_MUTED, font=font(9)).pack(side="left", padx=(0, 22))
        tk.Label(footer, text="F3  Editar item", bg=Colors.SURFACE_ALT, fg=Colors.INK_MUTED, font=font(9)).pack(side="left", padx=(0, 22))
        tk.Label(footer, text="F5  Cancelar item", bg=Colors.SURFACE_ALT, fg=Colors.INK_MUTED, font=font(9)).pack(side="left", padx=(0, 22))
        tk.Label(footer, text="F10  Pagamento", bg=Colors.SURFACE_ALT, fg=Colors.INK_MUTED, font=font(9)).pack(side="left", padx=(0, 22))
        tk.Label(footer, text="Esc  Cancelar modal", bg=Colors.SURFACE_ALT, fg=Colors.INK_MUTED, font=font(9)).pack(side="left")
        tk.Label(footer, text="Foco: código de barras", bg=Colors.SURFACE_ALT, fg=Colors.FOREST, font=font(9, "bold")).pack(side="right")

    def refresh_cash_status(self) -> None:
        if self.cash:
            self.cash_status_var.set("● CAIXA ABERTO")
            self.cash_status.configure(fg="#BDE8C8")
            self.cash_button.configure(text="Caixa")
            self.pay_button.configure(state="normal")
        else:
            self.cash_status_var.set("● CAIXA FECHADO — abra o caixa para vender")
            self.cash_status.configure(fg="#FFD29B")
            self.cash_button.configure(text="Abrir caixa")
            self.pay_button.configure(state="disabled")
        self._refresh_cash_balance()

    def _refresh_cash_balance(self) -> None:
        """Updates the visible operational cash amount without exposing it by default."""

        settings = getattr(self.controller, "settings", None)
        can_view = self.is_admin or bool(getattr(settings, "show_expected_to_operator", False))
        if not self.cash or not can_view:
            self.cash_balance_var.set("")
            return
        try:
            summary = invoke(
                self.controller,
                "cash_summary",
                int(field(self.cash, "id", 0)),
                int(field(self.user, "id", 0)),
            ) or {}
            balance = field(summary, "valor_em_caixa", None)
            self.cash_balance_var.set(f"Saldo atual: {money(balance)}" if balance is not None else "")
        except Exception:
            # A visual indicator must never block the sale or the closing flow.
            self.cash_balance_var.set("")

    def focus_scan(self) -> None:
        if self.winfo_exists():
            self.after_idle(self.scan_entry.focus_set)

    def handle_gtin(self, _event: tk.Event[Any] | None = None) -> str:
        if not self.cash:
            self.open_cash_actions()
            return "break"
        gtin = self.gtin_var.get().strip()
        self.gtin_var.set("")
        if not gtin:
            self.notice.show("Bipe ou digite um GTIN para incluir o produto.", "warning")
            self.focus_scan()
            return "break"
        try:
            self._scan_queue.enqueue(gtin)
        except OverflowError as exc:
            self.notice.show(str(exc) + " Espere a fila terminar antes de continuar.", "warning")
            return "break"
        if self._scan_inflight is not None:
            self.notice.show(
                f"Código recebido — {self._scan_queue.pending_count} aguardando na fila.",
                "info",
                ttl=1800,
            )
        self._start_next_scan()
        self.focus_scan()
        return "break"

    def _modal_is_open(self) -> bool:
        try:
            current = self.grab_current()
        except tk.TclError:
            return False
        return current is not None and current is not self

    def _schedule_next_scan(self, delay: int = 35) -> None:
        if self._scan_start_after_id is not None or not self.winfo_exists():
            return

        def resume() -> None:
            self._scan_start_after_id = None
            self._start_next_scan()

        self._scan_start_after_id = self.after(delay, resume)

    def _start_next_scan(self) -> None:
        if not self.winfo_exists() or self._scan_inflight is not None:
            return
        if not self._scan_queue.has_pending:
            self._busy = False
            self.focus_scan()
            return
        if self._modal_is_open():
            self._busy = True
            self._schedule_next_scan(80)
            return
        ticket = self._scan_queue.take_next()
        if ticket is None:
            return
        self._scan_inflight = ticket
        self._busy = True
        Thread(
            target=self._scan_in_background,
            args=(ticket,),
            daemon=True,
            name="pdv-gtin-lookup",
        ).start()

    def _scan_in_background(self, ticket: ScanTicket) -> None:
        """Keep the checkout responsive while an offline/API fallback is resolved.

        The controller must make this operation thread-safe (the service facade
        uses short-lived SQLite connections or its own lock).  Results are
        returned through a queue and rendered only on Tk's main thread.
        """

        try:
            response = invoke(self.controller, "scan_product", ticket.code, int(field(self.user, "id", 0)))
        except Exception as exc:
            self._scan_results.put((ticket, None, exc))
        else:
            self._scan_results.put((ticket, response, None))

    def _poll_scan_results(self) -> None:
        self._poll_after_id = None
        if not self.winfo_exists():
            return
        try:
            while True:
                ticket, response, error = self._scan_results.get_nowait()
                if not self._scan_queue.finish(ticket):
                    continue
                self._scan_inflight = None
                if error:
                    self.notice.show(str(error), "danger", ttl=7000)
                    self.focus_scan()
                else:
                    self._dispatch_scan_response(ticket.code, response)
                self._busy = self._scan_queue.has_pending
                self._schedule_next_scan()
        except Empty:
            pass
        self._poll_after_id = self.after(45, self._poll_scan_results)

    def _dispatch_scan_response(self, gtin: str, response: Mapping[str, Any] | Any) -> None:
        if not response:
            self.open_manual_dialog(gtin)
            return
        product = field(response, "product", response)
        status = str(field(response, "status", field(response, "action", "found"))).lower()
        if status in {"inactive", "inativo", "disabled"}:
            self.notice.show("Este produto está inativo e não pode ser vendido.", "danger")
            self.focus_scan()
            return
        if product is None or status in {"manual", "manual_entry_required", "not_found", "missing", "offline"}:
            message = str(field(response, "message", "") or "").strip()
            if message:
                kind = "warning" if status == "offline" else "info"
                self.notice.show(message, kind, ttl=8500)
            self.open_manual_dialog(gtin)
            return
        price = float(field(product, "preco", field(product, "price", 0)) or 0)
        if status in {"pricing", "price_required", "needs_price", "unpriced"} or price <= 0:
            self.open_pricing_dialog(product)
        else:
            self.add_product(product)

    def open_pricing_dialog(self, product: Mapping[str, Any]) -> None:
        gtin = str(field(product, "gtin", self.gtin_var.get()))
        dialog_ref: dict[str, PricingDialog] = {}

        def save(price: float) -> Any:
            try:
                saved = invoke(self.controller, "save_price", gtin, price, int(field(self.user, "id", 0)))
            except Exception as exc:
                if not self.is_admin and self._is_permission_error(exc):
                    self._request_admin_price_approval(product, price, dialog_ref)
                    return False
                raise
            final_product = saved or dict(product, preco=price)
            self.add_product(final_product)
            self.notice.show("Preço salvo e produto incluído na venda.", "success")
            return True

        dialog_ref["dialog"] = PricingDialog(self, product, save)

    def open_manual_dialog(self, gtin: str) -> None:
        dialog_ref: dict[str, ManualProductDialog] = {}

        def create(payload: ProductData) -> Any:
            try:
                product = invoke(self.controller, "create_product", payload, int(field(self.user, "id", 0)))
            except Exception as exc:
                if not self.is_admin and self._is_permission_error(exc):
                    self._request_admin_create_approval(payload, dialog_ref)
                    return False
                raise
            self.add_product(product or payload)
            self.notice.show("Produto cadastrado e incluído na venda.", "success")
            return True

        dialog_ref["dialog"] = ManualProductDialog(self, gtin, create)

    @staticmethod
    def _is_permission_error(error: Exception) -> bool:
        message = str(error).lower()
        return any(token in message for token in ("permiss", "autoriza", "acesso neg", "forbidden", "perfil"))

    def _request_admin_price_approval(
        self,
        product: Mapping[str, Any],
        price: float,
        dialog_ref: Mapping[str, PricingDialog],
    ) -> None:
        """Keep a cashier sale moving without granting price-change permission."""

        gtin = str(field(product, "gtin", ""))
        product_name = str(field(product, "nome", "produto"))

        def approve(login: str, password: str) -> Any:
            approved = invoke(
                self.controller,
                "admin_approve_and_price",
                int(field(self.user, "id", 0)),
                gtin,
                price,
                login,
                password,
            )
            final_product = approved or dict(product, preco=price)
            self.add_product(final_product)
            self.notice.show("Preço autorizado pelo administrador e item incluído.", "success")
            dialog = dialog_ref.get("dialog")
            if dialog and dialog.winfo_exists():
                dialog.cancel()
            return True

        AdminAuthorizationDialog(self, f"definir o preço de {product_name}", approve)

    def _request_admin_create_approval(
        self,
        payload: ProductData,
        dialog_ref: Mapping[str, ManualProductDialog],
    ) -> None:
        product_name = str(payload.get("nome", "produto"))

        def approve(login: str, password: str) -> Any:
            approved = invoke(
                self.controller,
                "admin_approve_and_create",
                int(field(self.user, "id", 0)),
                payload,
                login,
                password,
            )
            self.add_product(approved or payload)
            self.notice.show("Cadastro autorizado pelo administrador e item incluído.", "success")
            dialog = dialog_ref.get("dialog")
            if dialog and dialog.winfo_exists():
                dialog.cancel()
            return True

        AdminAuthorizationDialog(self, f"cadastrar {product_name}", approve)

    def add_product(self, product: Mapping[str, Any], quantity: float | None = None) -> None:
        gtin = str(field(product, "gtin", "")).strip()
        name = str(field(product, "nome", "Produto sem nome"))
        try:
            price = decimal_value(field(product, "preco", 0) or 0, "preço").quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        except Exception:
            price = Decimal("0.00")
        if not gtin:
            self.notice.show("O produto retornado não possui GTIN válido.", "danger")
            self.focus_scan()
            return
        if price <= 0:
            self.open_pricing_dialog(product)
            return
        unit = str(field(product, "unidade", "UN") or "UN").upper()
        if unit in {"KG", "QUILO", "KILOGRAM", "KILOGRAMA"} and quantity is None:
            WeightDialog(self, product, lambda weight: self.add_product(product, weight))
            return
        item_quantity = decimal_value(
            quantity if quantity is not None else 1, "quantidade"
        ).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
        if item_quantity <= 0:
            self.notice.show("A quantidade deve ser maior que zero.", "warning")
            self.focus_scan()
            return
        existing = next((line for line in self.cart if line.gtin == gtin and line.price == price and line.unit == unit), None)
        if existing:
            existing.quantity += item_quantity
        else:
            self._line_sequence += 1
            self.cart.append(
                CartLine(
                    key=self._line_sequence,
                    gtin=gtin,
                    name=name,
                    price=price,
                    quantity=item_quantity,
                    unit=unit,
                    line_kind="CATALOGO",
                    entered_code=gtin,
                    original_price=price,
                )
            )
        self.render_cart()
        self.notice.show(f"{name} incluído.", "success", ttl=2200)
        self.focus_scan()

    def render_cart(self) -> None:
        selected = self.cart_table.selection()
        self.cart_table.delete(*self.cart_table.get_children())
        for line in self.cart:
            self.cart_table.insert(
                "",
                "end",
                iid=str(line.key),
                values=(line.name, line.gtin, self._format_quantity(line.quantity), line.unit, money(line.price), money(line.subtotal)),
            )
        total = sum((line.subtotal for line in self.cart), Decimal("0.00"))
        item_count = sum((line.quantity for line in self.cart), Decimal("0.000"))
        self.total_var.set(money(total))
        self.items_var.set(f"{self._format_quantity(item_count)} item(ns)")
        if selected and self.cart_table.exists(selected[0]):
            self.cart_table.selection_set(selected[0])

    @staticmethod
    def _format_quantity(value: Decimal | float) -> str:
        return f"{Decimal(str(value)):.3f}".rstrip("0").rstrip(".")

    def selected_line(self) -> CartLine | None:
        selection = self.cart_table.selection()
        if not selection:
            return None
        key = int(selection[0])
        return next((line for line in self.cart if line.key == key), None)

    def shortcut_search(self, _event: tk.Event[Any] | None = None) -> str:
        if self._busy:
            return "break"

        def search(query: str) -> Sequence[Mapping[str, Any]]:
            return invoke(self.controller, "search_products", query)

        SearchDialog(self, search, self.add_product)
        return "break"

    def shortcut_manual_item(self, _event: tk.Event[Any] | None = None) -> str:
        if self._busy:
            return "break"
        if not self.cash:
            self.open_cash_actions()
            return "break"

        def include(payload: Mapping[str, Any]) -> bool:
            self._line_sequence += 1
            self.cart.append(
                CartLine(
                    key=self._line_sequence,
                    gtin=None,
                    entered_code=field(payload, "codigo_informado"),
                    name=str(field(payload, "descricao", "Item avulso")),
                    price=field(payload, "preco_unitario", "0"),
                    quantity=field(payload, "quantidade", "1"),
                    unit=str(field(payload, "unidade", "UN")),
                    line_kind="MANUAL",
                )
            )
            self.render_cart()
            self.notice.show("Item avulso incluído sem alterar o estoque.", "success")
            self.focus_scan()
            return True

        ManualSaleItemDialog(self, include)
        return "break"

    def shortcut_edit(self, _event: tk.Event[Any] | None = None) -> str:
        if self._busy:
            return "break"
        line = self.selected_line()
        if line is None:
            self.notice.show("Selecione um item para editar.", "warning")
            return "break"

        def apply(quantity_value: str, price_value: str) -> bool:
            line.quantity = decimal_value(quantity_value, "quantidade").quantize(
                Decimal("0.001"), rounding=ROUND_HALF_UP
            )
            line.price = decimal_value(price_value, "preço").quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            self.render_cart()
            self.notice.show("Item atualizado. O total foi recalculado.", "success")
            self.focus_scan()
            return True

        CartItemEditDialog(
            self,
            {
                "name": line.name,
                "unit": line.unit,
                "quantity": self._format_quantity(line.quantity),
                "price": f"{line.price:.2f}",
            },
            apply,
        )
        return "break"

    def shortcut_cancel(self, _event: tk.Event[Any] | None = None) -> str:
        line = self.selected_line()
        if not line:
            self.notice.show("Selecione um item para cancelar.", "warning")
            self.focus_scan()
            return "break"
        if self.is_admin:
            try:
                allowed = invoke(
                    self.controller,
                    "authorize_item_cancel",
                    int(field(self.user, "id", 0)),
                    line.as_payload(),
                    admin_user_id=int(field(self.user, "id", 0)),
                )
                if not allowed:
                    raise ValueError("O cancelamento não foi autorizado.")
            except Exception as exc:
                self.notice.show(str(exc), "danger")
                self.focus_scan()
                return "break"
            self._remove_line(line)
            return "break"

        def authorize(login: str, password: str) -> Any:
            item = line.as_payload()
            # The controller adapter resolves this request to SaleService/audit.
            allowed = invoke(
                self.controller,
                "authorize_item_cancel",
                int(field(self.user, "id", 0)),
                item,
                admin_login=login,
                admin_password=password,
            )
            if not allowed:
                raise ValueError("A autorização de administrador não foi concedida.")
            self._remove_line(line)
            return True

        AdminAuthorizationDialog(self, line.name, authorize)
        return "break"

    def _remove_line(self, line: CartLine) -> None:
        self.cart = [current for current in self.cart if current.key != line.key]
        self.render_cart()
        self.notice.show(f"{line.name} removido da venda.", "info")
        self.focus_scan()

    def shortcut_payment(self, _event: tk.Event[Any] | None = None) -> str:
        if self._busy or self._scan_queue.has_pending:
            self.notice.show("Aguarde a leitura do código terminar antes do pagamento.", "warning")
            return "break"
        if not self.cash:
            self.open_cash_actions()
            return "break"
        if not self.cart:
            self.notice.show("Inclua ao menos um item antes de ir ao pagamento.", "warning")
            self.focus_scan()
            return "break"
        cart_payload = [line.as_payload() for line in self.cart]
        try:
            quote = invoke(
                self.controller,
                "quote_sale",
                int(field(self.user, "id", 0)),
                cart_payload,
            )
            total = Decimal(str(field(quote, "total", "0.00")))
        except Exception as exc:
            self.notice.show(str(exc), "danger", ttl=8000)
            self.focus_scan()
            return "break"
        # Keep this key while the same dialog is open. A retry after a transient
        # UI failure resolves to the already-confirmed sale instead of duplicating it.
        confirmation_key = uuid4().hex

        def open_payment(manual_authorization: Mapping[str, Any] | None = None) -> bool:
            try:
                pix_payload = invoke(self.controller, "get_pix_payload", total)
            except Exception:
                pix_payload = ""

            def confirm(method: str, received: Decimal | None) -> Any:
                cash_id = int(field(self.cash or {}, "id", 0))
                response = invoke(
                    self.controller,
                    "finalize_sale",
                    cash_id,
                    int(field(self.user, "id", 0)),
                    cart_payload,
                    method,
                    received,
                    chave_idempotencia=confirmation_key,
                    manual_authorization=manual_authorization,
                )
                sale_total = Decimal(str(field(response, "total", total) or total))
                fallback_change = max(
                    (received or Decimal("0.00")) - total,
                    Decimal("0.00"),
                )
                change = Decimal(
                    str(field(response, "troco", field(response, "change", fallback_change)) or 0)
                )
                self.cart.clear()
                self._scan_queue.advance_generation()
                self._scan_inflight = None
                self._busy = False
                self._last_sale_id = int(field(response, "id", field(response, "venda_id", 0)) or 0) or None
                self._last_sale_cancelled = False
                self.second_copy_button.configure(
                    state="normal" if self._last_sale_id else "disabled"
                )
                if self.cancel_sale_button is not None:
                    self.cancel_sale_button.configure(
                        state="normal" if self._last_sale_id else "disabled"
                    )
                self.render_cart()
                self.refresh_cash_status()
                suffix = f" Troco: {money(change)}." if method == "Dinheiro" else ""
                print_warning = str(field(response, "print_warning", field(response, "impressao_alerta", "")) or "")
                if print_warning:
                    self.notice.show(
                        f"Venda de {money(sale_total)} concluída em {method}.{suffix} Atenção: {print_warning}",
                        "warning",
                        ttl=9500,
                    )
                else:
                    self.notice.show(f"Venda de {money(sale_total)} concluída em {method}.{suffix}", "success", ttl=6500)
                self.focus_scan()
                return response or True

            PaymentDialog(self, total, confirm, pix_payload=str(pix_payload or ""))
            return True

        if bool(field(quote, "requer_autorizacao", False)):
            reasons = set(field(quote, "motivos_autorizacao", []) or [])
            labels: list[str] = []
            if "TOTAL_MANUAL_ACIMA_LIMITE" in reasons:
                labels.append("itens avulsos acima de R$ 50,00")
            if "PRECO_EXCEPCIONAL" in reasons:
                labels.append("preço diferente do cadastro")
            message = "Esta venda contém " + " e ".join(labels or ["uma exceção administrativa"]) + "."

            def authorize(login: str, password: str, reason: str) -> bool:
                return open_payment(
                    {"login": login, "password": password, "reason": reason}
                )

            SaleAuthorizationDialog(
                self,
                message,
                authorize,
                require_credentials=not self.is_admin,
            )
        else:
            open_payment()
        return "break"

    def request_second_copy(self) -> None:
        if not self._last_sale_id:
            self.notice.show("Nenhuma venda desta sessão está disponível para segunda via.", "warning")
            return
        try:
            job = invoke(
                self.controller,
                "queue_second_copy",
                self._last_sale_id,
                int(field(self.user, "id", 0)),
                idempotency_key=uuid4().hex,
            )
        except Exception as exc:
            self.notice.show(str(exc), "danger", ttl=8000)
            return
        self.notice.show(
            f"Segunda via da venda #{self._last_sale_id} enviada para a fila de impressão.",
            "success",
        )
        return job

    def request_last_sale_cancellation(self) -> None:
        if not self._last_sale_id or self._last_sale_cancelled:
            self.notice.show("Não há uma venda recente disponível para cancelamento.", "warning")
            return
        idempotency_key = uuid4().hex

        def cancel(login: str, password: str, reason: str) -> Any:
            response = invoke(
                self.controller,
                "cancel_sale",
                self._last_sale_id,
                int(field(self.user, "id", 0)),
                reason,
                idempotency_key,
                admin_login=login,
                admin_password=password,
            )
            self._last_sale_cancelled = True
            if self.cancel_sale_button is not None:
                self.cancel_sale_button.configure(state="disabled")
            self.notice.show(
                "Venda cancelada localmente e estoque recomposto. Atenção: PIX ou cartão não são estornados automaticamente.",
                "warning",
                ttl=10000,
            )
            return response or True

        SaleAuthorizationDialog(
            self,
            "O cancelamento recompõe o estoque local, mas não estorna PIX nem cartão. Confirme com credenciais administrativas.",
            cancel,
            require_credentials=True,
        )

    def open_cash_actions(self) -> None:
        if not self.cash:
            self.open_cash_dialog()
            return
        CashActionsDialog(self, lambda: CashMovementDialog(self, self.register_cash_movement), self.request_cash_close)

    def open_cash_dialog(self) -> None:
        def open_cash(opening_float: float) -> Any:
            cash = invoke(self.controller, "open_cash", int(field(self.user, "id", 0)), opening_float)
            self.cash = cash
            self.refresh_cash_status()
            self.notice.show("Caixa aberto. Você já pode registrar vendas.", "success")
            self.focus_scan()
            return cash or True

        CashOpeningDialog(self, open_cash)

    def prompt_resume_cash(self, open_cash: Mapping[str, Any]) -> None:
        if not self.is_admin or self.cash:
            return

        def resume(reason: str) -> Any:
            cash = invoke(
                self.controller,
                "resume_open_cash",
                int(field(open_cash, "id", 0)),
                reason,
            )
            self.cash = cash
            self.refresh_cash_status()
            self.notice.show("Caixa retomado com sucesso. A justificativa foi registrada.", "success")
            self.focus_scan()
            return cash or True

        CashResumeDialog(self, open_cash, resume)

    def register_cash_movement(
        self,
        movement_type: str,
        amount: float,
        observation: str,
        idempotency_key: str,
    ) -> Any:
        if not self.cash:
            raise ValueError("Abra o caixa antes de registrar uma movimentação.")
        response = invoke(
            self.controller,
            "record_cash_movement",
            int(field(self.cash, "id", 0)),
            int(field(self.user, "id", 0)),
            movement_type,
            amount,
            observation,
            chave_idempotencia=idempotency_key,
        )
        label = "Retirada de dinheiro" if movement_type == "SANGRIA" else "Adição de dinheiro"
        self.refresh_cash_status()
        self.notice.show(f"{label} de {money(amount)} registrada.", "success")
        self.focus_scan()
        return response or True

    def request_cash_close(self) -> None:
        if not self.cash:
            self.notice.show("Não há um caixa aberto para fechar.", "warning")
            return
        if self.cart:
            self.notice.show("Conclua ou cancele a venda atual antes de fechar o caixa.", "warning")
            return

        def close(amount: float, justification: str) -> Any:
            response = invoke(
                self.controller,
                "close_cash",
                int(field(self.cash or {}, "id", 0)),
                int(field(self.user, "id", 0)),
                amount,
                justification,
            )
            if isinstance(response, Mapping) and response.get("requires_justification"):
                return response
            self.cash = None
            self.refresh_cash_status()
            backup_warning = str(field(response, "backup_warning", "") or "")
            if field(response, "valor_esperado", None) is not None:
                expected = float(field(response, "valor_esperado", 0) or 0)
                counted = float(field(response, "valor_informado", 0) or 0)
                difference = counted - expected
                result_label = "falta" if difference < 0 else "sobra" if difference > 0 else "sem diferença"
                result_text = f"Esperado {money(expected)} • contado {money(counted)} • {result_label}: {money(abs(difference))}"
                message = f"Caixa fechado com sucesso. {result_text}."
            else:
                message = "Caixa fechado com sucesso. Conferência registrada."
            if backup_warning:
                self.notice.show(f"{message} Atenção: {backup_warning}", "warning", ttl=9500)
            else:
                self.notice.show(f"{message} Backup registrado.", "success", ttl=6000)
            if self.on_cash_closed:
                self.after(500, self.on_cash_closed)
            return response or True

        CashCloseDialog(self, close)

    def load_counter_products(self) -> None:
        for child in self.quick_list.winfo_children():
            child.destroy()
        try:
            products = list(invoke(self.controller, "get_counter_products"))
        except Exception:
            products = []
        if not products:
            tk.Label(self.quick_list, text="Nenhum item rápido cadastrado.", bg=Colors.SURFACE, fg=Colors.INK_MUTED,
                     font=font(9), justify="left", wraplength=245).pack(anchor="w", pady=5)
            return
        for product in products[:8]:
            label = str(field(product, "nome", "Produto"))
            price = money(field(product, "preco", 0))
            button = tk.Button(
                self.quick_list,
                text=f"{label}\n{price}",
                command=lambda item=product: self.add_product(item),
                bg=Colors.SURFACE_ALT,
                fg=Colors.FOREST,
                activebackground="#E0D9C8",
                activeforeground=Colors.FOREST,
                font=font(9, "bold"),
                justify="left",
                anchor="w",
                bd=0,
                padx=10,
                pady=8,
                cursor="hand2",
                wraplength=230,
            )
            button.pack(fill="x", pady=(0, 6))


class AdminView(tk.Frame):
    """Admin-only inventory, cash-close and maintenance workspace."""

    def __init__(
        self,
        master: tk.Misc,
        controller: object,
        user: Mapping[str, Any],
        on_back: Callable[[], None],
        on_update_scheduled: Callable[[], None] | None = None,
        checkout_idle: Callable[[], bool] | None = None,
    ) -> None:
        super().__init__(master, bg=Colors.CREAM)
        self.controller = controller
        self.user = user
        self.on_back = on_back
        self.on_update_scheduled = on_update_scheduled
        self.checkout_idle = checkout_idle or (lambda: True)
        self.notice: NoticeBar | None = None
        self.products: list[Mapping[str, Any]] = []
        self.users: list[Mapping[str, Any]] = []
        self.printer_options: list[str] = []
        self._printer_task_after_id: str | None = None
        self._printer_task_queue: Queue[tuple[bool, Any]] | None = None
        self._printer_refresh_button: tk.Button | None = None
        self._printer_save_button: tk.Button | None = None
        self._printer_test_button: tk.Button | None = None
        self._update_task_after_id: str | None = None
        self._update_task_queue: Queue[tuple[bool, Any]] | None = None
        self._update_check_button: tk.Button | None = None
        self._update_apply_button: tk.Button | None = None
        self._build()
        self.refresh_dashboard()

    def _build(self) -> None:
        self.rowconfigure(2, weight=1)
        self.columnconfigure(0, weight=1)
        header = tk.Frame(self, bg=Colors.INK, padx=20, pady=12)
        header.grid(row=0, column=0, sticky="ew")
        tk.Label(header, text="TRIGO DE MINAS", bg=Colors.INK, fg="#FFFFFF", font=font(16, "bold")).pack(side="left")
        tk.Label(header, text="Administração", bg=Colors.INK, fg="#EDC04F", font=font(10, "bold")).pack(side="left", padx=(11, 0), pady=(4, 0))
        Button(header, "Voltar ao caixa", self.on_back, variant="ghost").pack(side="right")
        meta = tk.Frame(self, bg=Colors.CREAM, padx=20, pady=14)
        meta.grid(row=1, column=0, sticky="ew")
        self.notice = NoticeBar(meta)
        self.notice.pack(fill="x")
        self.notice.hide()
        tk.Label(meta, text="Gestão operacional", bg=Colors.CREAM, fg=Colors.INK, font=font(19, "bold")).pack(anchor="w")
        tk.Label(meta, text="Produtos, acompanhamento de caixa e manutenção do banco.", bg=Colors.CREAM,
                 fg=Colors.INK_MUTED, font=font(10)).pack(anchor="w", pady=(4, 0))
        self.notebook = ttk.Notebook(self)
        self.notebook.grid(row=2, column=0, sticky="nsew", padx=20, pady=(0, 18))
        self.dashboard_tab = tk.Frame(self.notebook, bg=Colors.CREAM, padx=2, pady=16)
        self.products_tab = tk.Frame(self.notebook, bg=Colors.CREAM, padx=2, pady=16)
        self.users_tab = tk.Frame(self.notebook, bg=Colors.CREAM, padx=2, pady=16)
        self.cash_tab = tk.Frame(self.notebook, bg=Colors.CREAM, padx=2, pady=16)
        self.reports_tab = tk.Frame(self.notebook, bg=Colors.CREAM, padx=2, pady=16)
        self.audit_tab = tk.Frame(self.notebook, bg=Colors.CREAM, padx=2, pady=16)
        self.printer_tab = tk.Frame(self.notebook, bg=Colors.CREAM, padx=2, pady=16)
        self.notebook.add(self.dashboard_tab, text="  Visão geral  ")
        self.notebook.add(self.products_tab, text="  Produtos  ")
        self.notebook.add(self.users_tab, text="  Usuários  ")
        self.notebook.add(self.cash_tab, text="  Caixa e manutenção  ")
        self.notebook.add(self.reports_tab, text="  Relatórios  ")
        self.notebook.add(self.audit_tab, text="  Auditoria  ")
        self.notebook.add(self.printer_tab, text="  Impressora  ")
        self._build_dashboard()
        self._build_products()
        self._build_users()
        self._build_cash()
        self._build_reports()
        self._build_audit()
        self._build_printer()
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_change)

    def _build_dashboard(self) -> None:
        outer = self.dashboard_tab
        self.dashboard_scroll_canvas = tk.Canvas(outer, bg=Colors.CREAM, highlightthickness=0, bd=0)
        dashboard_scrollbar = ttk.Scrollbar(outer, orient="vertical", command=self.dashboard_scroll_canvas.yview)
        self.dashboard_scroll_canvas.configure(yscrollcommand=dashboard_scrollbar.set)
        self.dashboard_scroll_canvas.pack(side="left", fill="both", expand=True)
        dashboard_scrollbar.pack(side="right", fill="y")
        tab = tk.Frame(self.dashboard_scroll_canvas, bg=Colors.CREAM)
        dashboard_window = self.dashboard_scroll_canvas.create_window((0, 0), window=tab, anchor="nw")
        tab.bind(
            "<Configure>",
            lambda _event: self.dashboard_scroll_canvas.configure(scrollregion=self.dashboard_scroll_canvas.bbox("all")),
            add="+",
        )
        self.dashboard_scroll_canvas.bind(
            "<Configure>",
            lambda event: self.dashboard_scroll_canvas.itemconfigure(dashboard_window, width=event.width),
            add="+",
        )
        cards = tk.Frame(tab, bg=Colors.CREAM)
        cards.pack(fill="x")
        self.dashboard_values: dict[str, tk.StringVar] = {}
        spec = (
            ("vendas_hoje", "Vendas hoje", Colors.FOREST),
            ("faturamento_hoje", "Faturamento hoje", Colors.SUCCESS),
            ("caixas_abertos", "Caixas abertos", Colors.INFO),
            ("estoque_baixo", "Estoque baixo", Colors.WARNING),
        )
        for key, title, color in spec:
            card = Card(cards, padding=16, width=185)
            card.pack(side="left", fill="x", expand=True, padx=(0, 10))
            tk.Label(card, text=title.upper(), bg=Colors.SURFACE, fg=Colors.INK_MUTED, font=font(8, "bold")).pack(anchor="w")
            variable = tk.StringVar(value="—")
            self.dashboard_values[key] = variable
            tk.Label(card, textvariable=variable, bg=Colors.SURFACE, fg=color, font=font(21, "bold")).pack(anchor="w", pady=(5, 0))
        panel = Card(tab, padding=20)
        panel.pack(fill="x", pady=(18, 0))
        tk.Label(panel, text="Atalhos de administração", bg=Colors.SURFACE, fg=Colors.INK, font=font(13, "bold")).pack(anchor="w")
        tk.Label(panel, text="Cadastre produtos, confira os fechamentos e mantenha a base local otimizada.", bg=Colors.SURFACE,
                 fg=Colors.INK_MUTED, font=font(10)).pack(anchor="w", pady=(5, 14))
        action_row = tk.Frame(panel, bg=Colors.SURFACE)
        action_row.pack(fill="x")
        Button(action_row, "Novo produto", self.new_product, variant="accent").pack(side="left", padx=(0, 8))
        Button(action_row, "Novo usuário", self.new_user, variant="primary").pack(side="left", padx=(0, 8))
        Button(action_row, "Atualizar painel", self.refresh_dashboard, variant="ghost").pack(side="left")
        update = Card(tab, padding=16)
        update.pack(fill="x", pady=(14, 0))
        tk.Label(update, text="Atualização segura do sistema", bg=Colors.SURFACE, fg=Colors.INK,
                 font=font(12, "bold")).pack(anchor="w")
        self.update_status_var = tk.StringVar(value="Consultando estado local…")
        self.update_version_var = tk.StringVar(value="Versão —")
        tk.Label(update, textvariable=self.update_status_var, bg=Colors.SURFACE, fg=Colors.INK_MUTED,
                 font=font(9), anchor="w", justify="left", wraplength=850).pack(fill="x", pady=(4, 2))
        tk.Label(update, textvariable=self.update_version_var, bg=Colors.SURFACE, fg=Colors.FOREST,
                 font=font(9, "bold"), anchor="w").pack(fill="x", pady=(0, 9))
        update_actions = tk.Frame(update, bg=Colors.SURFACE)
        update_actions.pack(fill="x")
        self._update_check_button = Button(update_actions, "Verificar e baixar", self.check_for_update, variant="ghost")
        self._update_check_button.pack(side="left", padx=(0, 8))
        self._update_apply_button = Button(update_actions, "Instalar agora", self.confirm_apply_update, variant="accent")
        self._update_apply_button.pack(side="left")
        self.after_idle(self.refresh_update_status)

    def _build_products(self) -> None:
        tab = self.products_tab
        toolbar = tk.Frame(tab, bg=Colors.CREAM)
        toolbar.pack(fill="x", pady=(0, 12))
        self.product_query = tk.StringVar()
        entry = ttk.Entry(toolbar, textvariable=self.product_query)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda _event: self.load_products())
        Button(toolbar, "Buscar", self.load_products, variant="primary").pack(side="left", padx=(8, 0))
        Button(toolbar, "Novo produto", self.new_product, variant="accent").pack(side="left", padx=(8, 0))
        table_wrap = tk.Frame(tab, bg=Colors.SURFACE, highlightbackground=Colors.LINE, highlightthickness=1)
        table_wrap.pack(fill="both", expand=True)
        columns = ("gtin", "nome", "marca", "categoria", "preco", "estoque", "validade")
        self.product_table = ttk.Treeview(table_wrap, columns=columns, show="headings", selectmode="browse")
        labels = {"gtin": "GTIN / PLU", "nome": "Produto", "marca": "Marca", "categoria": "Categoria", "preco": "Preço", "estoque": "Estoque", "validade": "Validade"}
        widths = {"gtin": 145, "nome": 270, "marca": 130, "categoria": 125, "preco": 105, "estoque": 95, "validade": 110}
        for column in columns:
            self.product_table.heading(column, text=labels[column])
            self.product_table.column(column, width=widths[column], stretch=column == "nome", anchor="e" if column in {"preco", "estoque"} else "w")
        scrollbar = ttk.Scrollbar(table_wrap, orient="vertical", command=self.product_table.yview)
        horizontal = ttk.Scrollbar(table_wrap, orient="horizontal", command=self.product_table.xview)
        self.product_table.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal.set)
        self.product_table.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        horizontal.pack(side="bottom", fill="x")
        self.product_table.bind("<Double-1>", lambda _event: self.edit_selected_product())
        actions = tk.Frame(tab, bg=Colors.CREAM)
        actions.pack(fill="x", pady=(12, 0))
        Button(actions, "Editar selecionado", self.edit_selected_product, variant="ghost").pack(side="right")
        tk.Label(actions, text="Duplo clique para editar. Produtos próximos do vencimento aparecem em amarelo.",
                 bg=Colors.CREAM, fg=Colors.INK_MUTED, font=font(9)).pack(side="left", pady=(6, 0))

    def _build_users(self) -> None:
        outer = tk.Frame(self.users_tab, bg=Colors.CREAM)
        outer.pack(fill="both", expand=True)
        self.users_scroll_canvas = tk.Canvas(
            outer, bg=Colors.CREAM, highlightthickness=0, bd=0
        )
        users_scrollbar = ttk.Scrollbar(
            outer, orient="vertical", command=self.users_scroll_canvas.yview
        )
        self.users_scroll_canvas.configure(yscrollcommand=users_scrollbar.set)
        self.users_scroll_canvas.pack(side="left", fill="both", expand=True)
        users_scrollbar.pack(side="right", fill="y")
        tab = tk.Frame(self.users_scroll_canvas, bg=Colors.CREAM)
        self.users_scroll_window = self.users_scroll_canvas.create_window(
            (0, 0), window=tab, anchor="nw"
        )

        def sync_region(_event: tk.Event[Any] | None = None) -> None:
            self.users_scroll_canvas.configure(
                scrollregion=self.users_scroll_canvas.bbox("all")
            )

        def sync_width(event: tk.Event[Any]) -> None:
            self.users_scroll_canvas.itemconfigure(
                self.users_scroll_window, width=event.width
            )

        tab.bind("<Configure>", sync_region, add="+")
        self.users_scroll_canvas.bind("<Configure>", sync_width, add="+")

        self.recovery_panel = Card(tab, padding=14)
        self.recovery_panel.pack(fill="x", pady=(0, 12))
        self.recovery_title_var = tk.StringVar()
        self.recovery_message_var = tk.StringVar()
        tk.Label(
            self.recovery_panel,
            textvariable=self.recovery_title_var,
            bg=Colors.SURFACE,
            fg=Colors.INK,
            font=font(11, "bold"),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            self.recovery_panel,
            textvariable=self.recovery_message_var,
            bg=Colors.SURFACE,
            fg=Colors.INK_MUTED,
            font=font(9),
            justify="left",
            anchor="w",
            wraplength=780,
        ).pack(fill="x", pady=(4, 9))
        self.recovery_action_button = Button(
            self.recovery_panel,
            "Configurar recuperação",
            self.open_recovery_configuration,
            variant="accent",
        )
        self.recovery_action_button.pack(anchor="w")
        self._update_recovery_panel(self.user)

        toolbar = tk.Frame(tab, bg=Colors.CREAM)
        toolbar.pack(fill="x", pady=(0, 12))
        tk.Label(toolbar, text="Contas de acesso", bg=Colors.CREAM, fg=Colors.INK, font=font(14, "bold")).pack(side="left")
        Button(toolbar, "Atualizar", self.load_users, variant="ghost").pack(side="right")
        Button(toolbar, "Redefinir senha", self.reset_selected_user_password, variant="ghost").pack(side="right", padx=(0, 8))
        Button(toolbar, "Novo usuário", self.new_user, variant="accent").pack(side="right", padx=(0, 8))
        tk.Label(
            tab,
            text="As senhas não são exibidas nem armazenadas em texto puro. A conta nova troca a senha no primeiro acesso.",
            bg=Colors.CREAM,
            fg=Colors.INK_MUTED,
            font=font(9),
            justify="left",
        ).pack(anchor="w", pady=(0, 10))
        table_wrap = tk.Frame(tab, bg=Colors.SURFACE, highlightbackground=Colors.LINE, highlightthickness=1)
        table_wrap.pack(fill="both", expand=True)
        columns = ("id", "nome", "login", "perfil", "status", "troca_senha")
        self.user_table = ttk.Treeview(
            table_wrap, columns=columns, show="headings", selectmode="browse", height=9
        )
        spec = {
            "id": ("#", 55), "nome": ("Nome", 245), "login": ("Login", 170),
            "perfil": ("Perfil", 105), "status": ("Status", 105), "troca_senha": ("Troca de senha", 155),
        }
        for column, (label, width) in spec.items():
            self.user_table.heading(column, text=label)
            self.user_table.column(column, width=width, stretch=column == "nome", anchor="w")
        scrollbar = ttk.Scrollbar(table_wrap, orient="vertical", command=self.user_table.yview)
        horizontal = ttk.Scrollbar(table_wrap, orient="horizontal", command=self.user_table.xview)
        self.user_table.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal.set)
        self.user_table.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        horizontal.pack(side="bottom", fill="x")

    def _update_recovery_panel(self, account: Mapping[str, Any]) -> None:
        configured = bool(field(account, "recovery_configured", False))
        if configured:
            self.recovery_title_var.set("Recuperação administrativa protegida")
            self.recovery_message_var.set(
                "Existe um código configurado. Você pode trocá-lo a qualquer momento; o código anterior deixa de funcionar."
            )
            self.recovery_action_button.configure(text="Trocar código")
        else:
            self.recovery_title_var.set("Proteção de recuperação pendente")
            self.recovery_message_var.set(
                "Esta conta administrativa ainda não tem código de recuperação. Configure agora para recuperar o acesso sem atendimento presencial."
            )
            self.recovery_action_button.configure(text="Configurar recuperação")

    def _adopt_current_user(self, account: Mapping[str, Any]) -> None:
        values = dict(account)
        if isinstance(self.user, dict):
            self.user.clear()
            self.user.update(values)
        else:
            self.user = values
        self._update_recovery_panel(self.user)

    def open_recovery_configuration(self) -> None:
        try:
            recovery_code = str(
                invoke(self.controller, "prepare_own_recovery_code")
            )
        except Exception as exc:
            self.show_notice(str(exc), "danger")
            return

        def save(current_password: str, confirmed_code: str) -> Any:
            updated = invoke(
                self.controller,
                "configure_own_recovery_code",
                current_password,
                confirmed_code,
            )
            self._adopt_current_user(updated)
            self.show_notice(
                "Recuperação administrativa configurada com segurança.", "success"
            )
            return updated or True

        RecoveryCodeSetupDialog(self, recovery_code, save)

    def _build_cash(self) -> None:
        outer = self.cash_tab
        self.cash_scroll_canvas = tk.Canvas(
            outer, bg=Colors.CREAM, highlightthickness=0, bd=0
        )
        cash_scrollbar = ttk.Scrollbar(
            outer, orient="vertical", command=self.cash_scroll_canvas.yview
        )
        self.cash_scroll_canvas.configure(yscrollcommand=cash_scrollbar.set)
        self.cash_scroll_canvas.pack(side="left", fill="both", expand=True)
        cash_scrollbar.pack(side="right", fill="y")
        tab = tk.Frame(self.cash_scroll_canvas, bg=Colors.CREAM)
        cash_window = self.cash_scroll_canvas.create_window(
            (0, 0), window=tab, anchor="nw"
        )
        tab.bind(
            "<Configure>",
            lambda _event: self.cash_scroll_canvas.configure(
                scrollregion=self.cash_scroll_canvas.bbox("all")
            ),
            add="+",
        )
        self.cash_scroll_canvas.bind(
            "<Configure>",
            lambda event: self.cash_scroll_canvas.itemconfigure(
                cash_window, width=event.width
            ),
            add="+",
        )
        top = tk.Frame(tab, bg=Colors.CREAM)
        top.pack(fill="x", pady=(0, 12))
        tk.Label(top, text="Fechamentos recentes", bg=Colors.CREAM, fg=Colors.INK, font=font(14, "bold")).pack(side="left")
        Button(top, "Atualizar", self.load_cash_closures, variant="ghost").pack(side="right")
        table_wrap = tk.Frame(tab, bg=Colors.SURFACE, highlightbackground=Colors.LINE, highlightthickness=1, height=235)
        table_wrap.pack(fill="x")
        columns = ("id", "operador", "abertura", "fechamento", "informado", "quebra", "status")
        self.closure_table = ttk.Treeview(
            table_wrap, columns=columns, show="headings", height=6
        )
        spec = {
            "id": ("#", 50), "operador": ("Operador", 155), "abertura": ("Abertura", 135),
            "fechamento": ("Fechamento", 135), "informado": ("Informado", 105), "quebra": ("Quebra", 100), "status": ("Status", 90),
        }
        for column, (label, width) in spec.items():
            self.closure_table.heading(column, text=label)
            self.closure_table.column(column, width=width, stretch=column == "operador", anchor="e" if column in {"informado", "quebra"} else "w")
        scrollbar = ttk.Scrollbar(table_wrap, orient="vertical", command=self.closure_table.yview)
        horizontal = ttk.Scrollbar(table_wrap, orient="horizontal", command=self.closure_table.xview)
        self.closure_table.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal.set)
        self.closure_table.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        horizontal.pack(side="bottom", fill="x")
        maintenance = Card(tab, padding=15)
        maintenance.pack(fill="x", pady=(15, 0))
        tk.Label(maintenance, text="Cuidados do banco", bg=Colors.SURFACE, fg=Colors.INK, font=font(12, "bold")).pack(anchor="w")
        tk.Label(maintenance, text="Use fora do horário de pico. Faça um backup antes e aguarde a conclusão.",
                 bg=Colors.SURFACE, fg=Colors.INK_MUTED, font=font(9)).pack(anchor="w", pady=(4, 12))
        actions = tk.Frame(maintenance, bg=Colors.SURFACE)
        actions.pack(fill="x")
        Button(actions, "Compactar banco", lambda: self.confirm_maintenance("VACUUM"), variant="ghost").pack(side="left", padx=(0, 8))
        Button(actions, "Reorganizar índices", lambda: self.confirm_maintenance("REINDEX"), variant="ghost").pack(side="left")
        tk.Label(
            maintenance,
            text="Antes da abertura oficial",
            bg=Colors.SURFACE,
            fg=Colors.INK,
            font=font(10, "bold"),
            anchor="w",
        ).pack(fill="x", pady=(15, 3))
        tk.Label(
            maintenance,
            text="Após testar o caixa, use uma vez para criar backup e remover os testes. Depois faça o inventário real.",
            bg=Colors.SURFACE,
            fg=Colors.INK_MUTED,
            font=font(9),
            justify="left",
            anchor="w",
            wraplength=760,
        ).pack(fill="x", pady=(0, 8))
        self.production_status_var = tk.StringVar(value="Disponível somente antes da primeira venda real.")
        tk.Label(
            maintenance,
            textvariable=self.production_status_var,
            bg=Colors.SURFACE,
            fg=Colors.WARNING,
            font=font(9, "bold"),
            anchor="w",
        ).pack(fill="x", pady=(0, 8))
        self._production_button = Button(
            maintenance,
            "Limpar testes e iniciar produção",
            self.confirm_production_preparation,
            variant="danger",
        )
        self._production_button.pack(anchor="w")
        self.after_idle(self.refresh_production_preparation_status)

    def _build_reports(self) -> None:
        tab = self.reports_tab
        filters = Card(tab, padding=14)
        filters.pack(fill="x")
        tk.Label(filters, text="Relatório financeiro", bg=Colors.SURFACE, fg=Colors.INK, font=font(13, "bold")).pack(anchor="w")
        tk.Label(filters, text="Defina o período para consolidar vendas, formas de pagamento e itens mais vendidos.",
                 bg=Colors.SURFACE, fg=Colors.INK_MUTED, font=font(9)).pack(anchor="w", pady=(4, 12))
        row = tk.Frame(filters, bg=Colors.SURFACE)
        row.pack(fill="x")
        self.report_start = tk.StringVar(value=date.today().isoformat())
        self.report_end = tk.StringVar(value=date.today().isoformat())
        for label, variable in (("De (AAAA-MM-DD)", self.report_start), ("Até (AAAA-MM-DD)", self.report_end)):
            group = tk.Frame(row, bg=Colors.SURFACE)
            group.pack(side="left", padx=(0, 12))
            tk.Label(group, text=label.upper(), bg=Colors.SURFACE, fg=Colors.INK_MUTED, font=font(8, "bold")).pack(anchor="w")
            entry = ttk.Entry(group, textvariable=variable, width=15)
            entry.pack(anchor="w", pady=(4, 0))
            entry.bind("<Return>", lambda _event: self.load_financial_report())
        Button(row, "Gerar relatório", self.load_financial_report, variant="accent").pack(side="left", pady=(15, 0))
        overview = tk.Frame(tab, bg=Colors.CREAM)
        overview.pack(fill="x", pady=(14, 0))
        self.report_values = {"sales": tk.StringVar(value="—"), "total": tk.StringVar(value="—")}
        for key, label, color in (("sales", "Vendas no período", Colors.FOREST), ("total", "Faturamento no período", Colors.SUCCESS)):
            card = Card(overview, padding=14)
            card.pack(side="left", fill="x", expand=True, padx=(0, 10))
            tk.Label(card, text=label.upper(), bg=Colors.SURFACE, fg=Colors.INK_MUTED, font=font(8, "bold")).pack(anchor="w")
            tk.Label(card, textvariable=self.report_values[key], bg=Colors.SURFACE, fg=color, font=font(20, "bold")).pack(anchor="w", pady=(4, 0))
        tables = tk.Frame(tab, bg=Colors.CREAM)
        tables.pack(fill="both", expand=True, pady=(14, 0))
        payment_card = Card(tables, padding=12)
        payment_card.pack(side="left", fill="both", expand=True, padx=(0, 8))
        tk.Label(payment_card, text="Por forma de pagamento", bg=Colors.SURFACE, fg=Colors.INK, font=font(11, "bold")).pack(anchor="w", pady=(0, 8))
        self.payment_report_table = ttk.Treeview(payment_card, columns=("method", "count", "total"), show="headings", height=7)
        for column, label, width, anchor in (("method", "Forma", 140, "w"), ("count", "Vendas", 80, "e"), ("total", "Total", 120, "e")):
            self.payment_report_table.heading(column, text=label)
            self.payment_report_table.column(column, width=width, anchor=anchor, stretch=column == "method")
        self.payment_report_table.pack(fill="both", expand=True)
        product_card = Card(tables, padding=12)
        product_card.pack(side="left", fill="both", expand=True, padx=(8, 0))
        tk.Label(product_card, text="Itens mais vendidos", bg=Colors.SURFACE, fg=Colors.INK, font=font(11, "bold")).pack(anchor="w", pady=(0, 8))
        self.top_products_table = ttk.Treeview(product_card, columns=("name", "quantity", "total"), show="headings", height=7)
        for column, label, width, anchor in (("name", "Produto", 185, "w"), ("quantity", "Qtd.", 75, "e"), ("total", "Total", 115, "e")):
            self.top_products_table.heading(column, text=label)
            self.top_products_table.column(column, width=width, anchor=anchor, stretch=column == "name")
        self.top_products_table.pack(fill="both", expand=True)

    def _build_audit(self) -> None:
        tab = self.audit_tab
        toolbar = tk.Frame(tab, bg=Colors.CREAM)
        toolbar.pack(fill="x", pady=(0, 12))
        tk.Label(toolbar, text="Trilha de auditoria", bg=Colors.CREAM, fg=Colors.INK, font=font(14, "bold")).pack(side="left")
        self.audit_limit = tk.StringVar(value="200")
        ttk.Combobox(toolbar, textvariable=self.audit_limit, values=("50", "100", "200", "500"), state="readonly", width=7).pack(side="right")
        Button(toolbar, "Atualizar", self.load_audit_logs, variant="ghost").pack(side="right", padx=(0, 8))
        tk.Label(toolbar, text="Exibir", bg=Colors.CREAM, fg=Colors.INK_MUTED, font=font(9)).pack(side="right", padx=(0, 5))
        table_wrap = tk.Frame(tab, bg=Colors.SURFACE, highlightbackground=Colors.LINE, highlightthickness=1)
        table_wrap.pack(fill="both", expand=True)
        columns = ("date", "user", "action", "entity", "entity_id", "details")
        self.audit_table = ttk.Treeview(table_wrap, columns=columns, show="headings")
        spec = {
            "date": ("Data/hora", 165), "user": ("Usuário", 110), "action": ("Ação", 190),
            "entity": ("Entidade", 110), "entity_id": ("ID", 80), "details": ("Detalhes", 330),
        }
        for column, (label, width) in spec.items():
            self.audit_table.heading(column, text=label)
            self.audit_table.column(column, width=width, stretch=column == "details", anchor="w")
        scrollbar = ttk.Scrollbar(table_wrap, orient="vertical", command=self.audit_table.yview)
        horizontal = ttk.Scrollbar(table_wrap, orient="horizontal", command=self.audit_table.xview)
        self.audit_table.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal.set)
        self.audit_table.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        horizontal.pack(side="bottom", fill="x")
        tk.Label(tab, text="Os registros são imutáveis e acessíveis apenas por administradores.", bg=Colors.CREAM,
                 fg=Colors.INK_MUTED, font=font(9)).pack(anchor="w", pady=(10, 0))

    def _build_printer(self) -> None:
        tab = self.printer_tab
        card = Card(tab, padding=18)
        card.pack(fill="x", expand=False)
        tk.Label(card, text="Configurações de impressão", bg=Colors.SURFACE, fg=Colors.INK,
                 font=font(14, "bold")).pack(anchor="w")
        tk.Label(card, text="O sistema consulta as impressoras instaladas no Windows. USB, rede e compartilhadas aparecem na mesma lista.",
                 bg=Colors.SURFACE, fg=Colors.INK_MUTED, font=font(10), wraplength=900,
                 justify="left").pack(anchor="w", pady=(5, 14))

        self.printer_selected_var = tk.StringVar()
        self.printer_paper_var = tk.StringVar(value="80 mm")
        self.printer_status_var = tk.StringVar(value="Atualize a lista para detectar as impressoras desta máquina.")
        self.printer_default_var = tk.StringVar(value="Padrão do Windows: —")
        selection = tk.Frame(card, bg=Colors.SURFACE)
        selection.pack(fill="x", pady=(0, 10))
        tk.Label(selection, text="IMPRESSORA DO PDV", bg=Colors.SURFACE, fg=Colors.INK_MUTED,
                 font=font(8, "bold")).pack(anchor="w")
        selection_row = tk.Frame(selection, bg=Colors.SURFACE)
        selection_row.pack(fill="x", pady=(4, 0))
        self.printer_combo = ttk.Combobox(
            selection_row, textvariable=self.printer_selected_var, state="readonly", width=55
        )
        self.printer_combo.pack(side="left", fill="x", expand=True)
        self.printer_combo.bind("<<ComboboxSelected>>", lambda _event: self._update_printer_status_from_selection())

        status_row = tk.Frame(card, bg=Colors.SURFACE)
        status_row.pack(fill="x", pady=(3, 0))
        tk.Label(status_row, text="Status:", width=18, anchor="w", bg=Colors.SURFACE,
                 fg=Colors.INK_MUTED, font=font(9, "bold")).pack(side="left")
        tk.Label(status_row, textvariable=self.printer_status_var, anchor="w", bg=Colors.SURFACE,
                 fg=Colors.INK, font=font(10), wraplength=720, justify="left").pack(side="left", fill="x", expand=True)
        tk.Label(card, textvariable=self.printer_default_var, bg=Colors.SURFACE, fg=Colors.INK_MUTED,
                 font=font(9), anchor="w").pack(fill="x", pady=(2, 0))

        paper_row = tk.Frame(card, bg=Colors.SURFACE)
        paper_row.pack(fill="x", pady=(12, 0))
        tk.Label(paper_row, text="LARGURA DO PAPEL", bg=Colors.SURFACE, fg=Colors.INK_MUTED,
                 font=font(8, "bold")).pack(side="left", padx=(0, 10))
        self.printer_paper_combo = ttk.Combobox(
            paper_row, textvariable=self.printer_paper_var, state="readonly",
            values=("58 mm", "80 mm"), width=10,
        )
        self.printer_paper_combo.pack(side="left", padx=(0, 10))
        Button(paper_row, "Salvar largura", self.save_printer_paper_width, variant="ghost").pack(side="left")
        tk.Label(paper_row, text="58 mm: 32 colunas · 80 mm: 42 colunas", bg=Colors.SURFACE,
                 fg=Colors.INK_MUTED, font=font(9)).pack(side="left", padx=(12, 0))

        actions_primary = tk.Frame(card, bg=Colors.SURFACE)
        actions_primary.pack(fill="x", pady=(16, 0))
        self._printer_refresh_button = Button(actions_primary, "Atualizar impressoras", self.refresh_printers, variant="ghost")
        self._printer_refresh_button.pack(side="left", padx=(0, 8))
        self._printer_save_button = Button(actions_primary, "Salvar seleção", self.save_printer_selection, variant="accent")
        self._printer_save_button.pack(side="left", padx=(0, 8))
        self._printer_test_button = Button(actions_primary, "Testar impressão", self.test_printer, variant="primary")
        self._printer_test_button.pack(side="left", padx=(0, 8))
        actions_secondary = tk.Frame(card, bg=Colors.SURFACE)
        actions_secondary.pack(fill="x", pady=(8, 0))
        Button(actions_secondary, "Usar padrão do Windows", self.use_default_printer, variant="ghost").pack(side="left", padx=(0, 8))
        Button(actions_secondary, "Desativar impressão", self.disable_printer, variant="ghost").pack(side="left")
        tk.Label(tab, text="O teste não cria venda. Se a impressora for removida ou estiver desligada, o PDV informa o problema e mantém o comprovante para reimpressão.",
                 bg=Colors.CREAM, fg=Colors.INK_MUTED, font=font(9), wraplength=900,
                 justify="left").pack(anchor="w", pady=(12, 0))
        self.after_idle(self.refresh_printers)

    def refresh_update_status(self) -> None:
        try:
            status = invoke(self.controller, "admin_update_status", bool(self.checkout_idle())) or {}
        except Exception as exc:
            self.update_status_var.set(str(exc))
            self.update_version_var.set("Versão —")
            if self._update_check_button:
                self._update_check_button.configure(state="disabled")
            if self._update_apply_button:
                self._update_apply_button.configure(state="disabled")
            return
        configured = bool(status.get("configured"))
        message = str(status.get("status") or "Estado indisponível")
        if not bool(status.get("enabled")):
            message += " · atualização online desativada"
        elif not bool(status.get("trusted_root_installed")):
            message += " · falta instalar a raiz de confiança assinada"
        self.update_status_var.set(message)
        target = str(status.get("target_version") or "").strip()
        current = str(status.get("current_version") or "—")
        channel = str(status.get("channel") or "stable")
        self.update_version_var.set(
            f"Versão atual {current} · canal {channel}" + (f" · disponível {target}" if target else "")
        )
        if self._update_check_button:
            self._update_check_button.configure(state="normal" if configured else "disabled", text="Verificar e baixar")
        if self._update_apply_button:
            self._update_apply_button.configure(state="normal" if bool(status.get("can_apply")) else "disabled", text="Instalar agora")

    def _update_task(self, work: Callable[[], Any], done: Callable[[Any], None]) -> None:
        if self._update_task_queue is not None:
            self.show_notice("Aguarde a operação de atualização terminar.", "info")
            return
        result_queue: Queue[tuple[bool, Any]] = Queue()
        self._update_task_queue = result_queue

        def worker() -> None:
            try:
                result_queue.put((True, work()))
            except Exception as exc:
                result_queue.put((False, exc))

        Thread(target=worker, daemon=True, name="trigopdv-update-task").start()

        def poll() -> None:
            if not self.winfo_exists():
                return
            try:
                success, payload = result_queue.get_nowait()
            except Empty:
                self._update_task_after_id = self.after(80, poll)
                return
            self._update_task_after_id = None
            self._update_task_queue = None
            if success:
                done(payload)
            else:
                self.show_notice(str(payload), "danger")
            self.refresh_update_status()

        self._update_task_after_id = self.after(80, poll)

    def check_for_update(self) -> None:
        if self._update_task_queue is not None:
            return
        if self._update_check_button:
            self._update_check_button.configure(state="disabled", text="Verificando…")
        self._update_task(
            lambda: invoke(self.controller, "admin_check_for_update") or {},
            lambda result: self.show_notice(str(result.get("message") or "Verificação concluída."), "success" if result.get("available") else "info"),
        )

    def confirm_apply_update(self) -> None:
        def start() -> bool:
            if self._update_apply_button:
                self._update_apply_button.configure(state="disabled", text="Preparando…")
            self._update_task(
                lambda: invoke(
                    self.controller,
                    "admin_apply_downloaded_update",
                    bool(self.checkout_idle()),
                ) or {},
                self._update_apply_completed,
            )
            return True

        ConfirmDialog(
            self,
            "Instalar atualização",
            "O caixa precisa estar fechado. O sistema fará uma cópia de segurança, encerrará e reiniciará na nova versão. Continuar?",
            start,
        )

    def _update_apply_completed(self, result: Mapping[str, Any]) -> None:
        self.show_notice(str(result.get("message") or "Atualização preparada."), "success")
        if self.on_update_scheduled is not None:
            # This callback is reached by the Tk poller, never by the worker.
            # Destroying the root therefore performs controller.shutdown()
            # before Velopack replaces files instead of waiting to kill PDV.
            self.after_idle(self.on_update_scheduled)

    def _printer_task(self, work: Callable[[], Any], done: Callable[[Any], None]) -> None:
        """Run spooler/configuration work away from Tk's main loop."""

        if self._printer_task_queue is not None:
            return
        result_queue: Queue[tuple[bool, Any]] = Queue()
        self._printer_task_queue = result_queue

        def worker() -> None:
            try:
                result_queue.put((True, work()))
            except Exception as exc:
                result_queue.put((False, exc))

        Thread(target=worker, daemon=True, name="trigopdv-printer-task").start()

        def poll() -> None:
            if not self.winfo_exists():
                return
            try:
                success, payload = result_queue.get_nowait()
            except Empty:
                self._printer_task_after_id = self.after(50, poll)
                return
            self._printer_task_after_id = None
            self._printer_task_queue = None
            try:
                if success:
                    done(payload)
                else:
                    self.show_notice(str(payload), "danger")
            finally:
                self._restore_printer_actions()

        self._printer_task_after_id = self.after(50, poll)

    def _restore_printer_actions(self) -> None:
        if self._printer_refresh_button:
            self._printer_refresh_button.configure(state="normal", text="Atualizar impressoras")
        if self._printer_save_button:
            self._printer_save_button.configure(state="normal", text="Salvar seleção")
        if self._printer_test_button:
            self._printer_test_button.configure(state="normal", text="Testar impressão")

    def _printer_action_busy(self) -> bool:
        if self._printer_task_queue is None:
            return False
        self.show_notice("Aguarde a operação da impressora terminar e tente novamente.", "info")
        return True

    def refresh_printers(self) -> None:
        if self._printer_action_busy():
            return
        if self._printer_refresh_button:
            self._printer_refresh_button.configure(state="disabled", text="Atualizando…")
        self.printer_status_var.set("Consultando as impressoras instaladas…")

        def load() -> dict[str, Any]:
            printers = list(invoke(self.controller, "list_printers") or [])
            configuration = invoke(self.controller, "printer_configuration") or {}
            return {"printers": printers, "configuration": configuration}

        self._printer_task(load, self._on_printers_loaded)

    def _on_printers_loaded(self, payload: Mapping[str, Any]) -> None:
        printers = [item for item in list(payload.get("printers") or []) if isinstance(item, Mapping)]
        configuration = payload.get("configuration") if isinstance(payload.get("configuration"), Mapping) else {}
        names = [str(item.get("name") or "").strip() for item in printers if str(item.get("name") or "").strip()]
        configured = str(configuration.get("configured_name") or "").strip()
        if configured and configured.casefold() not in {name.casefold() for name in names}:
            names.append(configured)
        self.printer_options = names
        self.printer_combo.configure(values=tuple(names))
        self.printer_selected_var.set(configured)
        default_name = str(configuration.get("default_name") or "").strip()
        self.printer_default_var.set(f"Padrão do Windows: {default_name or 'nenhuma definida'}")
        paper_width = int(configuration.get("paper_width") or 80)
        self.printer_paper_var.set(f"{paper_width if paper_width in {58, 80} else 80} mm")
        self._update_printer_status(configuration)
        if self._printer_refresh_button:
            self._printer_refresh_button.configure(state="normal", text="Atualizar impressoras")
        if not names:
            self.show_notice("Nenhuma impressora foi encontrada no Windows. Instale o driver e atualize a lista.", "warning")

    def _update_printer_status(self, configuration: Mapping[str, Any] | None = None) -> None:
        config = configuration or {}
        if str(config.get("driver") or "").strip().lower() == "ipp":
            status = str(config.get("status") or "IPP configurada — use Testar impressão para confirmar a comunicação.").strip()
            warning = str(config.get("transport_warning") or "").strip()
            self.printer_status_var.set(f"{status} {warning}".strip())
            return
        selected = self.printer_selected_var.get().strip()
        if selected and selected.casefold() == str(config.get("configured_name") or "").strip().casefold():
            if bool(config.get("selected_found")) and bool(config.get("selected_available")):
                self.printer_status_var.set("Disponível — seleção salva e usada pelo PDV.")
            elif bool(config.get("selected_found")):
                self.printer_status_var.set("Indisponível — escolha outra impressora ou atualize a lista.")
            else:
                self.printer_status_var.set("Não encontrada — escolha outra impressora ou atualize a lista.")
        elif selected:
            item = next((row for row in self.printer_options if row.casefold() == selected.casefold()), None)
            self.printer_status_var.set("Seleção pronta — clique em Salvar seleção." if item else "Escolha uma impressora encontrada no Windows.")
        else:
            self.printer_status_var.set("Nenhuma impressora específica selecionada. O Windows informa o padrão abaixo.")

    def _update_printer_status_from_selection(self) -> None:
        self._update_printer_status()

    def save_printer_selection(self) -> None:
        if self._printer_action_busy():
            return
        name = self.printer_selected_var.get().strip()
        if not name:
            self.show_notice("Selecione uma impressora antes de salvar.", "warning")
            return
        if self._printer_save_button:
            self._printer_save_button.configure(state="disabled", text="Salvando…")
        self._printer_task(
            lambda: invoke(self.controller, "save_printer_selection", name),
            self._on_printer_saved,
        )

    def _on_printer_saved(self, configuration: Mapping[str, Any]) -> None:
        self.show_notice("Impressora salva. Os próximos comprovantes usarão essa seleção.", "success")
        self._on_printers_loaded({"printers": configuration.get("printers", []), "configuration": configuration})
        if self._printer_save_button:
            self._printer_save_button.configure(state="normal", text="Salvar seleção")

    def disable_printer(self) -> None:
        if self._printer_action_busy():
            return
        self._printer_task(
            lambda: invoke(self.controller, "save_printer_selection", ""),
            lambda configuration: self._on_printer_disabled(configuration),
        )

    def use_default_printer(self) -> None:
        if self._printer_action_busy():
            return
        self._printer_task(
            lambda: invoke(self.controller, "use_default_printer"),
            lambda configuration: self._on_printer_saved(configuration),
        )

    def save_printer_paper_width(self) -> None:
        if self._printer_action_busy():
            return
        paper_width = 58 if self.printer_paper_var.get().strip().startswith("58") else 80
        self._printer_task(
            lambda: invoke(self.controller, "save_printer_paper_width", paper_width),
            lambda configuration: self._on_printer_paper_width_saved(configuration),
        )

    def _on_printer_paper_width_saved(self, configuration: Mapping[str, Any]) -> None:
        self.show_notice("Largura do papel salva. O próximo comprovante usará esse formato.", "success")
        self._on_printers_loaded({"printers": configuration.get("printers", []), "configuration": configuration})

    def _on_printer_disabled(self, configuration: Mapping[str, Any]) -> None:
        self.show_notice("Impressão desativada. As vendas continuam sendo registradas e o comprovante fica salvo para consulta.", "info")
        self._on_printers_loaded({"printers": configuration.get("printers", []), "configuration": configuration})

    def test_printer(self) -> None:
        if self._printer_action_busy():
            return
        if self._printer_test_button:
            self._printer_test_button.configure(state="disabled", text="Testando…")
        self._printer_task(
            lambda: invoke(self.controller, "test_printer") or {},
            self._on_printer_tested,
        )

    def _on_printer_tested(self, result: Mapping[str, Any]) -> None:
        kind = "success" if bool(result.get("printed")) else "warning"
        self.show_notice(str(result.get("message") or "Teste concluído."), kind)
        if self._printer_test_button:
            self._printer_test_button.configure(state="normal", text="Testar impressão")

    def _on_tab_change(self, _event: tk.Event[Any]) -> None:
        selected = self.notebook.select()
        if selected == str(self.products_tab):
            self.load_products()
        elif selected == str(self.users_tab):
            self.load_users()
        elif selected == str(self.cash_tab):
            self.load_cash_closures()
        elif selected == str(self.reports_tab):
            self.load_financial_report()
        elif selected == str(self.audit_tab):
            self.load_audit_logs()
        elif selected == str(self.printer_tab):
            self.refresh_printers()

    def destroy(self) -> None:
        if self._printer_task_after_id:
            try:
                self.after_cancel(self._printer_task_after_id)
            except tk.TclError:
                pass
            self._printer_task_after_id = None
        self._printer_task_queue = None
        if self._update_task_after_id:
            try:
                self.after_cancel(self._update_task_after_id)
            except tk.TclError:
                pass
            self._update_task_after_id = None
        self._update_task_queue = None
        super().destroy()

    def show_notice(self, message: str, kind: str = "info") -> None:
        if self.notice:
            self.notice.show(message, kind)

    def refresh_dashboard(self) -> None:
        try:
            data = invoke(self.controller, "admin_dashboard") or {}
            values = {
                "vendas_hoje": str(field(data, "vendas_hoje", field(data, "sales_today", 0))),
                "faturamento_hoje": money(field(data, "faturamento_hoje", field(data, "revenue_today", 0))),
                "caixas_abertos": str(field(data, "caixas_abertos", field(data, "open_cash_count", 0))),
                "estoque_baixo": str(field(data, "estoque_baixo", field(data, "low_stock_count", 0))),
            }
            for key, value in values.items():
                self.dashboard_values[key].set(value)
        except Exception as exc:
            self.show_notice(str(exc), "danger")

    def load_products(self) -> None:
        try:
            self.products = list(invoke(self.controller, "admin_products", self.product_query.get().strip()))
        except Exception as exc:
            self.show_notice(str(exc), "danger")
            return
        self.product_table.delete(*self.product_table.get_children())
        today = date.today()
        for index, product in enumerate(self.products):
            valid = str(field(product, "data_validade", "") or "")
            tags: tuple[str, ...] = ()
            try:
                if valid and (datetime.strptime(valid[:10], "%Y-%m-%d").date() - today).days <= 7:
                    tags = ("near_expiry",)
            except ValueError:
                pass
            self.product_table.insert(
                "", "end", iid=str(index), tags=tags,
                values=(field(product, "gtin", ""), field(product, "nome", ""), field(product, "marca", "") or "—",
                        field(product, "categoria", "Outros"), money(field(product, "preco", 0)),
                        field(product, "estoque", 0), valid or "—"),
            )
        self.product_table.tag_configure("near_expiry", background=Colors.WARNING_SOFT)
        if self.products:
            self.show_notice(f"{len(self.products)} produto(s) exibido(s).", "info")
        else:
            self.show_notice("Nenhum produto encontrado.", "warning")

    def selected_product(self) -> Mapping[str, Any] | None:
        selection = self.product_table.selection()
        if not selection:
            return None
        return self.products[int(selection[0])]

    def new_product(self) -> None:
        def save(product: ProductData) -> Any:
            response = invoke(self.controller, "save_product_admin", product, int(field(self.user, "id", 0)))
            self.show_notice("Produto salvo com sucesso.", "success")
            self.load_products()
            return response or True

        def lookup(gtin: str) -> Mapping[str, Any]:
            return invoke(self.controller, "scan_product", gtin, int(field(self.user, "id", 0)))

        ProductEditorDialog(self, None, save, on_lookup=lookup)

    def edit_selected_product(self) -> None:
        product = self.selected_product()
        if not product:
            self.show_notice("Selecione um produto para editar.", "warning")
            return

        def save(payload: ProductData) -> Any:
            response = invoke(self.controller, "save_product_admin", payload, int(field(self.user, "id", 0)))
            self.show_notice("Produto atualizado com sucesso.", "success")
            self.load_products()
            return response or True

        ProductEditorDialog(self, product, save)

    def load_users(self) -> None:
        try:
            self.users = list(invoke(self.controller, "admin_users"))
        except Exception as exc:
            self.show_notice(str(exc), "danger")
            return
        self.user_table.delete(*self.user_table.get_children())
        for index, account in enumerate(self.users):
            active = bool(field(account, "ativo", field(account, "active", False)))
            change_required = bool(field(account, "deve_trocar_senha", field(account, "must_change_password", False)))
            self.user_table.insert(
                "", "end", iid=str(index), values=(
                    field(account, "id", ""),
                    field(account, "nome", ""),
                    field(account, "login", ""),
                    str(field(account, "perfil", "")).capitalize(),
                    "Ativo" if active else "Inativo",
                    "Obrigatória" if change_required else "Em dia",
                ),
            )
            if int(field(account, "id", 0)) == int(field(self.user, "id", 0)):
                self._adopt_current_user(account)
        self.show_notice(f"{len(self.users)} usuário(s) carregado(s).", "info")

    def selected_user(self) -> Mapping[str, Any] | None:
        selection = self.user_table.selection()
        if not selection:
            return None
        return self.users[int(selection[0])]

    def new_user(self) -> None:
        def save(name: str, login: str, password: str, role: str) -> Any:
            response = invoke(
                self.controller,
                "create_user_admin",
                name,
                login,
                password,
                role,
                int(field(self.user, "id", 0)),
            )
            self.show_notice("Usuário criado. A troca da senha inicial será exigida no primeiro acesso.", "success")
            self.load_users()
            return response or True

        UserCreateDialog(self, save)

    def reset_selected_user_password(self) -> None:
        account = self.selected_user()
        if not account:
            self.show_notice("Selecione o usuário que precisa recuperar o acesso.", "warning")
            return
        if int(field(account, "id", 0)) == int(field(self.user, "id", 0)):
            self.show_notice("Use a troca de senha da própria conta para alterar sua senha.", "warning")
            return

        def reset(temporary_password: str) -> Any:
            response = invoke(
                self.controller,
                "reset_user_password_admin",
                int(field(account, "id", 0)),
                temporary_password,
                int(field(self.user, "id", 0)),
            )
            self.show_notice("Senha temporária definida. A troca será exigida no próximo acesso.", "success")
            self.load_users()
            return response or True

        UserPasswordResetDialog(self, account, reset)

    def load_cash_closures(self) -> None:
        try:
            closures = list(invoke(self.controller, "admin_cash_closures"))
        except Exception as exc:
            self.show_notice(str(exc), "danger")
            return
        self.closure_table.delete(*self.closure_table.get_children())
        for index, closure in enumerate(closures):
            self.closure_table.insert(
                "", "end", iid=str(index), values=(
                    field(closure, "id", ""),
                    field(closure, "operador", field(closure, "usuario_nome", "—")),
                    field(closure, "data_abertura", "—"),
                    field(closure, "data_fechamento", "—") or "—",
                    money(field(closure, "valor_informado", 0)),
                    money(field(closure, "quebra", 0)),
                    field(closure, "status", "—"),
                ),
            )
        self.show_notice(f"{len(closures)} fechamento(s) carregado(s).", "info")

    def load_financial_report(self) -> None:
        start = self.report_start.get().strip()
        end = self.report_end.get().strip()
        try:
            report = invoke(self.controller, "admin_financial_report", start, end) or {}
        except Exception as exc:
            self.show_notice(str(exc), "danger")
            return
        sales_count = field(report, "quantidade_vendas", field(report, "sales_count", field(report, "vendas", 0)))
        if isinstance(sales_count, (list, tuple)):
            sales_count = len(sales_count)
        total = field(report, "total_vendido", field(report, "revenue", field(report, "total", 0)))
        self.report_values["sales"].set(str(sales_count or 0))
        self.report_values["total"].set(money(total))
        payment_totals = field(report, "por_forma_pagamento", field(report, "payment_totals", [])) or []
        self.payment_report_table.delete(*self.payment_report_table.get_children())
        if isinstance(payment_totals, Mapping):
            payment_totals = [{"forma_pagamento": key, "total": value, "quantidade": "—"} for key, value in payment_totals.items()]
        for index, entry in enumerate(payment_totals):
            self.payment_report_table.insert(
                "", "end", iid=str(index), values=(
                    field(entry, "forma_pagamento", field(entry, "method", "—")),
                    field(entry, "quantidade", field(entry, "count", "—")),
                    money(field(entry, "total", 0)),
                )
            )
        top_products = field(report, "top_produtos", field(report, "top_products", [])) or []
        self.top_products_table.delete(*self.top_products_table.get_children())
        for index, entry in enumerate(top_products):
            self.top_products_table.insert(
                "", "end", iid=str(index), values=(
                    field(entry, "nome_produto", field(entry, "nome", field(entry, "name", "—"))),
                    field(entry, "quantidade", field(entry, "quantity", 0)),
                    money(field(entry, "total", 0)),
                )
            )
        self.show_notice(f"Relatório financeiro de {start} até {end} carregado.", "success")

    def load_audit_logs(self) -> None:
        try:
            records = list(invoke(self.controller, "admin_audit_logs", int(self.audit_limit.get())))
        except Exception as exc:
            self.show_notice(str(exc), "danger")
            return
        self.audit_table.delete(*self.audit_table.get_children())
        for index, record in enumerate(records):
            details = field(record, "detalhes", field(record, "details", ""))
            if isinstance(details, (dict, list)):
                details = json.dumps(details, ensure_ascii=False, sort_keys=True)
            self.audit_table.insert(
                "", "end", iid=str(index), values=(
                    field(record, "criado_em", field(record, "created_at", "—")),
                    field(record, "usuario_login", field(record, "user", "sistema")) or "sistema",
                    field(record, "acao", field(record, "action", "—")),
                    field(record, "entidade", field(record, "entity", "—")),
                    field(record, "entidade_id", field(record, "entity_id", "—")) or "—",
                    str(details or "—"),
                ),
            )
        self.show_notice(f"{len(records)} registro(s) de auditoria carregado(s).", "info")

    def confirm_maintenance(self, operation: str) -> None:
        labels = {
            "VACUUM": ("Compactar banco", "libera espaço não utilizado e deixa a base mais enxuta"),
            "REINDEX": ("Reorganizar índices", "recria os índices para manter as buscas consistentes"),
        }
        title, explanation = labels.get(operation, ("Manutenção do banco", "organiza a base local"))
        message = f"{title}: esta ação {explanation}. Faça um backup e confirme que não há vendas em andamento. Continuar?"

        def execute() -> Any:
            response = invoke(self.controller, "run_maintenance", operation, int(field(self.user, "id", 0)))
            self.show_notice(f"{title} concluído com sucesso.", "success")
            return response or True

        ConfirmDialog(self, f"Confirmar: {title}", message, execute, dangerous=True)

    def confirm_production_preparation(self) -> None:
        if not self.checkout_idle():
            self.show_notice(
                "Finalize ou descarte a venda em andamento antes de limpar os testes.",
                "warning",
            )
            return

        def execute(confirmation: str) -> Any:
            response = invoke(
                self.controller,
                "prepare_for_production",
                confirmation,
                int(field(self.user, "id", 0)),
            )
            self.closure_table.delete(*self.closure_table.get_children())
            self.audit_table.delete(*self.audit_table.get_children())
            self._production_button.configure(
                state="disabled", text="Produção já iniciada"
            )
            self.production_status_var.set(
                "Produção iniciada. A limpeza de treinamento está bloqueada."
            )
            self.refresh_dashboard()
            self.show_notice(
                "Testes removidos com backup verificado. Agora revise preços, faça o inventário real e teste a impressora.",
                "success",
            )
            return response or True

        ProductionPreparationDialog(self, execute)

    def refresh_production_preparation_status(self) -> None:
        try:
            status = invoke(self.controller, "production_preparation_status") or {}
        except Exception as exc:
            self.show_notice(str(exc), "danger")
            return
        if bool(field(status, "prepared", False)):
            self._production_button.configure(
                state="disabled", text="Produção já iniciada"
            )
            self.production_status_var.set(
                "Produção iniciada. A limpeza de treinamento está bloqueada."
            )

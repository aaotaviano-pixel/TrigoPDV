"""Diálogos pequenos do checkout manual e da autorização de exceções."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Mapping
from decimal import Decimal, ROUND_HALF_UP
from tkinter import ttk
from typing import Any

from services.money import decimal_value
from services.errors import ValidationError

from .dialogs import BaseDialog
from .theme import Button, Colors, SectionLabel, font


def _positive_decimal(raw: Any, field: str, scale: str) -> Decimal:
    value = decimal_value(raw, field)
    if not value.is_finite() or value <= 0:
        raise ValueError(f"O {field} deve ser maior que zero.")
    rounded = value.quantize(Decimal(scale), rounding=ROUND_HALF_UP)
    if rounded <= 0 or rounded != value:
        places = 2 if scale == "0.01" else 3
        raise ValueError(f"O {field} aceita no máximo {places} casas decimais.")
    return rounded


class ManualSaleItemDialog(BaseDialog):
    """F2: inclui item avulso sem criar produto ou alterar estoque."""

    def __init__(
        self,
        parent: tk.Misc,
        on_submit: Callable[[Mapping[str, Any]], Any],
    ) -> None:
        super().__init__(
            parent,
            "Item avulso",
            "Use para um item sem cadastro. Ele não será incluído no estoque.",
            width=520,
            height=520,
            scrollable=True,
        )
        self.on_submit = on_submit
        self.description_var = tk.StringVar()
        self.code_var = tk.StringVar()
        self.unit_var = tk.StringVar(value="UN")
        self.quantity_var = tk.StringVar(value="1")
        self.price_var = tk.StringVar()

        for label, variable in (
            ("DESCRIÇÃO *", self.description_var),
            ("CÓDIGO INFORMADO (OPCIONAL)", self.code_var),
        ):
            SectionLabel(self.body, label).pack(fill="x", pady=(8, 5))
            entry = ttk.Entry(self.body, textvariable=variable)
            entry.pack(fill="x")
            if label.startswith("DESCRIÇÃO"):
                self.description_entry = entry

        row = tk.Frame(self.body, bg=Colors.CREAM)
        row.pack(fill="x", pady=(13, 0))
        unit_box = tk.Frame(row, bg=Colors.CREAM)
        unit_box.pack(side="left", fill="x", expand=True, padx=(0, 8))
        SectionLabel(unit_box, "UNIDADE *").pack(fill="x", pady=(0, 5))
        ttk.Combobox(
            unit_box,
            textvariable=self.unit_var,
            values=("UN", "KG"),
            state="readonly",
        ).pack(fill="x")
        quantity_box = tk.Frame(row, bg=Colors.CREAM)
        quantity_box.pack(side="left", fill="x", expand=True)
        SectionLabel(quantity_box, "QUANTIDADE *").pack(fill="x", pady=(0, 5))
        ttk.Entry(quantity_box, textvariable=self.quantity_var).pack(fill="x")

        SectionLabel(self.body, "PREÇO UNITÁRIO *").pack(fill="x", pady=(13, 5))
        self.price_entry = ttk.Entry(self.body, textvariable=self.price_var)
        self.price_entry.pack(fill="x")
        tk.Label(
            self.body,
            text="Até R$ 50,00 em itens avulsos por venda não exige administrador. Acima disso, o sistema solicitará autorização e justificativa.",
            bg=Colors.INFO_SOFT,
            fg=Colors.INFO,
            font=font(9),
            justify="left",
            wraplength=440,
            padx=10,
            pady=8,
        ).pack(fill="x", pady=(14, 0))
        actions = tk.Frame(self.body, bg=Colors.CREAM)
        actions.pack(fill="x", pady=(18, 0))
        Button(actions, "Cancelar", self.cancel, variant="ghost").pack(side="right")
        Button(actions, "Incluir item", self.submit, variant="accent").pack(
            side="right", padx=(0, 8)
        )
        self.bind("<Return>", lambda _event: self.submit())
        self._schedule_after(30, self.description_entry.focus_set)

    def submit(self) -> None:
        description = " ".join(self.description_var.get().split())
        if not description:
            self.show_error("Informe a descrição do item avulso.")
            return
        try:
            quantity = _positive_decimal(self.quantity_var.get(), "quantidade", "0.001")
            price = _positive_decimal(self.price_var.get(), "preço", "0.01")
            if self.unit_var.get() == "UN" and quantity != quantity.to_integral_value():
                raise ValueError("A quantidade em UN deve ser um número inteiro.")
        except (ValueError, TypeError, ValidationError) as exc:
            self.show_error(exc)
            return
        payload = {
            "tipo_lancamento": "MANUAL",
            "gtin": None,
            "codigo_informado": " ".join(self.code_var.get().split()) or None,
            "descricao": description,
            "unidade": self.unit_var.get(),
            "quantidade": f"{quantity:.0f}" if self.unit_var.get() == "UN" else f"{quantity:.3f}",
            "preco_unitario": f"{price:.2f}",
        }
        self._accept(self.on_submit, payload)


class CartItemEditDialog(BaseDialog):
    """F3: altera quantidade e preço da linha selecionada."""

    def __init__(
        self,
        parent: tk.Misc,
        item: Mapping[str, Any],
        on_submit: Callable[[str, str], Any],
    ) -> None:
        super().__init__(
            parent,
            "Editar item",
            str(item.get("name") or item.get("nome") or "Item selecionado"),
            width=500,
            height=390,
        )
        self.item = item
        self.on_submit = on_submit
        self.unit = str(item.get("unit") or item.get("unidade") or "UN").upper()
        self.quantity_var = tk.StringVar(value=str(item.get("quantity") or item.get("quantidade") or "1"))
        self.price_var = tk.StringVar(value=str(item.get("price") or item.get("preco_unitario") or "0"))
        SectionLabel(self.body, "QUANTIDADE *").pack(fill="x", pady=(5, 5))
        self.quantity_entry = ttk.Entry(self.body, textvariable=self.quantity_var)
        self.quantity_entry.pack(fill="x")
        SectionLabel(self.body, "PREÇO UNITÁRIO *").pack(fill="x", pady=(14, 5))
        ttk.Entry(self.body, textvariable=self.price_var).pack(fill="x")
        tk.Label(
            self.body,
            text="Alterar o preço de um produto cadastrado exigirá autorização administrativa antes do pagamento.",
            bg=Colors.INFO_SOFT,
            fg=Colors.INFO,
            font=font(9),
            justify="left",
            wraplength=420,
            padx=10,
            pady=8,
        ).pack(fill="x", pady=(14, 0))
        actions = tk.Frame(self.body, bg=Colors.CREAM)
        actions.pack(fill="x", pady=(18, 0))
        Button(actions, "Cancelar", self.cancel, variant="ghost").pack(side="right")
        Button(actions, "Aplicar", self.submit, variant="accent").pack(side="right", padx=(0, 8))
        self.bind("<Return>", lambda _event: self.submit())
        self._schedule_after(30, self.quantity_entry.focus_set)

    def submit(self) -> None:
        try:
            quantity = _positive_decimal(self.quantity_var.get(), "quantidade", "0.001")
            price = _positive_decimal(self.price_var.get(), "preço", "0.01")
            if self.unit == "UN" and quantity != quantity.to_integral_value():
                raise ValueError("A quantidade em UN deve ser um número inteiro.")
        except (ValueError, TypeError, ValidationError) as exc:
            self.show_error(exc)
            return
        self._accept(
            self.on_submit,
            f"{quantity:.0f}" if self.unit == "UN" else f"{quantity:.3f}",
            f"{price:.2f}",
        )


class SaleAuthorizationDialog(BaseDialog):
    """Solicita justificativa e, para caixa, credenciais administrativas."""

    def __init__(
        self,
        parent: tk.Misc,
        message: str,
        on_submit: Callable[[str, str, str], Any],
        *,
        require_credentials: bool,
    ) -> None:
        super().__init__(
            parent,
            "Autorizar exceção da venda",
            message,
            width=560,
            height=520 if require_credentials else 390,
            scrollable=True,
        )
        self.on_submit = on_submit
        self.require_credentials = require_credentials
        self.login_var = tk.StringVar()
        self.password_var = tk.StringVar()
        if require_credentials:
            SectionLabel(self.body, "LOGIN DO ADMINISTRADOR").pack(fill="x", pady=(4, 5))
            self.login_entry = ttk.Entry(self.body, textvariable=self.login_var)
            self.login_entry.pack(fill="x")
            SectionLabel(self.body, "SENHA").pack(fill="x", pady=(14, 5))
            ttk.Entry(self.body, textvariable=self.password_var, show="●").pack(fill="x")
        SectionLabel(self.body, "JUSTIFICATIVA (8 A 250 CARACTERES)").pack(fill="x", pady=(14, 5))
        self.reason_entry = tk.Text(self.body, height=5, wrap="word", font=font(10))
        self.reason_entry.pack(fill="x")
        actions = tk.Frame(self.body, bg=Colors.CREAM)
        actions.pack(fill="x", pady=(18, 0))
        Button(actions, "Cancelar", self.cancel, variant="ghost").pack(side="right")
        Button(actions, "Autorizar e continuar", self.submit, variant="danger").pack(side="right", padx=(0, 8))
        focus = self.login_entry if require_credentials else self.reason_entry
        self._schedule_after(30, focus.focus_set)

    def submit(self) -> None:
        login = self.login_var.get().strip()
        password = self.password_var.get()
        reason = " ".join(self.reason_entry.get("1.0", "end").split())
        if self.require_credentials and (not login or not password):
            self.show_error("Informe o login e a senha do administrador.")
            return
        if not 8 <= len(reason) <= 250:
            self.show_error("A justificativa deve ter entre 8 e 250 caracteres.")
            return
        self._accept(self.on_submit, login, password, reason)

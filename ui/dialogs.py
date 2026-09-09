"""Modal windows used by the PDV views.

Every dialog owns its keyboard focus, accepts Escape to cancel and returns focus
to its invoking view.  This is especially important with USB barcode readers,
which behave as a very fast keyboard at the checkout counter.
"""

from __future__ import annotations

import tkinter as tk
import secrets
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from tkinter import ttk
from typing import Any

from services.errors import ValidationError
from services.money import money as decimal_money

from .contracts import ProductData, field
from .theme import Button, Card, Colors, SectionLabel, center_window, font, money, parse_money


class BaseDialog(tk.Toplevel):
    """Consistent modal shell with an operator-friendly error area."""

    def __init__(
        self,
        parent: tk.Misc,
        title: str,
        subtitle: str = "",
        *,
        width: int = 500,
        height: int | None = None,
        resizable: bool = False,
        scrollable: bool = True,
    ) -> None:
        super().__init__(parent)
        self.parent = parent
        self.result: Any = None
        host = parent.winfo_toplevel()
        try:
            host.update_idletasks()
        except tk.TclError:
            pass
        host_width = host.winfo_width() if host.winfo_width() > 1 else host.winfo_screenwidth()
        host_height = host.winfo_height() if host.winfo_height() > 1 else host.winfo_screenheight()
        available_width = max(360, min(host.winfo_screenwidth(), host_width) - 24)
        available_height = max(320, min(host.winfo_screenheight(), host_height) - 24)
        effective_width = min(width, available_width)
        effective_height = min(height or 240, available_height)
        self.configure(bg=Colors.CREAM)
        self.title(f"PDV Trigo de Minas — {title}")
        self.minsize(effective_width, min(height or 220, effective_height))
        self.geometry(f"{effective_width}x{effective_height}")
        self.resizable(resizable, resizable)
        self.transient(parent.winfo_toplevel())
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.bind("<Escape>", lambda _event: self.cancel())
        self.bind("<F10>", lambda _event: "break")

        header = tk.Frame(self, bg=Colors.INK, padx=24, pady=18)
        header.pack(fill="x")
        tk.Label(header, text=title, bg=Colors.INK, fg="#FFFFFF", font=font(16, "bold"), anchor="w").pack(fill="x")
        if subtitle:
            tk.Label(header, text=subtitle, bg=Colors.INK, fg="#D5DDD7", font=font(9), anchor="w").pack(fill="x", pady=(4, 0))

        self._scroll_canvas: tk.Canvas | None = None
        self._scroll_body_window: int | None = None
        self._scrollbar: ttk.Scrollbar | None = None
        self._scrollbar_visible = False
        self._pending_after_ids: set[str] = set()
        if scrollable:
            content = tk.Frame(self, bg=Colors.CREAM)
            content.pack(fill="both", expand=True)
            self._scroll_canvas = tk.Canvas(content, bg=Colors.CREAM, highlightthickness=0, bd=0)
            self._scrollbar = ttk.Scrollbar(content, orient="vertical", command=self._scroll_canvas.yview)
            self._scroll_canvas.configure(yscrollcommand=self._scrollbar.set)
            self._scroll_canvas.pack(side="left", fill="both", expand=True)
            self.body = tk.Frame(self._scroll_canvas, bg=Colors.CREAM, padx=24, pady=20)
            self._scroll_body_window = self._scroll_canvas.create_window((0, 0), window=self.body, anchor="nw")
            self.body.bind("<Configure>", self._sync_scroll_region, add="+")
            self._scroll_canvas.bind("<Configure>", self._resize_scroll_body, add="+")
        else:
            self.body = tk.Frame(self, bg=Colors.CREAM, padx=24, pady=20)
            self.body.pack(fill="both", expand=True)
        self.error_var = tk.StringVar(value="")
        self.error_label = tk.Label(
            self.body,
            textvariable=self.error_var,
            bg=Colors.DANGER_SOFT,
            fg=Colors.DANGER,
            font=font(9),
            justify="left",
            anchor="w",
            padx=10,
            pady=7,
            wraplength=max(260, effective_width - 70),
        )
        self._centered = False
        self._schedule_after(10, self._finish_open)

    def _schedule_after(self, delay: int, callback: Callable[[], Any]) -> None:
        token = self.after(delay, lambda: self._run_scheduled(token, callback))
        self._pending_after_ids.add(token)

    def _schedule_idle(self, callback: Callable[[], Any]) -> None:
        token = self.after_idle(lambda: self._run_scheduled(token, callback))
        self._pending_after_ids.add(token)

    def _run_scheduled(self, token: str, callback: Callable[[], Any]) -> None:
        self._pending_after_ids.discard(token)
        if self.winfo_exists():
            callback()

    def _cancel_scheduled(self) -> None:
        for token in tuple(self._pending_after_ids):
            try:
                self.after_cancel(token)
            except tk.TclError:
                pass
        self._pending_after_ids.clear()

    def _sync_scroll_region(self, _event: tk.Event[Any] | None = None) -> None:
        if self._scroll_canvas and self._scroll_body_window is not None:
            self._scroll_canvas.configure(scrollregion=self._scroll_canvas.bbox(self._scroll_body_window))
            self._schedule_idle(self._update_scrollbar)

    def _resize_scroll_body(self, event: tk.Event[Any]) -> None:
        if self._scroll_canvas and self._scroll_body_window is not None:
            self._scroll_canvas.itemconfigure(self._scroll_body_window, width=event.width)
            self._schedule_idle(self._update_scrollbar)

    def _update_scrollbar(self) -> None:
        """Show the vertical scrollbar only when the dialog content needs it."""

        if not self.winfo_exists() or not self._scroll_canvas or not self._scrollbar:
            return
        bounds = self._scroll_canvas.bbox("all")
        content_height = max((bounds[3] - bounds[1]) if bounds else 0, self.body.winfo_reqheight())
        needs_scrollbar = content_height > self._scroll_canvas.winfo_height()
        if needs_scrollbar == self._scrollbar_visible:
            return
        self._scrollbar_visible = needs_scrollbar
        if needs_scrollbar:
            self._scrollbar.pack(side="right", fill="y")
        else:
            self._scrollbar.pack_forget()

    def _scroll_to_error(self) -> None:
        self._update_scrollbar()
        if self._scroll_canvas:
            self._scroll_canvas.yview_moveto(1.0)

    def _finish_open(self) -> None:
        if not self.winfo_exists():
            return
        center_window(self, self.parent.winfo_toplevel())
        self.grab_set()
        self._centered = True

    def show_error(self, message: object) -> None:
        self.error_var.set(str(message) or "Não foi possível concluir a operação.")
        if not self.error_label.winfo_manager():
            self.error_label.pack(fill="x", pady=(12, 0), side="bottom")
        if self._scroll_canvas:
            self._schedule_idle(self._scroll_to_error)

    def clear_error(self) -> None:
        self.error_var.set("")
        if self.error_label.winfo_manager():
            self.error_label.pack_forget()
        if self._scroll_canvas:
            self._schedule_idle(self._update_scrollbar)

    def cancel(self) -> None:
        self._cancel_scheduled()
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()
        focus = getattr(self.parent, "focus_scan", None)
        if callable(focus):
            self.parent.after_idle(focus)

    def destroy(self) -> None:
        self._cancel_scheduled()
        super().destroy()

    def _accept(self, callback: Callable[..., Any], *args: Any) -> None:
        self.clear_error()
        try:
            response = callback(*args)
            if response is False:
                return
        except Exception as exc:  # Services turn domain failures into operator text.
            self.show_error(exc)
            return
        self.result = response
        self.cancel()


def _entry(parent: tk.Misc, variable: tk.StringVar, *, show: str | None = None, width: int | None = None) -> ttk.Entry:
    opts: dict[str, Any] = {"textvariable": variable}
    if show is not None:
        opts["show"] = show
    if width is not None:
        opts["width"] = width
    return ttk.Entry(parent, **opts)


class PricingDialog(BaseDialog):
    """Fast price definition opened after a GTIN was found without a price."""

    def __init__(self, parent: tk.Misc, product: Mapping[str, Any], on_submit: Callable[[float], Any]) -> None:
        super().__init__(parent, "Definir preço", "Produto encontrado; informe o preço para incluí-lo na venda.", width=500, height=350)
        self.product = product
        self.on_submit = on_submit
        card = Card(self.body, padding=15)
        card.pack(fill="x")
        tk.Label(card, text=str(field(product, "nome", "Produto sem nome")), bg=Colors.SURFACE, fg=Colors.INK,
                 font=font(14, "bold"), anchor="w", wraplength=410).pack(fill="x")
        details = " • ".join(part for part in [str(field(product, "marca", "") or ""), f"GTIN {field(product, 'gtin', '')}"] if part)
        tk.Label(card, text=details, bg=Colors.SURFACE, fg=Colors.INK_MUTED, font=font(9), anchor="w").pack(fill="x", pady=(5, 0))

        SectionLabel(self.body, "PREÇO DE VENDA").pack(fill="x", pady=(18, 5))
        row = tk.Frame(self.body, bg=Colors.CREAM)
        row.pack(fill="x")
        tk.Label(row, text="R$", bg=Colors.CREAM, fg=Colors.INK, font=font(13, "bold")).pack(side="left", padx=(2, 8))
        self.price_var = tk.StringVar()
        self.price_entry = _entry(row, self.price_var)
        self.price_entry.pack(side="left", fill="x", expand=True)
        actions = tk.Frame(self.body, bg=Colors.CREAM)
        actions.pack(fill="x", pady=(18, 0))
        Button(actions, "Cancelar", self.cancel, variant="ghost").pack(side="right")
        Button(actions, "Salvar e incluir", self.submit, variant="accent").pack(side="right", padx=(0, 8))
        self.price_entry.bind("<Return>", lambda _event: self.submit())
        self._schedule_after(30, self.price_entry.focus_set)

    def submit(self) -> None:
        try:
            price = parse_money(self.price_var.get())
            if price <= 0:
                raise ValueError("Informe um preço maior que zero.")
        except ValueError as exc:
            self.show_error(exc)
            self.price_entry.focus_set()
            return
        self._accept(self.on_submit, price)


class ManualProductDialog(BaseDialog):
    """Offline/unidentified GTIN fallback that keeps a sale flowing."""

    def __init__(self, parent: tk.Misc, gtin: str, on_submit: Callable[[ProductData], Any]) -> None:
        super().__init__(parent, "Cadastro rápido", "Não localizamos este código. Cadastre o produto para continuar.", width=540, height=510)
        self.gtin = gtin
        self.on_submit = on_submit
        self.name_var = tk.StringVar()
        self.brand_var = tk.StringVar()
        self.price_var = tk.StringVar()
        self._form_row("GTIN", tk.StringVar(value=gtin), readonly=True)
        self.name_entry = self._form_row("NOME DO PRODUTO", self.name_var)
        self._form_row("MARCA (OPCIONAL)", self.brand_var)
        self.price_entry = self._form_row("PREÇO DE VENDA", self.price_var, prefix="R$")
        actions = tk.Frame(self.body, bg=Colors.CREAM)
        actions.pack(fill="x", pady=(18, 0))
        Button(actions, "Cancelar", self.cancel, variant="ghost").pack(side="right")
        Button(actions, "Cadastrar e incluir", self.submit, variant="accent").pack(side="right", padx=(0, 8))
        self.bind("<Return>", lambda _event: self.submit())
        self._schedule_after(30, self.name_entry.focus_set)

    def _form_row(self, label: str, variable: tk.StringVar, *, readonly: bool = False, prefix: str | None = None) -> ttk.Entry:
        SectionLabel(self.body, label).pack(fill="x", pady=(12, 5))
        row = tk.Frame(self.body, bg=Colors.CREAM)
        row.pack(fill="x")
        if prefix:
            tk.Label(row, text=prefix, bg=Colors.CREAM, fg=Colors.INK, font=font(11, "bold")).pack(side="left", padx=(2, 8))
        entry = _entry(row, variable)
        entry.pack(side="left", fill="x", expand=True)
        if readonly:
            entry.state(["readonly"])
        return entry

    def submit(self) -> None:
        name = self.name_var.get().strip()
        try:
            price = parse_money(self.price_var.get())
            if not name:
                raise ValueError("Informe o nome do produto.")
            if price <= 0:
                raise ValueError("Informe um preço maior que zero.")
        except ValueError as exc:
            self.show_error(exc)
            return
        payload: ProductData = {
            "gtin": self.gtin,
            "nome": name,
            "marca": self.brand_var.get().strip(),
            "preco": price,
            # O cadastro rápido só conhece nome e preço. Sem um saldo físico
            # informado, ele não deve bloquear a venda por estoque zero; o
            # administrador pode ativar o controle no editor do produto.
            "estoque_controlado": False,
        }
        self._accept(self.on_submit, payload)


class WeightDialog(BaseDialog):
    """Keyboard-first quantity prompt for products sold by kilogram."""

    def __init__(self, parent: tk.Misc, product: Mapping[str, Any], on_submit: Callable[[float], Any]) -> None:
        super().__init__(parent, "Informar peso", "Produto vendido por quilograma. Informe o peso pesado para incluir na venda.", width=500, height=345)
        self.product = product
        self.on_submit = on_submit
        self.weight_var = tk.StringVar()
        card = Card(self.body, padding=13)
        card.pack(fill="x")
        tk.Label(card, text=str(field(product, "nome", "Produto")), bg=Colors.SURFACE, fg=Colors.INK,
                 font=font(13, "bold"), anchor="w").pack(fill="x")
        tk.Label(card, text=f"{money(field(product, 'preco', 0))} por kg", bg=Colors.SURFACE, fg=Colors.INK_MUTED,
                 font=font(9), anchor="w").pack(fill="x", pady=(4, 0))
        SectionLabel(self.body, "PESO EM KG").pack(fill="x", pady=(17, 5))
        row = tk.Frame(self.body, bg=Colors.CREAM)
        row.pack(fill="x")
        self.weight_entry = _entry(row, self.weight_var)
        self.weight_entry.pack(side="left", fill="x", expand=True)
        tk.Label(row, text="kg", bg=Colors.CREAM, fg=Colors.INK, font=font(11, "bold")).pack(side="left", padx=(8, 2))
        actions = tk.Frame(self.body, bg=Colors.CREAM)
        actions.pack(fill="x", pady=(20, 0))
        Button(actions, "Cancelar", self.cancel, variant="ghost").pack(side="right")
        Button(actions, "Incluir", self.submit, variant="accent").pack(side="right", padx=(0, 8))
        self.weight_entry.bind("<Return>", lambda _event: self.submit())
        self._schedule_after(30, self.weight_entry.focus_set)

    def submit(self) -> None:
        try:
            weight = parse_money(self.weight_var.get())
            if weight <= 0:
                raise ValueError("Informe um peso maior que zero.")
            if weight > 1000:
                raise ValueError("Confira o peso informado antes de incluir o item.")
        except ValueError as exc:
            self.show_error(exc)
            self.weight_entry.focus_set()
            return
        self._accept(self.on_submit, weight)


class SearchDialog(BaseDialog):
    """F1 name search with keyboard-first result selection."""

    def __init__(self, parent: tk.Misc, on_search: Callable[[str], Sequence[Mapping[str, Any]]], on_select: Callable[[Mapping[str, Any]], Any]) -> None:
        super().__init__(parent, "Buscar produto", "Digite parte do nome, marca ou GTIN e pressione Enter.", width=790, height=550, resizable=True)
        self.on_search = on_search
        self.on_select = on_select
        self.query_var = tk.StringVar()
        row = tk.Frame(self.body, bg=Colors.CREAM)
        row.pack(fill="x")
        self.query_entry = _entry(row, self.query_var)
        self.query_entry.pack(side="left", fill="x", expand=True)
        Button(row, "Buscar", self.search, variant="primary").pack(side="left", padx=(8, 0))
        table_frame = tk.Frame(self.body, bg=Colors.CREAM)
        table_frame.pack(fill="both", expand=True, pady=(15, 0))
        columns = ("gtin", "nome", "marca", "preco", "estoque")
        self.table = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        headings = {"gtin": "GTIN", "nome": "Produto", "marca": "Marca", "preco": "Preço", "estoque": "Estoque"}
        widths = {"gtin": 135, "nome": 270, "marca": 125, "preco": 105, "estoque": 90}
        for name in columns:
            self.table.heading(name, text=headings[name])
            self.table.column(name, width=widths[name], anchor="e" if name in {"preco", "estoque"} else "w", stretch=name == "nome")
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        horizontal = ttk.Scrollbar(table_frame, orient="horizontal", command=self.table.xview)
        self.table.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal.set)
        self.table.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        horizontal.pack(side="bottom", fill="x")
        actions = tk.Frame(self.body, bg=Colors.CREAM)
        actions.pack(fill="x", pady=(14, 0))
        tk.Label(actions, text="↑↓ navega  •  Enter seleciona  •  Esc fecha", bg=Colors.CREAM, fg=Colors.INK_MUTED, font=font(9)).pack(side="left")
        Button(actions, "Incluir selecionado", self.select, variant="accent").pack(side="right")
        self.query_entry.bind("<Return>", lambda _event: self.search())
        self.table.bind("<Double-1>", lambda _event: self.select())
        self.table.bind("<Return>", lambda _event: self.select())
        self._schedule_after(30, self.query_entry.focus_set)

    def search(self) -> None:
        query = self.query_var.get().strip()
        self.clear_error()
        try:
            products = self.on_search(query)
        except Exception as exc:
            self.show_error(exc)
            return
        self.table.delete(*self.table.get_children())
        for index, product in enumerate(products):
            self.table.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    field(product, "gtin", ""),
                    field(product, "nome", ""),
                    field(product, "marca", "") or "—",
                    money(field(product, "preco", 0)),
                    field(product, "estoque", 0),
                ),
                tags=("product",),
            )
            self.table.set(str(index), "_payload", "") if "_payload" in self.table["columns"] else None
        self._products = list(products)
        children = self.table.get_children()
        if children:
            self.table.selection_set(children[0])
            self.table.focus(children[0])
            self.table.focus_set()
        elif query:
            self.show_error("Nenhum produto encontrado.")

    def select(self) -> None:
        selection = self.table.selection()
        if not selection:
            self.show_error("Selecione um produto para incluir.")
            return
        index = int(selection[0])
        self._accept(self.on_select, self._products[index])


class PaymentDialog(BaseDialog):
    """F10 payment flow for cash, PIX and card."""

    def __init__(
        self,
        parent: tk.Misc,
        total: Decimal | str | float,
        on_confirm: Callable[[str, Decimal | None], Any],
        *,
        pix_payload: str = "",
    ) -> None:
        super().__init__(parent, "Pagamento", "Conclua a venda após confirmar a forma de pagamento.", width=650, height=570)
        self.total = decimal_money(total, "total")
        self.on_confirm = on_confirm
        self.pix_payload = pix_payload
        self.payment_method = "Dinheiro"
        self._submitting = False
        self.received_var = tk.StringVar()
        self.change_var = tk.StringVar(value=money(0))
        self.card_approved = tk.BooleanVar(value=False)
        self.pix_confirmed_manually = tk.BooleanVar(value=False)

        total_card = Card(self.body, padding=14)
        total_card.pack(fill="x")
        tk.Label(total_card, text="TOTAL A PAGAR", bg=Colors.SURFACE, fg=Colors.INK_MUTED, font=font(9, "bold")).pack(anchor="w")
        tk.Label(total_card, text=money(self.total), bg=Colors.SURFACE, fg=Colors.FOREST, font=font(25, "bold")).pack(anchor="w", pady=(2, 0))

        tab_bar = tk.Frame(self.body, bg=Colors.CREAM)
        tab_bar.pack(fill="x", pady=(16, 0))
        self.tab_buttons: dict[str, Button] = {}
        for method in ("Dinheiro", "PIX", "Cartão"):
            button = Button(tab_bar, method, lambda item=method: self.show_method(item), variant="ghost")
            button.pack(side="left", padx=(0, 7))
            self.tab_buttons[method] = button
        self.pages = tk.Frame(self.body, bg=Colors.CREAM)
        self.pages.pack(fill="both", expand=True, pady=(12, 0))
        self.cash_page = self._build_cash_page()
        self.pix_page = self._build_pix_page()
        self.card_page = self._build_card_page()
        self.method_pages = {"Dinheiro": self.cash_page, "PIX": self.pix_page, "Cartão": self.card_page}

        actions = tk.Frame(self.body, bg=Colors.CREAM)
        actions.pack(fill="x", pady=(10, 0), side="bottom")
        Button(actions, "Cancelar", self.cancel, variant="ghost").pack(side="right")
        self.confirm_button = Button(actions, "Confirmar pagamento", self.confirm, variant="accent")
        self.confirm_button.pack(side="right", padx=(0, 8))
        self.received_var.trace_add("write", self._update_change)
        self.bind("<Return>", lambda _event: self.confirm())
        self.show_method("Dinheiro")

    def _build_cash_page(self) -> tk.Frame:
        page = tk.Frame(self.pages, bg=Colors.CREAM)
        SectionLabel(page, "VALOR RECEBIDO").pack(fill="x", pady=(0, 5))
        row = tk.Frame(page, bg=Colors.CREAM)
        row.pack(fill="x")
        tk.Label(row, text="R$", bg=Colors.CREAM, fg=Colors.INK, font=font(12, "bold")).pack(side="left", padx=(2, 8))
        self.received_entry = _entry(row, self.received_var)
        self.received_entry.pack(side="left", fill="x", expand=True)
        change = Card(page, padding=14)
        change.pack(fill="x", pady=(15, 0))
        tk.Label(change, text="TROCO", bg=Colors.SURFACE, fg=Colors.INK_MUTED, font=font(9, "bold")).pack(anchor="w")
        tk.Label(change, textvariable=self.change_var, bg=Colors.SURFACE, fg=Colors.SUCCESS, font=font(20, "bold")).pack(anchor="w", pady=(2, 0))
        tk.Label(page, text="O valor recebido deve ser igual ou maior que o total da venda.", bg=Colors.CREAM, fg=Colors.INK_MUTED, font=font(9)).pack(anchor="w", pady=(12, 0))
        return page

    def _build_pix_page(self) -> tk.Frame:
        page = tk.Frame(self.pages, bg=Colors.CREAM)
        left = tk.Frame(page, bg=Colors.CREAM)
        left.pack(side="left", fill="both", expand=True)
        tk.Label(left, text="PIX", bg=Colors.CREAM, fg=Colors.FOREST, font=font(18, "bold")).pack(anchor="w")
        tk.Label(left, text="Apresente o QR Code ao cliente. Confira o recebimento fora do PDV e marque a confirmação manual abaixo.",
                 bg=Colors.CREAM, fg=Colors.INK_MUTED, font=font(10), justify="left", wraplength=260).pack(anchor="w", pady=(5, 12))
        tk.Label(left, text="O valor já está preenchido no código PIX.", bg=Colors.INFO_SOFT, fg=Colors.INFO,
                 font=font(9), padx=10, pady=8).pack(anchor="w")
        tk.Label(left, text="CÓDIGO PIX COPIA E COLA", bg=Colors.CREAM, fg=Colors.INK_MUTED,
                 font=font(8, "bold"), anchor="w").pack(anchor="w", pady=(13, 4))
        self.pix_code = tk.Text(left, height=3, bg=Colors.SURFACE, fg=Colors.INK, font=font(8), wrap="word",
                                highlightbackground=Colors.LINE, highlightthickness=1, bd=0, padx=7, pady=6)
        self.pix_code.insert("1.0", self.pix_payload or "PIX indisponível: configure a chave PIX antes de finalizar.")
        self.pix_code.configure(state="disabled")
        self.pix_code.pack(fill="x")
        Button(left, "Copiar código PIX", self.copy_pix, variant="ghost").pack(anchor="w", pady=(7, 0))
        self.pix_copy_var = tk.StringVar()
        tk.Label(left, textvariable=self.pix_copy_var, bg=Colors.CREAM, fg=Colors.SUCCESS, font=font(8), anchor="w").pack(anchor="w", pady=(4, 0))
        self.pix_confirmation = tk.Checkbutton(
            left,
            text="Confirmei manualmente o recebimento do PIX",
            variable=self.pix_confirmed_manually,
            bg=Colors.CREAM,
            fg=Colors.INK,
            activebackground=Colors.CREAM,
            activeforeground=Colors.INK,
            selectcolor=Colors.SURFACE,
            font=font(9, "bold"),
            anchor="w",
            justify="left",
            wraplength=270,
        )
        self.pix_confirmation.pack(anchor="w", pady=(10, 0))
        self.qr_canvas = tk.Canvas(page, width=190, height=190, bg="#FFFFFF", highlightbackground=Colors.LINE, highlightthickness=1)
        self.qr_canvas.pack(side="right", padx=(20, 0))
        self._draw_qr(self.pix_payload)
        return page

    def _build_card_page(self) -> tk.Frame:
        page = tk.Frame(self.pages, bg=Colors.CREAM)
        tk.Label(page, text="Cartão", bg=Colors.CREAM, fg=Colors.FOREST, font=font(18, "bold")).pack(anchor="w")
        tk.Label(page, text="Realize a cobrança na maquininha física. Só confirme após a aprovação do pagamento.",
                 bg=Colors.CREAM, fg=Colors.INK_MUTED, font=font(10), justify="left", wraplength=520).pack(anchor="w", pady=(5, 18))
        approval = tk.Checkbutton(
            page,
            text="Pagamento aprovado na maquininha",
            variable=self.card_approved,
            bg=Colors.CREAM,
            fg=Colors.INK,
            activebackground=Colors.CREAM,
            activeforeground=Colors.INK,
            selectcolor=Colors.SURFACE,
            font=font(10, "bold"),
            anchor="w",
        )
        approval.pack(anchor="w")
        return page

    def _draw_qr(self, payload: str) -> None:
        canvas = self.qr_canvas
        canvas.delete("all")
        if not payload:
            canvas.create_text(95, 84, text="QR Code PIX\nindisponível", fill=Colors.INK_MUTED, font=font(10, "bold"), justify="center")
            canvas.create_text(95, 118, text="Configure a chave PIX", fill=Colors.INK_MUTED, font=font(8), justify="center")
            return
        try:
            import qrcode  # Optional package configured by the application setup.

            code = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, border=0)
            code.add_data(payload)
            code.make(fit=True)
            matrix = code.get_matrix()
            size = len(matrix)
            scale = min(176 / size, 176 / size)
            offset = (190 - size * scale) / 2
            for y, row in enumerate(matrix):
                for x, filled in enumerate(row):
                    if filled:
                        canvas.create_rectangle(offset + x * scale, offset + y * scale, offset + (x + 1) * scale + 0.25,
                                                offset + (y + 1) * scale + 0.25, fill=Colors.INK, outline="")
        except Exception:
            canvas.create_text(95, 80, text="QR Code\nindisponível", fill=Colors.DANGER, font=font(12, "bold"), justify="center")
            canvas.create_text(95, 120, text="Use o código\ncopia e cola", fill=Colors.INK_MUTED, font=font(9), justify="center")

    def copy_pix(self) -> None:
        if not self.pix_payload:
            self.show_error("Configure uma chave PIX válida antes de gerar a cobrança.")
            return
        self.clipboard_clear()
        self.clipboard_append(self.pix_payload)
        self.pix_copy_var.set("Código PIX copiado para a área de transferência.")

    def show_method(self, method: str) -> None:
        self.payment_method = method
        for page in self.method_pages.values():
            page.pack_forget()
        self.method_pages[method].pack(fill="both", expand=True)
        for title, button in self.tab_buttons.items():
            background, foreground, active = Button._VARIANTS["primary" if title == method else "ghost"]
            button.configure(bg=background, fg=foreground, activebackground=active, activeforeground=foreground)
        if method == "Dinheiro":
            self.after_idle(self.received_entry.focus_set)
        elif method == "PIX":
            self.after_idle(self.pix_confirmation.focus_set)
        elif method == "Cartão":
            self.after_idle(lambda: self.confirm_button.focus_set())

    def _update_change(self, *_args: object) -> None:
        try:
            received = decimal_money(self.received_var.get(), "valor recebido")
            change = max(received - self.total, Decimal("0.00"))
            self.change_var.set(money(change))
        except (ValueError, ValidationError):
            self.change_var.set("—")

    def confirm(self) -> None:
        if self._submitting:
            return
        if self.payment_method == "Dinheiro":
            try:
                received = decimal_money(self.received_var.get(), "valor recebido")
                if received < self.total:
                    raise ValueError("O valor recebido é menor que o total da venda.")
            except (ValueError, ValidationError) as exc:
                self.show_error(exc)
                self.received_entry.focus_set()
                return
            self._confirm_once("Dinheiro", received)
            return
        if self.payment_method == "Cartão" and not self.card_approved.get():
            self.show_error("Confirme a aprovação na maquininha antes de concluir a venda.")
            return
        if self.payment_method == "PIX" and not self.pix_payload:
            self.show_error("Não foi possível gerar um PIX válido. Verifique a configuração da chave PIX.")
            return
        if self.payment_method == "PIX" and not self.pix_confirmed_manually.get():
            self.show_error(
                "Marque que o recebimento do PIX foi conferido manualmente. O PDV não consulta o banco."
            )
            self.pix_confirmation.focus_set()
            return
        self._confirm_once(self.payment_method, None)

    def _confirm_once(self, method: str, received: Decimal | None) -> None:
        """Disable the confirmation action until the sale service responds.

        A mouse double click or an impatient Enter press must never submit the
        same cart twice, even when database/printing work takes a moment.
        """

        self._submitting = True
        self.confirm_button.configure(state="disabled", text="Processando…")
        self.clear_error()
        self.update_idletasks()
        try:
            response = self.on_confirm(method, received)
        except Exception as exc:
            self._submitting = False
            self.confirm_button.configure(state="normal", text="Confirmar pagamento")
            self.show_error(exc)
            return
        if response is False:
            self._submitting = False
            self.confirm_button.configure(state="normal", text="Confirmar pagamento")
            return
        self.result = response
        self.cancel()


class CashOpeningDialog(BaseDialog):
    def __init__(self, parent: tk.Misc, on_submit: Callable[[float], Any]) -> None:
        super().__init__(parent, "Abrir caixa", "Informe apenas o fundo inicial disponível no caixa.", width=480, height=310)
        self.on_submit = on_submit
        self.amount_var = tk.StringVar()
        SectionLabel(self.body, "FUNDO INICIAL").pack(fill="x", pady=(5, 5))
        row = tk.Frame(self.body, bg=Colors.CREAM)
        row.pack(fill="x")
        tk.Label(row, text="R$", bg=Colors.CREAM, fg=Colors.INK, font=font(12, "bold")).pack(side="left", padx=(2, 8))
        self.amount_entry = _entry(row, self.amount_var)
        self.amount_entry.pack(side="left", fill="x", expand=True)
        tk.Label(self.body, text="O valor pode ser zero quando não houver troco de abertura.", bg=Colors.CREAM,
                 fg=Colors.INK_MUTED, font=font(9)).pack(anchor="w", pady=(10, 0))
        actions = tk.Frame(self.body, bg=Colors.CREAM)
        actions.pack(fill="x", pady=(18, 0))
        Button(actions, "Cancelar", self.cancel, variant="ghost").pack(side="right")
        Button(actions, "Abrir caixa", self.submit, variant="accent").pack(side="right", padx=(0, 8))
        self.amount_entry.bind("<Return>", lambda _event: self.submit())
        self._schedule_after(30, self.amount_entry.focus_set)

    def submit(self) -> None:
        try:
            value = parse_money(self.amount_var.get())
            if value < 0:
                raise ValueError("O fundo inicial não pode ser negativo.")
        except ValueError as exc:
            self.show_error(exc)
            return
        self._accept(self.on_submit, value)


class CashResumeDialog(BaseDialog):
    """Explicit administrative takeover of the single physical cash drawer."""

    def __init__(self, parent: tk.Misc, cash: Mapping[str, Any], on_submit: Callable[[str], Any]) -> None:
        super().__init__(
            parent,
            "Retomar caixa aberto",
            "Já existe um caixa aberto por outro operador. A retomada exige justificativa e fica registrada.",
            width=540,
            height=390,
        )
        self.cash = cash
        self.on_submit = on_submit
        card = Card(self.body, padding=14)
        card.pack(fill="x")
        tk.Label(
            card,
            text=f"Caixa #{field(cash, 'id', '—')} · aberto em {field(cash, 'data_abertura', '—')}",
            bg=Colors.SURFACE,
            fg=Colors.INK,
            font=font(11, "bold"),
            anchor="w",
        ).pack(fill="x")
        SectionLabel(self.body, "JUSTIFICATIVA (8 A 250 CARACTERES)").pack(fill="x", pady=(16, 5))
        self.reason_entry = tk.Text(
            self.body,
            height=5,
            wrap="word",
            bg=Colors.SURFACE,
            fg=Colors.INK,
            font=font(10),
        )
        self.reason_entry.pack(fill="x")
        actions = tk.Frame(self.body, bg=Colors.CREAM)
        actions.pack(fill="x", pady=(18, 0))
        Button(actions, "Cancelar", self.cancel, variant="ghost").pack(side="right")
        Button(actions, "Retomar este caixa", self.submit, variant="accent").pack(side="right", padx=(0, 8))
        self._schedule_after(30, self.reason_entry.focus_set)

    def submit(self) -> None:
        reason = " ".join(self.reason_entry.get("1.0", "end").split())
        if not 8 <= len(reason) <= 250:
            self.show_error("A justificativa deve ter entre 8 e 250 caracteres.")
            self.reason_entry.focus_set()
            return
        self._accept(self.on_submit, reason)


class CashMovementDialog(BaseDialog):
    def __init__(self, parent: tk.Misc, on_submit: Callable[[str, float, str, str], Any]) -> None:
        super().__init__(parent, "Movimentação de caixa", "Registre uma adição ou retirada de dinheiro e informe o motivo.", width=520, height=470)
        self.on_submit = on_submit
        self.idempotency_key = secrets.token_hex(16)
        self.kind_var = tk.StringVar(value="SANGRIA")
        self.amount_var = tk.StringVar()
        self.note_var = tk.StringVar()
        SectionLabel(self.body, "TIPO").pack(fill="x", pady=(2, 5))
        type_row = tk.Frame(self.body, bg=Colors.CREAM)
        type_row.pack(fill="x")
        for value, label in (("SANGRIA", "Retirar dinheiro"), ("SUPRIMENTO", "Adicionar dinheiro")):
            tk.Radiobutton(type_row, text=label, value=value, variable=self.kind_var, bg=Colors.CREAM, fg=Colors.INK,
                           activebackground=Colors.CREAM, selectcolor=Colors.SURFACE, font=font(10, "bold")).pack(side="left", padx=(0, 16))
        SectionLabel(self.body, "VALOR").pack(fill="x", pady=(15, 5))
        amount_row = tk.Frame(self.body, bg=Colors.CREAM)
        amount_row.pack(fill="x")
        tk.Label(amount_row, text="R$", bg=Colors.CREAM, fg=Colors.INK, font=font(12, "bold")).pack(side="left", padx=(2, 8))
        self.amount_entry = _entry(amount_row, self.amount_var)
        self.amount_entry.pack(side="left", fill="x", expand=True)
        SectionLabel(self.body, "MOTIVO").pack(fill="x", pady=(15, 5))
        self.reason_var = tk.StringVar()
        self.reason_selector = ttk.Combobox(
            self.body,
            textvariable=self.reason_var,
            values=("Troco inicial adicional", "Reforço de troco", "Depósito", "Pagamento autorizado", "Segurança", "Correção autorizada", "Outro motivo"),
            state="normal",
        )
        self.reason_selector.pack(fill="x")
        SectionLabel(self.body, "OBSERVAÇÃO (OPCIONAL)").pack(fill="x", pady=(12, 5))
        self.note_entry = _entry(self.body, self.note_var)
        self.note_entry.pack(fill="x")
        actions = tk.Frame(self.body, bg=Colors.CREAM)
        actions.pack(fill="x", pady=(18, 0))
        Button(actions, "Cancelar", self.cancel, variant="ghost").pack(side="right")
        Button(actions, "Registrar", self.submit, variant="accent").pack(side="right", padx=(0, 8))
        self.note_entry.bind("<Return>", lambda _event: self.submit())
        self._schedule_after(30, self.amount_entry.focus_set)

    def submit(self) -> None:
        reason = self.reason_var.get().strip()
        note = self.note_var.get().strip()
        try:
            amount = parse_money(self.amount_var.get())
            if amount <= 0:
                raise ValueError("Informe um valor maior que zero.")
            if not reason:
                raise ValueError("Escolha ou escreva o motivo da movimentação.")
        except ValueError as exc:
            self.show_error(exc)
            return
        details = reason if not note else f"{reason} — {note}"
        self._accept(
            self.on_submit,
            self.kind_var.get(),
            amount,
            details,
            self.idempotency_key,
        )


class CashActionsDialog(BaseDialog):
    """Presents the two allowed actions for an already-open cash drawer."""

    def __init__(self, parent: tk.Misc, on_movement: Callable[[], None], on_close: Callable[[], None]) -> None:
        super().__init__(
            parent,
            "Operações de caixa",
            "Escolha a operação que deseja registrar.",
            width=520,
            height=430,
            scrollable=True,
        )
        self.on_movement = on_movement
        self.on_close = on_close
        movement = Card(self.body, padding=13)
        movement.pack(fill="x")
        tk.Label(movement, text="Adicionar ou retirar dinheiro", bg=Colors.SURFACE, fg=Colors.INK, font=font(12, "bold")).pack(anchor="w")
        tk.Label(movement, text="Registre retirada ou inclusão de dinheiro com observação.", bg=Colors.SURFACE,
                 fg=Colors.INK_MUTED, font=font(9)).pack(anchor="w", pady=(4, 9))
        Button(movement, "Registrar movimentação", self.select_movement, variant="primary").pack(anchor="w")
        closing = Card(self.body, padding=13)
        closing.pack(fill="x", pady=(11, 0))
        tk.Label(closing, text="Fechamento de caixa", bg=Colors.SURFACE, fg=Colors.INK, font=font(12, "bold")).pack(anchor="w")
        tk.Label(closing, text="Conte o dinheiro e informe somente o valor físico encontrado.", bg=Colors.SURFACE,
                 fg=Colors.INK_MUTED, font=font(9)).pack(anchor="w", pady=(4, 9))
        Button(closing, "Iniciar fechamento", self.select_close, variant="danger").pack(anchor="w")

    def select_movement(self) -> None:
        self.cancel()
        self.on_movement()

    def select_close(self) -> None:
        self.cancel()
        self.on_close()


class CashCloseDialog(BaseDialog):
    """Blind close: it never renders expected amounts or any calculated break."""

    def __init__(self, parent: tk.Misc, on_submit: Callable[[float, str], Mapping[str, Any] | Any]) -> None:
        super().__init__(parent, "Fechamento de caixa", "Conte o dinheiro físico e informe somente o valor contado.", width=540, height=450)
        self.on_submit = on_submit
        self.amount_var = tk.StringVar()
        SectionLabel(self.body, "VALOR FÍSICO CONTADO").pack(fill="x", pady=(2, 5))
        row = tk.Frame(self.body, bg=Colors.CREAM)
        row.pack(fill="x")
        tk.Label(row, text="R$", bg=Colors.CREAM, fg=Colors.INK, font=font(12, "bold")).pack(side="left", padx=(2, 8))
        self.amount_entry = _entry(row, self.amount_var)
        self.amount_entry.pack(side="left", fill="x", expand=True)
        self.justification_label = SectionLabel(self.body, "JUSTIFICATIVA (SE FOR SOLICITADA)")
        self.justification_entry = tk.Text(self.body, height=4, font=font(10), bg=Colors.SURFACE, fg=Colors.INK,
                                           highlightbackground=Colors.LINE, highlightthickness=1, bd=0, padx=10, pady=8)
        self.justification_label.pack(fill="x", pady=(15, 5))
        self.justification_entry.pack(fill="x")
        tk.Label(self.body, text="O sistema não exibirá o valor esperado nem diferenças durante a conferência.",
                 bg=Colors.WARNING_SOFT, fg=Colors.WARNING, font=font(9), justify="left", anchor="w", padx=10, pady=8).pack(fill="x", pady=(12, 0))
        actions = tk.Frame(self.body, bg=Colors.CREAM)
        actions.pack(fill="x", pady=(18, 0))
        Button(actions, "Cancelar", self.cancel, variant="ghost").pack(side="right")
        Button(actions, "Conferir e fechar", self.submit, variant="danger").pack(side="right", padx=(0, 8))
        self.amount_entry.bind("<Return>", lambda _event: self.submit())
        self._schedule_after(30, self.amount_entry.focus_set)

    def submit(self) -> None:
        try:
            amount = parse_money(self.amount_var.get())
            if amount < 0:
                raise ValueError("O valor contado não pode ser negativo.")
        except ValueError as exc:
            self.show_error(exc)
            return
        justification = self.justification_entry.get("1.0", "end").strip()
        self.clear_error()
        try:
            response = self.on_submit(amount, justification)
        except Exception as exc:
            self.show_error(exc)
            return
        if isinstance(response, Mapping) and response.get("requires_justification") and not justification:
            self.show_error("A conferência exige uma justificativa. Descreva o ocorrido para concluir o fechamento.")
            self.justification_entry.focus_set()
            return
        if response is False:
            return
        self.result = response
        self.cancel()


class AdminAuthorizationDialog(BaseDialog):
    """Used for F5 item cancellation when the current operator is not admin."""

    def __init__(self, parent: tk.Misc, item_name: str, on_submit: Callable[[str, str], Any]) -> None:
        super().__init__(parent, "Autorizar cancelamento", f"O cancelamento de “{item_name}” exige credenciais de administrador.", width=520, height=370)
        self.on_submit = on_submit
        self.login_var = tk.StringVar()
        self.password_var = tk.StringVar()
        SectionLabel(self.body, "LOGIN DO ADMINISTRADOR").pack(fill="x", pady=(3, 5))
        self.login_entry = _entry(self.body, self.login_var)
        self.login_entry.pack(fill="x")
        SectionLabel(self.body, "SENHA").pack(fill="x", pady=(14, 5))
        self.password_entry = _entry(self.body, self.password_var, show="●")
        self.password_entry.pack(fill="x")
        actions = tk.Frame(self.body, bg=Colors.CREAM)
        actions.pack(fill="x", pady=(20, 0))
        Button(actions, "Cancelar", self.cancel, variant="ghost").pack(side="right")
        Button(actions, "Autorizar", self.submit, variant="danger").pack(side="right", padx=(0, 8))
        self.password_entry.bind("<Return>", lambda _event: self.submit())
        self._schedule_after(30, self.login_entry.focus_set)

    def submit(self) -> None:
        login = self.login_var.get().strip()
        secret = self.password_var.get()
        if not login or not secret:
            self.show_error("Informe o login e a senha do administrador.")
            return
        self._accept(self.on_submit, login, secret)


class UserCreateDialog(BaseDialog):
    """Administrative account creation without retaining or displaying secrets."""

    def __init__(self, parent: tk.Misc, on_submit: Callable[[str, str, str, str], Any]) -> None:
        super().__init__(
            parent,
            "Novo usuário",
            "A senha inicial será trocada pelo usuário no primeiro acesso.",
            width=480,
            height=620,
            scrollable=True,
        )
        self.on_submit = on_submit
        self.name_var = tk.StringVar()
        self.login_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.confirmation_var = tk.StringVar()
        self.role_var = tk.StringVar(value="caixa")

        for label, variable, hidden in (
            ("NOME *", self.name_var, False),
            ("LOGIN *", self.login_var, False),
            ("SENHA INICIAL *", self.password_var, True),
            ("CONFIRME A SENHA *", self.confirmation_var, True),
        ):
            SectionLabel(self.body, label).pack(fill="x", pady=(10, 5))
            entry = _entry(self.body, variable, show="●" if hidden else None)
            entry.pack(fill="x")
            if label == "NOME *":
                self.name_entry = entry

        SectionLabel(self.body, "PERFIL *").pack(fill="x", pady=(10, 5))
        self.role_combo = ttk.Combobox(
            self.body,
            textvariable=self.role_var,
            values=("caixa", "admin"),
            state="readonly",
        )
        self.role_combo.pack(fill="x")
        tk.Label(
            self.body,
            text="Use o perfil Administrador somente para quem deve gerir produtos, relatórios e usuários.",
            bg=Colors.CREAM,
            fg=Colors.INK_MUTED,
            font=font(8),
            justify="left",
            wraplength=395,
        ).pack(anchor="w", pady=(6, 0))
        actions = tk.Frame(self.body, bg=Colors.CREAM)
        actions.pack(fill="x", pady=(18, 0))
        Button(actions, "Cancelar", self.cancel, variant="ghost").pack(side="right")
        Button(actions, "Criar usuário", self.submit, variant="accent").pack(side="right", padx=(0, 8))
        self.bind("<Return>", lambda _event: self.submit())
        self._schedule_after(30, self.name_entry.focus_set)

    def submit(self) -> None:
        name = self.name_var.get().strip()
        login = self.login_var.get().strip()
        password = self.password_var.get()
        confirmation = self.confirmation_var.get()
        if not name or not login or not password:
            self.show_error("Informe nome, login e senha inicial.")
            return
        if len(password) < 8:
            self.show_error("A senha inicial deve ter pelo menos 8 caracteres.")
            return
        if password != confirmation:
            self.show_error("A confirmação da senha não coincide.")
            return
        self._accept(self.on_submit, name, login, password, self.role_var.get())


class UserPasswordResetDialog(BaseDialog):
    """Administrative password reset that always requires a first-login change."""

    def __init__(self, parent: tk.Misc, user: Mapping[str, Any], on_submit: Callable[[str], Any]) -> None:
        name = str(field(user, "nome", field(user, "login", "usuário")))
        login = str(field(user, "login", ""))
        super().__init__(
            parent,
            "Redefinir senha",
            f"Defina uma senha temporária para {name} ({login}). A troca será obrigatória no próximo acesso.",
            width=520,
            height=430,
        )
        self.on_submit = on_submit
        self.password_var = tk.StringVar()
        self.confirmation_var = tk.StringVar()
        SectionLabel(self.body, "SENHA TEMPORÁRIA *").pack(fill="x", pady=(4, 5))
        self.password_entry = _entry(self.body, self.password_var, show="●")
        self.password_entry.pack(fill="x")
        SectionLabel(self.body, "CONFIRME A SENHA *").pack(fill="x", pady=(14, 5))
        self.confirmation_entry = _entry(self.body, self.confirmation_var, show="●")
        self.confirmation_entry.pack(fill="x")
        tk.Label(
            self.body,
            text="A senha não será exibida nem registrada em texto puro.",
            bg=Colors.INFO_SOFT,
            fg=Colors.INFO,
            font=font(9),
            justify="left",
            anchor="w",
            padx=10,
            pady=8,
        ).pack(fill="x", pady=(14, 0))
        actions = tk.Frame(self.body, bg=Colors.CREAM)
        actions.pack(fill="x", pady=(18, 0))
        Button(actions, "Cancelar", self.cancel, variant="ghost").pack(side="right")
        Button(actions, "Redefinir senha", self.submit, variant="danger").pack(side="right", padx=(0, 8))
        self.confirmation_entry.bind("<Return>", lambda _event: self.submit())
        self._schedule_after(30, self.password_entry.focus_set)

    def submit(self) -> None:
        password = self.password_var.get()
        confirmation = self.confirmation_var.get()
        if len(password) < 8:
            self.show_error("A senha temporária deve ter pelo menos 8 caracteres.")
            return
        if password != confirmation:
            self.show_error("A confirmação da senha não coincide.")
            return
        self._accept(self.on_submit, password)


class PasswordRecoveryDialog(BaseDialog):
    """Recuperação de senha administrativa com código local rotativo."""

    def __init__(self, parent: tk.Misc, login: str, on_submit: Callable[[str, str, str, str], Any]) -> None:
        available_height = max(
            420, min(670, parent.winfo_toplevel().winfo_height() - 30)
        )
        super().__init__(
            parent,
            "Recuperar acesso",
            "Confirme o código de recuperação que foi guardado pelo administrador. Ao concluir, informe e guarde um novo código.",
            width=560,
            height=available_height,
            scrollable=True,
        )
        self.on_submit = on_submit
        self.login_var = tk.StringVar(value=login)
        self.code_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.confirmation_var = tk.StringVar()
        self.new_code_var = tk.StringVar()
        self.new_code_confirmation_var = tk.StringVar()
        for label, variable, hidden, readonly in (
            ("USUÁRIO ADMINISTRADOR", self.login_var, False, True),
            ("CÓDIGO DE RECUPERAÇÃO ATUAL", self.code_var, True, False),
            ("NOVA SENHA", self.password_var, True, False),
            ("CONFIRME A NOVA SENHA", self.confirmation_var, True, False),
            ("NOVO CÓDIGO DE RECUPERAÇÃO", self.new_code_var, True, False),
            ("CONFIRME O NOVO CÓDIGO", self.new_code_confirmation_var, True, False),
        ):
            SectionLabel(self.body, label).pack(fill="x", pady=(10, 5))
            entry = _entry(self.body, variable, show="●" if hidden else None)
            entry.pack(fill="x")
            if readonly:
                entry.state(["readonly"])
            if label == "CÓDIGO DE RECUPERAÇÃO ATUAL":
                self.code_entry = entry
        tk.Label(
            self.body,
            text="O código deve ter ao menos 12 caracteres. Ele não é exibido nem registrado em texto puro e será substituído nesta recuperação.",
            bg=Colors.INFO_SOFT,
            fg=Colors.INFO,
            font=font(9),
            justify="left",
            anchor="w",
            wraplength=470,
            padx=10,
            pady=8,
        ).pack(fill="x", pady=(14, 0))
        actions = tk.Frame(self.body, bg=Colors.CREAM)
        actions.pack(fill="x", pady=(18, 0))
        Button(actions, "Cancelar", self.cancel, variant="ghost").pack(side="right")
        Button(actions, "Redefinir acesso", self.submit, variant="accent").pack(side="right", padx=(0, 8))
        self.bind("<Return>", lambda _event: self.submit())
        self._schedule_after(30, self.code_entry.focus_set)

    def submit(self) -> None:
        if self.password_var.get() != self.confirmation_var.get():
            self.show_error("A confirmação da nova senha não confere.")
            return
        if self.new_code_var.get() != self.new_code_confirmation_var.get():
            self.show_error("A confirmação do novo código não confere.")
            return
        self._accept(
            self.on_submit,
            self.login_var.get().strip(),
            self.code_var.get(),
            self.password_var.get(),
            self.new_code_var.get(),
        )


class RecoveryCodeSetupDialog(BaseDialog):
    """Configura ou rotaciona o codigo do proprio administrador autenticado."""

    def __init__(
        self,
        parent: tk.Misc,
        recovery_code: str,
        on_submit: Callable[[str, str], Any],
    ) -> None:
        available_height = max(
            420, min(590, parent.winfo_toplevel().winfo_height() - 30)
        )
        super().__init__(
            parent,
            "Proteção de recuperação",
            "Guarde o novo código em local seguro e confirme-o exatamente antes de salvar.",
            width=560,
            height=available_height,
            scrollable=True,
        )
        self.on_submit = on_submit
        self.current_password_var = tk.StringVar()
        self.recovery_code_var = tk.StringVar(value=str(recovery_code))
        self.confirmation_var = tk.StringVar()

        SectionLabel(self.body, "SENHA ATUAL DO ADMINISTRADOR").pack(fill="x", pady=(3, 5))
        self.current_password_entry = _entry(
            self.body, self.current_password_var, show="●"
        )
        self.current_password_entry.pack(fill="x")
        SectionLabel(self.body, "NOVO CÓDIGO DE RECUPERAÇÃO").pack(
            fill="x", pady=(16, 5)
        )
        self.code_entry = _entry(self.body, self.recovery_code_var)
        self.code_entry.pack(fill="x")
        self.code_entry.state(["readonly"])
        tk.Label(
            self.body,
            text=(
                "Este código aparece somente nesta etapa. Copie e guarde fora do computador do caixa. "
                "O sistema armazena apenas uma verificação protegida."
            ),
            bg=Colors.WARNING_SOFT,
            fg=Colors.WARNING,
            font=font(9),
            justify="left",
            anchor="w",
            wraplength=470,
            padx=10,
            pady=9,
        ).pack(fill="x", pady=(12, 0))
        SectionLabel(self.body, "DIGITE O CÓDIGO NOVAMENTE").pack(
            fill="x", pady=(16, 5)
        )
        self.confirmation_entry = _entry(
            self.body, self.confirmation_var, show="●"
        )
        self.confirmation_entry.pack(fill="x")
        actions = tk.Frame(self.body, bg=Colors.CREAM)
        actions.pack(fill="x", pady=(20, 0))
        Button(actions, "Cancelar", self.cancel, variant="ghost").pack(side="right")
        Button(actions, "Salvar recuperação", self.submit, variant="accent").pack(
            side="right", padx=(0, 8)
        )
        self.confirmation_entry.bind("<Return>", lambda _event: self.submit())
        self._schedule_after(30, self.current_password_entry.focus_set)

    def submit(self) -> None:
        code = self.recovery_code_var.get()
        if not secrets.compare_digest(code, self.confirmation_var.get()):
            self.show_error("A confirmação do código não confere.")
            return
        self._accept(self.on_submit, self.current_password_var.get(), code)

    def cancel(self) -> None:
        self.current_password_var.set("")
        self.recovery_code_var.set("")
        self.confirmation_var.set("")
        super().cancel()


class PasswordChangeDialog(BaseDialog):
    """Mandatory first-login password change for default/expired accounts."""

    def __init__(
        self,
        parent: tk.Misc,
        user_name: str,
        on_submit: Callable[[str, str], Any],
        on_abort: Callable[[], None],
    ) -> None:
        super().__init__(
            parent,
            "Troca de senha obrigatória",
            f"Olá, {user_name}. Defina uma nova senha antes de acessar o PDV.",
            width=535,
            height=460,
        )
        self.on_submit = on_submit
        self.on_abort = on_abort
        self._completed = False
        self.current_var = tk.StringVar()
        self.new_var = tk.StringVar()
        self.confirm_var = tk.StringVar()
        SectionLabel(self.body, "SENHA ATUAL").pack(fill="x", pady=(2, 5))
        self.current_entry = _entry(self.body, self.current_var, show="●")
        self.current_entry.pack(fill="x")
        SectionLabel(self.body, "NOVA SENHA").pack(fill="x", pady=(14, 5))
        self.new_entry = _entry(self.body, self.new_var, show="●")
        self.new_entry.pack(fill="x")
        SectionLabel(self.body, "CONFIRME A NOVA SENHA").pack(fill="x", pady=(14, 5))
        self.confirm_entry = _entry(self.body, self.confirm_var, show="●")
        self.confirm_entry.pack(fill="x")
        tk.Label(
            self.body,
            text="Use ao menos 8 caracteres e não reutilize a senha padrão.",
            bg=Colors.INFO_SOFT,
            fg=Colors.INFO,
            font=font(9),
            padx=10,
            pady=8,
            anchor="w",
        ).pack(fill="x", pady=(13, 0))
        actions = tk.Frame(self.body, bg=Colors.CREAM)
        actions.pack(fill="x", pady=(18, 0))
        Button(actions, "Sair", self.abort, variant="ghost").pack(side="right")
        Button(actions, "Salvar nova senha", self.submit, variant="accent").pack(side="right", padx=(0, 8))
        self.confirm_entry.bind("<Return>", lambda _event: self.submit())
        self._schedule_after(30, self.current_entry.focus_set)

    def cancel(self) -> None:
        """Escape and the window close button do not reveal operational screens."""

        if self._completed:
            super().cancel()
            return
        self.show_error("A troca de senha é obrigatória para continuar. Use “Sair” para voltar ao login.")

    def abort(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()
        self.on_abort()

    def submit(self) -> None:
        current = self.current_var.get()
        new_password = self.new_var.get()
        confirmation = self.confirm_var.get()
        if not current:
            self.show_error("Informe a senha atual.")
            self.current_entry.focus_set()
            return
        if len(new_password) < 8:
            self.show_error("A nova senha deve ter pelo menos 8 caracteres.")
            self.new_entry.focus_set()
            return
        if new_password != confirmation:
            self.show_error("A confirmação não coincide com a nova senha.")
            self.confirm_entry.focus_set()
            return
        self.clear_error()
        try:
            response = self.on_submit(current, new_password)
        except Exception as exc:
            self.show_error(exc)
            return
        if response is False:
            return
        self.result = response
        self._completed = True
        super().cancel()


class ProductEditorDialog(BaseDialog):
    """Admin product creation/editing dialog with stock and validity fields."""

    def __init__(
        self,
        parent: tk.Misc,
        product: Mapping[str, Any] | None,
        on_submit: Callable[[ProductData], Any],
        *,
        on_lookup: Callable[[str], Mapping[str, Any]] | None = None,
    ) -> None:
        creating = product is None
        title = "Novo produto" if creating else "Editar produto"
        super().__init__(parent, title, "Campos com * são obrigatórios.", width=600, height=700, resizable=True)
        self.product = product or {}
        self.on_submit = on_submit
        self.on_lookup = on_lookup if creating else None
        self.vars = {
            "gtin": tk.StringVar(value=str(field(self.product, "gtin", ""))),
            "nome": tk.StringVar(value=str(field(self.product, "nome", ""))),
            "marca": tk.StringVar(value=str(field(self.product, "marca", "") or "")),
            "preco": tk.StringVar(value=str(field(self.product, "preco", "") or "")),
            "estoque": tk.StringVar(value=str(field(self.product, "estoque", "") or "")),
            "data_validade": tk.StringVar(value=str(field(self.product, "data_validade", "") or "")),
            "unidade": tk.StringVar(value=str(field(self.product, "unidade", "un") or "un").lower()),
            "categoria": tk.StringVar(value=str(field(self.product, "categoria", "Outros") or "Outros")),
            "subcategoria": tk.StringVar(value=str(field(self.product, "subcategoria", "") or "")),
            "detalhes_embalagem": tk.StringVar(value=str(field(self.product, "detalhes_embalagem", "") or "")),
        }
        self.item_balcao_var = tk.BooleanVar(value=bool(field(self.product, "item_balcao", False)))
        self.estoque_controlado_var = tk.BooleanVar(value=bool(field(self.product, "estoque_controlado", True)))
        self.entries: dict[str, ttk.Entry] = {}
        self._row("GTIN / CÓDIGO INTERNO *", "gtin", readonly=not creating)
        if self.on_lookup:
            lookup_row = tk.Frame(self.body, bg=Colors.CREAM)
            lookup_row.pack(fill="x", pady=(7, 0))
            Button(lookup_row, "Preencher pela Cosmos  Ctrl+F11", self.lookup_gtin, variant="ghost").pack(side="left")
        self._row("NOME *", "nome")
        self._row("MARCA", "marca")
        self._category_row()
        self._row("SUBCATEGORIA", "subcategoria")
        self._row("EMBALAGEM / PESO / VOLUME", "detalhes_embalagem")
        self._row("PREÇO *", "preco", prefix="R$")
        self._unit_row()
        self._row("ESTOQUE", "estoque")
        self._row("VALIDADE (AAAA-MM-DD)", "data_validade")
        options = tk.Frame(self.body, bg=Colors.CREAM)
        options.pack(fill="x", pady=(12, 0))
        tk.Checkbutton(
            options,
            text="Item de balcão (exibir entre os botões rápidos do caixa)",
            variable=self.item_balcao_var,
            bg=Colors.CREAM,
            fg=Colors.INK,
            activebackground=Colors.CREAM,
            activeforeground=Colors.INK,
            selectcolor=Colors.SURFACE,
            font=font(9, "bold"),
            anchor="w",
        ).pack(anchor="w")
        tk.Checkbutton(
            options,
            text="Controlar estoque deste produto",
            variable=self.estoque_controlado_var,
            bg=Colors.CREAM,
            fg=Colors.INK,
            activebackground=Colors.CREAM,
            activeforeground=Colors.INK,
            selectcolor=Colors.SURFACE,
            font=font(9, "bold"),
            anchor="w",
        ).pack(anchor="w", pady=(5, 0))
        actions = tk.Frame(self.body, bg=Colors.CREAM)
        actions.pack(fill="x", pady=(20, 0))
        Button(actions, "Cancelar", self.cancel, variant="ghost").pack(side="right")
        Button(actions, "Salvar produto", self.submit, variant="accent").pack(side="right", padx=(0, 8))
        self.bind("<Control-Return>", lambda _event: self.submit())
        self.bind("<Control-F11>", self.lookup_gtin)
        self._schedule_after(30, (self.entries["nome"] if not creating else self.entries["gtin"]).focus_set)

    def lookup_gtin(self, _event: tk.Event[Any] | None = None) -> str:
        """Completa o cadastro a partir de um GTIN sem definir preço da loja."""

        if not self.on_lookup:
            return "break"
        gtin = self.vars["gtin"].get().strip()
        if not gtin:
            self.show_error("Digite ou bipse o GTIN antes de consultar.")
            self.entries["gtin"].focus_set()
            return "break"
        self.clear_error()
        try:
            response = self.on_lookup(gtin)
        except Exception as exc:
            self.show_error(exc)
            return "break"
        product = field(response, "product")
        status = str(field(response, "status", "")).upper()
        if not isinstance(product, Mapping) or status in {"OFFLINE", "MANUAL_ENTRY_REQUIRED", "INACTIVE"}:
            self.show_error(str(field(response, "message", "Produto não encontrado na consulta.")))
            return "break"
        self.vars["gtin"].set(str(field(product, "gtin", gtin)))
        self.vars["nome"].set(str(field(product, "nome", "")))
        self.vars["marca"].set(str(field(product, "marca", "") or ""))
        self.clear_error()
        self.entries["preco"].focus_set()
        return "break"

    def _row(self, label: str, key: str, *, prefix: str | None = None, readonly: bool = False) -> None:
        SectionLabel(self.body, label).pack(fill="x", pady=(9, 4))
        frame = tk.Frame(self.body, bg=Colors.CREAM)
        frame.pack(fill="x")
        if prefix:
            tk.Label(frame, text=prefix, bg=Colors.CREAM, fg=Colors.INK, font=font(11, "bold")).pack(side="left", padx=(2, 8))
        entry = _entry(frame, self.vars[key])
        entry.pack(side="left", fill="x", expand=True)
        if readonly:
            entry.state(["readonly"])
        self.entries[key] = entry

    def _unit_row(self) -> None:
        SectionLabel(self.body, "UNIDADE DE VENDA").pack(fill="x", pady=(9, 4))
        row = tk.Frame(self.body, bg=Colors.CREAM)
        row.pack(fill="x")
        selector = ttk.Combobox(row, textvariable=self.vars["unidade"], values=("un", "kg"), state="readonly", width=12)
        selector.pack(side="left")
        tk.Label(
            row,
            text="Use kg para itens pesados por balança, como pães e frios.",
            bg=Colors.CREAM,
            fg=Colors.INK_MUTED,
            font=font(9),
        ).pack(side="left", padx=(10, 0))

    def _category_row(self) -> None:
        SectionLabel(self.body, "CATEGORIA").pack(fill="x", pady=(9, 4))
        row = tk.Frame(self.body, bg=Colors.CREAM)
        row.pack(fill="x")
        selector = ttk.Combobox(
            row,
            textvariable=self.vars["categoria"],
            values=("Padaria", "Pães", "Salgados", "Doces", "Bolos", "Lanches", "Bebidas", "Refrigerantes", "Sucos", "Águas", "Cafés", "Laticínios", "Frios", "Biscoitos", "Chocolates", "Conveniência", "Ingredientes", "Congelados", "Outros"),
            state="readonly",
        )
        selector.pack(fill="x", expand=True)

    def submit(self) -> None:
        try:
            gtin = self.vars["gtin"].get().strip()
            name = self.vars["nome"].get().strip()
            price = parse_money(self.vars["preco"].get())
            stock_value = self.vars["estoque"].get().strip()
            stock = parse_money(stock_value) if stock_value else 0.0
            if not gtin:
                raise ValueError("Informe o GTIN do produto.")
            if not name:
                raise ValueError("Informe o nome do produto.")
            if price < 0 or stock < 0:
                raise ValueError("Preço e estoque não podem ser negativos.")
        except ValueError as exc:
            self.show_error(exc)
            return
        payload: ProductData = {
            "gtin": gtin,
            "nome": name,
            "marca": self.vars["marca"].get().strip(),
            "preco": price,
            "estoque": stock,
            "data_validade": self.vars["data_validade"].get().strip() or None,
            "unidade": self.vars["unidade"].get(),
            "categoria": self.vars["categoria"].get(),
            "subcategoria": self.vars["subcategoria"].get().strip(),
            "detalhes_embalagem": self.vars["detalhes_embalagem"].get().strip(),
            "item_balcao": self.item_balcao_var.get(),
            "estoque_controlado": self.estoque_controlado_var.get(),
        }
        self._accept(self.on_submit, payload)


class ConfirmDialog(BaseDialog):
    """Simple themed confirmation dialog for non-reversible UI actions."""

    def __init__(self, parent: tk.Misc, title: str, message: str, on_confirm: Callable[[], Any], *, dangerous: bool = False) -> None:
        super().__init__(parent, title, "Confirme a ação para continuar.", width=490, height=290)
        self.on_confirm = on_confirm
        tk.Label(self.body, text=message, bg=Colors.CREAM, fg=Colors.INK, font=font(11), justify="left", anchor="w",
                 wraplength=430).pack(fill="x", pady=(3, 10))
        actions = tk.Frame(self.body, bg=Colors.CREAM)
        actions.pack(fill="x", pady=(18, 0))
        Button(actions, "Cancelar", self.cancel, variant="ghost").pack(side="right")
        Button(actions, "Confirmar", self.submit, variant="danger" if dangerous else "accent").pack(side="right", padx=(0, 8))
        self.bind("<Return>", lambda _event: self.submit())

    def submit(self) -> None:
        self._accept(self.on_confirm)


class ProductionPreparationDialog(BaseDialog):
    """One-time, typed confirmation for removing training operations."""

    CONFIRMATION = "INICIAR PRODUCAO"

    def __init__(self, parent: tk.Misc, on_submit: Callable[[str], Any]) -> None:
        super().__init__(
            parent,
            "Preparar para produção",
            "Use uma única vez, depois dos testes e antes da primeira venda real.",
            width=560,
            height=560,
        )
        self.on_submit = on_submit
        self.confirmation_var = tk.StringVar()

        tk.Label(
            self.body,
            text=(
                "O sistema fará um backup automático e depois removerá vendas, "
                "caixas, movimentações, comprovantes e auditoria de treinamento."
            ),
            bg=Colors.CREAM,
            fg=Colors.INK,
            font=font(10),
            justify="left",
            anchor="w",
            wraplength=490,
        ).pack(fill="x")
        tk.Label(
            self.body,
            text=(
                "Produtos, preços, configurações e usuários serão mantidos. O estoque "
                "voltará a zero e ficará sem bloqueio até o inventário real. Esta ação "
                "fica bloqueada para sempre após a primeira execução."
            ),
            bg=Colors.WARNING_SOFT,
            fg=Colors.WARNING,
            font=font(9, "bold"),
            justify="left",
            anchor="w",
            padx=12,
            pady=10,
            wraplength=470,
        ).pack(fill="x", pady=(14, 0))
        SectionLabel(self.body, f"DIGITE {self.CONFIRMATION}").pack(
            fill="x", pady=(18, 5)
        )
        self.confirmation_entry = _entry(self.body, self.confirmation_var)
        self.confirmation_entry.pack(fill="x")
        actions = tk.Frame(self.body, bg=Colors.CREAM)
        actions.pack(fill="x", pady=(22, 0))
        Button(actions, "Cancelar", self.cancel, variant="ghost").pack(side="right")
        Button(actions, "Criar backup e limpar testes", self.submit, variant="danger").pack(
            side="right", padx=(0, 8)
        )
        self.confirmation_entry.bind("<Return>", lambda _event: self.submit())
        self._schedule_after(30, self.confirmation_entry.focus_set)

    def submit(self) -> None:
        confirmation = " ".join(self.confirmation_var.get().strip().upper().split())
        if confirmation != self.CONFIRMATION:
            self.show_error(f"Digite exatamente {self.CONFIRMATION} para continuar.")
            self.confirmation_entry.focus_set()
            return
        self._accept(self.on_submit, confirmation)

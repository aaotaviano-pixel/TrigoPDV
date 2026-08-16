"""Mandatory first-use assistant for creating the initial administrator."""

from __future__ import annotations

import hmac
import tkinter as tk
from collections.abc import Callable
from tkinter import ttk
from typing import Any

from .contracts import invoke
from .theme import Button, Card, Colors, MONO_FONT, SectionLabel, font


def validate_setup_identity(
    name: str, login: str, password: str, password_confirmation: str
) -> tuple[str, str, str]:
    """Validate non-visual first-step rules without requiring a Tk display."""

    normalized_name = name.strip()
    normalized_login = login.strip()
    if not normalized_name or not normalized_login:
        raise ValueError("Informe o nome completo e o usuário do administrador.")
    if not password:
        raise ValueError("Informe a senha do administrador.")
    if password != password_confirmation:
        raise ValueError("As senhas informadas não conferem.")
    return normalized_name, normalized_login, password


def recovery_code_matches(code: str, confirmation: str) -> bool:
    """Compare the backend code exactly, preserving whitespace and case."""

    return hmac.compare_digest(code, confirmation)


class SetupView(tk.Frame):
    """Collects the first identity and confirms a backend-generated code."""

    def __init__(
        self,
        master: tk.Misc,
        controller: object,
        on_complete: Callable[[], None],
        on_exit: Callable[[], None],
    ) -> None:
        super().__init__(master, bg=Colors.CREAM)
        self.controller = controller
        self.on_complete = on_complete
        self.on_exit = on_exit
        self.name_var = tk.StringVar()
        self.login_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.password_confirmation_var = tk.StringVar()
        self.recovery_code_var = tk.StringVar()
        self.recovery_confirmation_var = tk.StringVar()
        self.error_var = tk.StringVar()
        self._code_generated = False
        self._submitting = False
        self._completed = False
        self._pending_after_ids: set[str] = set()
        self._build()

    def _build(self) -> None:
        header = tk.Frame(self, bg=Colors.INK, padx=28, pady=18)
        header.pack(fill="x")
        tk.Label(
            header,
            text="Configuração inicial",
            bg=Colors.INK,
            fg="#FFFFFF",
            font=font(19, "bold"),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            header,
            text="Crie o primeiro administrador antes de acessar o caixa.",
            bg=Colors.INK,
            fg="#D5DDD7",
            font=font(10),
            anchor="w",
        ).pack(fill="x", pady=(4, 0))

        scroll_wrap = tk.Frame(self, bg=Colors.CREAM)
        scroll_wrap.pack(fill="both", expand=True)
        self.scroll_canvas = tk.Canvas(
            scroll_wrap, bg=Colors.CREAM, highlightthickness=0, bd=0, takefocus=True
        )
        self.scrollbar = ttk.Scrollbar(
            scroll_wrap, orient="vertical", command=self.scroll_canvas.yview
        )
        self.scroll_canvas.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side="right", fill="y")
        self.scroll_canvas.pack(side="left", fill="both", expand=True)
        self.body = tk.Frame(self.scroll_canvas, bg=Colors.CREAM, padx=28, pady=22)
        self._body_window = self.scroll_canvas.create_window(
            (0, 0), window=self.body, anchor="nw"
        )
        self.body.bind("<Configure>", self._sync_scroll_region, add="+")
        self.scroll_canvas.bind("<Configure>", self._resize_body, add="+")
        self.scroll_canvas.bind("<MouseWheel>", self._scroll_wheel, add="+")
        self.bind("<Up>", lambda _event: self._scroll_units(-1), add="+")
        self.bind("<Down>", lambda _event: self._scroll_units(1), add="+")
        self.bind("<Prior>", lambda _event: self._scroll_pages(-1), add="+")
        self.bind("<Next>", lambda _event: self._scroll_pages(1), add="+")

        intro = Card(self.body, padding=17)
        intro.pack(fill="x")
        tk.Label(
            intro,
            text="Administrador responsável",
            bg=Colors.SURFACE,
            fg=Colors.INK,
            font=font(15, "bold"),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            intro,
            text=(
                "Use dados exclusivos do responsável pela instalação. Depois, anote o "
                "código de recuperação e guarde-o em local seguro."
            ),
            bg=Colors.SURFACE,
            fg=Colors.INK_MUTED,
            font=font(10),
            justify="left",
            anchor="w",
            wraplength=720,
        ).pack(fill="x", pady=(6, 0))

        form = Card(self.body, padding=17)
        form.pack(fill="x", pady=(14, 0))
        self.name_entry = self._field(form, "NOME COMPLETO", self.name_var)
        self.login_entry = self._field(form, "USUÁRIO", self.login_var)
        self.password_entry = self._field(form, "SENHA", self.password_var, show="●")
        self.password_confirmation_entry = self._field(
            form, "CONFIRME A SENHA", self.password_confirmation_var, show="●"
        )

        self.error_label = tk.Label(
            self.body,
            textvariable=self.error_var,
            bg=Colors.DANGER_SOFT,
            fg=Colors.DANGER,
            font=font(9),
            justify="left",
            anchor="w",
            padx=12,
            pady=9,
            wraplength=740,
        )

        self.initial_actions = tk.Frame(self.body, bg=Colors.CREAM)
        self.initial_actions.pack(fill="x", pady=(15, 0))
        self.cancel_button = Button(
            self.initial_actions, "Sair", self.cancel, variant="ghost"
        )
        self.cancel_button.pack(side="right")
        self.continue_button = Button(
            self.initial_actions,
            "Continuar",
            self.continue_to_confirmation,
            variant="accent",
        )
        self.continue_button.pack(side="right", padx=(0, 8))

        self.confirmation_panel = Card(self.body, padding=17)
        tk.Label(
            self.confirmation_panel,
            text="Código de recuperação",
            bg=Colors.SURFACE,
            fg=Colors.INK,
            font=font(15, "bold"),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            self.confirmation_panel,
            text=(
                "Este código é exibido uma única vez. Anote exatamente como aparece e "
                "digite-o abaixo para confirmar que foi guardado."
            ),
            bg=Colors.SURFACE,
            fg=Colors.INK_MUTED,
            font=font(10),
            justify="left",
            anchor="w",
            wraplength=700,
        ).pack(fill="x", pady=(6, 12))
        self.recovery_code_label = tk.Label(
            self.confirmation_panel,
            text="",
            bg=Colors.INFO_SOFT,
            fg=Colors.INFO,
            font=font(12, "bold", MONO_FONT),
            justify="center",
            padx=12,
            pady=12,
            wraplength=690,
        )
        self.recovery_code_label.pack(fill="x")
        self.recovery_confirmation_entry = self._field(
            self.confirmation_panel,
            "REPITA O CÓDIGO EXATAMENTE",
            self.recovery_confirmation_var,
        )
        final_actions = tk.Frame(self.confirmation_panel, bg=Colors.SURFACE)
        final_actions.pack(fill="x", pady=(16, 0))
        self.finish_button = Button(
            final_actions, "Concluir configuração", self.finish_setup, variant="accent"
        )
        self.finish_button.pack(side="right")
        Button(final_actions, "Sair", self.cancel, variant="ghost").pack(
            side="right", padx=(0, 8)
        )

        for control in (
            self.name_entry,
            self.login_entry,
            self.password_entry,
            self.password_confirmation_entry,
            self.continue_button,
            self.cancel_button,
            self.recovery_confirmation_entry,
            self.finish_button,
        ):
            control.configure(takefocus=True)
            control.bind("<MouseWheel>", self._scroll_wheel, add="+")
            control.bind("<Prior>", lambda _event: self._scroll_pages(-1), add="+")
            control.bind("<Next>", lambda _event: self._scroll_pages(1), add="+")

        self.name_entry.bind(
            "<Return>", lambda _event: self.login_entry.focus_set(), add="+"
        )
        self.login_entry.bind(
            "<Return>", lambda _event: self.password_entry.focus_set(), add="+"
        )
        self.password_entry.bind(
            "<Return>", lambda _event: self.password_confirmation_entry.focus_set(), add="+"
        )
        self.password_confirmation_entry.bind(
            "<Return>", lambda _event: self.continue_to_confirmation(), add="+"
        )
        self.recovery_confirmation_entry.bind(
            "<Return>", lambda _event: self.finish_setup(), add="+"
        )
        self._schedule_idle(self.name_entry.focus_set)

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

    def _field(
        self,
        parent: tk.Misc,
        label: str,
        variable: tk.StringVar,
        *,
        show: str | None = None,
    ) -> ttk.Entry:
        SectionLabel(parent, label).pack(fill="x", pady=(12, 5))
        options: dict[str, Any] = {"textvariable": variable, "takefocus": True}
        if show is not None:
            options["show"] = show
        entry = ttk.Entry(parent, **options)
        entry.pack(fill="x")
        return entry

    def _sync_scroll_region(self, _event: tk.Event[Any] | None = None) -> None:
        bounds = self.scroll_canvas.bbox(self._body_window)
        if bounds:
            self.scroll_canvas.configure(scrollregion=bounds)

    def _resize_body(self, event: tk.Event[Any]) -> None:
        self.scroll_canvas.itemconfigure(self._body_window, width=event.width)
        self._sync_scroll_region()

    def _scroll_wheel(self, event: tk.Event[Any]) -> str:
        if event.delta:
            self.scroll_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

    def _scroll_units(self, amount: int) -> str:
        self.scroll_canvas.yview_scroll(amount, "units")
        return "break"

    def _scroll_pages(self, amount: int) -> str:
        self.scroll_canvas.yview_scroll(amount, "pages")
        return "break"

    def _show_error(self, message: object) -> None:
        self.error_var.set(str(message) or "Não foi possível concluir a configuração.")
        if not self.error_label.winfo_manager():
            if self.confirmation_panel.winfo_manager():
                self.error_label.pack(fill="x", pady=(14, 0), before=self.confirmation_panel)
            else:
                self.error_label.pack(fill="x", pady=(14, 0), before=self.initial_actions)
        self._schedule_idle(self._scroll_to_error)

    def _clear_error(self) -> None:
        self.error_var.set("")
        if self.error_label.winfo_manager():
            self.error_label.pack_forget()

    def _scroll_to_error(self) -> None:
        if self.winfo_exists():
            self.update_idletasks()
            self._sync_scroll_region()
            bounds = self.scroll_canvas.bbox(self._body_window)
            if not bounds:
                return
            content_height = max(bounds[3] - bounds[1], 1)
            viewport_height = self.scroll_canvas.winfo_height()
            label_top = self.error_label.winfo_y()
            max_offset = max(content_height - viewport_height, 0)
            target_offset = min(max(label_top - 8, 0), max_offset)
            self.scroll_canvas.yview_moveto(target_offset / content_height)

    def _validate_identity(self) -> bool:
        try:
            validate_setup_identity(
                self.name_var.get(),
                self.login_var.get(),
                self.password_var.get(),
                self.password_confirmation_var.get(),
            )
        except ValueError as exc:
            self._show_error(exc)
            return False
        return True

    def continue_to_confirmation(self) -> None:
        if self._code_generated or self._completed:
            if self._code_generated:
                self.recovery_confirmation_entry.focus_set()
            return
        self._clear_error()
        if not self._validate_identity():
            return
        try:
            code = invoke(self.controller, "generate_recovery_code")
            if not isinstance(code, str) or not code:
                raise RuntimeError("O código de recuperação não pôde ser gerado.")
        except Exception as exc:
            self._show_error(exc)
            return
        self._code_generated = True
        self.recovery_code_var.set(code)
        self.recovery_code_label.configure(text=code)
        self.initial_actions.pack_forget()
        self.confirmation_panel.pack(fill="x", pady=(14, 0))
        self._sync_scroll_region()
        self._schedule_idle(self.recovery_confirmation_entry.focus_set)

    def finish_setup(self) -> None:
        if self._submitting or self._completed:
            return
        self._clear_error()
        if not self._code_generated or not self._validate_identity():
            return
        if not recovery_code_matches(
            self.recovery_code_var.get(), self.recovery_confirmation_var.get()
        ):
            self._show_error("O código repetido não confere. Digite-o exatamente como exibido.")
            self.recovery_confirmation_entry.focus_set()
            return
        self._submitting = True
        self.finish_button.configure(state="disabled")
        try:
            invoke(
                self.controller,
                "provision_initial_admin",
                self.name_var.get().strip(),
                self.login_var.get().strip(),
                self.password_var.get(),
                self.recovery_code_var.get(),
            )
        except Exception as exc:
            self._submitting = False
            self.finish_button.configure(state="normal")
            self._show_error(exc)
            return
        self._completed = True
        self._clear_sensitive_fields()
        self.on_complete()

    def _clear_sensitive_fields(self) -> None:
        self.name_var.set("")
        self.login_var.set("")
        self.password_var.set("")
        self.password_confirmation_var.set("")
        self.recovery_code_var.set("")
        self.recovery_confirmation_var.set("")
        self.recovery_code_label.configure(text="")

    def cancel(self) -> None:
        self.on_exit()

    def destroy(self) -> None:
        self._cancel_scheduled()
        self._clear_sensitive_fields()
        super().destroy()

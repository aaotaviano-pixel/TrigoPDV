"""Tk application coordinator for the PDV desktop interface.

The bootstrap supplies one controller object.  It can be a small adapter over
the project's Auth/Product/Cash/Sale services and does not need to know about
any widgets in this package.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Mapping
from typing import Any

from .contracts import UserData, field, invoke
from .dialogs import ConfirmDialog, PasswordChangeDialog
from .setup import SetupView
from .theme import Colors, configure_style
from .views import AdminView, LoginView, SaleView


class PDVApplication(tk.Tk):
    """Application shell that owns navigation, sessions and global shortcuts."""

    def __init__(self, controller: object) -> None:
        super().__init__()
        self.controller = controller
        self.current_user: Mapping[str, Any] | None = None
        self.current_view: tk.Frame | None = None
        self.sale_view: SaleView | None = None
        self.admin_view: AdminView | None = None
        self.title("PDV Trigo de Minas")
        self.configure(bg=Colors.CREAM)
        # Dimensiona pela área útil do monitor. Em resoluções menores a janela
        # não sai da tela; em telas maiores aproveita espaço sem esconder os
        # comandos do caixa.
        screen_w = max(1, self.winfo_screenwidth())
        screen_h = max(1, self.winfo_screenheight())
        # Keep the familiar outer margin when the display has room for it,
        # but never invent a larger screen or force the root outside a compact
        # 720x520 (or smaller) display.
        usable_w = screen_w - 40 if screen_w > 760 else screen_w
        usable_h = screen_h - 80 if screen_h > 600 else screen_h
        width = min(1280, usable_w)
        height = min(800, usable_h)
        self.minsize(min(900, width), min(600, height))
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.protocol("WM_DELETE_WINDOW", self.request_exit)
        configure_style(self)
        self._bind_shortcuts()
        self.show_initial_route()
        self._update_start_after_id = self.after(2000, self._start_update_monitor)

    def _start_update_monitor(self) -> None:
        self._update_start_after_id = None
        start = getattr(self.controller, "start_update_monitor", None)
        if callable(start):
            try:
                start()
            except Exception:
                # Atualização é auxiliar e offline-first: venda e login seguem
                # disponíveis quando a internet ou o repositório falham.
                pass

    def _bind_shortcuts(self) -> None:
        # ``bind_all`` keeps barcode/keyboard operation reliable regardless of
        # which checkout child currently owns focus. Modal toplevels are
        # intentionally excluded below so Esc/F10 retain their local meaning.
        self.bind_all("<F1>", self._shortcut_f1, add="+")
        self.bind_all("<F2>", self._shortcut_f2, add="+")
        self.bind_all("<F3>", self._shortcut_f3, add="+")
        self.bind_all("<Control-F11>", self._shortcut_gtin_lookup, add="+")
        self.bind_all("<F5>", self._shortcut_f5, add="+")
        self.bind_all("<F10>", self._shortcut_f10, add="+")
        self.bind_all("<Escape>", self._shortcut_escape, add="+")

    @staticmethod
    def _in_child_toplevel(event: tk.Event[Any], root: tk.Tk) -> bool:
        try:
            return event.widget.winfo_toplevel() is not root
        except tk.TclError:
            return True

    def _shortcut_f1(self, event: tk.Event[Any]) -> str | None:
        if self._in_child_toplevel(event, self):
            return "break"
        if isinstance(self.current_view, SaleView):
            return self.current_view.shortcut_search(event)
        return None

    def _shortcut_f2(self, event: tk.Event[Any]) -> str | None:
        if self._in_child_toplevel(event, self):
            return "break"
        if isinstance(self.current_view, SaleView):
            return self.current_view.shortcut_manual_item(event)
        return None

    def _shortcut_f3(self, event: tk.Event[Any]) -> str | None:
        if self._in_child_toplevel(event, self):
            return "break"
        if isinstance(self.current_view, SaleView):
            return self.current_view.shortcut_edit(event)
        return None

    def _shortcut_gtin_lookup(self, event: tk.Event[Any]) -> str | None:
        """Consulta o GTIN já digitado sem tirar a mão do teclado."""

        if self._in_child_toplevel(event, self):
            return "break"
        if isinstance(self.current_view, SaleView):
            return self.current_view.handle_gtin(event)
        return None

    def _shortcut_f5(self, event: tk.Event[Any]) -> str | None:
        if self._in_child_toplevel(event, self):
            return "break"
        if isinstance(self.current_view, SaleView):
            return self.current_view.shortcut_cancel(event)
        return None

    def _shortcut_f10(self, event: tk.Event[Any]) -> str | None:
        if self._in_child_toplevel(event, self):
            return "break"
        if isinstance(self.current_view, SaleView):
            return self.current_view.shortcut_payment(event)
        return None

    def _shortcut_escape(self, event: tk.Event[Any]) -> str | None:
        # BaseDialog handles Escape on the toplevel itself. Stopping propagation
        # here prevents an already-closing dialog from triggering a sale action.
        if self._in_child_toplevel(event, self):
            return "break"
        if isinstance(self.current_view, SetupView):
            self.request_exit()
            return "break"
        if isinstance(self.current_view, SaleView):
            self.current_view.focus_scan()
            return "break"
        return None

    def _replace_view(self, view: tk.Frame) -> None:
        if self.current_view and self.current_view is not view and self.current_view.winfo_exists():
            self.current_view.destroy()
        self.current_view = view
        view.pack(fill="both", expand=True)

    def _requires_initial_setup(self) -> bool:
        status = invoke(self.controller, "installation_status")
        return bool(field(status, "requires_provisioning", False))

    def _clear_session_views(self) -> None:
        # The sale screen may be hidden behind the admin workspace; dispose it
        # explicitly so a completed/logout session cannot retain a cart.
        if self.current_user:
            logout = getattr(self.controller, "logout", None)
            if callable(logout):
                try:
                    logout()
                except Exception:
                    # Rendering a login screen must stay possible even if a
                    # local session cleanup encounters a transient DB error.
                    pass
        for old_view in (self.sale_view, self.admin_view):
            if old_view and old_view.winfo_exists():
                old_view.destroy()
        self.current_user = None
        self.sale_view = None
        self.admin_view = None

    def _show_setup_view(self) -> None:
        self._clear_session_views()
        self._replace_view(
            SetupView(self, self.controller, self.show_login, self.request_exit)
        )

    def _show_login_view(self) -> None:
        self._clear_session_views()
        self._replace_view(LoginView(self, self.controller, self.on_authenticated))

    def show_initial_route(self) -> None:
        if self._requires_initial_setup():
            self._show_setup_view()
            return
        self._show_login_view()

    def show_login(self) -> None:
        # Rechecking here keeps programmatic navigation and cancellation from
        # bypassing mandatory first-use setup.
        if self._requires_initial_setup():
            self._show_setup_view()
            return
        self._show_login_view()

    def on_authenticated(self, user: UserData) -> None:
        self.current_user = user
        if self._must_change_password(user):
            self._prompt_password_change(user)
            return
        self.show_sale(user)

    @staticmethod
    def _must_change_password(user: Mapping[str, Any]) -> bool:
        value = field(user, "deve_trocar_senha", field(user, "must_change_password", False))
        if isinstance(value, str):
            return value.strip().lower() not in {"", "0", "false", "nao", "não", "none"}
        return bool(value)

    def _prompt_password_change(self, user: UserData) -> None:
        name = str(field(user, "nome", field(user, "login", "usuário")))

        def change(current_password: str, new_password: str) -> Any:
            response = invoke(
                self.controller,
                "change_password",
                int(field(user, "id", 0)),
                current_password,
                new_password,
            )
            updated = dict(user)
            updated["deve_trocar_senha"] = False
            self.current_user = updated
            self.after_idle(lambda: self.show_sale(updated))
            return response or True

        PasswordChangeDialog(self, name, change, self.show_login)

    def show_sale(self, user: Mapping[str, Any] | None = None) -> None:
        if self._requires_initial_setup():
            self._show_setup_view()
            return
        active_user = user or self.current_user
        if not active_user:
            self.show_login()
            return
        try:
            cash = invoke(self.controller, "get_open_cash", int(field(active_user, "id", 0)))
        except Exception:
            # A user can still enter the checkout and explicitly open a new
            # cash drawer. Service failures surface in the relevant workflow.
            cash = None
        global_cash = None
        if cash is None:
            try:
                global_cash = invoke(self.controller, "get_global_open_cash")
            except Exception:
                global_cash = None
        self.current_user = active_user
        self.sale_view = SaleView(
            self,
            self.controller,
            active_user,
            cash,
            on_logout=self.request_logout,
            on_open_admin=self.show_admin,
            # Fechar o caixa encerra somente o turno financeiro. A sessão do
            # usuário permanece aberta para consultar o resultado ou abrir um
            # novo turno autorizado, sem voltar à tela de login.
            on_cash_closed=self._after_cash_closed,
        )
        self._replace_view(self.sale_view)
        if global_cash:
            if str(field(active_user, "perfil", "")).lower() == "admin":
                self.after_idle(
                    lambda: self.sale_view
                    and self.sale_view.prompt_resume_cash(global_cash)
                )
            else:
                self.after_idle(
                    lambda: self.sale_view
                    and self.sale_view.notice.show(
                        "O caixa físico já está aberto por outro operador. Chame um administrador para retomar ou fechar o turno anterior.",
                        "warning",
                        ttl=10000,
                    )
                )

    def _after_cash_closed(self) -> None:
        if self.sale_view and self.sale_view.winfo_exists():
            self.sale_view.focus_scan()

    def show_admin(self) -> None:
        if self._requires_initial_setup():
            self._show_setup_view()
            return
        if not self.current_user or str(field(self.current_user, "perfil", "")).lower() != "admin":
            return
        if self.sale_view and self.sale_view.winfo_exists():
            self.sale_view.pack_forget()
        self.admin_view = AdminView(self, self.controller, self.current_user, self.back_to_sale)
        self.current_view = self.admin_view
        self.admin_view.pack(fill="both", expand=True)

    def back_to_sale(self) -> None:
        if self.admin_view and self.admin_view.winfo_exists():
            self.admin_view.destroy()
        self.admin_view = None
        if self.sale_view and self.sale_view.winfo_exists():
            self.current_view = self.sale_view
            self.sale_view.pack(fill="both", expand=True)
            self.sale_view.focus_scan()
        else:
            self.show_login()

    def request_logout(self) -> None:
        if self.sale_view and self.sale_view.cart:
            ConfirmDialog(
                self,
                "Sair da operação",
                "Há itens na venda atual. Sair irá descartar este carrinho que ainda não foi pago.",
                self.show_login,
                dangerous=True,
            )
            return
        self.show_login()

    def request_exit(self) -> None:
        if isinstance(self.current_view, SetupView):
            ConfirmDialog(
                self,
                "Fechar configuração",
                "O PDV continuará bloqueado até a configuração inicial ser concluída.",
                self.destroy,
                dangerous=True,
            )
            return
        if self.sale_view and self.sale_view.cart:
            ConfirmDialog(
                self,
                "Fechar PDV",
                "Há uma venda em andamento. Fechar agora descartará os itens ainda não finalizados.",
                self.destroy,
                dangerous=True,
            )
            return
        self.destroy()

    def destroy(self) -> None:
        pending = getattr(self, "_update_start_after_id", None)
        if pending:
            try:
                self.after_cancel(pending)
            except tk.TclError:
                pass
            self._update_start_after_id = None
        if not getattr(self, "_controller_shutdown", False):
            self._controller_shutdown = True
            shutdown = getattr(self.controller, "shutdown", None)
            if callable(shutdown):
                try:
                    shutdown()
                except Exception:
                    # O Windows ainda precisa conseguir encerrar a interface;
                    # jobs pendentes continuam duráveis no SQLite.
                    pass
        super().destroy()


def launch(controller: object) -> None:
    """Create and run the desktop application from the project bootstrap."""

    app = PDVApplication(controller)
    app.mainloop()

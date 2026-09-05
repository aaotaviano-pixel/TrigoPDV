from __future__ import annotations

import tempfile
import tkinter as tk
import threading
import time
from decimal import Decimal
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from tkinter import ttk

from db.database import Database
from desktop_controller import DesktopController
from services.pdv_service import PDVService
from ui.app import PDVApplication
from ui.dialogs import CashActionsDialog, CashCloseDialog, CashResumeDialog, PasswordRecoveryDialog, PaymentDialog, ProductEditorDialog, ProductionPreparationDialog, RecoveryCodeSetupDialog, SearchDialog, UserCreateDialog, UserPasswordResetDialog
from tests.support import (
    TEST_ADMIN_LOGIN,
    TEST_ADMIN_PASSWORD,
    provision_test_admin,
)


def _find_button(parent: tk.Misc, label: str) -> tk.Button:
    for child in parent.winfo_children():
        try:
            if isinstance(child, tk.Button) and child.cget("text") == label:
                return child
        except tk.TclError:
            continue
        try:
            return _find_button(child, label)
        except LookupError:
            pass
    raise LookupError(label)


def _has_scrollbar(parent: tk.Misc) -> bool:
    if isinstance(parent, ttk.Scrollbar):
        return True
    return any(_has_scrollbar(child) for child in parent.winfo_children())


def _assert_visible(test: unittest.TestCase, dialog: tk.Toplevel, widget: tk.Widget) -> None:
    test.assertGreaterEqual(widget.winfo_rooty(), dialog.winfo_rooty())
    test.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(), dialog.winfo_rooty() + dialog.winfo_height())


class UIAccessibilityTestCase(unittest.TestCase):
    """Regression tests for actions that must remain reachable in the desktop UI."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.controller = DesktopController(PDVService(database=Database(Path(self.tempdir.name) / "pdv.sqlite3")))
        provision_test_admin(self.controller.service.database)
        admin = self.controller.authenticate(TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD)
        assert admin is not None
        self.admin = dict(admin)
        self.admin["deve_trocar_senha"] = False
        try:
            self.app = PDVApplication(self.controller)
        except tk.TclError as exc:
            self.tempdir.cleanup()
            self.skipTest(f"Tk indisponível neste ambiente: {exc}")
        self.app.on_authenticated(self.admin)
        self.app.geometry("1060x680+0+0")
        self.app.update()

    def tearDown(self) -> None:
        if hasattr(self, "app") and self.app.winfo_exists():
            self.app.destroy()
        self.tempdir.cleanup()

    def test_cash_close_action_and_confirmation_are_reachable(self) -> None:
        sale = self.app.sale_view
        assert sale is not None
        sale.cash = self.controller.open_cash(self.admin["id"], 0)
        sale.refresh_cash_status()
        sale.open_cash_actions()
        self.app.update_idletasks()

        cash_actions = next(child for child in sale.winfo_children() if isinstance(child, CashActionsDialog))
        self.assertGreaterEqual(cash_actions.winfo_height(), 430)
        self.assertTrue(_has_scrollbar(cash_actions))
        close_action = _find_button(cash_actions, "Iniciar fechamento")
        _assert_visible(self, cash_actions, close_action)
        close_action.invoke()
        self.app.update()

        closing = next(child for child in sale.winfo_children() if isinstance(child, CashCloseDialog))
        confirm = _find_button(closing, "Conferir e fechar")
        _assert_visible(self, closing, confirm)

    def test_cash_close_keeps_user_session_open(self) -> None:
        sale = self.app.sale_view
        assert sale is not None
        self.assertIsNotNone(self.app.current_user)
        self.assertIsNotNone(sale.on_cash_closed)
        sale.on_cash_closed()
        self.app.update_idletasks()
        self.assertIsNotNone(self.app.current_user)
        self.assertIs(self.app.current_view, sale)

    def test_admin_is_prompted_to_resume_the_single_open_cash_with_reason(self) -> None:
        cashier = self.controller.create_user_admin(
            "Operadora do turno", "operadora.turno", "SenhaTemporaria8", "caixa", self.admin["id"]
        )
        with self.controller.service.database.transaction(write=True) as connection:
            connection.execute(
                "UPDATE usuarios SET deve_trocar_senha = 0 WHERE id = ?", (cashier["id"],)
            )
        opened = self.controller.service.cash.open_cash(
            cashier["id"], 25, actor_id=self.admin["id"]
        )

        self.app.show_sale(self.admin)
        self.app.update()
        sale = self.app.sale_view
        assert sale is not None
        dialog = next(
            child for child in sale.winfo_children() if isinstance(child, CashResumeDialog)
        )
        dialog.reason_entry.insert("1.0", "Troca de operador no início do turno")
        dialog.submit()
        self.app.update()

        self.assertFalse(dialog.winfo_exists())
        self.assertEqual(int(sale.cash["id"]), int(opened["id"]))
        self.assertIn("ABERTO", sale.cash_status_var.get())

    def test_second_copy_action_uses_the_last_sale_without_changing_cart(self) -> None:
        sale = self.app.sale_view
        assert sale is not None
        sale._last_sale_id = 42
        sale.second_copy_button.configure(state="normal")
        original_cart = list(sale.cart)
        self.controller.queue_second_copy = Mock(return_value={"id": 7, "tipo": "SEGUNDA_VIA"})

        sale.request_second_copy()

        self.controller.queue_second_copy.assert_called_once()
        args, kwargs = self.controller.queue_second_copy.call_args
        self.assertEqual(args[:2], (42, self.admin["id"]))
        self.assertGreaterEqual(len(kwargs["idempotency_key"]), 16)
        self.assertEqual(sale.cart, original_cart)

    def test_admin_maintenance_uses_simple_labels(self) -> None:
        self.app.show_admin()
        self.app.update_idletasks()
        admin_view = self.app.admin_view
        assert admin_view is not None
        self.assertIsNotNone(_find_button(admin_view, "Compactar banco"))
        self.assertIsNotNone(_find_button(admin_view, "Reorganizar índices"))

    def test_production_preparation_is_reachable_and_blocks_after_use(self) -> None:
        self.app.show_admin()
        admin_view = self.app.admin_view
        assert admin_view is not None
        admin_view.notebook.select(admin_view.cash_tab)
        self.app.update()

        action = _find_button(admin_view, "Limpar testes e iniciar produção")
        self.assertTrue(_has_scrollbar(admin_view.cash_tab))
        admin_view.cash_scroll_canvas.yview_moveto(1.0)
        self.app.update()
        self.assertTrue(action.winfo_viewable())
        _assert_visible(self, self.app, action)
        action.invoke()
        self.app.update()

        dialog = next(
            child
            for child in admin_view.winfo_children()
            if isinstance(child, ProductionPreparationDialog)
        )
        dialog.confirmation_var.set("INICIAR PRODUCAO")
        confirm = _find_button(dialog, "Criar backup e limpar testes")
        _assert_visible(self, dialog, confirm)
        confirm.invoke()
        self.app.update()

        self.assertEqual(action.cget("state"), "disabled")
        self.assertTrue(self.controller.production_preparation_status()["prepared"])

    def test_justification_error_is_scrolled_into_view(self) -> None:
        sale = self.app.sale_view
        assert sale is not None
        dialog = CashCloseDialog(sale, lambda _amount, _justification: {"requires_justification": True})
        self.app.update()
        dialog.amount_var.set("10")
        dialog.submit()
        self.app.update()

        self.assertIn("exige uma justificativa", dialog.error_var.get())
        self.assertTrue(_has_scrollbar(dialog))
        _assert_visible(self, dialog, dialog.error_label)
        _assert_visible(self, dialog, dialog.justification_entry)

    def test_user_create_tab_and_confirmation_are_reachable(self) -> None:
        self.app.show_admin()
        self.app.update_idletasks()
        admin_view = self.app.admin_view
        assert admin_view is not None
        tabs = [admin_view.notebook.tab(tab, "text").strip() for tab in admin_view.notebook.tabs()]
        self.assertIn("Usuários", tabs)

        admin_view.new_user()
        self.app.update()
        dialog = next(child for child in admin_view.winfo_children() if isinstance(child, UserCreateDialog))
        self.assertGreaterEqual(dialog.winfo_height(), 620)
        self.assertTrue(_has_scrollbar(dialog))
        dialog.name_var.set("Nova Operadora")
        dialog.login_var.set("nova.operadora")
        dialog.password_var.set("SenhaSegura8")
        dialog.confirmation_var.set("SenhaSegura8")
        create = _find_button(dialog, "Criar usuário")
        _assert_visible(self, dialog, create)
        create.invoke()
        self.app.update()

        created = next(user for user in self.controller.admin_users() if user["login"] == "nova.operadora")
        self.assertTrue(created["deve_trocar_senha"])
        self.assertNotIn("senha_hash", created)

    def test_admin_can_reach_password_reset_for_selected_user(self) -> None:
        account = self.controller.create_user_admin(
            "Operadora Esqueceu", "operadora-esqueceu", "SenhaInicial8", "caixa", self.admin["id"]
        )
        self.app.show_admin()
        self.app.update()
        admin_view = self.app.admin_view
        assert admin_view is not None
        admin_view.load_users()
        target = next(
            item for item in admin_view.user_table.get_children()
            if admin_view.user_table.item(item, "values")[2] == account["login"]
        )
        admin_view.user_table.selection_set(target)
        admin_view.reset_selected_user_password()
        self.app.update()

        dialog = next(child for child in admin_view.winfo_children() if isinstance(child, UserPasswordResetDialog))
        reset_button = _find_button(dialog, "Redefinir senha")
        _assert_visible(self, dialog, reset_button)
        dialog.password_var.set("SenhaTemporaria9")
        dialog.confirmation_var.set("SenhaTemporaria9")
        reset_button.invoke()
        self.app.update()

        updated = next(user for user in self.controller.admin_users() if user["id"] == account["id"])
        self.assertTrue(updated["deve_trocar_senha"])

    def test_control_f11_consults_the_gtin_typed_in_checkout(self) -> None:
        sale = self.app.sale_view
        assert sale is not None
        sale.handle_gtin = Mock(return_value="break")
        event = SimpleNamespace(widget=self.app)

        result = self.app._shortcut_gtin_lookup(event)

        self.assertEqual(result, "break")
        sale.handle_gtin.assert_called_once_with(event)
        self.assertTrue(self.app.bind_all("<Control-F11>"))

    def test_rapid_scanner_reads_are_processed_once_and_in_order(self) -> None:
        sale = self.app.sale_view
        assert sale is not None
        sale.cash = self.controller.open_cash(self.admin["id"], 0)
        first_started = threading.Event()
        release_first = threading.Event()
        calls: list[str] = []

        def scan(code: str, _actor_id: int) -> dict:
            calls.append(code)
            if len(calls) == 1:
                first_started.set()
                release_first.wait(2)
            return {
                "status": "FOUND",
                "product": {
                    "gtin": code,
                    "nome": f"Produto {code[-1]}",
                    "preco": 2.50,
                    "unidade": "UN",
                },
            }

        self.controller.scan_product = Mock(side_effect=scan)
        sale.gtin_var.set("7891234567895")
        sale.handle_gtin()
        self.assertTrue(first_started.wait(1))
        sale.gtin_var.set("7891234567888")
        sale.handle_gtin()
        self.assertEqual(sale._scan_queue.pending_count, 1)
        release_first.set()

        deadline = time.monotonic() + 3
        while len(sale.cart) < 2 and time.monotonic() < deadline:
            self.app.update()
            time.sleep(0.01)

        self.assertEqual(calls, ["7891234567895", "7891234567888"])
        self.assertEqual([line.gtin for line in sale.cart], calls)
        self.assertFalse(sale._scan_queue.has_pending)

    def test_forgot_password_appears_after_five_admin_failures(self) -> None:
        self.app.show_login()
        self.app.update()
        login = self.app.current_view
        login.login_var.set(TEST_ADMIN_LOGIN)
        login.password_var.set("senha-incorreta")
        for _ in range(5):
            login.login()
            login.password_var.set("senha-incorreta")
        self.app.update()
        button = _find_button(login, "Esqueci minha senha")
        _assert_visible(self, self.app, button)
        button.invoke()
        self.app.update()
        dialog = next(child for child in login.winfo_children() if isinstance(child, PasswordRecoveryDialog))
        self.assertTrue(_has_scrollbar(dialog))
        self.assertIn("recuperar acesso", dialog.title().lower())
        dialog.destroy()

    def test_forgot_password_hides_when_login_changes_and_rechecks_before_opening(self) -> None:
        self.app.show_login()
        self.app.update()
        login = self.app.current_view
        login.login_var.set(TEST_ADMIN_LOGIN)
        for _ in range(5):
            login.password_var.set("senha-incorreta")
            login.login()
        self.app.update()
        self.assertTrue(login.recovery_button.winfo_manager())

        login.login_var.set("outro.login")
        self.app.update()
        self.assertFalse(login.recovery_button.winfo_manager())
        login.recovery_button.pack(fill="x")
        login.open_password_recovery()
        self.app.update()
        self.assertFalse(
            any(isinstance(child, PasswordRecoveryDialog) for child in login.winfo_children())
        )

    def test_legacy_admin_recovery_action_is_scrollable_and_requires_exact_confirmation(self) -> None:
        with self.controller.service.database.transaction(write=True) as connection:
            connection.execute(
                "UPDATE usuarios SET codigo_recuperacao_hash = NULL WHERE id = ?",
                (self.admin["id"],),
            )
        refreshed = self.controller.authenticate(TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD)
        assert refreshed is not None
        self.app.current_user = refreshed
        self.app.geometry("640x480+0+0")
        self.app.show_admin()
        self.app.update()
        admin_view = self.app.admin_view
        assert admin_view is not None
        self.assertTrue(_has_scrollbar(admin_view.users_tab))
        action = _find_button(admin_view, "Configurar recuperação")
        action.invoke()
        self.app.update()
        dialog = next(
            child for child in admin_view.winfo_children() if isinstance(child, RecoveryCodeSetupDialog)
        )
        self.assertTrue(_has_scrollbar(dialog))
        self.assertLessEqual(dialog.winfo_height(), self.app.winfo_height())
        self.assertGreaterEqual(len(dialog.recovery_code_var.get()), 12)
        dialog.current_password_var.set(TEST_ADMIN_PASSWORD)
        dialog.confirmation_var.set("confirmacao-diferente")
        dialog.submit()
        self.app.update()
        self.assertIn("confere", dialog.error_var.get().lower())
        self.assertTrue(dialog.winfo_exists())

        dialog.confirmation_var.set(dialog.recovery_code_var.get())
        dialog.submit()
        self.app.update()
        self.assertFalse(dialog.winfo_exists())
        self.assertTrue(self.controller.service.current_user()["recovery_configured"])

    def test_new_product_can_autofill_name_and_brand_from_gtin(self) -> None:
        lookup = Mock(return_value={
            "status": "PRICE_REQUIRED",
            "product": {"gtin": "7898341430258", "nome": "Suco Del Valle", "marca": "Del Valle"},
        })
        dialog = ProductEditorDialog(self.app.sale_view, None, lambda _product: True, on_lookup=lookup)
        self.app.update()
        dialog.vars["gtin"].set("7898341430258")

        result = dialog.lookup_gtin()

        self.assertEqual(result, "break")
        lookup.assert_called_once_with("7898341430258")
        self.assertEqual(dialog.vars["nome"].get(), "Suco Del Valle")
        self.assertEqual(dialog.vars["marca"].get(), "Del Valle")
        self.assertTrue(dialog.bind("<Control-F11>"))
        dialog.destroy()

    def test_printer_settings_expose_live_selection_and_refresh_actions(self) -> None:
        self.app.show_admin()
        self.app.update_idletasks()
        admin_view = self.app.admin_view
        assert admin_view is not None
        self.assertIsInstance(admin_view.printer_combo, ttk.Combobox)
        button_texts: list[str] = []
        def collect_buttons(parent: tk.Misc) -> None:
            for child in parent.winfo_children():
                if isinstance(child, tk.Button):
                    button_texts.append(str(child.cget("text")))
                collect_buttons(child)
        collect_buttons(admin_view)
        self.assertTrue(any("Atual" in text for text in button_texts))
        self.assertTrue(any("Salvar" in text for text in button_texts))
        self.assertTrue(any("Test" in text for text in button_texts))
        self.assertTrue(self.app.bind_all("<Control-F11>"))
        # The refresh is dispatched to a worker; invoking it must return while
        # the Tk event loop remains usable even if the spooler is slow.
        admin_view.refresh_printers()
        self.assertIsNotNone(admin_view._printer_task_queue)
        for _ in range(20):
            self.app.update()
            if admin_view._printer_task_queue is None:
                break

    def test_scheduled_update_exits_through_the_graceful_tk_shutdown_path(self) -> None:
        self.app.show_admin()
        self.app.update()
        admin_view = self.app.admin_view
        assert admin_view is not None
        shutdown = Mock()
        admin_view.on_update_scheduled = shutdown

        admin_view._update_apply_completed({"message": "Atualização preparada."})
        self.app.update()

        shutdown.assert_called_once_with()

    def test_printer_actions_recover_after_background_failure(self) -> None:
        self.app.show_admin()
        self.app.update()
        admin_view = self.app.admin_view
        assert admin_view is not None
        deadline = time.monotonic() + 10
        while admin_view._printer_task_queue is not None and time.monotonic() < deadline:
            self.app.update()
            time.sleep(0.01)
        self.assertIsNone(admin_view._printer_task_queue)
        admin_view._printer_refresh_button.configure(state="disabled", text="Atualizando…")
        admin_view._printer_save_button.configure(state="disabled", text="Salvando…")
        admin_view._printer_test_button.configure(state="disabled", text="Testando…")
        admin_view._printer_task(
            lambda: (_ for _ in ()).throw(RuntimeError("falha simulada da fila")),
            lambda _result: self.fail("não deveria concluir com sucesso"),
        )
        deadline = time.monotonic() + 3
        while admin_view._printer_task_queue is not None and time.monotonic() < deadline:
            self.app.update()
            time.sleep(0.01)

        self.assertEqual(str(admin_view._printer_refresh_button.cget("state")), "normal")
        self.assertEqual(str(admin_view._printer_save_button.cget("state")), "normal")
        self.assertEqual(str(admin_view._printer_test_button.cget("state")), "normal")

    def test_large_checkout_dialogs_fit_inside_a_640_by_480_app(self) -> None:
        sale = self.app.sale_view
        assert sale is not None
        self.app.geometry("640x480+0+0")
        self.app.update()

        dialogs = [
            PaymentDialog(sale, Decimal("3.13"), lambda _method, _received: True),
            SearchDialog(sale, lambda _query: [], lambda _product: True),
            ProductEditorDialog(sale, None, lambda _product: True),
        ]
        for dialog in dialogs:
            with self.subTest(dialog=type(dialog).__name__):
                self.app.update()
                self.assertLessEqual(dialog.winfo_width(), self.app.winfo_width())
                self.assertLessEqual(dialog.winfo_height(), self.app.winfo_height())
                self.assertTrue(_has_scrollbar(dialog))
                dialog.destroy()


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from tkinter import ttk
from unittest.mock import patch

from db.database import Database
from desktop_controller import DesktopController
from services.pdv_service import PDVService
from tests.support import (
    TEST_ADMIN_LOGIN,
    TEST_ADMIN_NAME,
    TEST_ADMIN_PASSWORD,
    TEST_RECOVERY_CODE,
)
from ui.app import PDVApplication
from ui.dialogs import ConfirmDialog
import ui.setup as setup_module
from ui.setup import SetupView
from ui.views import LoginView, SaleView


class SetupContractTestCase(unittest.TestCase):
    """Security decisions remain testable when Tk has no display."""

    def test_identity_validation_rejects_password_mismatch_without_tk(self) -> None:
        validate = getattr(setup_module, "validate_setup_identity", None)
        self.assertTrue(callable(validate))

        with self.assertRaisesRegex(ValueError, "senhas"):
            validate("Responsável", "admin", "SenhaSegura8", "outra-senha")

    def test_recovery_confirmation_is_exact_without_trimming(self) -> None:
        matches = getattr(setup_module, "recovery_code_matches", None)
        self.assertTrue(callable(matches))

        self.assertTrue(matches("codigo-exato", "codigo-exato"))
        self.assertFalse(matches("codigo-exato", "codigo-exato "))
        self.assertFalse(matches("codigo-exato", "CODIGO-EXATO"))


class CountingController(DesktopController):
    """Real controller with narrow counters at the UI boundary."""

    def __init__(self, service: PDVService) -> None:
        super().__init__(service)
        self.generate_calls = 0
        self.provision_calls = 0
        self.fail_next_provision_message: str | None = None

    def generate_recovery_code(self) -> str:
        self.generate_calls += 1
        return super().generate_recovery_code()

    def provision_initial_admin(
        self, name: str, login: str, password: str, recovery_code: str
    ) -> dict:
        self.provision_calls += 1
        if self.fail_next_provision_message is not None:
            message = self.fail_next_provision_message
            self.fail_next_provision_message = None
            raise RuntimeError(message)
        return super().provision_initial_admin(name, login, password, recovery_code)


def _has_scrollbar(parent: tk.Misc) -> bool:
    if isinstance(parent, ttk.Scrollbar):
        return True
    return any(_has_scrollbar(child) for child in parent.winfo_children())


class FirstUseSetupUITestCase(unittest.TestCase):
    """First use is exercised only against a temporary database and Tk root."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        database = Database(Path(self.tempdir.name) / "pdv.sqlite3")
        self.controller = CountingController(PDVService(database=database))
        try:
            self.app = PDVApplication(self.controller)
        except tk.TclError as exc:
            self.tempdir.cleanup()
            self.skipTest(f"Tk indisponível neste ambiente: {exc}")
        self.app.geometry("900x650+0+0")
        self.app.update()

    def tearDown(self) -> None:
        if hasattr(self, "app") and self.app.winfo_exists():
            self.app.destroy()
        self.tempdir.cleanup()

    def _setup(self) -> tk.Frame:
        view = self.app.current_view
        self.assertIsNotNone(view)
        self.assertEqual(type(view).__name__, "SetupView")
        return view

    def _fill_valid_identity(self, view: tk.Frame) -> None:
        view.name_var.set(TEST_ADMIN_NAME)
        view.login_var.set(TEST_ADMIN_LOGIN)
        view.password_var.set(TEST_ADMIN_PASSWORD)
        view.password_confirmation_var.set(TEST_ADMIN_PASSWORD)

    def _advance_to_code(self, view: tk.Frame) -> str:
        self._fill_valid_identity(view)
        view.continue_to_confirmation()
        self.app.update()
        code = view.recovery_code_var.get()
        self.assertTrue(code)
        return code

    def _user_count(self) -> int:
        with self.controller.service.database.transaction() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0])

    def test_fresh_database_starts_in_setup_without_authenticating(self) -> None:
        self._setup()
        self.assertIsNone(self.app.current_user)
        self.assertTrue(self.controller.installation_status().requires_provisioning)
        self.assertIsNone(self.app.sale_view)

    def test_ready_database_starts_in_login(self) -> None:
        self.app.destroy()
        self.controller.provision_initial_admin(
            TEST_ADMIN_NAME,
            TEST_ADMIN_LOGIN,
            TEST_ADMIN_PASSWORD,
            TEST_RECOVERY_CODE,
        )

        self.app = PDVApplication(self.controller)
        self.app.update()

        self.assertIsInstance(self.app.current_view, LoginView)
        self.assertIsNone(self.app.current_user)
        self.assertFalse(self.controller.installation_status().requires_provisioning)

    def test_forced_show_login_redirects_fresh_database_back_to_setup(self) -> None:
        first = self._setup()

        self.app.show_login()
        self.app.update()

        current = self._setup()
        self.assertIsNot(current, first)
        self.assertNotIsInstance(current, LoginView)

    def test_forced_show_admin_redirects_fresh_database_back_to_setup(self) -> None:
        first = self._setup()
        self.app.current_user = {
            "id": 999,
            "nome": "Admin apenas em memória",
            "login": "admin.injetado",
            "perfil": "admin",
        }

        self.app.show_admin()
        self.app.update()

        current = self._setup()
        self.assertIsNot(current, first)
        self.assertIsNone(self.app.admin_view)
        self.assertIsNone(self.app.current_user)
        self.assertTrue(self.controller.installation_status().requires_provisioning)

    def test_password_mismatch_does_not_generate_code_or_call_provisioning(self) -> None:
        view = self._setup()
        self._fill_valid_identity(view)
        view.password_confirmation_var.set("confirmacao-diferente")

        view.continue_to_confirmation()
        self.app.update()

        self.assertEqual(self.controller.generate_calls, 0)
        self.assertEqual(self.controller.provision_calls, 0)
        self.assertEqual(view.recovery_code_var.get(), "")
        self.assertIn("senhas", view.error_var.get().lower())
        self.assertEqual(self._user_count(), 0)

    def test_backend_code_is_hidden_until_confirmation_and_generated_only_once(self) -> None:
        view = self._setup()
        self.assertEqual(view.recovery_code_var.get(), "")
        self.assertFalse(view.confirmation_panel.winfo_manager())

        code = self._advance_to_code(view)
        view.continue_to_confirmation()
        self.app.update()

        self.assertEqual(self.controller.generate_calls, 1)
        self.assertEqual(view.recovery_code_var.get(), code)
        self.assertTrue(view.confirmation_panel.winfo_manager())
        self.assertEqual(view.recovery_code_label.cget("text"), code)

    def test_different_code_confirmation_prevents_provisioning(self) -> None:
        view = self._setup()
        self._advance_to_code(view)
        view.recovery_confirmation_var.set("valor-diferente")

        view.finish_setup()
        self.app.update()

        self.assertEqual(self.controller.provision_calls, 0)
        self.assertTrue(self.controller.installation_status().requires_provisioning)
        self.assertIn("código", view.error_var.get().lower())
        self.assertEqual(self._user_count(), 0)

    def test_backend_error_stays_scrollable_and_fields_can_be_corrected(self) -> None:
        view = self._setup()
        self.app.minsize(720, 520)
        self.app.geometry("720x520+0+0")
        self.app.update()
        code = self._advance_to_code(view)
        view.recovery_confirmation_var.set(code)
        message = "Falha temporária ao concluir. " * 16
        self.controller.fail_next_provision_message = message

        view.finish_setup()
        self.app.update()

        self.assertEqual(view.error_var.get(), message)
        self.assertTrue(view.error_label.winfo_manager())
        self.assertTrue(_has_scrollbar(view))
        canvas_top = view.scroll_canvas.winfo_rooty()
        canvas_bottom = canvas_top + view.scroll_canvas.winfo_height()
        error_top = view.error_label.winfo_rooty()
        error_bottom = error_top + view.error_label.winfo_height()
        self.assertGreaterEqual(error_top, canvas_top)
        self.assertLessEqual(error_bottom, canvas_bottom)
        self.assertEqual(view.password_var.get(), TEST_ADMIN_PASSWORD)
        view.login_var.set("admin.corrigido")
        view.finish_setup()
        self.app.update()

        self.assertIsInstance(self.app.current_view, LoginView)
        self.assertEqual(self.controller.generate_calls, 1)
        self.assertEqual(self.controller.provision_calls, 2)
        with self.controller.service.database.transaction() as connection:
            login = connection.execute("SELECT login FROM usuarios").fetchone()[0]
        self.assertEqual(login, "admin.corrigido")

    def test_double_finish_creates_one_account_clears_secrets_and_returns_to_login(self) -> None:
        view = self._setup()
        code = self._advance_to_code(view)
        view.recovery_confirmation_var.set(code)

        view.finish_setup()
        view.finish_setup()
        self.app.update()

        self.assertEqual(self.controller.provision_calls, 1)
        self.assertEqual(self._user_count(), 1)
        self.assertIsInstance(self.app.current_view, LoginView)
        self.assertIsNone(self.app.current_user)
        self.assertEqual(view.password_var.get(), "")
        self.assertEqual(view.password_confirmation_var.get(), "")
        self.assertEqual(view.recovery_code_var.get(), "")
        self.assertEqual(view.recovery_confirmation_var.get(), "")
        self.app.show_initial_route()
        self.app.update()
        self.assertIsInstance(self.app.current_view, LoginView)

    def test_cancel_escape_and_close_never_navigate_to_login_or_checkout(self) -> None:
        view = self._setup()

        view.cancel()
        self.app.update()

        self.assertIs(self.app.current_view, view)
        self.assertNotIsInstance(self.app.current_view, (LoginView, SaleView))
        confirmation = next(
            child for child in self.app.winfo_children() if isinstance(child, ConfirmDialog)
        )
        confirmation.cancel()
        result = self.app._shortcut_escape(SimpleNamespace(widget=self.app))
        self.app.update()
        self.assertEqual(result, "break")
        self.assertIs(self.app.current_view, view)
        self.assertIsNone(self.app.current_user)
        self.assertIsNone(self.app.sale_view)

    def test_small_geometry_exposes_scrollbar_wheel_keyboard_and_tab_controls(self) -> None:
        view = self._setup()
        self.app.geometry("720x520+0+0")
        self.app.update()

        self.assertTrue(_has_scrollbar(view))
        self.assertTrue(view.scroll_canvas.bind("<MouseWheel>"))
        self.assertTrue(view.bind("<Down>"))
        bounds = view.scroll_canvas.bbox("all")
        self.assertIsNotNone(bounds)
        self.assertGreater(bounds[3] - bounds[1], view.scroll_canvas.winfo_height())
        for control in (
            view.name_entry,
            view.login_entry,
            view.password_entry,
            view.password_confirmation_entry,
            view.continue_button,
            view.cancel_button,
        ):
            self.assertNotIn(str(control.cget("takefocus")).lower(), {"", "0", "false"})

    def test_reported_720x520_screen_never_creates_a_larger_root_or_minimum(self) -> None:
        self.app.destroy()
        with (
            patch.object(PDVApplication, "winfo_screenwidth", return_value=720),
            patch.object(PDVApplication, "winfo_screenheight", return_value=520),
        ):
            self.app = PDVApplication(self.controller)
            self.app.update()

        minimum_width, minimum_height = self.app.minsize()
        self.assertLessEqual(self.app.winfo_width(), 720)
        self.assertLessEqual(self.app.winfo_height(), 520)
        self.assertLessEqual(minimum_width, 720)
        self.assertLessEqual(minimum_height, 520)

    def test_destroy_before_idle_focus_does_not_leave_tcl_callback(self) -> None:
        background_errors: list[str] = []
        callback_name = self.app.register(background_errors.append)
        self.app.tk.eval(
            f"proc bgerror {{message}} {{{callback_name} $message}}"
        )
        orphan = SetupView(self.app, self.controller, lambda: None, lambda: None)
        orphan.pack(fill="both", expand=True)

        orphan.destroy()
        self.app.update()

        self.assertEqual(background_errors, [])

    def test_login_created_after_setup_can_be_destroyed_without_focus_callback(self) -> None:
        background_errors: list[str] = []
        callback_name = self.app.register(background_errors.append)
        self.app.tk.eval(
            f"proc bgerror {{message}} {{{callback_name} $message}}"
        )
        orphan = LoginView(self.app, self.controller, lambda _user: None)
        orphan.pack(fill="both", expand=True)

        orphan.destroy()
        self.app.tk.call("after", 80)
        self.app.update()

        self.assertEqual(background_errors, [])


if __name__ == "__main__":
    unittest.main()

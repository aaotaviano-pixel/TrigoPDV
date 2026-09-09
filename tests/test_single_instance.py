"""Contratos multiplataforma do lock de instância única por arquivo de banco.

Os testes entre processos usam ``spawn`` real. Todos os waits e joins têm
limite e o cleanup encerra qualquer filho que não tenha finalizado.
"""

from __future__ import annotations

import ctypes
import importlib
import multiprocessing
import os
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


PROCESS_TIMEOUT_SECONDS = 10


def _single_instance_module():
    return importlib.import_module("runtime.single_instance")


def _try_acquire_worker(
    database_path: str,
    lock_dir: str,
    outcome: multiprocessing.sharedctypes.Synchronized,
) -> None:
    """Registra 1 para aquisição e 2 para bloqueio esperado."""

    module = _single_instance_module()
    guard = module.SingleInstanceGuard(database_path, lock_dir=Path(lock_dir))
    try:
        guard.acquire()
    except module.SingleInstanceError:
        outcome.value = 2
    else:
        outcome.value = 1
        guard.release()


def _hold_then_crash_worker(
    database_path: str,
    lock_dir: str,
    acquired: multiprocessing.synchronize.Event,
    crash_now: multiprocessing.synchronize.Event,
) -> None:
    module = _single_instance_module()
    guard = module.SingleInstanceGuard(database_path, lock_dir=Path(lock_dir))
    guard.acquire()
    acquired.set()
    if not crash_now.wait(PROCESS_TIMEOUT_SECONDS):
        guard.release()
        raise SystemExit(3)
    os._exit(23)


def _join_or_terminate(testcase: unittest.TestCase, process: multiprocessing.Process) -> None:
    process.join(PROCESS_TIMEOUT_SECONDS)
    if process.is_alive():
        process.terminate()
        process.join(PROCESS_TIMEOUT_SECONDS)
        testcase.fail("Processo filho excedeu o timeout e precisou ser encerrado.")


@unittest.skipUnless(os.name == "nt", "CreateFileW é validado somente no Windows")
class SingleInstanceWin32TestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.root = Path(self.temporary.name)
        self.lock_dir = self.root / "runtime-locks"
        self.context = multiprocessing.get_context("spawn")
        self.children: list[multiprocessing.Process] = []

    def tearDown(self) -> None:
        for process in self.children:
            if process.is_alive():
                process.terminate()
            process.join(PROCESS_TIMEOUT_SECONDS)
        self.temporary.cleanup()

    def _start(self, target, args: tuple[object, ...]) -> multiprocessing.Process:
        process = self.context.Process(target=target, args=args)
        process.start()
        self.children.append(process)
        return process

    def test_same_database_is_exclusive_across_spawned_process_and_never_creates_database(self) -> None:
        module = _single_instance_module()
        database_path = self.root / "dados" / "pdv.sqlite3"
        outcome = self.context.Value("i", 0)

        with module.SingleInstanceGuard(database_path, lock_dir=self.lock_dir):
            process = self._start(
                _try_acquire_worker,
                (str(database_path), str(self.lock_dir), outcome),
            )
            _join_or_terminate(self, process)
            self.assertEqual(process.exitcode, 0)
            self.assertEqual(outcome.value, 2)

        self.assertFalse(database_path.exists())

    def test_relative_absolute_and_case_variants_use_the_same_lock(self) -> None:
        module = _single_instance_module()
        absolute = (self.root / "Dados" / "PDV.sqlite3").resolve()
        relative = Path(os.path.relpath(absolute, Path.cwd()))
        case_variant = Path(str(absolute).swapcase())

        absolute_guard = module.SingleInstanceGuard(absolute, lock_dir=self.lock_dir)
        relative_guard = module.SingleInstanceGuard(relative, lock_dir=self.lock_dir)
        case_guard = module.SingleInstanceGuard(case_variant, lock_dir=self.lock_dir)

        self.assertEqual(absolute_guard.lock_path, relative_guard.lock_path)
        self.assertEqual(absolute_guard.lock_path, case_guard.lock_path)
        with absolute_guard:
            with self.assertRaises(module.SingleInstanceError):
                relative_guard.acquire()
            with self.assertRaises(module.SingleInstanceError):
                case_guard.acquire()

    def test_different_databases_can_coexist_across_processes(self) -> None:
        module = _single_instance_module()
        first_database = self.root / "first.sqlite3"
        second_database = self.root / "second.sqlite3"
        outcome = self.context.Value("i", 0)

        with module.SingleInstanceGuard(first_database, lock_dir=self.lock_dir):
            process = self._start(
                _try_acquire_worker,
                (str(second_database), str(self.lock_dir), outcome),
            )
            _join_or_terminate(self, process)

        self.assertEqual(process.exitcode, 0)
        self.assertEqual(outcome.value, 1)

    def test_release_is_idempotent_and_context_exception_closes_handle(self) -> None:
        module = _single_instance_module()
        database_path = self.root / "pdv.sqlite3"
        guard = module.SingleInstanceGuard(database_path, lock_dir=self.lock_dir)

        guard.acquire()
        self.assertIs(guard.acquire(), guard)
        guard.release()
        guard.release()
        with self.assertRaisesRegex(RuntimeError, "falha simulada"):
            with guard:
                raise RuntimeError("falha simulada")

        with module.SingleInstanceGuard(database_path, lock_dir=self.lock_dir):
            pass

    def test_handle_is_explicitly_non_inheritable(self) -> None:
        module = _single_instance_module()
        guard = module.SingleInstanceGuard(self.root / "pdv.sqlite3", lock_dir=self.lock_dir)
        with guard:
            flags = ctypes.wintypes.DWORD()
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.GetHandleInformation.argtypes = [
                ctypes.wintypes.HANDLE,
                ctypes.POINTER(ctypes.wintypes.DWORD),
            ]
            kernel32.GetHandleInformation.restype = ctypes.wintypes.BOOL
            self.assertTrue(kernel32.GetHandleInformation(guard._handle, ctypes.byref(flags)))
            self.assertEqual(flags.value & 0x00000001, 0)

    def test_crashed_owner_releases_kernel_handle(self) -> None:
        module = _single_instance_module()
        database_path = self.root / "pdv.sqlite3"
        acquired = self.context.Event()
        crash_now = self.context.Event()
        process = self._start(
            _hold_then_crash_worker,
            (str(database_path), str(self.lock_dir), acquired, crash_now),
        )
        self.assertTrue(acquired.wait(PROCESS_TIMEOUT_SECONDS), "Filho não adquiriu o lock no prazo.")
        crash_now.set()
        _join_or_terminate(self, process)
        self.assertEqual(process.exitcode, 23)

        with module.SingleInstanceGuard(database_path, lock_dir=self.lock_dir):
            pass

    def test_preexisting_unlocked_file_does_not_block(self) -> None:
        module = _single_instance_module()
        guard = module.SingleInstanceGuard(self.root / "pdv.sqlite3", lock_dir=self.lock_dir)
        guard.lock_path.parent.mkdir(parents=True, exist_ok=True)
        guard.lock_path.write_text("conteúdo editável não representa o lock", encoding="utf-8")

        with guard:
            pass
        self.assertTrue(guard.lock_path.exists())

    def test_lock_filename_contains_only_fixed_prefix_and_sha256_digest(self) -> None:
        module = _single_instance_module()
        database_path = self.root / "nome-comercial-secreto.sqlite3"
        guard = module.SingleInstanceGuard(database_path, lock_dir=self.lock_dir)

        self.assertRegex(guard.lock_path.name, re.compile(r"^trigopdv-[0-9a-f]{64}\.lock$"))
        self.assertNotIn(database_path.name, guard.lock_path.name)
        with guard:
            pass
        self.assertTrue(guard.lock_path.exists())

    def test_lock_creation_failure_is_safe_and_does_not_create_database(self) -> None:
        module = _single_instance_module()
        database_path = self.root / "dados" / "pdv.sqlite3"
        invalid_lock_dir = self.root / "not-a-directory"
        invalid_lock_dir.write_text("arquivo", encoding="utf-8")
        guard = module.SingleInstanceGuard(database_path, lock_dir=invalid_lock_dir)

        with self.assertRaises(module.SingleInstanceError) as captured:
            guard.acquire()

        message = str(captured.exception)
        self.assertNotIn(str(database_path), message)
        self.assertNotIn(str(invalid_lock_dir), message)
        self.assertFalse(database_path.exists())

    def test_canonicalization_failure_is_wrapped_without_exposing_raw_path(self) -> None:
        module = _single_instance_module()
        raw_path = "C:/segredo-cliente/pdv.sqlite3"
        with patch.object(
            module,
            "_canonical_database_path",
            side_effect=OSError(f"falha ao resolver {raw_path}"),
        ):
            with self.assertRaises(module.SingleInstanceError) as captured:
                module.SingleInstanceGuard(raw_path, lock_dir=self.lock_dir)

        self.assertEqual(str(captured.exception), module._SAFE_FAILURE_MESSAGE)
        self.assertNotIn(raw_path, str(captured.exception))


@unittest.skipUnless(os.name == "posix", "flock é validado somente em sistemas POSIX")
class SingleInstancePosixTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.root = Path(self.temporary.name)
        self.lock_dir = self.root / "runtime-locks"
        self.context = multiprocessing.get_context("spawn")
        self.children: list[multiprocessing.Process] = []

    def tearDown(self) -> None:
        for process in self.children:
            if process.is_alive():
                process.terminate()
            process.join(PROCESS_TIMEOUT_SECONDS)
        self.temporary.cleanup()

    def _start(self, target, args: tuple[object, ...]) -> multiprocessing.Process:
        process = self.context.Process(target=target, args=args)
        process.start()
        self.children.append(process)
        return process

    def test_same_database_is_exclusive_across_processes(self) -> None:
        module = _single_instance_module()
        database_path = self.root / "dados" / "pdv.sqlite3"
        outcome = self.context.Value("i", 0)

        with module.SingleInstanceGuard(database_path, lock_dir=self.lock_dir):
            process = self._start(
                _try_acquire_worker,
                (str(database_path), str(self.lock_dir), outcome),
            )
            _join_or_terminate(self, process)

        self.assertEqual(process.exitcode, 0)
        self.assertEqual(outcome.value, 2)
        self.assertFalse(database_path.exists())

    def test_different_databases_can_coexist(self) -> None:
        module = _single_instance_module()
        outcome = self.context.Value("i", 0)

        with module.SingleInstanceGuard(self.root / "first.sqlite3", lock_dir=self.lock_dir):
            process = self._start(
                _try_acquire_worker,
                (str(self.root / "second.sqlite3"), str(self.lock_dir), outcome),
            )
            _join_or_terminate(self, process)

        self.assertEqual(process.exitcode, 0)
        self.assertEqual(outcome.value, 1)

    def test_release_is_idempotent_and_crash_releases_lock(self) -> None:
        module = _single_instance_module()
        database_path = self.root / "pdv.sqlite3"
        acquired = self.context.Event()
        crash_now = self.context.Event()
        process = self._start(
            _hold_then_crash_worker,
            (str(database_path), str(self.lock_dir), acquired, crash_now),
        )
        self.assertTrue(acquired.wait(PROCESS_TIMEOUT_SECONDS))
        crash_now.set()
        _join_or_terminate(self, process)
        self.assertEqual(process.exitcode, 23)

        guard = module.SingleInstanceGuard(database_path, lock_dir=self.lock_dir)
        guard.acquire()
        self.assertIs(guard.acquire(), guard)
        guard.release()
        guard.release()

    def test_descriptor_is_not_inherited(self) -> None:
        module = _single_instance_module()
        guard = module.SingleInstanceGuard(self.root / "pdv.sqlite3", lock_dir=self.lock_dir)

        with guard:
            self.assertFalse(os.get_inheritable(guard._handle))


class EntrypointSingleInstanceOrderTestCase(unittest.TestCase):
    def _guard_type(self, events: list[str], *, fail: BaseException | None = None):
        class RecordingGuard:
            def __init__(self, database_path: Path) -> None:
                self.database_path = database_path
                events.append("guard_construct")

            def __enter__(self):
                events.append("guard_acquire")
                if fail is not None:
                    raise fail
                return self

            def __exit__(self, exc_type, exc, traceback) -> None:
                events.append("guard_release")

        return RecordingGuard

    def test_main_acquires_before_service_and_holds_lock_through_launch(self) -> None:
        module = _single_instance_module()
        main_module = importlib.import_module("main")
        events: list[str] = []
        settings = SimpleNamespace(database_path=Path("C:/dados/pdv.sqlite3"))
        service = object()
        controller = object()
        fake_ui_app = types.ModuleType("ui.app")
        fake_ui_app.launch = lambda supplied: events.append("launch")

        with patch.object(main_module, "load_settings", side_effect=lambda: events.append("settings") or settings), \
             patch.object(main_module, "SingleInstanceGuard", self._guard_type(events)), \
             patch.object(main_module, "PDVService", side_effect=lambda **kwargs: events.append("service") or service), \
             patch.object(main_module, "DesktopController", side_effect=lambda *args: events.append("controller") or controller), \
             patch.dict(sys.modules, {"ui.app": fake_ui_app}):
            result = main_module.main()

        self.assertEqual(result, 0)
        self.assertEqual(
            events,
            ["settings", "guard_construct", "guard_acquire", "service", "controller", "launch", "guard_release"],
        )

    def test_main_lock_failure_warns_without_constructing_service(self) -> None:
        module = _single_instance_module()
        main_module = importlib.import_module("main")
        events: list[str] = []
        message = "O TrigoPDV já está aberto para este banco de dados."
        settings = SimpleNamespace(database_path=Path("C:/segredo/comercial.sqlite3"))
        service = Mock()

        with patch.object(main_module, "load_settings", return_value=settings), \
             patch.object(
                 main_module,
                 "SingleInstanceGuard",
                 self._guard_type(events, fail=module.SingleInstanceError(message)),
             ), \
             patch.object(main_module, "PDVService", service), \
             patch.object(main_module, "_show_startup_warning") as warning:
            result = main_module.main()

        self.assertEqual(result, 1)
        service.assert_not_called()
        warning.assert_called_once_with(message)
        self.assertNotIn("segredo", warning.call_args.args[0])

    def test_init_db_acquires_before_database_construction_and_initialize(self) -> None:
        init_module = importlib.import_module("init_db")
        events: list[str] = []
        config_path = Path("C:/configuracao/config.ini")
        database_path = Path("C:/dados/pdv.sqlite3")
        settings = SimpleNamespace(database_path=database_path)

        class RecordingDatabase:
            def __init__(self, path: Path) -> None:
                self.path = path
                events.append("database_construct")

            def initialize(self) -> None:
                events.append("database_initialize")

        with patch.object(init_module, "ensure_default_config", side_effect=lambda path: events.append("config")), \
             patch.object(init_module, "load_settings", side_effect=lambda path: events.append("settings") or settings), \
             patch.object(init_module, "SingleInstanceGuard", self._guard_type(events)), \
             patch.object(init_module, "Database", RecordingDatabase):
            result = init_module.initialize(config_path)

        self.assertEqual(result, database_path)
        self.assertEqual(
            events,
            [
                "config",
                "settings",
                "guard_construct",
                "guard_acquire",
                "database_construct",
                "database_initialize",
                "guard_release",
            ],
        )

    def test_init_db_lock_failure_does_not_construct_database(self) -> None:
        module = _single_instance_module()
        init_module = importlib.import_module("init_db")
        settings = SimpleNamespace(database_path=Path("C:/dados/pdv.sqlite3"))
        database = Mock()

        with patch.object(init_module, "load_settings", return_value=settings), \
             patch.object(
                 init_module,
                 "SingleInstanceGuard",
                 self._guard_type([], fail=module.SingleInstanceError("Instância já aberta.")),
             ), \
             patch.object(init_module, "Database", database):
            with self.assertRaises(module.SingleInstanceError):
                init_module.initialize()

        database.assert_not_called()


if __name__ == "__main__":
    unittest.main()

"""Desktop entry point for PDV Trigo de Minas."""

from __future__ import annotations

import sys

# Mantidos como atributos para testes/injeção, mas carregados somente depois do
# bootstrap Velopack. Nenhum serviço do PDV é importado antes desse ponto.
ConfigurationError = None
load_settings = None
DesktopController = None
SingleInstanceError = None
SingleInstanceGuard = None
PDVService = None
startup_preflight = None


def _load_runtime_dependencies() -> None:
    global ConfigurationError, load_settings, DesktopController
    global SingleInstanceError, SingleInstanceGuard, PDVService, startup_preflight

    if ConfigurationError is None:
        from config.settings import ConfigurationError as configuration_error

        ConfigurationError = configuration_error
    if load_settings is None:
        from config.settings import load_settings as settings_loader

        load_settings = settings_loader
    if DesktopController is None:
        from desktop_controller import DesktopController as controller_type

        DesktopController = controller_type
    if SingleInstanceError is None:
        from runtime.single_instance import SingleInstanceError as instance_error

        SingleInstanceError = instance_error
    if SingleInstanceGuard is None:
        from runtime.single_instance import SingleInstanceGuard as guard_type

        SingleInstanceGuard = guard_type
    if PDVService is None:
        from services.pdv_service import PDVService as service_type

        PDVService = service_type
    if startup_preflight is None:
        from updates.startup import startup_preflight as preflight

        startup_preflight = preflight


def _show_startup_warning(message: str) -> None:
    """Exibe um aviso mesmo no executável sem console, carregando Tk tardiamente."""

    root = None
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showwarning("TrigoPDV", message, parent=root)
    except Exception:
        # O stderr continua útil na execução por terminal; nunca substituímos a
        # mensagem segura por detalhes de caminho ou do erro do sistema.
        return
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass


def main() -> int:
    from updates.velopack_adapter import VelopackError, run_velopack_startup

    try:
        run_velopack_startup()
    except VelopackError as exc:
        print(f"Não foi possível iniciar o PDV: {exc}", file=sys.stderr)
        _show_startup_warning(str(exc))
        return 1
    _load_runtime_dependencies()
    try:
        settings = load_settings()
        with SingleInstanceGuard(settings.database_path):
            startup_preflight(settings)
            service = PDVService(settings=settings)
            from ui.app import launch

            launch(DesktopController(service, settings))
        return 0
    except SingleInstanceError as exc:
        message = str(exc)
        print(f"Não foi possível iniciar o PDV: {message}", file=sys.stderr)
        _show_startup_warning(message)
        return 1
    except (ConfigurationError, OSError, RuntimeError) as exc:
        print(f"Não foi possível iniciar o PDV: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

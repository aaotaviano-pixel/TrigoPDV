from __future__ import annotations

from pathlib import Path
import sys
from typing import Callable


class VelopackError(RuntimeError):
    pass


def run_velopack_startup(*, frozen: bool | None = None, app_factory: Callable | None = None) -> bool:
    """Executa os hooks antes de importar o aplicativo e desliga auto-apply."""

    packaged = bool(getattr(sys, "frozen", False)) if frozen is None else bool(frozen)
    if not packaged and app_factory is None:
        return False
    try:
        if app_factory is None:
            from velopack import App

            app_factory = App
        app_factory().set_auto_apply_on_startup(False).run()
    except Exception as exc:
        raise VelopackError("Não foi possível validar o inicializador de atualizações.") from exc
    return True


class VelopackAdapter:
    """Aplica somente um feed local já autenticado pelo TUF."""

    def __init__(self, manager_factory: Callable | None = None):
        self._manager_factory = manager_factory

    def apply_local_bundle(self, bundle_directory: str | Path, *, restart_args: list[str] | None = None) -> None:
        path = Path(bundle_directory).resolve()
        if not path.is_dir():
            raise VelopackError("O pacote local de atualização não está disponível.")
        try:
            if self._manager_factory is None:
                from velopack import UpdateManager

                manager = UpdateManager(str(path))
            else:
                manager = self._manager_factory(str(path))
            update = manager.check_for_updates()
            if update is None:
                raise VelopackError("O pacote local não oferece uma versão aplicável.")
            manager.download_updates(update)
            manager.wait_exit_then_apply_updates(update, silent=True, restart=True, restart_args=restart_args)
        except VelopackError:
            raise
        except Exception as exc:
            raise VelopackError("Não foi possível preparar a aplicação da atualização.") from exc


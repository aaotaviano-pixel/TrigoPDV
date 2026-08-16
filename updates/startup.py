from __future__ import annotations

from pathlib import Path

from services.catalog_bootstrap import CatalogBootstrapError, bootstrap_database_from_catalog
from .coordinator import UpdateCoordinator
from .event_log import UpdateEventLogger
from .models import UpdatePolicy
from .state import UpdateStateStore
from .velopack_adapter import VelopackAdapter


def _catalog_paths(project_root: Path) -> tuple[Path, Path]:
    candidates = (
        project_root / "catalog",
        project_root / "TrigoPDV_Instalacao_PenDrive" / "dados-iniciais",
    )
    for directory in candidates:
        catalog = directory / "catalogo-produtos.sqlite3"
        manifest = directory / "catalogo-produtos.manifest.json"
        if catalog.is_file() and manifest.is_file():
            return catalog, manifest
    raise CatalogBootstrapError("O catálogo inicial autenticado não acompanha esta instalação.")


def startup_preflight(settings) -> None:
    """Retoma atualização antes de criar/migrar o banco e só então faz bootstrap."""

    required = ("resource_directory", "update_state_path", "updates_enabled", "update_channel", "update_base_url")
    if not all(hasattr(settings, name) for name in required):
        return
    policy = UpdatePolicy(
        enabled=bool(settings.updates_enabled), channel=str(settings.update_channel),
        base_url=str(settings.update_base_url),
        check_interval_hours=int(settings.update_check_interval_hours),
    )
    store = UpdateStateStore(settings.update_state_path)
    coordinator = UpdateCoordinator(
        policy=policy, state_store=store, database_path=settings.database_path,
        backup_directory=settings.backup_path, adapter=VelopackAdapter(), repository=None,
        event_logger=UpdateEventLogger(settings.data_directory / "updates" / "events.jsonl"),
    )
    coordinator.resume_pending_update()
    if Path(settings.database_path).exists():
        return
    catalog, manifest = _catalog_paths(Path(settings.resource_directory))
    bootstrap_database_from_catalog(
        settings.database_path, catalog, manifest,
        update_pending=coordinator.has_pending_update(),
    )

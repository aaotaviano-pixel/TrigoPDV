"""Regras de negócio do PDV Trigo de Minas.

Os imports são preguiçosos para que integrações possam importar ``errors`` sem
iniciar a fachada completa e formar ciclo de importação.
"""

from importlib import import_module


__all__ = [
    "AuthService",
    "BackupService",
    "CashService",
    "PDVService",
    "ProductService",
    "ProductionPreparationService",
    "ProvisioningService",
    "ProvisioningStatus",
    "SaleService",
]

_EXPORTS = {
    "AuthService": ("auth", "AuthService"),
    "BackupService": ("backup", "BackupService"),
    "CashService": ("cash", "CashService"),
    "PDVService": ("pdv_service", "PDVService"),
    "ProductService": ("products", "ProductService"),
    "ProductionPreparationService": ("production", "ProductionPreparationService"),
    "ProvisioningService": ("provisioning", "ProvisioningService"),
    "ProvisioningStatus": ("provisioning", "ProvisioningStatus"),
    "SaleService": ("sales", "SaleService"),
}


def __getattr__(name: str):
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(import_module(f"{__name__}.{module_name}"), attribute)
    globals()[name] = value
    return value

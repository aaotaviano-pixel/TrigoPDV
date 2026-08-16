"""Fixtures de identidade exclusivas dos testes.

Nenhum valor deste módulo é importado por produção, seed ou ferramentas de
instalação.  Os testes provisionam explicitamente cada banco temporário.
"""

from __future__ import annotations

from typing import Any

from db.database import Database
from services.auth import AuthService


TEST_ADMIN_NAME = "Administradora de Teste"
TEST_ADMIN_LOGIN = "admin.teste"
TEST_ADMIN_PASSWORD = "SenhaTesteSegura8"
TEST_RECOVERY_CODE = "codigo-recuperacao-teste-seguro"


def provision_test_admin(
    database: Database,
    *,
    name: str = TEST_ADMIN_NAME,
    login: str = TEST_ADMIN_LOGIN,
    password: str = TEST_ADMIN_PASSWORD,
    recovery_code: str = TEST_RECOVERY_CODE,
) -> dict:
    """Provisiona e autentica o admin de uma base temporária de teste."""

    from services.provisioning import ProvisioningService

    ProvisioningService(database).provision_initial_admin(name, login, password, recovery_code)
    authenticated = AuthService(database).authenticate(login, password)
    if authenticated is None:
        raise AssertionError("A fixture administrativa não pôde ser autenticada.")
    return authenticated


def provision_test_pdv(service: Any) -> dict:
    """Provisiona a base da fachada e estabelece sua sessão administrativa."""

    provision_test_admin(service.database)
    authenticated = service.authenticate(TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD)
    if authenticated is None:
        raise AssertionError("A fachada não autenticou a fixture administrativa.")
    return authenticated

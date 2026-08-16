"""Exceções de domínio exibíveis em português pela interface."""

from dataclasses import dataclass


class ServiceError(Exception):
    """Erro base de regra de negócio."""


class ValidationError(ServiceError):
    """Entrada inválida ou incompleta."""


class NotFoundError(ServiceError):
    """Registro solicitado não existe."""


class ConflictError(ServiceError):
    """Estado atual não permite a operação."""


class AuthorizationError(ServiceError):
    """Usuário não tem permissão para a operação."""


@dataclass(frozen=True, slots=True)
class AuthorizationRequirement:
    """Dados públicos que a UI usa para solicitar aprovação administrativa."""

    reasons: tuple[str, ...]
    manual_total: str
    price_exception_count: int


class AuthorizationRequiredError(AuthorizationError):
    """A operação é válida, mas depende de administrador e justificativa."""

    def __init__(self, requirement: AuthorizationRequirement):
        super().__init__("Esta venda exige autorização de administrador e justificativa.")
        self.requirement = requirement


class PasswordChangeRequiredError(AuthorizationError):
    """Usuário precisa trocar a senha antes de continuar a operação."""


class AuthenticationError(ServiceError):
    """Credenciais não puderam ser verificadas."""


class PaymentError(ServiceError):
    """Dados de pagamento inválidos."""


class InsufficientStockError(ServiceError):
    """Estoque insuficiente para confirmar uma venda."""


class ExternalLookupError(ServiceError):
    """Consulta a serviço externo indisponível ou inválida."""


class BackupError(ServiceError):
    """Falha ao criar cópia de segurança ou fazer manutenção."""

"""Recursos de execução do aplicativo desktop."""

from .single_instance import SingleInstanceError, SingleInstanceGuard

__all__ = ["SingleInstanceError", "SingleInstanceGuard"]

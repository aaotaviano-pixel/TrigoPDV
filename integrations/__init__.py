"""Clientes de serviços externos, isolados das regras locais do PDV."""

from .open_food_facts import OpenFoodFactsClient, OpenFoodFactsProduct

__all__ = ["OpenFoodFactsClient", "OpenFoodFactsProduct"]

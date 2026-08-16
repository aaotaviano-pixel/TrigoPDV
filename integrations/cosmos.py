"""Cliente opcional da Cosmos para completar consultas de GTIN.

A credencial é lida exclusivamente do ``config.ini`` local. Este módulo nunca
registra token, cabeçalhos ou respostas completas da API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

try:
    import requests
except ImportError:  # pragma: no cover - só ocorre antes da instalação inicial
    requests = None  # type: ignore[assignment]

from integrations.open_food_facts import normalize_gtin
from services.errors import ExternalLookupError, ValidationError


# O endereço canônico é exibido no painel autenticado da Cosmos. O domínio
# api.cosmos possui uma camada de autenticação diferente e rejeita tokens da
# conta com 401, mesmo quando a credencial é válida no endpoint contratado.
API_URL = "https://cosmos.bluesoft.com.br/api/gtins/{gtin}.json"


@dataclass(frozen=True)
class CosmosProduct:
    gtin: str
    name: str
    brand: str = ""
    category: str = "Outros"
    packaging: str = ""


def _clean_text(value: Any, limit: int) -> str:
    """Trim and limit API text without changing legitimate inner spacing.

    Control and line-separator sanitization belongs to ``ProductService`` at
    the persistence boundary shared by every external provider.
    """

    if isinstance(value, dict):
        value = value.get("name") or value.get("description") or value.get("nome") or ""
    return ("" if value is None else str(value)).strip()[:limit]


class CosmosClient:
    """Consulta Cosmos somente quando token e User-Agent estão configurados."""

    def __init__(self, *, token: str = "", user_agent: str = "", timeout: float = 3.0, session: Any = None):
        if timeout <= 0 or timeout > 30:
            raise ValidationError("O timeout de consulta externa é inválido.")
        self.token = token.strip()
        self.user_agent = user_agent.strip()
        self.timeout = float(timeout)
        self.session = session

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.user_agent)

    def lookup(self, gtin: str) -> Optional[CosmosProduct]:
        barcode = normalize_gtin(gtin)
        if not self.enabled:
            return None
        if requests is None and self.session is None:
            raise ExternalLookupError("A dependência de consulta externa não está instalada.")
        client = self.session or requests
        try:
            response = client.get(
                API_URL.format(gtin=barcode),
                headers={
                    "X-Cosmos-Token": self.token,
                    "User-Agent": self.user_agent,
                    "Accept": "application/json",
                },
                timeout=self.timeout,
                allow_redirects=False,
            )
        except Exception as exc:
            raise ExternalLookupError("A consulta Cosmos está indisponível. Tente novamente ou cadastre manualmente.") from exc
        status_code = getattr(response, "status_code", None)
        if isinstance(status_code, int) and 300 <= status_code < 400:
            raise ExternalLookupError(
                "A consulta Cosmos não respondeu corretamente. Tente novamente ou cadastre manualmente."
            )
        if status_code == 404:
            return None
        if status_code in {401, 403}:
            raise ExternalLookupError(
                "A credencial Cosmos foi recusada. Confirme o token e o User-Agent no config.ini local."
            )
        if status_code == 429:
            raise ExternalLookupError(
                "O limite de consultas Cosmos foi atingido. Aguarde ou cadastre o produto manualmente."
            )
        try:
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise ExternalLookupError("A consulta Cosmos não respondeu corretamente. Tente novamente ou cadastre manualmente.") from exc
        if not isinstance(payload, dict):
            return None
        product = payload.get("product") if isinstance(payload.get("product"), dict) else payload
        name = _clean_text(
            product.get("description")
            or product.get("product_name")
            or product.get("name")
            or product.get("nome"),
            180,
        )
        if not name:
            return None
        category_data = product.get("category") or {}
        category = _clean_text(category_data, 100) or "Outros"
        packaging = _clean_text(product.get("quantity") or product.get("net_weight") or "", 120)
        if not packaging:
            gtins = product.get("gtins")
            if isinstance(gtins, list) and gtins and isinstance(gtins[0], dict):
                commercial = gtins[0].get("commercial_unit") or {}
                if isinstance(commercial, dict):
                    packaging = _clean_text(commercial.get("type_packaging") or "", 120)
        return CosmosProduct(
            gtin=barcode,
            name=name,
            brand=_clean_text(product.get("brand") or product.get("manufacturer") or product.get("marca"), 120),
            category=category,
            packaging=packaging,
        )

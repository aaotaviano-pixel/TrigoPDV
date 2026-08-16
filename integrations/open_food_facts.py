"""Cliente resiliente da API pública Open Food Facts.

Uma indisponibilidade externa nunca impede uma venda: o serviço de produtos
transforma qualquer falha deste cliente em fluxo de cadastro manual.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

try:
    import requests
except ImportError:  # pragma: no cover - só ocorre antes da instalação inicial
    requests = None  # type: ignore[assignment]

from services.errors import ExternalLookupError, ValidationError
from services.text import normalize_display_text


API_URL = "https://world.openfoodfacts.org/api/v2/product/{gtin}.json"
USER_AGENT = "PDV-Trigo-de-Minas/1.0 (local point-of-sale client)"


def normalize_gtin(value: Any) -> str:
    """Normaliza e valida GTIN-8/12/13/14 sem aceitar código inválido.

    O dígito verificador é calculado da direita para a esquerda, alternando
    pesos 3 e 1. Guardamos o valor como texto para preservar zeros à esquerda.
    """

    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise ValidationError("Informe um GTIN válido.")
    normalized = str(value).strip().replace(" ", "").replace("-", "")
    if not normalized.isdigit() or len(normalized) not in {8, 12, 13, 14}:
        raise ValidationError("O GTIN deve conter 8, 12, 13 ou 14 dígitos.")
    body = normalized[:-1]
    total = sum(int(digit) * (3 if (len(body) - index) % 2 == 1 else 1) for index, digit in enumerate(body))
    expected = (10 - (total % 10)) % 10
    if expected != int(normalized[-1]):
        raise ValidationError("O GTIN informado não possui dígito verificador válido.")
    return normalized


@dataclass(frozen=True)
class OpenFoodFactsProduct:
    gtin: str
    name: str
    brand: str = ""
    category: str = "Outros"
    packaging: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"gtin": self.gtin, "nome": self.name, "marca": self.brand}


class OpenFoodFactsClient:
    def __init__(self, *, timeout: float = 3.0, session: Any = None):
        if timeout <= 0 or timeout > 30:
            raise ValidationError("O timeout de consulta externa é inválido.")
        self.timeout = float(timeout)
        self.session = session

    def lookup(self, gtin: str) -> Optional[OpenFoodFactsProduct]:
        barcode = normalize_gtin(gtin)
        if requests is None and self.session is None:
            raise ExternalLookupError("A dependência de consulta externa não está instalada.")
        client = self.session or requests
        try:
            response = client.get(
                API_URL.format(gtin=barcode),
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=self.timeout,
            )
        except Exception as exc:
            request_error = getattr(requests, "RequestException", ()) if requests is not None else ()
            if request_error and isinstance(exc, request_error):
                exceptions = getattr(requests, "exceptions", None)
                timeout_error = getattr(exceptions, "Timeout", ()) if exceptions else ()
                connection_error = getattr(exceptions, "ConnectionError", ()) if exceptions else ()
                if timeout_error and isinstance(exc, timeout_error):
                    message = "A consulta demorou mais que o esperado. Verifique a conexão e tente novamente."
                elif connection_error and isinstance(exc, connection_error):
                    message = "Sem conexão com a internet. Você pode cadastrar o produto manualmente e continuar a venda."
                else:
                    message = "A consulta de produtos está indisponível agora. Tente novamente ou cadastre manualmente."
                raise ExternalLookupError(message) from exc
            raise ExternalLookupError("A consulta externa falhou. Cadastre o produto manualmente.") from exc

        if getattr(response, "status_code", None) == 404:
            return None
        try:
            response.raise_for_status()
        except Exception as exc:
            raise ExternalLookupError("O Open Food Facts não respondeu corretamente. Cadastre o produto manualmente.") from exc
        try:
            payload = response.json()
        except Exception as exc:
            raise ExternalLookupError("A resposta do Open Food Facts é inválida. Cadastre o produto manualmente.") from exc
        if not isinstance(payload, dict) or payload.get("status") != 1:
            return None
        product = payload.get("product")
        if not isinstance(product, dict):
            return None
        raw_name = product.get("product_name_pt") or product.get("product_name") or product.get("generic_name_pt") or product.get("generic_name")
        name = normalize_display_text(raw_name, 180)
        if not name:
            return None
        return OpenFoodFactsProduct(
            gtin=barcode,
            name=name,
            brand=normalize_display_text(product.get("brands"), 120),
            category=normalize_display_text(product.get("categories_pt") or product.get("categories"), 100) or "Outros",
            packaging=normalize_display_text(product.get("quantity"), 120),
        )

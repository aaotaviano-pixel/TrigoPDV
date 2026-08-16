from __future__ import annotations

import unittest
from urllib.parse import urlsplit

from integrations.cosmos import API_URL, CosmosClient
from services.errors import ExternalLookupError


DUMMY_TOKEN = "dummy-token-for-redirect-test"
DUMMY_USER_AGENT = "Cosmos-API-Request"
VALID_GTIN = "7898341430258"


class _Response:
    def __init__(self, status_code: int, *, location: str = "", payload=None) -> None:
        self.status_code = status_code
        self.headers = {"Location": location} if location else {}
        self._payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _RedirectAwareSession:
    """Fake que evidencia o comportamento inseguro padrão de seguir redirects."""

    def __init__(self, status_code: int, location: str) -> None:
        self.status_code = status_code
        self.location = location
        self.calls: list[dict] = []

    def get(self, url: str, *, headers: dict, timeout: float, allow_redirects: bool = True):
        self.calls.append(
            {
                "url": url,
                "headers": dict(headers),
                "timeout": timeout,
                "allow_redirects": allow_redirects,
            }
        )
        if allow_redirects:
            # requests preserva cabeçalhos personalizados em redirects; o fake
            # torna observável qualquer segunda saída sem acessar a rede real.
            self.calls.append(
                {
                    "url": self.location,
                    "headers": dict(headers),
                    "timeout": timeout,
                    "allow_redirects": allow_redirects,
                }
            )
            return _Response(
                200,
                payload={"description": "Produto que não deve ser aceito após redirect"},
            )
        return _Response(self.status_code, location=self.location)


class _SuccessSession:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def get(self, url: str, *, headers: dict, timeout: float, allow_redirects: bool = True):
        self.calls.append(
            {
                "url": url,
                "headers": dict(headers),
                "timeout": timeout,
                "allow_redirects": allow_redirects,
            }
        )
        return _Response(
            200,
            payload={
                "description": "Suco de maracujá 290 ml",
                "brand": "Marca de teste",
            },
        )


class CosmosRedirectSecurityTestCase(unittest.TestCase):
    def test_rejects_every_redirect_without_following_or_leaking_token(self) -> None:
        destinations = {
            "same-host": "https://cosmos.bluesoft.com.br/api/redirected",
            "cross-host": "https://redirect.invalid/collect?source=cosmos",
        }

        for status_code in (301, 302, 303, 307, 308):
            for destination_kind, location in destinations.items():
                with self.subTest(status_code=status_code, destination=destination_kind):
                    session = _RedirectAwareSession(status_code, location)
                    client = CosmosClient(
                        token=DUMMY_TOKEN,
                        user_agent=DUMMY_USER_AGENT,
                        timeout=2.5,
                        session=session,
                    )

                    with self.assertRaises(ExternalLookupError) as raised:
                        client.lookup(VALID_GTIN)

                    self.assertEqual(len(session.calls), 1)
                    self.assertEqual(
                        session.calls[0]["url"],
                        API_URL.format(gtin=VALID_GTIN),
                    )
                    self.assertFalse(session.calls[0]["allow_redirects"])
                    self.assertTrue(
                        all(
                            call["url"] == API_URL.format(gtin=VALID_GTIN)
                            for call in session.calls
                            if call["headers"].get("X-Cosmos-Token") == DUMMY_TOKEN
                        )
                    )

                    message = str(raised.exception)
                    self.assertNotIn(DUMMY_TOKEN, message)
                    self.assertNotIn(location, message)
                    self.assertNotIn("redirect.invalid", message)
                    self.assertNotIn("source=cosmos", message)

    def test_success_uses_only_canonical_https_endpoint_and_required_headers(self) -> None:
        session = _SuccessSession()
        client = CosmosClient(
            token=DUMMY_TOKEN,
            user_agent=DUMMY_USER_AGENT,
            timeout=2.5,
            session=session,
        )

        product = client.lookup(VALID_GTIN)

        self.assertIsNotNone(product)
        assert product is not None
        self.assertEqual(product.gtin, VALID_GTIN)
        self.assertEqual(len(session.calls), 1)
        call = session.calls[0]
        expected_url = API_URL.format(gtin=VALID_GTIN)
        self.assertEqual(call["url"], expected_url)
        parsed = urlsplit(call["url"])
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.hostname, "cosmos.bluesoft.com.br")
        self.assertEqual(parsed.query, "")
        self.assertEqual(call["headers"]["X-Cosmos-Token"], DUMMY_TOKEN)
        self.assertEqual(call["headers"]["User-Agent"], DUMMY_USER_AGENT)
        self.assertEqual(call["headers"]["Accept"], "application/json")
        self.assertEqual(call["timeout"], 2.5)
        self.assertFalse(call["allow_redirects"])


if __name__ == "__main__":
    unittest.main()

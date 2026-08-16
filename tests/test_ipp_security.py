from __future__ import annotations

import tempfile
import traceback
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from db.database import Database
from desktop_controller import DesktopController
import printing.ipp as ipp
from printing.receipt_printer import ReceiptPrinter
from services.pdv_service import PDVService
from tests.support import TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD, provision_test_admin
from ui.views import AdminView


MAX_RESPONSE_BYTES = 64 * 1024


class FakeStreamingResponse:
    def __init__(
        self,
        status_code: int,
        chunks: list[bytes],
        *,
        headers: dict[str, str] | None = None,
        stream_error: Exception | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = dict(headers or {})
        self.chunks = list(chunks)
        self.stream_error = stream_error
        self.iter_calls = 0
        self.closed = False

    @property
    def content(self) -> bytes:
        raise AssertionError("o cliente IPP não deve carregar response.content sem limite")

    def iter_content(self, chunk_size: int) -> object:
        self.iter_calls += 1
        if self.stream_error is not None:
            raise self.stream_error
        return iter(self.chunks)

    def close(self) -> None:
        self.closed = True


class FakeVar:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


class IPPSecurityTestCase(unittest.TestCase):
    def _success_response(self) -> FakeStreamingResponse:
        body = b"\x01\x01\x00\x00\x00\x00\x00\x01\x03"
        return FakeStreamingResponse(200, [body], headers={"Content-Length": str(len(body))})

    @staticmethod
    def _protocol_response(body: bytes) -> FakeStreamingResponse:
        return FakeStreamingResponse(200, [body], headers={"Content-Length": str(len(body))})

    @staticmethod
    def _attribute(tag: int, name: str, value: bytes) -> bytes:
        encoded_name = name.encode("utf-8")
        return (
            bytes([tag])
            + len(encoded_name).to_bytes(2, "big")
            + encoded_name
            + len(value).to_bytes(2, "big")
            + value
        )

    def _assert_protocol_rejected_by_client_and_receipt(self, body: bytes) -> None:
        direct_response = self._protocol_response(body)
        receipt_response = self._protocol_response(body)
        with tempfile.TemporaryDirectory() as directory, patch(
            "printing.ipp.requests.post", side_effect=[direct_response, receipt_response]
        ) as post:
            with self.assertRaises(ipp.IPPError) as raised:
                ipp.print_job("http://printer.local/p/virtual?access=private", b"private-receipt")
            result = ReceiptPrinter(
                {
                    "enabled": True,
                    "driver": "ipp",
                    "uri": "http://printer.local/p/virtual?access=private",
                    "queue_dir": directory,
                }
            ).print_receipt({"test_print": True, "printer_name": "virtual"})

        self.assertEqual(post.call_count, 2)
        self.assertTrue(direct_response.closed)
        self.assertTrue(receipt_response.closed)
        self.assertFalse(result.printed)
        error = "".join(traceback.format_exception(raised.exception))
        for secret in ("printer.local", "access=private", "private-receipt", "PRIVATE_RESPONSE"):
            self.assertNotIn(secret, error)
            self.assertNotIn(secret, result.message)
        self.assertNotIn(repr(body), error)

    def test_response_shorter_than_eight_byte_header_is_rejected(self) -> None:
        self._assert_protocol_rejected_by_client_and_receipt(b"\x01\x01\x00\x00\x00\x00\x00")

    def test_eight_byte_header_without_end_of_attributes_is_rejected(self) -> None:
        self._assert_protocol_rejected_by_client_and_receipt(b"\x01\x01\x00\x00\x00\x00\x00\x01")

    def test_invalid_or_unsupported_ipp_version_is_rejected(self) -> None:
        for version in (b"\x00\x00", b"\x09\x09"):
            with self.subTest(version=version.hex()):
                body = version + b"\x00\x00\x00\x00\x00\x01\x03PRIVATE_RESPONSE"
                self._assert_protocol_rejected_by_client_and_receipt(body)

    def test_response_request_id_must_match_submitted_job(self) -> None:
        body = b"\x01\x01\x00\x00\x00\x00\x00\x02\x03PRIVATE_RESPONSE"
        self._assert_protocol_rejected_by_client_and_receipt(body)

    def test_status_outside_success_class_is_rejected(self) -> None:
        body = b"\x01\x01\x04\x00\x00\x00\x00\x01\x03PRIVATE_RESPONSE"
        self._assert_protocol_rejected_by_client_and_receipt(body)

    def test_valid_nonzero_success_status_and_structured_job_id_are_preserved(self) -> None:
        job_id = 27
        job_attribute = (
            b"\x02"
            + b"\x21\x00\x06job-id\x00\x04"
            + job_id.to_bytes(4, "big")
            + b"\x03"
        )
        body = b"\x01\x01\x00\x01\x00\x00\x00\x01" + job_attribute
        response = self._protocol_response(body)
        with patch("printing.ipp.requests.post", return_value=response):
            result = ipp.print_job("http://printer.local/p/virtual", b"receipt")

        self.assertEqual(result["job_id"], job_id)
        self.assertTrue(response.closed)

    def test_job_id_and_state_without_attribute_group_are_rejected(self) -> None:
        body = (
            b"\x01\x01\x00\x00\x00\x00\x00\x01"
            + self._attribute(0x21, "job-id", (27).to_bytes(4, "big"))
            + self._attribute(0x23, "job-state", (5).to_bytes(4, "big"))
            + b"\x03"
        )
        self._assert_protocol_rejected_by_client_and_receipt(body)

    def test_integer_and_enum_attributes_require_exactly_four_value_bytes(self) -> None:
        cases = (
            self._attribute(0x21, "job-id", b"\x00\x1b"),
            self._attribute(0x23, "job-state", b"\x00\x00\x00\x00\x05"),
        )
        for attribute in cases:
            with self.subTest(tag=attribute[0], value_length=len(attribute)):
                body = b"\x01\x01\x00\x00\x00\x00\x00\x01\x02" + attribute + b"\x03"
                self._assert_protocol_rejected_by_client_and_receipt(body)

    def test_reserved_tags_25_and_27_are_rejected_for_job_attributes(self) -> None:
        for tag, name in ((0x25, "job-id"), (0x27, "job-state")):
            with self.subTest(tag=hex(tag), name=name):
                attribute = self._attribute(tag, name, (27).to_bytes(4, "big"))
                body = b"\x01\x01\x00\x00\x00\x00\x00\x01\x02" + attribute + b"\x03"
                self._assert_protocol_rejected_by_client_and_receipt(body)

    def test_generic_attribute_before_any_group_is_rejected(self) -> None:
        attribute = self._attribute(0x41, "status-message", b"PRIVATE_RESPONSE")
        body = b"\x01\x01\x00\x00\x00\x00\x00\x01" + attribute + b"\x03"
        self._assert_protocol_rejected_by_client_and_receipt(body)

    def test_all_supported_ipp_group_delimiters_allow_well_formed_attributes(self) -> None:
        attribute = self._attribute(0x41, "status-message", b"ok")
        for group_tag in (0x01, 0x02, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A):
            with self.subTest(group_tag=hex(group_tag)):
                body = b"\x01\x01\x00\x00\x00\x00\x00\x01" + bytes([group_tag]) + attribute + b"\x03"
                response = self._protocol_response(body)
                with patch("printing.ipp.requests.post", return_value=response):
                    result = ipp.print_job("http://printer.local/p/virtual", b"receipt")
                self.assertIsNone(result["job_id"])
                self.assertTrue(response.closed)

    def test_unknown_group_delimiter_is_rejected(self) -> None:
        attribute = self._attribute(0x41, "status-message", b"PRIVATE_RESPONSE")
        body = b"\x01\x01\x00\x00\x00\x00\x00\x01\x0b" + attribute + b"\x03"
        self._assert_protocol_rejected_by_client_and_receipt(body)

    def test_end_of_attributes_rejects_any_trailing_bytes(self) -> None:
        body = b"\x01\x01\x00\x00\x00\x00\x00\x01\x01\x03PRIVATE_RESPONSE"
        self._assert_protocol_rejected_by_client_and_receipt(body)

    def test_truncated_attribute_is_rejected_fail_closed(self) -> None:
        body = b"\x01\x01\x00\x00\x00\x00\x00\x01\x02\x21\x00\x06job"
        self._assert_protocol_rejected_by_client_and_receipt(body)

    def test_valid_grouped_job_id_and_state_are_preserved(self) -> None:
        body = (
            b"\x01\x01\x00\x00\x00\x00\x00\x01\x01"
            + self._attribute(0x47, "attributes-charset", b"utf-8")
            + b"\x02"
            + self._attribute(0x21, "job-id", (27).to_bytes(4, "big"))
            + self._attribute(0x23, "job-state", (5).to_bytes(4, "big"))
            + b"\x03"
        )
        response = self._protocol_response(body)
        with patch("printing.ipp.requests.post", return_value=response):
            result = ipp.print_job("http://printer.local/p/virtual", b"receipt")

        self.assertEqual(result, {"job_id": 27, "job_state": 5})
        self.assertTrue(response.closed)

    def test_redirects_are_never_followed_or_resent_for_same_or_cross_host(self) -> None:
        source = "http://printer.local:10631/p/virtual?access=private-query"
        locations = (
            "http://printer.local:10631/p/other?redirect=same-host-secret",
            "https://other.invalid/p/capture?redirect=cross-host-secret",
        )
        for status_code in (301, 302, 303, 307, 308):
            for location in locations:
                with self.subTest(status_code=status_code, location=location):
                    response = FakeStreamingResponse(
                        status_code,
                        [b"private redirect response body"],
                        headers={"Location": location},
                    )
                    with patch("printing.ipp.requests.post", return_value=response) as post:
                        with self.assertRaises(ipp.IPPError) as raised:
                            ipp.print_job(source, b"private receipt", timeout=1.75)

                    post.assert_called_once()
                    self.assertFalse(post.call_args.kwargs["allow_redirects"])
                    self.assertTrue(post.call_args.kwargs["stream"])
                    self.assertEqual(post.call_args.kwargs["timeout"], 1.75)
                    self.assertEqual(response.iter_calls, 0)
                    self.assertTrue(response.closed)
                    error = str(raised.exception)
                    for secret in (source, location, "private-query", "private receipt", "private redirect response body"):
                        self.assertNotIn(secret, error)

    def test_excessive_declared_content_length_is_rejected_before_body_read(self) -> None:
        response = FakeStreamingResponse(
            200,
            [b"not read"],
            headers={"Content-Length": str(MAX_RESPONSE_BYTES + 1)},
        )
        with patch("printing.ipp.requests.post", return_value=response):
            with self.assertRaises(ipp.IPPError) as raised:
                ipp.print_job("http://printer.local/p/virtual?access=private", b"receipt")

        self.assertEqual(response.iter_calls, 0)
        self.assertTrue(response.closed)
        self.assertIn("limite seguro", str(raised.exception))
        self.assertNotIn("private", str(raised.exception))

    def test_stream_without_content_length_is_strictly_limited(self) -> None:
        response = FakeStreamingResponse(200, [b"x" * MAX_RESPONSE_BYTES, b"y"])
        with patch("printing.ipp.requests.post", return_value=response):
            with self.assertRaises(ipp.IPPError) as raised:
                ipp.print_job("ipp://printer.local/p/virtual?access=private", b"receipt")

        self.assertEqual(response.iter_calls, 1)
        self.assertTrue(response.closed)
        self.assertIn("limite seguro", str(raised.exception))
        self.assertNotIn("private", str(raised.exception))

    def test_stream_read_failure_is_converted_to_safe_error(self) -> None:
        failures = (
            requests.RequestException(
                "failed at http://printer.local/p/virtual?access=private with response private-body"
            ),
            RuntimeError(
                "unexpected reader failure at http://printer.local/p/virtual?access=private: private-body"
            ),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                response = FakeStreamingResponse(200, [], stream_error=failure)
                with patch("printing.ipp.requests.post", return_value=response):
                    with self.assertRaises(ipp.IPPError) as raised:
                        ipp.print_job("http://printer.local/p/virtual?access=private", b"private-receipt")

                self.assertTrue(response.closed)
                error = str(raised.exception)
                formatted_error = "".join(traceback.format_exception(raised.exception))
                for secret in ("printer.local", "access=private", "private-body", "private-receipt"):
                    self.assertNotIn(secret, error)
                    self.assertNotIn(secret, formatted_error)

    def test_submission_failure_traceback_does_not_expose_request_details(self) -> None:
        failure = requests.RequestException(
            "failed at http://printer.local/p/virtual?access=private with private-receipt"
        )
        with patch("printing.ipp.requests.post", side_effect=failure):
            with self.assertRaises(ipp.IPPError) as raised:
                ipp.print_job("http://printer.local/p/virtual?access=private", b"private-receipt")

        formatted_error = "".join(traceback.format_exception(raised.exception))
        for secret in ("printer.local", "access=private", "private-receipt"):
            self.assertNotIn(secret, str(raised.exception))
            self.assertNotIn(secret, formatted_error)

    def test_malformed_success_response_has_safe_protocol_error(self) -> None:
        body = b"\x01\x01\x00\x00\x00\x00\x00\x01\x21\xff\xff"
        response = FakeStreamingResponse(200, [body], headers={"Content-Length": str(len(body))})
        with patch("printing.ipp.requests.post", return_value=response):
            with self.assertRaises(ipp.IPPError) as raised:
                ipp.print_job("http://printer.local/p/virtual?access=private", b"private-receipt")

        self.assertEqual(str(raised.exception), "resposta IPP inválida")

    def test_success_uses_streaming_preserves_timeout_and_allows_local_http(self) -> None:
        response = self._success_response()
        with patch("printing.ipp.requests.post", return_value=response) as post:
            result = ipp.print_job("http://printer.local:10631/p/virtual", b"teste", timeout=2.25)

        self.assertIsNone(result["job_id"])
        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], "http://printer.local:10631/p/virtual")
        self.assertEqual(post.call_args.kwargs["timeout"], 2.25)
        self.assertFalse(post.call_args.kwargs["allow_redirects"])
        self.assertTrue(post.call_args.kwargs["stream"])
        self.assertEqual(response.iter_calls, 1)
        self.assertTrue(response.closed)

    def test_plaintext_schemes_warn_and_secure_schemes_are_preferred(self) -> None:
        for uri in ("http://printer.local/p/virtual", "ipp://printer.local/p/virtual"):
            with self.subTest(uri=uri):
                diagnostic = ipp.transport_security(uri)
                self.assertFalse(diagnostic["encrypted"])
                self.assertIn("não criptografada", diagnostic["warning"])
                self.assertIn("HTTPS/IPPS", diagnostic["warning"])

        for uri in ("https://printer.local/p/virtual", "ipps://printer.local/p/virtual"):
            with self.subTest(uri=uri):
                diagnostic = ipp.transport_security(uri)
                self.assertTrue(diagnostic["encrypted"])
                self.assertEqual(diagnostic["warning"], "")

    def test_receipt_diagnostic_warns_but_does_not_block_plaintext_ipp(self) -> None:
        with patch("printing.ipp.print_job", return_value={"job_id": 4, "job_state": 5}) as submit:
            result = ReceiptPrinter(
                {"enabled": True, "driver": "ipp", "uri": "http://printer.local/p/virtual"}
            ).print_receipt({"test_print": True, "printer_name": "virtual"})

        self.assertTrue(result.printed)
        self.assertIn("não criptografada", result.message)
        self.assertIn("HTTPS/IPPS", result.message)
        submit.assert_called_once()

    def test_controller_configuration_exposes_plaintext_warning_without_uri(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "pdv.sqlite3")
            service = PDVService(database=database)
            provision_test_admin(database)
            settings = replace(
                service.settings,
                printer_enabled=True,
                printer_driver="ipp",
                printer_name="Virtual IPP",
                printer_uri="http://printer.local/p/virtual?access=private",
            )
            service.settings = settings
            controller = DesktopController(service, settings)
            controller.authenticate(TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD)
            with patch("desktop_controller.list_windows_printers", return_value=[]), patch(
                "desktop_controller.default_printer_name", return_value=""
            ):
                configuration = controller.printer_configuration()

        self.assertEqual(configuration["driver"], "ipp")
        self.assertFalse(configuration["transport_encrypted"])
        self.assertIn("não criptografada", configuration["transport_warning"])
        self.assertNotIn("printer.local", repr(configuration))
        self.assertNotIn("access=private", repr(configuration))

    def test_admin_view_displays_transport_warning_from_safe_configuration(self) -> None:
        view = object.__new__(AdminView)
        view.printer_selected_var = FakeVar("Virtual IPP")
        view.printer_status_var = FakeVar()
        view.printer_options = ["Virtual IPP"]

        AdminView._update_printer_status(
            view,
            {
                "driver": "ipp",
                "configured_name": "Virtual IPP",
                "status": "IPP configurada — use Testar impressão para confirmar a comunicação.",
                "transport_warning": "Aviso: conexão IPP não criptografada. Prefira HTTPS/IPPS quando houver suporte.",
            },
        )

        self.assertIn("IPP configurada", view.printer_status_var.get())
        self.assertIn("não criptografada", view.printer_status_var.get())


if __name__ == "__main__":
    unittest.main()

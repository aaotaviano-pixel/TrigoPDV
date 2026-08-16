"""Regressoes de seguranca para hashes de senha persistidos."""

from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from db.database import Database
from services import passwords
from services.auth import AuthService
from services.errors import ValidationError
from services.provisioning import ProvisioningService
from tests.support import (
    TEST_ADMIN_LOGIN,
    TEST_ADMIN_NAME,
    TEST_ADMIN_PASSWORD,
    TEST_RECOVERY_CODE,
    provision_test_admin,
)


VALID_SALT = base64.b64encode(b"s" * 16).decode("ascii")
VALID_DIGEST = base64.b64encode(b"d" * 64).decode("ascii")
LONG_UTF8_PASSWORD_A = ("😀" * 18) + "A"
LONG_UTF8_PASSWORD_B = ("😀" * 18) + "B"


def _scrypt_hash(
    n: str = "16384",
    r: str = "8",
    p: str = "1",
    salt: str = VALID_SALT,
    digest: str = VALID_DIGEST,
) -> str:
    return f"scrypt${n}${r}${p}${salt}${digest}"


def _with_noncanonical_pad_bits(encoded: str) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    if not encoded.endswith("=="):
        raise AssertionError("A fixture precisa terminar com dois paddings.")
    index = alphabet.index(encoded[-3])
    if index % 16 != 0:
        raise AssertionError("A fixture precisa usar padding canonico.")
    return encoded[:-3] + alphabet[index + 1] + "=="


class PasswordHashHardeningTestCase(unittest.TestCase):
    def _assert_scrypt_rejected_without_kdf(self, stored_hashes: tuple[object, ...]) -> None:
        with patch.object(
            passwords.hashlib,
            "scrypt",
            side_effect=AssertionError("scrypt nao pode rodar para hash invalido"),
        ) as mocked_scrypt:
            for stored_hash in stored_hashes:
                with self.subTest(stored_hash=repr(stored_hash)[:100]):
                    self.assertFalse(passwords.verify_password("SenhaSegura8", stored_hash))
        mocked_scrypt.assert_not_called()

    def test_malformed_unknown_and_oversized_hashes_are_rejected_before_scrypt(self) -> None:
        self._assert_scrypt_rejected_without_kdf(
            (
                None,
                b"scrypt",
                "",
                "scrypt",
                "scrypt$16384$8$1",
                _scrypt_hash() + "$campo-extra",
                "argon2id$16384$8$1$" + VALID_SALT + "$" + VALID_DIGEST,
                "scrypt$16384$8$1$" + ("A" * 10_000) + "$" + VALID_DIGEST,
            )
        )

    def test_invalid_base64_and_noncanonical_lengths_are_rejected_before_scrypt(self) -> None:
        self._assert_scrypt_rejected_without_kdf(
            (
                _scrypt_hash(salt="!" * len(VALID_SALT)),
                _scrypt_hash(digest="!" * len(VALID_DIGEST)),
                _scrypt_hash(salt=base64.b64encode(b"s" * 15).decode("ascii")),
                _scrypt_hash(salt=base64.b64encode(b"s" * 17).decode("ascii")),
                _scrypt_hash(digest=base64.b64encode(b"d" * 63).decode("ascii")),
                _scrypt_hash(digest=base64.b64encode(b"d" * 65).decode("ascii")),
            )
        )

    def test_noncanonical_base64_pad_bits_are_rejected_before_scrypt(self) -> None:
        noncanonical_salt = _with_noncanonical_pad_bits(VALID_SALT)
        noncanonical_digest = _with_noncanonical_pad_bits(VALID_DIGEST)
        self.assertEqual(base64.b64decode(noncanonical_salt, validate=True), b"s" * 16)
        self.assertEqual(base64.b64decode(noncanonical_digest, validate=True), b"d" * 64)
        self._assert_scrypt_rejected_without_kdf(
            (
                _scrypt_hash(salt=noncanonical_salt),
                _scrypt_hash(digest=noncanonical_digest),
            )
        )

    def test_untrusted_scrypt_parameters_are_rejected_before_scrypt(self) -> None:
        invalid_parameters = (
            ("2147483648", "8", "1"),
            ("32768", "8", "1"),
            ("-16384", "8", "1"),
            ("0", "8", "1"),
            ("True", "8", "1"),
            ("16384", "2147483648", "1"),
            ("16384", "-8", "1"),
            ("16384", "0", "1"),
            ("16384", "False", "1"),
            ("16384", "8", "2147483648"),
            ("16384", "8", "-1"),
            ("16384", "8", "0"),
            ("16384", "8", "True"),
        )
        self._assert_scrypt_rejected_without_kdf(
            tuple(_scrypt_hash(n=n, r=r, p=p) for n, r, p in invalid_parameters)
        )

    @unittest.skipIf(passwords.bcrypt is None, "bcrypt nao esta instalado")
    def test_malformed_or_expensive_bcrypt_is_rejected_before_checkpw(self) -> None:
        assert passwords.bcrypt is not None
        with patch.object(
            passwords.bcrypt,
            "checkpw",
            side_effect=AssertionError("bcrypt nao pode rodar para hash invalido"),
        ) as mocked_checkpw:
            invalid_hashes = (
                "$2b$31$" + ("." * 53),
                "$2b$04$" + ("." * 53),
                "$2x$12$" + ("." * 53),
                "$2b$12$" + ("!" * 53),
                "$2b$12$" + ("." * 54),
                "$2b$12$" + ("." * 10_000),
            )
            for stored_hash in invalid_hashes:
                with self.subTest(stored_hash=stored_hash[:20]):
                    self.assertFalse(passwords.verify_password("SenhaSegura8", stored_hash))
        mocked_checkpw.assert_not_called()

    def test_current_scrypt_hash_remains_verifiable(self) -> None:
        with patch.object(passwords, "bcrypt", None):
            stored_hash = passwords.hash_password("SenhaSegura8")
            self.assertTrue(passwords.verify_password("SenhaSegura8", stored_hash))
            self.assertFalse(passwords.verify_password("SenhaIncorreta8", stored_hash))

    def test_scrypt_fallback_and_legacy_verification_preserve_long_utf8_password(self) -> None:
        self.assertEqual(len(LONG_UTF8_PASSWORD_A), 19)
        self.assertEqual(len(LONG_UTF8_PASSWORD_A.encode("utf-8")), 73)
        with patch.object(passwords, "bcrypt", None):
            stored_hash = passwords.hash_password(LONG_UTF8_PASSWORD_A)
        self.assertTrue(passwords.verify_password(LONG_UTF8_PASSWORD_A, stored_hash))
        self.assertFalse(passwords.verify_password(LONG_UTF8_PASSWORD_B, stored_hash))

    @unittest.skipIf(passwords.bcrypt is None, "bcrypt nao esta instalado")
    def test_current_bcrypt_hash_remains_verifiable(self) -> None:
        stored_hash = passwords.hash_password("SenhaSegura8")
        self.assertTrue(stored_hash.startswith("$2b$12$"))
        self.assertTrue(passwords.verify_password("SenhaSegura8", stored_hash))
        self.assertFalse(passwords.verify_password("SenhaIncorreta8", stored_hash))

    @unittest.skipIf(passwords.bcrypt is None, "bcrypt nao esta instalado")
    def test_bcrypt_2a_and_2y_cost_twelve_remain_verifiable(self) -> None:
        stored_hash = passwords.hash_password("SenhaSegura8")
        for prefix in ("$2a$", "$2y$"):
            with self.subTest(prefix=prefix):
                variant = prefix + stored_hash[4:]
                self.assertTrue(passwords.verify_password("SenhaSegura8", variant))
                self.assertFalse(passwords.verify_password("SenhaIncorreta8", variant))

    @unittest.skipIf(passwords.bcrypt is None, "bcrypt nao esta instalado")
    def test_bcrypt_rejects_passwords_that_only_diverge_after_byte_seventy_two(self) -> None:
        assert passwords.bcrypt is not None
        self.assertEqual(LONG_UTF8_PASSWORD_A.encode("utf-8")[:72], LONG_UTF8_PASSWORD_B.encode("utf-8")[:72])
        self.assertNotEqual(LONG_UTF8_PASSWORD_A, LONG_UTF8_PASSWORD_B)
        seventy_two_byte_password = "😀" * 18
        stored_hash = passwords.hash_password(seventy_two_byte_password)
        self.assertTrue(passwords.verify_password(seventy_two_byte_password, stored_hash))
        with patch.object(
            passwords.bcrypt,
            "hashpw",
            side_effect=AssertionError("bcrypt nao pode receber senha acima de 72 bytes"),
        ) as mocked_hashpw:
            for candidate in (LONG_UTF8_PASSWORD_A, LONG_UTF8_PASSWORD_B):
                with self.subTest(last_character=candidate[-1]):
                    with self.assertRaises(ValidationError) as captured:
                        passwords.hash_password(candidate)
                    self.assertNotIn(candidate, str(captured.exception))
        mocked_hashpw.assert_not_called()
        with patch.object(
            passwords.bcrypt,
            "checkpw",
            side_effect=AssertionError("bcrypt nao pode verificar senha acima de 72 bytes"),
        ) as mocked_checkpw:
            self.assertFalse(passwords.verify_password(LONG_UTF8_PASSWORD_A, stored_hash))
            self.assertFalse(passwords.verify_password(LONG_UTF8_PASSWORD_B, stored_hash))
        mocked_checkpw.assert_not_called()


@unittest.skipIf(passwords.bcrypt is None, "bcrypt nao esta instalado")
class PasswordUtf8LimitIntegrationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_provisioning_rejects_overlong_utf8_before_bcrypt_and_any_write(self) -> None:
        assert passwords.bcrypt is not None
        database = Database(self.root / "provisioning.sqlite3")
        database.initialize()
        with patch.object(
            passwords.bcrypt,
            "hashpw",
            side_effect=AssertionError("bcrypt nao pode receber senha acima de 72 bytes"),
        ) as mocked_hashpw:
            with self.assertRaises(ValidationError) as captured:
                ProvisioningService(database).provision_initial_admin(
                    TEST_ADMIN_NAME,
                    TEST_ADMIN_LOGIN,
                    LONG_UTF8_PASSWORD_A,
                    TEST_RECOVERY_CODE,
                )
        mocked_hashpw.assert_not_called()
        self.assertNotIn(LONG_UTF8_PASSWORD_A, str(captured.exception))
        self.assertEqual(database.fetch_one("SELECT COUNT(*) AS total FROM usuarios")["total"], 0)
        self.assertEqual(database.fetch_one("SELECT state FROM installation_state")["state"], "UNINITIALIZED")

    def test_user_creation_and_password_change_reject_before_bcrypt_and_preserve_rows(self) -> None:
        assert passwords.bcrypt is not None
        database = Database(self.root / "auth.sqlite3")
        database.initialize()
        admin = provision_test_admin(database)
        auth = AuthService(database)
        cashier = auth.create_user(
            "Caixa UTF-8",
            "caixa.utf8",
            "SenhaInicial8",
            "caixa",
            actor_id=admin["id"],
        )
        original_hash = database.fetch_one(
            "SELECT senha_hash FROM usuarios WHERE id = ?", (cashier["id"],)
        )["senha_hash"]

        with patch.object(
            passwords.bcrypt,
            "hashpw",
            side_effect=AssertionError("bcrypt nao pode receber senha acima de 72 bytes"),
        ) as mocked_hashpw:
            with self.assertRaises(ValidationError) as create_error:
                auth.create_user(
                    "Outra Caixa",
                    "outra.caixa.utf8",
                    LONG_UTF8_PASSWORD_A,
                    "caixa",
                    actor_id=admin["id"],
                )
            with self.assertRaises(ValidationError) as change_error:
                auth.change_password(
                    cashier["id"],
                    "SenhaInicial8",
                    LONG_UTF8_PASSWORD_B,
                    actor_id=cashier["id"],
                )
        mocked_hashpw.assert_not_called()
        self.assertNotIn(LONG_UTF8_PASSWORD_A, str(create_error.exception))
        self.assertNotIn(LONG_UTF8_PASSWORD_B, str(change_error.exception))
        self.assertIsNone(database.fetch_one("SELECT id FROM usuarios WHERE login = 'outra.caixa.utf8'"))
        unchanged = database.fetch_one(
            "SELECT senha_hash, deve_trocar_senha FROM usuarios WHERE id = ?", (cashier["id"],)
        )
        self.assertEqual(unchanged["senha_hash"], original_hash)
        self.assertEqual(unchanged["deve_trocar_senha"], 1)


if __name__ == "__main__":
    unittest.main()

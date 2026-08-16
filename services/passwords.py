"""Hash seguro de senhas com bcrypt e fallback scrypt da biblioteca padrão."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import re
from typing import Final

from .errors import ValidationError

try:  # bcrypt é a opção preferida e declarada em requirements.txt.
    import bcrypt  # type: ignore
except ImportError:  # Mantém o primeiro início funcional até instalar dependências.
    bcrypt = None


SCRYPT_N: Final[int] = 2**14
SCRYPT_R: Final[int] = 8
SCRYPT_P: Final[int] = 1
SCRYPT_SALT_BYTES: Final[int] = 16
SCRYPT_DIGEST_BYTES: Final[int] = 64
BCRYPT_ROUNDS: Final[int] = 12
BCRYPT_MAX_PASSWORD_CHARACTERS: Final[int] = 72
BCRYPT_MAX_PASSWORD_BYTES: Final[int] = 72
MAX_PASSWORD_CHARACTERS: Final[int] = 256
MAX_STORED_HASH_LENGTH: Final[int] = 256
_BCRYPT_HASH_PATTERN: Final[re.Pattern[str]] = re.compile(
    rf"\A\$(?:2a|2b|2y)\${BCRYPT_ROUNDS:02d}\$[./A-Za-z0-9]{{53}}\Z",
    re.ASCII,
)


def validate_new_password(password: str) -> str:
    if not isinstance(password, str) or len(password) < 8:
        raise ValidationError("A senha deve ter pelo menos 8 caracteres.")
    if len(password) > BCRYPT_MAX_PASSWORD_CHARACTERS and bcrypt is not None:
        raise ValidationError("A senha deve ter no máximo 72 caracteres.")
    if len(password) > MAX_PASSWORD_CHARACTERS:
        raise ValidationError("A senha deve ter no máximo 256 caracteres.")
    try:
        encoded = password.encode("utf-8")
    except UnicodeError:
        raise ValidationError("A senha contém caracteres inválidos.") from None
    if bcrypt is not None and len(encoded) > BCRYPT_MAX_PASSWORD_BYTES:
        raise ValidationError("A senha é longa demais. Reduza o tamanho e tente novamente.")
    return password


def hash_password(password: str) -> str:
    """Retorna hash independente de SHA puro, nunca a senha em claro."""

    password = validate_new_password(password)
    encoded = password.encode("utf-8")
    if bcrypt is not None:
        return bcrypt.hashpw(encoded, bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("utf-8")
    salt = os.urandom(SCRYPT_SALT_BYTES)
    derived = hashlib.scrypt(
        encoded,
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DIGEST_BYTES,
    )
    return "scrypt${}${}${}${}${}".format(
        SCRYPT_N,
        SCRYPT_R,
        SCRYPT_P,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(derived).decode("ascii"),
    )


def verify_password(password: str, stored_hash: str) -> bool:
    if (
        not isinstance(password, str)
        or len(password) > MAX_PASSWORD_CHARACTERS
        or not isinstance(stored_hash, str)
        or len(stored_hash) > MAX_STORED_HASH_LENGTH
    ):
        return False
    try:
        encoded_password = password.encode("utf-8")
        if stored_hash.startswith("$"):
            if (
                bcrypt is None
                or len(encoded_password) > BCRYPT_MAX_PASSWORD_BYTES
                or _BCRYPT_HASH_PATTERN.fullmatch(stored_hash) is None
            ):
                return False
            return bool(bcrypt.checkpw(encoded_password, stored_hash.encode("utf-8")))
        parts = stored_hash.split("$")
        if len(parts) != 6 or tuple(parts[:4]) != (
            "scrypt",
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
        ):
            return False
        if len(parts[4]) != 24 or len(parts[5]) != 88:
            return False
        salt = base64.b64decode(parts[4].encode("ascii"), validate=True)
        expected = base64.b64decode(parts[5].encode("ascii"), validate=True)
        if (
            len(salt) != SCRYPT_SALT_BYTES
            or len(expected) != SCRYPT_DIGEST_BYTES
            or base64.b64encode(salt).decode("ascii") != parts[4]
            or base64.b64encode(expected).decode("ascii") != parts[5]
        ):
            return False
        actual = hashlib.scrypt(
            encoded_password,
            salt=salt,
            n=SCRYPT_N,
            r=SCRYPT_R,
            p=SCRYPT_P,
            dklen=SCRYPT_DIGEST_BYTES,
        )
        return hmac.compare_digest(actual, expected)
    except (binascii.Error, ValueError, TypeError, UnicodeError):
        return False

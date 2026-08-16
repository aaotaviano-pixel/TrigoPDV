"""Exclusão entre processos vinculada ao caminho canônico do banco.

No Windows, o lock é o *handle* exclusivo mantido pelo kernel, não o conteúdo
nem a existência do arquivo. Assim, um encerramento abrupto libera a instância
automaticamente e um arquivo antigo pode permanecer com segurança.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import threading
from typing import Self


_ERROR_SHARING_VIOLATION = 32
_ERROR_LOCK_VIOLATION = 33
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_OPEN_ALWAYS = 4
_FILE_ATTRIBUTE_NORMAL = 0x00000080
_HANDLE_FLAG_INHERIT = 0x00000001

_ALREADY_RUNNING_MESSAGE = "O TrigoPDV já está aberto para este banco de dados."
_SAFE_FAILURE_MESSAGE = (
    "Não foi possível reservar a execução segura do TrigoPDV. "
    "Feche o programa e tente novamente."
)


class SingleInstanceError(RuntimeError):
    """Falha segura ao reservar a instância associada ao banco."""


def _canonical_database_path(database_path: str | Path) -> tuple[Path, str]:
    resolved = Path(database_path).expanduser().resolve(strict=False)
    canonical = os.path.normcase(os.path.normpath(str(resolved)))
    return resolved, canonical


def _win32_api():
    if os.name != "nt":
        raise SingleInstanceError(
            "A proteção de instância deste aplicativo requer o Windows."
        )
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.SetHandleInformation.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    kernel32.SetHandleInformation.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def _as_extended_win32_path(path: Path) -> str:
    """Evita o limite legado MAX_PATH sem alterar a identidade do lock."""

    raw = str(path.resolve(strict=False))
    if raw.startswith("\\\\?\\"):
        return raw
    if raw.startswith("\\\\"):
        return "\\\\?\\UNC\\" + raw[2:]
    return "\\\\?\\" + raw


class SingleInstanceGuard:
    """Mantém um arquivo aberto sem compartilhamento durante toda a execução."""

    def __init__(self, database_path: str | Path, *, lock_dir: Path | None = None):
        try:
            resolved_database, canonical_database = _canonical_database_path(database_path)
            directory = (
                Path(lock_dir).expanduser().resolve(strict=False)
                if lock_dir is not None
                else resolved_database.parent / ".trigopdv-runtime"
            )
        except (OSError, RuntimeError) as exc:
            raise SingleInstanceError(_SAFE_FAILURE_MESSAGE) from exc
        digest = hashlib.sha256(
            canonical_database.encode("utf-8", errors="surrogatepass")
        ).hexdigest()
        self.lock_path = directory / f"trigopdv-{digest}.lock"
        self._handle: int | None = None
        self._state_lock = threading.RLock()

    def acquire(self) -> Self:
        """Adquire o handle exclusivo; chamadas repetidas no mesmo guard são neutras."""

        with self._state_lock:
            if self._handle is not None:
                return self
            try:
                self.lock_path.parent.mkdir(parents=True, exist_ok=True)
                kernel32 = _win32_api()
                handle = kernel32.CreateFileW(
                    _as_extended_win32_path(self.lock_path),
                    _GENERIC_READ | _GENERIC_WRITE,
                    0,
                    None,
                    _OPEN_ALWAYS,
                    _FILE_ATTRIBUTE_NORMAL,
                    None,
                )
            except SingleInstanceError:
                raise
            except OSError as exc:
                raise SingleInstanceError(_SAFE_FAILURE_MESSAGE) from exc

            invalid_handle = wintypes.HANDLE(-1).value
            if handle == invalid_handle:
                error_code = ctypes.get_last_error()
                if error_code in {_ERROR_SHARING_VIOLATION, _ERROR_LOCK_VIOLATION}:
                    raise SingleInstanceError(_ALREADY_RUNNING_MESSAGE)
                raise SingleInstanceError(_SAFE_FAILURE_MESSAGE)

            if not kernel32.SetHandleInformation(
                handle,
                _HANDLE_FLAG_INHERIT,
                0,
            ):
                kernel32.CloseHandle(handle)
                raise SingleInstanceError(_SAFE_FAILURE_MESSAGE)

            self._handle = handle
            return self

    def release(self) -> None:
        """Fecha o handle uma única vez e preserva o arquivo de lock."""

        with self._state_lock:
            handle = self._handle
            if handle is None:
                return
            try:
                kernel32 = _win32_api()
                closed = bool(kernel32.CloseHandle(handle))
            except (OSError, SingleInstanceError) as exc:
                raise SingleInstanceError(_SAFE_FAILURE_MESSAGE) from exc
            if not closed:
                raise SingleInstanceError(_SAFE_FAILURE_MESSAGE)
            self._handle = None

    def __enter__(self) -> Self:
        return self.acquire()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.release()

    def __del__(self) -> None:
        """Última defesa para usos fora de context manager; nunca lança no GC."""

        try:
            self.release()
        except BaseException:
            pass

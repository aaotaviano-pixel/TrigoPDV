"""Adaptação mínima do cache TUF para Windows sem privilégio de symlink."""

from __future__ import annotations

from pathlib import Path

from tuf.ngclient import Updater


class WindowsSafeUpdater(Updater):
    """Mantém `root.json` como cópia atômica, não como symlink.

    Python-TUF 7 cria um symlink no construtor. Windows normalmente nega essa
    operação a um usuário de caixa sem Developer Mode. O histórico versionado
    continua sendo persistido e validado pela implementação upstream; apenas o
    alias de compatibilidade vira arquivo regular.
    """

    def _update_root_symlink(self) -> None:
        version = int(self._trusted_set.root.version)
        source = Path(self._dir) / "root_history" / f"{version}.root.json"
        data = source.read_bytes()
        self._persist_file(str(Path(self._dir) / "root.json"), data)

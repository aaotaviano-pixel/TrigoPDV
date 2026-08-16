"""Gera metadados do Inno/PyInstaller a partir de release/version.toml."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.version import load_release_metadata


def render_inno(version: str, sequence: int, schema_target: int, pack_id: str) -> str:
    return (
        "; Gerado por tools/render_release_metadata.py. Não editar manualmente.\n"
        f'#define TrigoVersion "{version}"\n'
        f"#define TrigoReleaseSequence {sequence}\n"
        f"#define TrigoSchemaTarget {schema_target}\n"
        f'#define TrigoPackId "{pack_id}"\n'
    )


def render_pe(version: str) -> str:
    numeric = tuple(int(part) for part in version.split(".")) + (0,)
    return f'''# UTF-8
# Gerado por tools/render_release_metadata.py. Não editar manualmente.
VSVersionInfo(
  ffi=FixedFileInfo(filevers={numeric}, prodvers={numeric}, mask=0x3f,
    flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('041604B0', [
    StringStruct('CompanyName', 'Padaria Trigo de Minas'),
    StringStruct('FileDescription', 'Sistema de ponto de venda TrigoPDV'),
    StringStruct('FileVersion', '{version}'),
    StringStruct('InternalName', 'TrigoPDV'),
    StringStruct('OriginalFilename', 'TrigoPDV.exe'),
    StringStruct('ProductName', 'TrigoPDV'),
    StringStruct('ProductVersion', '{version}')
  ])]), VarFileInfo([VarStruct('Translation', [1046, 1200])])]
)
'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    metadata = load_release_metadata(ROOT / "release" / "version.toml")
    outputs = {
        ROOT / "release" / "version.iss": render_inno(
            metadata.version, metadata.sequence, metadata.schema_target, metadata.pack_id
        ),
        ROOT / "release" / "version_info.txt": render_pe(metadata.version),
    }
    if args.check:
        stale = [path for path, content in outputs.items() if not path.exists() or path.read_text(encoding="utf-8") != content]
        if stale:
            print("Metadados de versão desatualizados.", file=sys.stderr)
            return 1
        return 0
    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


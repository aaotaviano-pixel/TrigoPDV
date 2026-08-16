from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from config.version import RELEASE
from db.schema import SCHEMA_VERSION


ROOT = Path(__file__).resolve().parent.parent


class ReleaseVersionTestCase(unittest.TestCase):
    def test_runtime_release_matches_database_schema(self) -> None:
        self.assertEqual(RELEASE.version, "1.1.0")
        self.assertEqual(RELEASE.sequence, 2)
        self.assertEqual(RELEASE.schema_target, SCHEMA_VERSION)
        self.assertEqual(RELEASE.pack_id, "TrigoDeMinas.TrigoPDV")

    def test_generated_installer_metadata_matches_single_source(self) -> None:
        generated = (ROOT / "release" / "version.iss").read_text(encoding="utf-8")
        self.assertIn(f'#define TrigoVersion "{RELEASE.version}"', generated)
        self.assertIn(f"#define TrigoReleaseSequence {RELEASE.sequence}", generated)
        self.assertIn(f"#define TrigoSchemaTarget {RELEASE.schema_target}", generated)
        installer = (ROOT / "installer" / "TrigoPDV.iss").read_text(encoding="utf-8")
        self.assertIn('#include "..\\release\\version.iss"', installer)
        self.assertNotRegex(installer, re.compile(r'#define\s+MyAppVersion\s+"'))
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "render_release_metadata.py"), "--check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_build_uses_committed_spec_and_hash_locked_dependencies(self) -> None:
        build = (ROOT / "build_release.bat").read_text(encoding="utf-8")
        self.assertIn("--require-hashes", build)
        self.assertIn("requirements.lock", build)
        self.assertIn("TrigoPDV.spec", build)
        self.assertNotIn("del /q TrigoPDV.spec", build)
        self.assertNotIn("pip install --upgrade pip", build)
        installer = (ROOT / "TrigoPDV_Instalacao_PenDrive" / "instalador" / "Instalar_TrigoPDV.cmd").read_text(encoding="utf-8")
        self.assertIn("ProductVersion", installer)
        self.assertIn("1.1.0", installer)

    def test_windows_build_uses_a_supported_python_with_official_binaries(self) -> None:
        build = (ROOT / "build_release.bat").read_text(encoding="utf-8")
        self.assertIn("py -3.13", build)
        self.assertIn("(3, 13, 14)", build)
        self.assertNotIn("3.12.13", build)
        lock = (ROOT / "requirements.lock").read_text(encoding="utf-8")
        self.assertIn("Python 3.13", lock)

    def test_installer_builder_detects_machine_and_user_scope_inno_setup(self) -> None:
        build = (ROOT / "installer" / "build_installer.bat").read_text(encoding="utf-8")
        self.assertIn("%LOCALAPPDATA%\\Programs\\Inno Setup 6\\ISCC.exe", build)
        self.assertIn("%ProgramFiles%\\Inno Setup 6\\ISCC.exe", build)

    def test_lock_digest_is_independent_of_windows_line_endings(self) -> None:
        import tools.release_gate as release_gate

        self.assertTrue(hasattr(release_gate, "_normalized_text_sha256"))

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lf = root / "lf.lock"
            crlf = root / "crlf.lock"
            lf.write_bytes(b"package==1.0 --hash=sha256:abc\nnext==2.0\n")
            crlf.write_bytes(b"package==1.0 --hash=sha256:abc\r\nnext==2.0\r\n")
            self.assertEqual(
                release_gate._normalized_text_sha256(lf),
                release_gate._normalized_text_sha256(crlf),
            )

    def test_source_release_gate_passes_and_production_fails_closed_without_keys(self) -> None:
        source = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "release_gate.py")],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(source.returncode, 0, source.stderr)
        environment = dict(__import__("os").environ)
        environment.pop("TRIGOPDV_UPDATE_BASE_URL", None)
        production = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "release_gate.py"), "--production"],
            cwd=ROOT, capture_output=True, text=True, check=False, env=environment,
        )
        self.assertNotEqual(production.returncode, 0)
        self.assertIn("GATE BLOQUEADO", production.stderr)


if __name__ == "__main__":
    unittest.main()

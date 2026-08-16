from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from tools.stage_usb_installer import UsbStageError, stage_usb_package, verify_usb_package


class UsbInstallerStagingTestCase(unittest.TestCase):
    def test_stages_setup_and_writes_verified_manifest_last(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            source = root / "source"
            (source / "dados-iniciais").mkdir(parents=True)
            (source / "INSTALAR.txt").write_text("instruções", encoding="utf-8")
            (source / "dados-iniciais/catalogo-produtos.sqlite3").write_bytes(b"catalog")
            (source / "dados-iniciais/catalogo-produtos.manifest.json").write_text(
                "{}", encoding="utf-8"
            )
            setup = root / "TrigoPDV-Setup.exe"
            setup.write_bytes(b"velopack setup")

            result = stage_usb_package(source, setup, root / "staged")

            self.assertEqual(result.manifest.name, "MANIFESTO-SHA256.txt")
            self.assertTrue((result.root / "TrigoPDV-Setup.exe").is_file())
            self.assertTrue(verify_usb_package(result.root))
            lines = result.manifest.read_text(encoding="utf-8").splitlines()
            paths = [line.split(" *", 1)[1] for line in lines]
            self.assertEqual(paths, sorted(paths, key=str.casefold))
            self.assertFalse(any("MANIFESTO-SHA256.txt" in line for line in lines))

    def test_rejects_operational_database_sidecars_and_stale_generated_files(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            setup = root / "setup.exe"
            setup.write_bytes(b"setup")
            for name in ("trigo_pdv.sqlite3", "catalogo-produtos.sqlite3-wal", "config.ini"):
                with self.subTest(name=name):
                    candidate = source / name
                    candidate.write_bytes(b"private runtime data")
                    with self.assertRaises(UsbStageError):
                        stage_usb_package(source, setup, root / f"stage-{name.replace('.', '-')}")
                    candidate.unlink()


if __name__ == "__main__":
    unittest.main()

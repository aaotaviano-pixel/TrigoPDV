from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zipfile

from tools.stage_usb_installer import UsbStageError, stage_usb_package, verify_usb_package


ROOT = Path(__file__).resolve().parent.parent
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")


class UsbInstallerStagingTestCase(unittest.TestCase):
    @staticmethod
    def _complete_source(root: Path) -> Path:
        source = root / "source"
        files = {
            "INSTALAR.txt": "instruções",
            "VERSAO.txt": "1.2.1",
            "config-impressora/LEIA-ME.md": "impressora",
            "config-impressora/Listar_Impressoras.ps1": "Get-Printer",
            "config-impressora/config.ini.exemplo": "[printing]",
            "dados-iniciais/catalogo-produtos.manifest.json": "{}",
            "instalador/Instalar_TrigoPDV.cmd": "@echo off",
            "instalador/Migrar_Instalacao_Legada.ps1": "param()",
            "instalador/Verificar_Pacote.ps1": "param()",
            "manual-de-uso/CHECKLIST_INSTALACAO_AMANHA.md": "checklist",
            "manual-de-uso/CHECKLIST_OPERACAO_DIARIA.md": "rotina",
            "manual-de-uso/LEIA-ME.txt": "manual",
        }
        for relative, content in files.items():
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        (source / "dados-iniciais/catalogo-produtos.sqlite3").write_bytes(b"catalog")
        manual = source / "manual-de-uso/Manual_de_Uso_TrigoPDV.docx"
        with zipfile.ZipFile(manual, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types />")
            archive.writestr("word/document.xml", "<document />")
        return source

    def test_stages_setup_and_writes_verified_manifest_last(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            source = self._complete_source(root)
            setup = root / "TrigoPDV-Setup.exe"
            setup.write_bytes(b"velopack setup")

            result = stage_usb_package(source, setup, root / "staged")

            self.assertEqual(result.manifest.name, "MANIFESTO-SHA256.txt")
            self.assertTrue((result.root / "TrigoPDV-Setup.exe").is_file())
            self.assertTrue((result.root / "instalador/Verificar_Pacote.ps1").is_file())
            self.assertTrue(verify_usb_package(result.root))
            lines = result.manifest.read_text(encoding="utf-8").splitlines()
            paths = [line.split(" *", 1)[1] for line in lines]
            self.assertEqual(paths, sorted(paths, key=str.casefold))
            self.assertFalse(any("MANIFESTO-SHA256.txt" in line for line in lines))

    def test_rejects_operational_database_sidecars_and_stale_generated_files(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            source = self._complete_source(root)
            setup = root / "setup.exe"
            setup.write_bytes(b"setup")
            for name in (
                "trigo_pdv.sqlite3",
                "catalogo-produtos.sqlite3-wal",
                "trigo_pdv.sqlite3-journal",
                "legacy.db-journal",
                "config.ini",
            ):
                with self.subTest(name=name):
                    candidate = source / name
                    candidate.write_bytes(b"private runtime data")
                    with self.assertRaises(UsbStageError):
                        stage_usb_package(source, setup, root / f"stage-{name.replace('.', '-')}")
                    candidate.unlink()

    def test_rejects_package_when_manual_is_missing_or_invalid(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            source = self._complete_source(root)
            setup = root / "setup.exe"
            setup.write_bytes(b"setup")
            manual = source / "manual-de-uso/Manual_de_Uso_TrigoPDV.docx"

            manual.unlink()
            with self.assertRaisesRegex(UsbStageError, "arquivos obrigatórios"):
                stage_usb_package(source, setup, root / "missing-manual")

            manual.write_text("não é um DOCX", encoding="utf-8")
            with self.assertRaisesRegex(UsbStageError, "DOCX.*inválido"):
                stage_usb_package(source, setup, root / "invalid-manual")

    @unittest.skipUnless(POWERSHELL, "PowerShell não está disponível")
    def test_powershell_verifier_accepts_intact_package_and_rejects_tampering(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            source = self._complete_source(root)
            setup = root / "TrigoPDV-Setup.exe"
            setup.write_bytes(b"velopack setup")
            staged = stage_usb_package(source, setup, root / "staged").root
            verifier = (
                ROOT
                / "TrigoPDV_Instalacao_PenDrive"
                / "instalador"
                / "Verificar_Pacote.ps1"
            )

            intact = subprocess.run(
                [
                    str(POWERSHELL), "-NoProfile", "-NonInteractive",
                    "-ExecutionPolicy", "Bypass", "-File", str(verifier),
                    "-PackageRoot", str(staged),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(intact.returncode, 0, intact.stderr)
            self.assertIn("integro", intact.stdout)

            (staged / "VERSAO.txt").write_text("arquivo alterado", encoding="utf-8")
            tampered = subprocess.run(
                [
                    str(POWERSHELL), "-NoProfile", "-NonInteractive",
                    "-ExecutionPolicy", "Bypass", "-File", str(verifier),
                    "-PackageRoot", str(staged),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(tampered.returncode, 0)
            self.assertIn("alterado ou corrompido", tampered.stderr)


if __name__ == "__main__":
    unittest.main()

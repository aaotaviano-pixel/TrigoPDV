# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all
from pathlib import Path
from tools.release_gate import trusted_root_for_build

datas = [
    ('config.ini.example', '.'),
    ('release/version.toml', 'release'),
    ('TrigoPDV_Instalacao_PenDrive/dados-iniciais/catalogo-produtos.sqlite3', 'catalog'),
    ('TrigoPDV_Instalacao_PenDrive/dados-iniciais/catalogo-produtos.manifest.json', 'catalog'),
]
trusted_root = trusted_root_for_build(Path.cwd())
if trusted_root is not None:
    datas.append((str(trusted_root), 'updates/trusted'))
binaries = []
hiddenimports = ['win32print', 'printing.discovery', 'printing.ipp', 'escpos.printer']
tmp_ret = collect_all('qrcode')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('escpos')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('velopack')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='TrigoPDV',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='release/version_info.txt',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='TrigoPDV',
)

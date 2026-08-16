@echo off
REM Gera um pacote Windows standalone do TrigoPDV. Python e pip sao usados
REM somente nesta maquina de montagem, nunca na maquina do caixa.
setlocal EnableExtensions
cd /d "%~dp0"

echo.
echo ==================================================
echo   TrigoPDV - Geracao do executavel Windows
echo ==================================================
echo.

where py >nul 2>&1
if errorlevel 1 (
    echo ERRO: o Python Launcher nao foi encontrado nesta maquina de montagem.
    exit /b 1
)

set "BUILD_PY=py -3.13"
%BUILD_PY% -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 13) and sys.version_info[:3] >= (3, 13, 14) else 1)"
if errorlevel 1 (
    echo ERRO: use CPython 3.13.14 ou mais novo da linha 3.13.
    exit /b 1
)

echo [1/4] Validando metadados da versao...
%BUILD_PY% tools\release_gate.py
if errorlevel 1 goto :error

echo [2/4] Criando ambiente de montagem isolado...
if exist .build-venv rmdir /s /q .build-venv
%BUILD_PY% -m venv .build-venv
if errorlevel 1 goto :error
.build-venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock
if errorlevel 1 goto :error

echo [3/4] Gerando TrigoPDV.exe standalone...
if exist build rmdir /s /q build
if exist dist\TrigoPDV rmdir /s /q dist\TrigoPDV
.build-venv\Scripts\python.exe -m PyInstaller --noconfirm --clean TrigoPDV.spec
if errorlevel 1 goto :error

echo [4/4] Executavel pronto em dist\TrigoPDV\TrigoPDV.exe
echo Para gerar o instalador, execute installer\build_installer.bat.
exit /b 0

:error
echo.
echo *** A geracao do executavel falhou. Verifique as mensagens acima. ***
exit /b 1

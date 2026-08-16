@echo off
REM Compila o instalador se o Inno Setup 6 estiver instalado nesta maquina.
setlocal EnableExtensions
cd /d "%~dp0"

set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
  echo ERRO: Inno Setup 6 nao encontrado.
  echo Instale o Inno Setup 6 nesta maquina de montagem e execute este arquivo novamente.
  exit /b 1
)

if not exist "..\dist\TrigoPDV\TrigoPDV.exe" (
  echo ERRO: execute ..\build_release.bat antes de compilar o instalador.
  exit /b 1
)

"%ISCC%" "TrigoPDV.iss"
if errorlevel 1 exit /b 1
echo Instalador criado em installer\Output\TrigoPDV-Setup.exe

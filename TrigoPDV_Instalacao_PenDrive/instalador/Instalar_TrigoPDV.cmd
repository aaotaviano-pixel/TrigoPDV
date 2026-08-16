@echo off
REM Alternativa para abrir o instalador canonico Velopack do pen drive.
REM Nao copia o app manualmente: Update.exe e sq.version sao obrigatorios para
REM que as proximas versoes possam ser aplicadas online com seguranca.
setlocal EnableExtensions

set "SETUP=%~dp0..\TrigoPDV-Setup.exe"
set "MIGRATION=%~dp0Migrar_Instalacao_Legada.ps1"
set "TRIGOPDV_PACKAGE_EXE=%SETUP%"

if not exist "%SETUP%" (
    echo ERRO: nao encontrei ..\TrigoPDV-Setup.exe.
    echo Mantenha a pasta do pacote completa no pen drive e tente novamente.
    pause
    exit /b 1
)

if not exist "%MIGRATION%" (
    echo ERRO: nao encontrei o assistente de migracao do instalador.
    pause
    exit /b 1
)

powershell -NoProfile -NonInteractive -Command "$v=(Get-Item -LiteralPath $env:TRIGOPDV_PACKAGE_EXE).VersionInfo.ProductVersion; if (-not $v -or -not $v.StartsWith('1.2.0')) { exit 1 }"
if errorlevel 1 (
    echo ERRO: o instalador deste pacote ainda nao e a versao 1.2.0 validada.
    echo Nao instale uma versao antiga. Atualize o pacote e tente novamente.
    pause
    exit /b 1
)

echo.
echo ==================================================
echo  Abrindo o instalador seguro do TrigoPDV...
echo ==================================================
echo.
echo Feche o TrigoPDV se ele ja estiver aberto antes de continuar.

powershell -NoProfile -ExecutionPolicy Bypass -File "%MIGRATION%" -SetupPath "%SETUP%"
if errorlevel 1 (
    echo ERRO: o instalador nao concluiu a operacao.
    pause
    exit /b 1
)

exit /b 0

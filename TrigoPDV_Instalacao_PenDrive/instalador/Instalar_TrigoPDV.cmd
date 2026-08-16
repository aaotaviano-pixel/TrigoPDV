@echo off
REM Instalador local/automatico do TrigoPDV para uso a partir do pen drive.
REM Nao instala Python: copia o aplicativo standalone para o perfil do Windows.
setlocal EnableExtensions

set "SOURCE=%~dp0app"
set "TRIGOPDV_PACKAGE_EXE=%SOURCE%\TrigoPDV.exe"
set "TARGET=%LOCALAPPDATA%\TrigoDeMinas.TrigoPDV"
set "EXE=%TARGET%\TrigoPDV.exe"
set "CATALOG=%~dp0..\dados-iniciais\catalogo-produtos.sqlite3"
set "CATALOG_MANIFEST=%~dp0..\dados-iniciais\catalogo-produtos.manifest.json"
set "DATA_ROOT=%LOCALAPPDATA%\TrigoPDV"

if not exist "%SOURCE%\TrigoPDV.exe" (
    echo ERRO: nao encontrei instalador\app\TrigoPDV.exe.
    echo Mantenha a pasta do pacote completa no pen drive e tente novamente.
    pause
    exit /b 1
)
powershell -NoProfile -NonInteractive -Command "$v=(Get-Item -LiteralPath $env:TRIGOPDV_PACKAGE_EXE).VersionInfo.ProductVersion; if (-not $v -or -not $v.StartsWith('1.1.0')) { exit 1 }"
if errorlevel 1 (
    echo ERRO: o aplicativo deste pacote ainda nao e a versao 1.1.0 validada.
    echo Nao instale uma versao antiga com o catalogo novo. Atualize o pacote e tente novamente.
    pause
    exit /b 1
)
if not exist "%CATALOG%" (
    echo ERRO: catalogo inicial de produtos nao encontrado.
    pause
    exit /b 1
)
if not exist "%CATALOG_MANIFEST%" (
    echo ERRO: manifesto do catalogo inicial nao encontrado.
    pause
    exit /b 1
)

echo.
echo ==================================================
echo  Instalando TrigoPDV...
echo ==================================================
echo.
echo Destino: %TARGET%
echo Feche o TrigoPDV se ele ja estiver aberto antes de continuar.

if not exist "%TARGET%" mkdir "%TARGET%"
REM O pacote historicamente pode conter uma copia antiga aninhada de TrigoPDV.
REM O instalador usa somente o executavel da raiz e nao copia essa duplicidade.
robocopy "%SOURCE%" "%TARGET%" /E /R:2 /W:1 /XD "%SOURCE%\TrigoPDV" /COPY:DAT /DCOPY:DAT >nul
set "COPY_RESULT=%ERRORLEVEL%"
if %COPY_RESULT% GEQ 8 (
    echo ERRO: nao foi possivel copiar o aplicativo. Codigo: %COPY_RESULT%
    pause
    exit /b %COPY_RESULT%
)

if not exist "%TARGET%\catalog" mkdir "%TARGET%\catalog"
copy /Y "%CATALOG%" "%TARGET%\catalog\catalogo-produtos.sqlite3" >nul
if errorlevel 1 goto :catalog_error
copy /Y "%CATALOG_MANIFEST%" "%TARGET%\catalog\catalogo-produtos.manifest.json" >nul
if errorlevel 1 goto :catalog_error
echo Catalogo inicial copiado. O aplicativo criara o banco local somente se ele nao existir.

if /I "%TRIGOPDV_SKIP_SHORTCUTS%"=="1" (
    echo Criacao de atalhos ignorada por configuracao de teste.
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$shell = New-Object -ComObject WScript.Shell; $desktop = [Environment]::GetFolderPath('Desktop'); $shortcut = $shell.CreateShortcut((Join-Path $desktop 'TrigoPDV.lnk')); $shortcut.TargetPath = '%EXE%'; $shortcut.WorkingDirectory = '%TARGET%'; $shortcut.Description = 'PDV Trigo de Minas'; $shortcut.Save(); $menu = Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs'; $shortcut = $shell.CreateShortcut((Join-Path $menu 'TrigoPDV.lnk')); $shortcut.TargetPath = '%EXE%'; $shortcut.WorkingDirectory = '%TARGET%'; $shortcut.Description = 'PDV Trigo de Minas'; $shortcut.Save()"
    if errorlevel 1 (
        echo AVISO: o programa foi instalado, mas os atalhos nao puderam ser criados.
        echo Abra %EXE% manualmente.
    ) else (
        echo Atalhos criados na Area de Trabalho e no Menu Iniciar.
    )
)

echo.
echo Instalacao concluida. Os dados operacionais ficarao em:
echo %DATA_ROOT%
echo.
if /I "%TRIGOPDV_NO_START%"=="1" exit /b 0
start "TrigoPDV" "%EXE%"
exit /b 0

:catalog_error
echo ERRO: nao foi possivel copiar e validar os arquivos do catalogo.
pause
exit /b 1

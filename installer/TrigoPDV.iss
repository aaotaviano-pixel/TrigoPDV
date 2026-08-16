; Instalador Windows do TrigoPDV. Compilar com Inno Setup 6.
; O executável deve ser gerado primeiro por ..\build_release.bat.

#include "..\release\version.iss"
#define MyAppName "TrigoPDV"
#define MyAppPublisher "Padaria Trigo de Minas"
#define MyAppExeName "TrigoPDV.exe"

[Setup]
AppId={{C0E4412C-7F2A-48C8-8FCA-22D962B1B4C4}
AppName={#MyAppName}
AppVersion={#TrigoVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\{#TrigoPackId}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=Output
OutputBaseFilename=TrigoPDV-Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na Area de Trabalho"; GroupDescription: "Atalhos adicionais:"

[Files]
Source: "..\dist\TrigoPDV\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\TrigoPDV_Instalacao_PenDrive\dados-iniciais\catalogo-produtos.sqlite3"; DestDir: "{app}\catalog"; Flags: ignoreversion
Source: "..\TrigoPDV_Instalacao_PenDrive\dados-iniciais\catalogo-produtos.manifest.json"; DestDir: "{app}\catalog"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Desinstalar {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Abrir {#MyAppName} agora"; Flags: nowait postinstall skipifsilent

; Configuração, banco, backups e fila de impressão ficam em
; %LOCALAPPDATA%\TrigoPDV. A desinstalação não apaga dados operacionais.

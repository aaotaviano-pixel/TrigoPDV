param(
    [Parameter(Mandatory = $true)]
    [string]$SetupPath
)

$ErrorActionPreference = 'Stop'
Import-Module Microsoft.PowerShell.Utility -ErrorAction Stop
$legacyPackId = 'TrigoDeMinas.TrigoPDV'
$newPackId = 'TrigoDeMinas.TrigoPDV.V2'
$legacyAppId = 'C0E4412C-7F2A-48C8-8FCA-22D962B1B4C4'
$dataRoot = Join-Path $env:LOCALAPPDATA 'TrigoPDV'
$legacyRoot = Join-Path $env:LOCALAPPDATA $legacyPackId
$newRoot = Join-Path $env:LOCALAPPDATA $newPackId

function Get-DataFingerprint {
    param([string]$Root)
    $result = @{}
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) { return $result }
    Get-ChildItem -LiteralPath $Root -File -Recurse -Force | ForEach-Object {
        $relative = $_.FullName.Substring($Root.Length).TrimStart('\')
        $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
        $result[$relative] = "$($_.Length):$hash"
    }
    return $result
}

function Assert-FingerprintsEqual {
    param($Before, $After)
    if ($Before.Count -ne $After.Count) {
        throw 'A instalacao foi interrompida porque a pasta de dados operacionais mudou.'
    }
    foreach ($key in $Before.Keys) {
        if (-not $After.ContainsKey($key) -or $After[$key] -ne $Before[$key]) {
            throw 'A instalacao foi interrompida porque a pasta de dados operacionais mudou.'
        }
    }
}

function Get-LegacyUninstaller {
    $keys = @(
        "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{$legacyAppId}_is1",
        "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{$legacyAppId}_is1",
        "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\{$legacyAppId}_is1"
    )
    foreach ($key in $keys) {
        $record = Get-ItemProperty -LiteralPath $key -ErrorAction SilentlyContinue
        if ($null -eq $record -or [string]::IsNullOrWhiteSpace($record.UninstallString)) { continue }
        $command = [string]$record.UninstallString
        $candidate = if ($command.StartsWith('"')) {
            [regex]::Match($command, '^"([^"]+)"').Groups[1].Value
        } else {
            $command.Split(' ')[0]
        }
        if (-not [string]::IsNullOrWhiteSpace($candidate)) { return $candidate }
    }
    return $null
}

function Assert-VelopackTopology {
    if (-not (Test-Path -LiteralPath (Join-Path $newRoot 'Update.exe') -PathType Leaf)) {
        throw 'A nova instalacao nao criou Update.exe.'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $newRoot 'current') -PathType Container)) {
        throw 'A nova instalacao nao criou a pasta current.'
    }
    $versionMarker = Get-ChildItem -LiteralPath (Join-Path $newRoot 'current') -Filter 'sq.version' -File -Recurse | Select-Object -First 1
    if ($null -eq $versionMarker) { throw 'A nova instalacao nao criou sq.version.' }
    if (-not (Test-Path -LiteralPath (Join-Path $newRoot 'TrigoPDV.exe') -PathType Leaf)) {
        throw 'A nova instalacao nao criou o inicializador estavel.'
    }
}

function Set-TrigoShortcuts {
    $shell = New-Object -ComObject WScript.Shell
    $target = Join-Path $newRoot 'TrigoPDV.exe'
    $startDirectory = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\TrigoPDV'
    New-Item -ItemType Directory -Path $startDirectory -Force | Out-Null
    foreach ($shortcutPath in @(
        (Join-Path ([Environment]::GetFolderPath('Desktop')) 'TrigoPDV.lnk'),
        (Join-Path $startDirectory 'TrigoPDV.lnk')
    )) {
        $shortcut = $shell.CreateShortcut($shortcutPath)
        $shortcut.TargetPath = $target
        $shortcut.WorkingDirectory = $newRoot
        $shortcut.Save()
    }
}

try {
    $setup = (Resolve-Path -LiteralPath $SetupPath).Path
    if (Get-Process -Name 'TrigoPDV' -ErrorAction SilentlyContinue) {
        throw 'Feche o TrigoPDV antes de iniciar a atualizacao do instalador.'
    }
    $before = Get-DataFingerprint -Root $dataRoot
    $installation = Start-Process -FilePath $setup -ArgumentList '--norestart' -Wait -PassThru
    if ($installation.ExitCode -ne 0) { throw 'O instalador Velopack nao concluiu a instalacao.' }
    Assert-VelopackTopology

    $uninstaller = Get-LegacyUninstaller
    if ($null -ne $uninstaller) {
        $resolvedUninstaller = (Resolve-Path -LiteralPath $uninstaller).Path
        $legacyPrefix = [IO.Path]::GetFullPath($legacyRoot).TrimEnd('\') + '\'
        if (-not $resolvedUninstaller.StartsWith($legacyPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'O desinstalador legado registrado aponta para uma pasta inesperada.'
        }
        $removal = Start-Process -FilePath $resolvedUninstaller -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART' -Wait -PassThru
        if ($removal.ExitCode -ne 0) { throw 'O desinstalador legado nao concluiu a limpeza.' }
    } elseif ((Test-Path -LiteralPath $legacyRoot -PathType Container) -and
              -not [IO.Path]::GetFullPath($legacyRoot).Equals([IO.Path]::GetFullPath($newRoot), [StringComparison]::OrdinalIgnoreCase)) {
        $suffix = Get-Date -Format 'yyyyMMdd-HHmmss'
        $retired = "$legacyRoot.legado-$suffix"
        Move-Item -LiteralPath $legacyRoot -Destination $retired
    }

    Assert-VelopackTopology
    if ($env:TRIGOPDV_MIGRATION_TEST_MODE -ne '1') { Set-TrigoShortcuts }
    $after = Get-DataFingerprint -Root $dataRoot
    Assert-FingerprintsEqual -Before $before -After $after
    Write-Host 'TrigoPDV 1.2 instalado; dados locais preservados e atualizador online validado.'
    exit 0
} catch {
    Write-Error $_.Exception.Message
    exit 1
}

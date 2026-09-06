param(
    [string]$PackageRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'

function Get-RelativePackagePath {
    param(
        [string]$Root,
        [string]$FullName
    )

    return $FullName.Substring($Root.Length).TrimStart('\', '/') -replace '\\', '/'
}

try {
    $root = (Resolve-Path -LiteralPath $PackageRoot).Path.TrimEnd('\', '/')
    $rootPrefix = $root + [IO.Path]::DirectorySeparatorChar
    $manifestPath = Join-Path $root 'MANIFESTO-SHA256.txt'
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw 'O manifesto de integridade do pacote nao foi encontrado.'
    }

    $reparsePoint = Get-ChildItem -LiteralPath $root -Force -Recurse |
        Where-Object { ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 } |
        Select-Object -First 1
    if ($null -ne $reparsePoint) {
        throw 'O pacote contem um atalho ou link inesperado.'
    }

    $expected = [Collections.Generic.Dictionary[string, string]]::new(
        [StringComparer]::OrdinalIgnoreCase
    )
    foreach ($line in [IO.File]::ReadAllLines($manifestPath)) {
        if ($line -notmatch '^(?<hash>[0-9a-f]{64}) \*(?<path>[^:!]+)$') {
            throw 'O manifesto de integridade possui uma linha invalida.'
        }
        $relative = $Matches.path
        $parts = @($relative -split '/')
        if (
            [IO.Path]::IsPathRooted($relative) -or
            $relative.Contains('\') -or
            $parts.Count -eq 0 -or
            @($parts | Where-Object { $_ -in @('', '.', '..') }).Count -ne 0
        ) {
            throw 'O manifesto de integridade possui um caminho inseguro.'
        }
        if ($expected.ContainsKey($relative)) {
            throw 'O manifesto de integridade possui um arquivo repetido.'
        }
        $candidate = [IO.Path]::GetFullPath(
            (Join-Path $root ($relative -replace '/', [IO.Path]::DirectorySeparatorChar))
        )
        if (-not $candidate.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'O manifesto de integridade aponta para fora do pacote.'
        }
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            throw "O pacote esta incompleto: $relative"
        }
        $actualHash = (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actualHash -ne $Matches.hash) {
            throw "Um arquivo do pacote foi alterado ou corrompido: $relative"
        }
        $expected.Add($relative, $actualHash)
    }

    $actual = @(Get-ChildItem -LiteralPath $root -File -Force -Recurse |
        Where-Object { $_.FullName -ne $manifestPath })
    if ($actual.Count -ne $expected.Count) {
        throw 'O pacote contem arquivo ausente ou inesperado.'
    }
    foreach ($file in $actual) {
        $relative = Get-RelativePackagePath -Root $root -FullName $file.FullName
        if (-not $expected.ContainsKey($relative)) {
            throw "O pacote contem um arquivo inesperado: $relative"
        }
    }

    Write-Host "Pacote TrigoPDV integro: $($expected.Count) arquivos conferidos."
    exit 0
} catch {
    Write-Error $_.Exception.Message
    exit 1
}

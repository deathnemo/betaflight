#Requires -Version 5.1
<#
.SYNOPSIS
  Массовая DFU-прошивка одного CONFIG: одна сборка, далее по Enter — следующая плата.

.DESCRIPTION
  Запускает scripts/betaflight-flash-repeat.sh в MSYS2 bash (нужны make, dfu-util).

.PARAMETER Config
  Имя unified config (CONFIG=...).

.PARAMETER ComPort
  Номер COM-порта Windows (3 для COM3).

.PARAMETER Options
  Опции компиляции, как в betaflight-flash.ps1.

.PARAMETER Bash
  Путь к bash.exe (по умолчанию C:\msys64\usr\bin\bash.exe).

.PARAMETER RepoRoot
  Корень репозитория Betaflight.

.PARAMETER PresetFile
  Путь к файлу CLI (diff all). После каждой прошивки — пауза и применение пресета.
#>
param(
    [Parameter(Mandatory = $true)]
    [string] $Config,

    [Parameter(Mandatory = $true)]
    [int] $ComPort,

    [string] $Options = '',

    [string] $PresetFile = '',

    [string] $Bash = '',

    [string] $RepoRoot = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function ConvertTo-MsysPath([string] $WindowsPath) {
    if ([string]::IsNullOrWhiteSpace($WindowsPath)) { return '' }
    $p = (Resolve-Path -LiteralPath $WindowsPath).Path
    if ($p -match '^([A-Za-z]):') {
        $d = $Matches[1].ToLowerInvariant()
        return '/' + $d + ($p.Substring(2) -replace '\\', '/')
    }
    return ($p -replace '\\', '/')
}

function Find-DefaultBash {
    $candidates = @(
        'C:\msys64\usr\bin\bash.exe',
        'C:\msys2\usr\bin\bash.exe',
        'C:\msys64_mingw64\usr\bin\bash.exe'
    )
    foreach ($c in $candidates) {
        if (Test-Path -LiteralPath $c) { return $c }
    }
    return $null
}

if (-not $RepoRoot) {
    $RepoRoot = Split-Path -Parent $PSScriptRoot
}
$repoUnix = ConvertTo-MsysPath $RepoRoot

$bashExe = $Bash
if (-not $bashExe) {
    $bashExe = Find-DefaultBash
}
if (-not $bashExe -or -not (Test-Path -LiteralPath $bashExe)) {
    throw "bash not found (install MSYS2 or pass -Bash 'C:\msys64\usr\bin\bash.exe')."
}

$inner = "export MSYSTEM=UCRT64; source /etc/profile 2>/dev/null; cd '$repoUnix' && bash ./scripts/betaflight-flash-repeat.sh -c '$Config' -d '$ComPort'"
if ($Options) {
    $inner += " -o `"$($Options -replace '"', '\"')`""
}
if ($PresetFile) {
    if (-not (Test-Path -LiteralPath $PresetFile)) {
        throw "Preset file not found: $PresetFile"
    }
    $pu = ConvertTo-MsysPath ((Resolve-Path -LiteralPath $PresetFile).Path)
    $inner += " -P '$pu'"
}
& $bashExe -lc $inner

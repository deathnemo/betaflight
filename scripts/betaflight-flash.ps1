#Requires -Version 5.1
<#
.SYNOPSIS
  Сборка и прошивка Betaflight по TARGET/CONFIG с OPTIONS (как в Makefile).

.DESCRIPTION
  Обертка над GNU make: передает TARGET или CONFIG, OPTIONS (через -D в компиляторе),
  затем при необходимости вызывает dfu_flash, tty_flash или st-flash.

  Рецепты Makefile используют stty/stm32flash/dfu-util (Unix). На Windows ожидается
  MSYS2/Git Bash: укажите путь к bash.exe параметром -Bash или в манифесте "bash".

.PARAMETER Manifest
  JSON-файл со списком плат (см. scripts/boards-flash.example.json).

.PARAMETER Target
  Имя таргета (альтернатива манифесту для одной платы).

.PARAMETER Config
  Имя unified config (CONFIG=...), не использовать вместе с -Target.

.PARAMETER Options
  Список опций компиляции, например: USE_GPS (без префикса -D).
  Не перечисляйте макросы, которые уже задаёт common_pre.h (USE_DSHOT,
  USE_PINIO, USE_SERIALRX_*, USE_VTX, …), иначе gcc выдаст redefined [-Werror].

.PARAMETER Flash
  none | dfu | serial | stlink

.PARAMETER SerialDevice
  Для serial: путь устройства в стиле MSYS, например /dev/ttyS4 (COM5).

.PARAMETER ComPort
  Номер COM-порта Windows; для MSYS2: SERIAL_DEVICE=/dev/ttyS(n-1).

.PARAMETER Bash
  Полный путь к bash.exe (MSYS2: C:\msys64\usr\bin\bash.exe).

.PARAMETER RepoRoot
  Корень репозитория Betaflight (по умолчанию — родитель каталога scripts).

.PARAMETER DryRun
  Только вывести команды, не выполнять.

.EXAMPLE
  .\betaflight-flash.ps1 -Target MATEKF405 -Options @('USE_GPS') -Flash dfu

.EXAMPLE
  .\betaflight-flash.ps1 -Manifest .\boards-flash.json
#>

[CmdletBinding(DefaultParameterSetName = 'Single')]
param(
    [Parameter(ParameterSetName = 'Manifest')]
    [string] $Manifest,

    [Parameter(ParameterSetName = 'Single')]
    [string] $Target = '',

    [Parameter(ParameterSetName = 'Single')]
    [string] $Config = '',

    [Parameter(ParameterSetName = 'Single')]
    [string[]] $Options = @(),

    [Parameter(ParameterSetName = 'Single')]
    [ValidateSet('none', 'dfu', 'serial', 'stlink')]
    [string] $Flash = 'none',

    [Parameter(ParameterSetName = 'Single')]
    [string] $SerialDevice = '',

    [Parameter(ParameterSetName = 'Single')]
    [int] $ComPort = 0,

    [string] $Bash = '',

    [string] $RepoRoot = '',

    [switch] $DryRun
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
    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($git) {
        $gitBash = Join-Path (Split-Path (Split-Path $git.Source)) 'bin\bash.exe'
        if (Test-Path -LiteralPath $gitBash) { return $gitBash }
    }
    return $null
}

function Invoke-MakeInRepo {
    param(
        [string] $BashExe,
        [string] $RepoUnix,
        [string] $Goal,
        [hashtable] $MakeVars
    )
    $varArgs = @()
    foreach ($key in $MakeVars.Keys) {
        $val = $MakeVars[$key]
        if ($null -eq $val -or $val -eq '') { continue }
        $escaped = ($val -replace "'", "'\''")
        $varArgs += "$key='$escaped'"
    }
    $makeLine = "make $Goal"
    if ($varArgs.Count -gt 0) {
        $makeLine += ' ' + ($varArgs -join ' ')
    }
    $inner = "cd '$RepoUnix' && $makeLine"
    if ($DryRun) {
        Write-Host "DRY: & `"$BashExe`" -lc `"$inner`""
        return
    }
    & $BashExe -lc $inner
    if ($LASTEXITCODE -ne 0) {
        throw "make failed with exit code $LASTEXITCODE"
    }
}

function Resolve-SerialDevice {
    param([string] $SerialDevice, [int] $ComPort)
    if ($SerialDevice) { return $SerialDevice }
    if ($ComPort -gt 0) { return "/dev/ttyS$($ComPort - 1)" }
    return ''
}

function Invoke-Board {
    param(
        [string] $Name,
        [string] $Target,
        [string] $Config,
        [string[]] $Options,
        [string] $FlashMode,
        [string] $SerialDevice,
        [string] $BashExe,
        [string] $RepoUnix
    )
    $optsJoined = ($Options | Where-Object { $_ } | ForEach-Object { $_.Trim() } | Where-Object { $_ }) -join ' '
    $makeVars = @{}
    if ($optsJoined) {
        $makeVars['OPTIONS'] = $optsJoined
    }
    if ($Target -and $Config) {
        throw "Board '$Name': specify either target or config, not both."
    }
    if ($Target) {
        $makeVars['TARGET'] = $Target
    }
    elseif ($Config) {
        $makeVars['CONFIG'] = $Config
    }
    else {
        throw "Board '$Name': target or config is required."
    }

    Write-Host "=== $($Name): build (fwo) ===" -ForegroundColor Cyan
    Invoke-MakeInRepo -BashExe $BashExe -RepoUnix $RepoUnix -Goal 'fwo' -MakeVars $makeVars

    switch ($FlashMode) {
        'none' { return }
        'dfu' {
            Write-Host "=== $($Name): dfu_flash ===" -ForegroundColor Cyan
            if ($SerialDevice) {
                $makeVars['SERIAL_DEVICE'] = $SerialDevice
            }
            Invoke-MakeInRepo -BashExe $BashExe -RepoUnix $RepoUnix -Goal 'dfu_flash' -MakeVars $makeVars
        }
        'serial' {
            if (-not $SerialDevice) {
                throw "Board '$Name': serial flash requires serialDevice or comPort."
            }
            $makeVars['SERIAL_DEVICE'] = $SerialDevice
            Write-Host "=== $($Name): tty_flash ($SerialDevice) ===" -ForegroundColor Cyan
            Invoke-MakeInRepo -BashExe $BashExe -RepoUnix $RepoUnix -Goal 'tty_flash' -MakeVars $makeVars
        }
        'stlink' {
            Write-Host "=== $($Name): st-flash ===" -ForegroundColor Cyan
            Invoke-MakeInRepo -BashExe $BashExe -RepoUnix $RepoUnix -Goal 'st-flash' -MakeVars $makeVars
        }
    }
}

# --- main ---
if (-not $RepoRoot) {
    $RepoRoot = Split-Path -Parent $PSScriptRoot
}
$repoRootResolved = ConvertTo-MsysPath $RepoRoot

$bashExe = $Bash
if (-not $bashExe) {
    $bashExe = Find-DefaultBash
}
if (-not $bashExe -or -not (Test-Path -LiteralPath $bashExe)) {
    throw "bash not found (install MSYS2 or pass -Bash 'C:\msys64\usr\bin\bash.exe')."
}

if ($PSCmdlet.ParameterSetName -eq 'Single') {
    $sd = Resolve-SerialDevice -SerialDevice $SerialDevice -ComPort $ComPort
    $boardName = if ($Target) { $Target } elseif ($Config) { $Config } else { 'single' }
    Invoke-Board -Name $boardName -Target $Target -Config $Config -Options $Options `
        -FlashMode $Flash -SerialDevice $sd -BashExe $bashExe -RepoUnix $repoRootResolved
    exit 0
}

if (-not (Test-Path -LiteralPath $Manifest)) {
    throw "Manifest not found: $Manifest"
}

$data = Get-Content -LiteralPath $Manifest -Raw -Encoding UTF8 | ConvertFrom-Json
if ($data.repoRoot) {
    $repoRootResolved = ConvertTo-MsysPath $data.repoRoot
}
if ($data.bash) {
    $bashExe = $data.bash
}

$idx = 0
foreach ($b in $data.boards) {
    $idx++
    $name = if ($b.name) { [string]$b.name } else { "board-$idx" }
    $opts = @()
    if ($b.options) {
        $opts = @($b.options | ForEach-Object { [string]$_ })
    }
    $flashMode = if ($b.flash) { [string]$b.flash } else { 'none' }
    if ($flashMode -notin @('none', 'dfu', 'serial', 'stlink')) {
        throw "Board '$name': invalid flash mode '$flashMode'."
    }
    $sd = Resolve-SerialDevice -SerialDevice ([string]$b.serialDevice) -ComPort ([int]$b.comPort)
    $tgt = [string]$b.target
    $cfg = [string]$b.config
    Invoke-Board -Name $name -Target $tgt -Config $cfg -Options $opts `
        -FlashMode $flashMode -SerialDevice $sd -BashExe $bashExe -RepoUnix $repoRootResolved
}

Write-Host "Done: $idx board(s) processed." -ForegroundColor Green

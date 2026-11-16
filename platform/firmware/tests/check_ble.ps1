# Checks for the Bluetooth locket (locket_ble) that do not need the board. From platform/firmware:
#
#   .\tests\check_ble.ps1                         uses c++, g++ or clang++ if one is on the path
#   .\tests\check_ble.ps1 -Compiler "C:\path\g++.exe"
#
# Builds locket_ble/ble_core.cpp and core.cpp with strict warnings and runs tests/test_ble_core.cpp. This says
# nothing about the board: only `arduino-cli compile` builds the sketch, and nothing here was ever flashed.

param([string]$Compiler = $env:ASK_FIRMWARE_CXX)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not $Compiler) {
  foreach ($name in "c++", "g++", "clang++") {
    if (Get-Command $name -ErrorAction SilentlyContinue) { $Compiler = $name; break }
  }
}
if (-not $Compiler) { Write-Error "No C++ compiler found. Pass -Compiler or set ASK_FIRMWARE_CXX."; exit 2 }
$parts = @([regex]::Matches($Compiler, '"[^"]+"|\S+') | ForEach-Object { $_.Value.Trim('"') })
$exe = $parts[0]
$lead = if ($parts.Count -gt 1) { $parts[1..($parts.Count - 1)] } else { @() }

$build = Join-Path ([IO.Path]::GetTempPath()) "ask-firmware-ble-check"
New-Item -ItemType Directory -Force $build | Out-Null

Write-Host "== logic tests (locket_ble/ble_core.cpp)"
& $exe @lead -std=c++17 -Wall -Wextra -Werror -pedantic -D_USE_MATH_DEFINES -I locket_ble tests\test_ble_core.cpp locket_ble\ble_core.cpp locket_ble\core.cpp -o "$build\test_ble_core.exe"
if ($LASTEXITCODE -ne 0) { exit 1 }
& "$build\test_ble_core.exe"
if ($LASTEXITCODE -ne 0) { exit 1 }
Write-Host "Bluetooth locket logic checks passed. This is not a build for the board."

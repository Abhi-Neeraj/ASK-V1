# Runs every check the firmware has that does not need the board. From platform/firmware:
#
#   .\tests\check.ps1                                  uses c++, g++ or clang++ if one is on the path
#   .\tests\check.ps1 -Compiler "python -m ziglang c++"  or name one explicitly
#
# 1. The logic in locket/core.cpp is compiled with strict warnings and run against its tests.
#    (The button recorder, locket_web_upload, has its own script: check_web_upload.ps1.)
# 2. Every source file is compiled against stand-in Arduino headers (tests/stubs). That catches mistakes between
#    the sketch's own files. It says NOTHING about the real board libraries: only a build for the board does.
#
# Needs a C++11 compiler. None of this builds or flashes the board.

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
# Split on spaces, but keep a quoted path (with spaces in it) as one word.
$parts = @([regex]::Matches($Compiler, '"[^"]+"|\S+') | ForEach-Object { $_.Value.Trim('"') })
$exe = $parts[0]
$lead = if ($parts.Count -gt 1) { $parts[1..($parts.Count - 1)] } else { @() }

$build = Join-Path ([IO.Path]::GetTempPath()) "ask-firmware-check"
New-Item -ItemType Directory -Force $build | Out-Null

Write-Host "== logic tests (locket/core.cpp)"
& $exe @lead -std=c++11 -Wall -Wextra -Werror -pedantic -I locket tests\test_core.cpp locket\core.cpp -o "$build\test_core.exe"
if ($LASTEXITCODE -ne 0) { exit 1 }
& "$build\test_core.exe"
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host "== every source file against stand-in headers"
$failed = $false
foreach ($file in Get-ChildItem locket -Include *.cpp, *.ino -Recurse) {
  $language = if ($file.Extension -eq ".ino") { @("-x", "c++") } else { @() }
  & $exe @lead -std=c++17 -c -Wall -Wextra @language -I tests\stubs -I locket $file.FullName -o "$build\$($file.Name).o"
  if ($LASTEXITCODE -ne 0) { $failed = $true } else { Write-Host ("ok  " + $file.Name) }
}
if ($failed) { exit 1 }
Write-Host "All firmware checks passed. This is not a build for the board."

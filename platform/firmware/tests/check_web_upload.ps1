# Runs every check the button recorder (locket_web_upload) has that does not need the board. From platform/firmware:
#
#   .\tests\check_web_upload.ps1                                  uses c++, g++ or clang++ if one is on the path
#   .\tests\check_web_upload.ps1 -Compiler "python -m ziglang c++"  or name one explicitly
#
# 1. The logic in locket_web_upload/core.cpp is compiled with strict warnings and run against BOTH test files: the
#    shared tests (test_core.cpp, the same ones the continuous-clip firmware passes) and the new ones.
# 2. The firmware's real source files and the sketch's own setup() are compiled against SIMULATED hardware
#    (tests/hostsim) and one recording is made, uploaded to a fake server and checked. This runs the firmware's
#    logic. It is not the board and says nothing about real timing, the card, the radio, TLS or the microphone.
#    The full set of simulated-hardware scenarios (recording, card, upload, Wi-Fi) and the run against the real server
#    are pytest (from platform/api):
#       python -m pytest tests/test_firmware_web_upload.py tests/test_firmware_button_workflow.py tests/test_firmware_wifi.py tests/test_firmware_real_server.py
#    (The build stamp is named because some compilers refuse __DATE__ and __TIME__, which the firmware uses to tell builds apart.)
#
# Needs a C++17 compiler. None of this builds or flashes the board.

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

$build = Join-Path ([IO.Path]::GetTempPath()) "ask-firmware-check-web-upload"
New-Item -ItemType Directory -Force $build | Out-Null
$sketch = "locket_web_upload"

Write-Host "== logic tests, shared half (tests/test_core.cpp against $sketch/core.cpp)"
& $exe @lead -std=c++11 -Wall -Wextra -Werror -pedantic -I $sketch tests\test_core.cpp "$sketch\core.cpp" -o "$build\test_core.exe"
if ($LASTEXITCODE -ne 0) { exit 1 }
& "$build\test_core.exe"
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host "== logic tests, button recorder half (tests/test_web_upload_core.cpp)"
& $exe @lead -std=c++11 -Wall -Wextra -Werror -pedantic -I $sketch tests\test_web_upload_core.cpp "$sketch\core.cpp" -o "$build\test_wu_core.exe"
if ($LASTEXITCODE -ne 0) { exit 1 }
& "$build\test_wu_core.exe"
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host "== the firmware's sources against simulated hardware, one recording end to end"
$modules = "core", "status", "settings", "storage", "sdwriter", "recorder", "uploader", "dashboard", "console", "wifi_manager" | ForEach-Object { "$sketch\$_.cpp" }
$libs = @()
if ($env:OS -eq "Windows_NT") { $libs = @("-lws2_32") }
foreach ($variant in @(@{ name = "shipped"; defines = @() }, @{ name = "development switches on"; defines = @("-DASK_ALLOW_PLAIN_HTTP=1", "-DASK_UPLOAD_WHILE_RECORDING=0", "-DASK_DASHBOARD_AUDIO_DOWNLOAD=1") })) {
  & $exe @lead -std=c++17 -O1 -Wall -Wextra -DASK_BUILD_STAMP=checkbuild @($variant.defines) -I tests\hostsim\stubs -I $sketch tests\hostsim\hostsim_main.cpp tests\hostsim\hostsim_http.cpp @modules @libs -o "$build\hostsim.exe"
  if ($LASTEXITCODE -ne 0) { Write-Host ("compile failed: " + $variant.name); exit 1 }
  Write-Host ("compiled: " + $variant.name)
}
# (the last build is the development one; the shipped one is built and exercised by the pytest suite)
$world = Join-Path $build "world"
Remove-Item -Recurse -Force $world -ErrorAction SilentlyContinue
$out = & "$build\hostsim.exe" --dir $world --paired --scale 20 --pref maxclip=6 --server-max-clip 6 record=14 until-empty=300
$result = ($out | Where-Object { $_ -like "RESULT *" } | Select-Object -Last 1)
if (-not $result) { Write-Host ($out -join "`n"); Write-Error "The simulator printed no result."; exit 1 }
$json = $result.Substring(7) | ConvertFrom-Json
Write-Host ("recorded {0} parts, uploaded {1}, left on the card {2}, bad requests {3}, card-access violations {4}" -f $json.counters.partsSaved, $json.uploaded, $json.dirs.pending.Count, $json.server.badRequests, $json.violations)
if ($json.counters.partsSaved -lt 2 -or $json.uploaded -ne $json.counters.partsSaved -or $json.dirs.pending.Count -ne 0 -or $json.server.badRequests -ne 0 -or $json.violations -ne 0) { exit 1 }

Write-Host "All button-recorder checks passed. This is not a build for the board, and nothing was flashed."

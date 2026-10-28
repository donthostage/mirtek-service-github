$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$runtimePython = 'C:\Users\Даниил\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$localVendor = Join-Path (Split-Path (Split-Path $projectRoot -Parent) -Parent) 'work\vendor'
if (-not (Test-Path -LiteralPath $runtimePython) -or -not (Test-Path -LiteralPath $localVendor)) {
    throw 'Локальная среда не найдена. Используйте инструкцию установки в README.md.'
}
$env:PYTHONPATH = $localVendor
$env:PYTHONIOENCODING = 'utf-8'
Push-Location -LiteralPath $projectRoot
try {
    & $runtimePython -m scripts.run_demo
} finally {
    Pop-Location
}

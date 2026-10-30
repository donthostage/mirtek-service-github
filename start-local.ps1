param([int]$ApiPort = 8766, [int]$SourcePort = 8767)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$runtimePython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $runtimePython)) {
    throw 'Сначала создайте .venv и установите зависимости по README.md.'
}
$previousMode = $env:MIRTEK_CLASSIFIER
Push-Location -LiteralPath $projectRoot
try {
    $env:MIRTEK_CLASSIFIER = 'baseline'
    & $runtimePython -m scripts.run_demo --api-port $ApiPort --source-port $SourcePort
    if ($LASTEXITCODE -ne 0) { throw "Сервис завершился с кодом $LASTEXITCODE." }
} finally {
    $env:MIRTEK_CLASSIFIER = $previousMode
    Pop-Location
}

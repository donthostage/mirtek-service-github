param([string]$Model = $env:AITUNNEL_MODEL)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$vendorPath = $null
if (-not (Test-Path -LiteralPath $pythonPath)) {
    $pythonPath = 'C:\Users\Даниил\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    $vendorPath = Join-Path (Split-Path (Split-Path $projectRoot -Parent) -Parent) 'work\vendor'
    if (-not (Test-Path -LiteralPath $pythonPath) -or -not (Test-Path -LiteralPath $vendorPath)) {
        throw 'Сначала подготовьте Python и зависимости по README.md.'
    }
}
if (-not $Model) { $Model = Read-Host 'Точный id модели AITUNNEL с поддержкой JSON Schema' }
if (-not $Model.Trim()) { throw 'Нужно указать модель.' }
$previousKey = $env:AITUNNEL_API_KEY
$previousModel = $env:AITUNNEL_MODEL
$previousMode = $env:MIRTEK_CLASSIFIER
$previousPythonPath = $env:PYTHONPATH
$secret = $null
Push-Location -LiteralPath $projectRoot
try {
    if (-not $env:AITUNNEL_API_KEY) {
        $secret = Read-Host 'API-ключ AITUNNEL (скрытый ввод; не сохраняется)' -AsSecureString
        $env:AITUNNEL_API_KEY = [System.Net.NetworkCredential]::new('', $secret).Password
    }
    if (-not $env:AITUNNEL_API_KEY.Trim()) { throw 'Ключ не задан.' }
    $env:MIRTEK_CLASSIFIER = 'aitunnel'
    $env:AITUNNEL_MODEL = $Model.Trim()
    if ($vendorPath) { $env:PYTHONPATH = $vendorPath }
    Write-Host 'AITUNNEL выбран. Вопросы будут отправляться провайдеру; возможны списания за запросы.'
    & $pythonPath -m scripts.run_demo
} finally {
    $env:AITUNNEL_API_KEY = $previousKey
    $env:AITUNNEL_MODEL = $previousModel
    $env:MIRTEK_CLASSIFIER = $previousMode
    $env:PYTHONPATH = $previousPythonPath
    if ($secret) { $secret.Dispose() }
    Pop-Location
}

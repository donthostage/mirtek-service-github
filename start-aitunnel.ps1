param([string]$Model = $env:AITUNNEL_MODEL, [int]$ApiPort = 8766, [int]$SourcePort = 8767,
      [string]$PythonPath = (Join-Path $PSScriptRoot '.venv\Scripts\python.exe'),
      [switch]$OpenBrowser, [switch]$Reload)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Сначала создайте .venv и установите зависимости по README.md.'
}
if (-not $Model) { $Model = Read-Host 'Точный id модели AITUNNEL с поддержкой JSON Schema' }
if (-not $Model.Trim()) { throw 'Нужно указать модель.' }
$previousKey = $env:AITUNNEL_API_KEY
$previousModel = $env:AITUNNEL_MODEL
$previousMode = $env:MIRTEK_CLASSIFIER
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
    Write-Host 'AITUNNEL выбран. Вопросы будут отправляться провайдеру; возможны списания за запросы.'
    $demoArgs = @('-m', 'scripts.run_demo', '--api-port', $ApiPort, '--source-port', $SourcePort)
    if ($OpenBrowser) { $demoArgs += '--open-browser' }
    if ($Reload) { $demoArgs += '--reload' }
    & $pythonPath @demoArgs
    if ($LASTEXITCODE -ne 0) { throw "Сервис завершился с кодом $LASTEXITCODE." }
} finally {
    $env:AITUNNEL_API_KEY = $previousKey
    $env:AITUNNEL_MODEL = $previousModel
    $env:MIRTEK_CLASSIFIER = $previousMode
    if ($secret) { $secret.Dispose() }
    Pop-Location
}

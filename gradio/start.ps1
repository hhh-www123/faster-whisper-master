$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
$env:PYTHONUTF8 = '1'
$env:GRADIO_ANALYTICS_ENABLED = 'False'
$taskPython = Join-Path $taskRoot '.venv-web/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    throw 'Create .venv-web and install gradio/requirements.txt first; see gradio/README.md.'
}
& $taskPython -u (Join-Path $PSScriptRoot 'app.py') @args

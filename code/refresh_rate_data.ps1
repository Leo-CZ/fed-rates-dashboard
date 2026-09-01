$ErrorActionPreference = 'Stop'

$PythonCommand = Get-Command python -CommandType Application -All -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($null -eq $PythonCommand) {
    $PythonCommand = Get-Command python3 -CommandType Application -All -ErrorAction SilentlyContinue |
        Select-Object -First 1
}
if ($null -eq $PythonCommand) {
    throw 'Python 3.10 or newer is required.'
}

& $PythonCommand.Source (Join-Path $PSScriptRoot 'refresh_fred_data.py')
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

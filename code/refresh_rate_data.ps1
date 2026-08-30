$ErrorActionPreference = 'Stop'

$StartDate = '2019-01-01'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$RawDirectory = Join-Path $ProjectRoot 'data\fred\raw'
$Series = @('DGS2', 'DGS10', 'DGS30', 'T10YIE', 'DFII10', 'DFII30')

New-Item -ItemType Directory -Force -Path $RawDirectory | Out-Null

foreach ($SeriesId in $Series) {
    $Url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=$SeriesId&cosd=$StartDate"
    $Destination = Join-Path $RawDirectory "$SeriesId.csv"
    $TemporaryDestination = Join-Path $RawDirectory ".$SeriesId.download"
    for ($Attempt = 1; $Attempt -le 3; $Attempt++) {
        try {
            Invoke-WebRequest -Uri $Url -OutFile $TemporaryDestination -Headers @{ 'User-Agent' = 'rate-analysis-data-refresh/1.0' }
            $Header = Get-Content -LiteralPath $TemporaryDestination -TotalCount 1
            if ($Header -ne "observation_date,$SeriesId") {
                throw "Unexpected CSV header for ${SeriesId}: $Header"
            }
            Move-Item -LiteralPath $TemporaryDestination -Destination $Destination -Force
            break
        }
        catch {
            if (Test-Path -LiteralPath $TemporaryDestination) {
                Remove-Item -LiteralPath $TemporaryDestination
            }
            if ($Attempt -eq 3) { throw }
            Start-Sleep -Seconds (2 * $Attempt)
        }
    }
    Write-Output "Downloaded $SeriesId to $Destination"
}

Write-Output 'FRED downloads complete. No missing observations were imputed.'

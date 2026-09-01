$ErrorActionPreference = 'Stop'

$StartDate = '2019-01-01'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$RawDirectory = Join-Path $ProjectRoot 'data\fred\raw'
$Series = @('DGS2', 'DGS10', 'DGS30', 'T10YIE', 'DFII10', 'DFII30')
$CurlCommand = Get-Command curl.exe -CommandType Application -ErrorAction SilentlyContinue
if ($null -eq $CurlCommand) {
    $CurlCommand = Get-Command curl -CommandType Application -ErrorAction SilentlyContinue
}
if ($null -eq $CurlCommand) {
    throw 'A curl command-line application is required to download FRED data.'
}

New-Item -ItemType Directory -Force -Path $RawDirectory | Out-Null

foreach ($SeriesId in $Series) {
    $Destination = Join-Path $RawDirectory "$SeriesId.csv"
    $TemporaryDestination = Join-Path $RawDirectory ".$SeriesId.download"
    $MergeDestination = Join-Path $RawDirectory ".$SeriesId.merge"
    $ExistingRows = @()
    $LastStoredDate = $null

    if (Test-Path -LiteralPath $Destination) {
        $Header = Get-Content -LiteralPath $Destination -TotalCount 1
        if ($Header -ne "observation_date,$SeriesId") {
            throw "Unexpected existing CSV header for ${SeriesId}: $Header"
        }
        $ExistingRows = @(Import-Csv -LiteralPath $Destination)
        if ($ExistingRows.Count -gt 0) {
            $LastStoredDate = [datetime]::ParseExact(
                $ExistingRows[-1].observation_date,
                'yyyy-MM-dd',
                [Globalization.CultureInfo]::InvariantCulture
            )
        }
    }

    $DownloadStartDate = if ($null -eq $LastStoredDate) {
        $StartDate
    }
    else {
        $LastStoredDate.AddDays(1).ToString('yyyy-MM-dd')
    }
    $Url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=$SeriesId&cosd=$DownloadStartDate"

    for ($Attempt = 1; $Attempt -le 3; $Attempt++) {
        try {
            & $CurlCommand.Source `
                --location `
                --fail `
                --silent `
                --show-error `
                --connect-timeout 15 `
                --max-time 60 `
                --user-agent 'rate-analysis-data-refresh/1.0' `
                --output $TemporaryDestination `
                $Url
            if ($LASTEXITCODE -ne 0) {
                throw "curl exited with code $LASTEXITCODE while downloading $SeriesId."
            }
            $Header = Get-Content -LiteralPath $TemporaryDestination -TotalCount 1
            if ($Header -ne "observation_date,$SeriesId") {
                throw "Unexpected CSV header for ${SeriesId}: $Header"
            }
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

    $DownloadedRows = @(Import-Csv -LiteralPath $TemporaryDestination)
    if ($null -ne $LastStoredDate) {
        $DownloadedRows = @(
            $DownloadedRows | Where-Object {
                [datetime]::ParseExact(
                    $_.observation_date,
                    'yyyy-MM-dd',
                    [Globalization.CultureInfo]::InvariantCulture
                ) -gt $LastStoredDate
            }
        )
    }

    if ($DownloadedRows.Count -eq 0) {
        Remove-Item -LiteralPath $TemporaryDestination
        $CurrentThrough = if ($null -eq $LastStoredDate) { 'no stored observations' } else { $LastStoredDate.ToString('yyyy-MM-dd') }
        Write-Output "$SeriesId is already current through $CurrentThrough"
        continue
    }

    $DownloadedDates = @($DownloadedRows | ForEach-Object { $_.observation_date })
    if (($DownloadedDates | Select-Object -Unique).Count -ne $DownloadedDates.Count) {
        throw "The incremental FRED response for $SeriesId contains duplicate dates."
    }
    $SortedDates = @($DownloadedDates | Sort-Object)
    if ([string]::Join('|', $DownloadedDates) -ne [string]::Join('|', $SortedDates)) {
        throw "The incremental FRED response for $SeriesId is not ordered by date."
    }

    try {
        if (Test-Path -LiteralPath $Destination) {
            Copy-Item -LiteralPath $Destination -Destination $MergeDestination -Force
            $DownloadedRows |
                ForEach-Object { "$($_.observation_date),$($_.$SeriesId)" } |
                Add-Content -LiteralPath $MergeDestination
        }
        else {
            Move-Item -LiteralPath $TemporaryDestination -Destination $MergeDestination
        }

        $MergedRows = @(Import-Csv -LiteralPath $MergeDestination)
        $ExpectedCount = $ExistingRows.Count + $DownloadedRows.Count
        if ($MergedRows.Count -ne $ExpectedCount) {
            throw "Merged row count for $SeriesId is $($MergedRows.Count); expected $ExpectedCount."
        }
        Move-Item -LiteralPath $MergeDestination -Destination $Destination -Force
    }
    finally {
        if (Test-Path -LiteralPath $TemporaryDestination) {
            Remove-Item -LiteralPath $TemporaryDestination
        }
        if (Test-Path -LiteralPath $MergeDestination) {
            Remove-Item -LiteralPath $MergeDestination
        }
    }

    Write-Output "Appended $($DownloadedRows.Count) new $SeriesId rows beginning $($DownloadedRows[0].observation_date)"
}

Write-Output 'Incremental FRED update complete. Existing observations were not replaced and missing values were not imputed.'

param(
    [Parameter(Mandatory = $true)]
    [string[]]$Files,

    [string]$OutputPath,

    [string]$DateSuffix
)

$ErrorActionPreference = 'Stop'

function Resolve-WorkbookPath {
    param([string]$Path)

    $resolved = Resolve-Path -LiteralPath $Path -ErrorAction SilentlyContinue
    if ($null -eq $resolved) {
        throw "Workbook not found: $Path"
    }
    return $resolved.ProviderPath
}

function Get-RowData {
    param($Sheet)

    $used = $Sheet.UsedRange
    $values = $used.Value2
    $rowCount = [int]$used.Rows.Count
    $colCount = [int]$used.Columns.Count
    $startRow = [int]$used.Row
    $rows = @{}
    $lastDataRow = $startRow - 1

    for ($row = 1; $row -le $rowCount; $row++) {
        $builder = New-Object System.Text.StringBuilder
        $hasValue = $false

        for ($col = 1; $col -le $colCount; $col++) {
            if ($values -is [System.Array]) {
                $value = $values.GetValue($row, $col)
            } else {
                $value = $values
            }

            if ($null -ne $value -and [string]$value -ne '') {
                $hasValue = $true
                $text = [Convert]::ToString($value, [Globalization.CultureInfo]::InvariantCulture)
                [void]$builder.Append($col).Append(':').Append($text.Length).Append(':').Append($text).Append(';')
            }
        }

        if ($hasValue) {
            $absoluteRow = $startRow + $row - 1
            $rows[$absoluteRow] = $builder.ToString()
            $lastDataRow = $absoluteRow
        }
    }

    return @{ Rows = $rows; LastDataRow = $lastDataRow }
}

function Get-SheetByName {
    param($Workbook, [string]$Name)

    foreach ($sheet in @($Workbook.Worksheets)) {
        if ([string]$sheet.Name -eq $Name) {
            return $sheet
        }
    }
    return $null
}

if ($Files.Count -lt 2) {
    throw 'Provide at least two Excel workbooks to merge.'
}

$workbookPaths = @($Files | ForEach-Object { Resolve-WorkbookPath $_ })
$basePath = $workbookPaths[0]

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $baseItem = Get-Item -LiteralPath $basePath
    $suffix = if ([string]::IsNullOrWhiteSpace($DateSuffix)) { '_merged' } else { "_merged$DateSuffix" }
    $OutputPath = Join-Path $baseItem.DirectoryName ($baseItem.BaseName + $suffix + '.xlsx')
} elseif (-not [System.IO.Path]::IsPathRooted($OutputPath)) {
    $OutputPath = Join-Path (Get-Location).Path $OutputPath
}

if (Test-Path -LiteralPath $OutputPath) {
    Remove-Item -LiteralPath $OutputPath -Force
}

$excel = $null
$target = $null
try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $excel.AskToUpdateLinks = $false

    $target = $excel.Workbooks.Open($basePath, 0, $false)
    $null = $target.SaveAs($OutputPath, 51)
    $stats = @()

    foreach ($sourcePath in $workbookPaths[1..($workbookPaths.Count - 1)]) {
        $sourceItem = Get-Item -LiteralPath $sourcePath
        $source = $excel.Workbooks.Open($sourcePath, 0, $true)
        try {
            foreach ($sourceSheet in @($source.Worksheets)) {
                $sheetName = [string]$sourceSheet.Name
                $destinationSheet = Get-SheetByName -Workbook $target -Name $sheetName

                if ($null -eq $destinationSheet) {
                    $null = $sourceSheet.Copy([Type]::Missing, $target.Sheets.Item($target.Sheets.Count))
                    $stats += [pscustomobject]@{ Source = $sourceItem.Name; Sheet = $sheetName; AddedRows = 'sheet copied'; Status = 'added' }
                    continue
                }

                $destinationData = Get-RowData -Sheet $destinationSheet
                $sourceData = Get-RowData -Sheet $sourceSheet
                $knownRows = @{}

                foreach ($signature in $destinationData.Rows.Values) {
                    $knownRows[$signature] = $true
                }

                $nextRow = [Math]::Max($destinationData.LastDataRow, [int]$destinationSheet.UsedRange.Row) + 1
                $added = 0

                foreach ($sourceRow in ($sourceData.Rows.Keys | Sort-Object { [int]$_ })) {
                    $signature = $sourceData.Rows[$sourceRow]
                    if (-not $knownRows.ContainsKey($signature)) {
                        $null = $sourceSheet.Rows.Item([int]$sourceRow).Copy($destinationSheet.Rows.Item([int]$nextRow))
                        $knownRows[$signature] = $true
                        $nextRow++
                        $added++
                    }
                }

                $stats += [pscustomobject]@{ Source = $sourceItem.Name; Sheet = $sheetName; AddedRows = $added; Status = 'merged' }
            }
        } finally {
            $source.Close($false)
        }
    }

    $target.Save()
    $target.Close($true)
    $target = $null

    $check = $excel.Workbooks.Open($OutputPath, 0, $true)
    $sheetCount = $check.Worksheets.Count
    $check.Close($false)

    Write-Output "Created: $OutputPath"
    Write-Output "Verified: workbook opens successfully; sheets=$sheetCount"
    $stats | Format-Table -AutoSize | Out-String -Width 180 | Write-Output
} finally {
    if ($null -ne $target) {
        $target.Close($false)
    }
    if ($null -ne $excel) {
        $excel.Quit()
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($excel)
    }
}

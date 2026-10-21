#requires -Version 5.1
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression.FileSystem
$script = Join-Path (Split-Path $PSScriptRoot -Parent) 'scripts/generate-scan-hry-map.ps1'
$root = Join-Path ([System.IO.Path]::GetTempPath()) ('scan-hry-tests-' + [guid]::NewGuid().ToString('N'))
[void][System.IO.Directory]::CreateDirectory($root)
$passed = 0

function Assert($Condition, [string]$Message) {
    if (-not $Condition) { throw "ASSERTION FAILED: $Message" }
}

function Expect-Failure([scriptblock]$Action, [string]$Pattern) {
    $message = $null
    try { & $Action | Out-Null } catch { $message = $_.Exception.Message }
    Assert ($null -ne $message -and $message -like "*$Pattern*") "Expected '$Pattern', got '$message'"
    $script:passed++
}

function Write-Text([string]$Path, [string]$Text) {
    [System.IO.File]::WriteAllText($Path, $Text, [System.Text.UTF8Encoding]::new($false))
}

function New-Workbook([string]$Path, [array]$Rows, [switch]$Shared, [switch]$AbsoluteTarget,
    [string]$Merge = '', [switch]$Formula) {
    $ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    $relNs = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    $sheetName = 'scanin scanout io' + [char]0x6620 + [char]0x5c04 + [char]0x5173 + [char]0x7cfb
    $xml = '<worksheet xmlns="' + $ns + '"><sheetData><row r="1"><c r="M1" t="inlineStr"><is><t>Title</t></is></c></row>'
    $strings = @(); $rowNumber = 3
    foreach ($row in $Rows) {
        $xml += '<row r="' + $rowNumber + '">'
        for ($i = 0; $i -lt 4; $i++) {
            if ($null -eq $row[$i]) { continue }
            $ref = @('M', 'N', 'P', 'Q')[$i] + $rowNumber
            $value = [System.Security.SecurityElement]::Escape([string]$row[$i])
            if ($Shared) {
                $xml += '<c r="' + $ref + '" t="s"><v>' + $strings.Count + '</v></c>'
                # Rich shared strings exercise concatenation of multiple runs.
                $strings += '<si><r><t>' + $value + '</t></r><r><t></t></r></si>'
            } else {
                $formulaXml = if ($Formula -and $i -eq 2) { '<f>1+1</f>' } else { '' }
                $xml += '<c r="' + $ref + '" t="inlineStr">' + $formulaXml + '<is><t>' + $value + '</t></is></c>'
            }
        }
        $xml += '</row>'; $rowNumber++
    }
    $xml += '</sheetData>'
    if ($Merge) { $xml += '<mergeCells><mergeCell ref="' + $Merge + '"/></mergeCells>' }
    $xml += '</worksheet>'
    $target = if ($AbsoluteTarget) { '/xl/worksheets/sheet1.xml' } else { 'worksheets/sheet1.xml' }
    $parts = @{
        '[Content_Types].xml' = '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>'
        '_rels/.rels' = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="' + $relNs + '/officeDocument" Target="xl/workbook.xml"/></Relationships>'
        'xl/workbook.xml' = '<workbook xmlns="' + $ns + '" xmlns:r="' + $relNs + '"><sheets><sheet name="' + $sheetName + '" sheetId="1" r:id="rId1"/></sheets></workbook>'
        'xl/_rels/workbook.xml.rels' = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="' + $relNs + '/worksheet" Target="' + $target + '"/></Relationships>'
        'xl/worksheets/sheet1.xml' = $xml
    }
    if ($Shared) { $parts['xl/sharedStrings.xml'] = '<sst xmlns="' + $ns + '">' + ($strings -join '') + '</sst>' }
    $zip = [System.IO.Compression.ZipFile]::Open($Path, [System.IO.Compression.ZipArchiveMode]::Create)
    try {
        foreach ($name in $parts.Keys) {
            $writer = [System.IO.StreamWriter]::new($zip.CreateEntry($name).Open(), [System.Text.UTF8Encoding]::new($false))
            try { $writer.Write($parts[$name]) } finally { $writer.Dispose() }
        }
    } finally { $zip.Dispose() }
}

try {
    $template = Join-Path $root 'template.json'
    # Synthetic template: metadata and comma/bracket text must survive normalization.
    Write-Text $template @'
{
  "HRYLength": 99,
  "GroupCount": 1,
  "Metadata": "literal ,] and ,} and escaped quote \" text",
  "Patterns": [{
    "PatternRegex": "old",
    "Extra": true,
    "Groups": [{
      "GroupNumber": 7,
      "Pins": [{
        "PinRegex": "old",
        "PinMetadata": {"label": "preserve"},
        "Shifts": [{"ShiftStart": 4, "ShiftEnd": 12345, "HRYIndex": 9, "HRYPrint": "old", "Other": [1, 2],},],
      },],
    },],
  },],
}
'@
    $rows = @(
        @('G1', 'B2', 'OUTPUT_LOOKING_NAME', 'ScanIn'),
        @('G1', 'B1', 'INPUT_LOOKING_NAME', 'ScanOut'),
        @('G1', 'B2', 'SECOND_OUT', 'ScanOut'),
        @('G1', 'B1', 'THIRD_OUT', ' scanout '),
        @('G1', 'EMPTY', 'IGNORED', 'ScanIn'),
        @('trans_G3', 'ONLY', 'ONE_OUT', 'ScanOut'),
        @('G9', 'NONE', 'IGNORED', 'ScanIn'),
        @('G1', 'B1', 'THIRD_OUT', 'ScanOut')
    )
    $workbook = Join-Path $root 'inline.xlsx'
    New-Workbook $workbook $rows -Merge 'M1:R1'
    $hashes = @((Get-FileHash $workbook).Hash, (Get-FileHash $template).Hash)
    $out = Join-Path $root 'dry-run-output'
    $plans = @(& $script -WorkbookPath $workbook -TemplatePath $template -OutputDirectory $out -DryRun -WarningAction SilentlyContinue)
    Assert ($plans.Count -eq 2 -and -not (Test-Path $out)) 'DryRun must not write files or directories'
    $passed++
    $plans = @(& $script -WorkbookPath $workbook -TemplatePath $template -OutputDirectory $out -WarningAction SilentlyContinue)
    $g1Path = Join-Path $out 'UNCORE_G1_HRY_Map.json'
    $g1 = Get-Content $g1Path -Raw | ConvertFrom-Json
    $pins = $g1.Patterns[0].Groups[0].Pins
    Assert ($g1.HRYLength -eq 2 -and $pins.Count -eq 2) 'Drop empty blocks and recalculate length'
    Assert ($pins[0].Shifts[0].HRYPrint -ceq 'B2' -and $pins[1].Shifts[0].HRYPrint -ceq 'B1') 'Block first-appearance order'
    Assert ($pins[0].Shifts[0].HRYIndex -eq 0 -and $pins[1].Shifts[0].HRYIndex -eq 1) 'Contiguous indices'
    Assert ($pins[0].PinRegex -ceq 'SECOND_OUT' -and $pins[1].PinRegex -ceq 'INPUT_LOOKING_NAME|THIRD_OUT|THIRD_OUT') 'Use Q not pin name; retain order and duplicates'
    Assert ($g1.Patterns[0].PatternRegex -ceq 'CCD_G1' -and $g1.GroupCount -eq 1 -and $g1.Patterns[0].Groups[0].GroupNumber -eq 7) 'Preserve non-mapping values'
    Assert ($pins[0].Shifts[0].ShiftStart -eq 4 -and $pins[0].Shifts[0].ShiftEnd -eq 12345 -and $pins[0].PinMetadata.label -eq 'preserve') 'Preserve nested metadata'
    Assert ($g1.Metadata -ceq 'literal ,] and ,} and escaped quote " text') 'Do not remove commas inside strings'
    Assert (-not (Test-Path (Join-Path $out 'UNCORE_G9_HRY_Map.json'))) 'Omit all-empty groups'
    $trans = Get-Content (Join-Path $out 'UNCORE_trans_G3_HRY_Map.json') -Raw | ConvertFrom-Json
    Assert ($trans.Patterns[0].PatternRegex -ceq 'CCD_trans_G3' -and $trans.HRYLength -eq 1 -and $trans.Patterns[0].Groups[0].Pins -is [array]) 'Preserve prefixes and singleton arrays'
    Assert ((Get-FileHash $workbook).Hash -eq $hashes[0] -and (Get-FileHash $template).Hash -eq $hashes[1]) 'Inputs unchanged'
    $passed++
    $outputHash = (Get-FileHash $g1Path).Hash
    Expect-Failure { & $script -WorkbookPath $workbook -TemplatePath $template -OutputDirectory $out -WarningAction SilentlyContinue } 'Output exists'
    & $script -WorkbookPath $workbook -TemplatePath $template -OutputDirectory $out -Force -WarningAction SilentlyContinue | Out-Null
    Assert ((Get-FileHash $g1Path).Hash -eq $outputHash) 'Force regeneration is deterministic'
    $passed++
    $shared = Join-Path $root 'shared.xlsx'
    New-Workbook $shared $rows -Shared -AbsoluteTarget
    $sharedOut = Join-Path $root 'shared-output'
    & $script -WorkbookPath $shared -TemplatePath $template -OutputDirectory $sharedOut -WarningAction SilentlyContinue | Out-Null
    Assert ((Get-FileHash (Join-Path $sharedOut 'UNCORE_G1_HRY_Map.json')).Hash -eq $outputHash) 'Shared rich strings and absolute targets match inline strings'
    $passed++
    $keepOut = Join-Path $root 'never-match'
    & $script -WorkbookPath $workbook -TemplatePath $template -OutputDirectory $keepOut -EmptyBlockPolicy NeverMatch -WarningAction SilentlyContinue | Out-Null
    $kept = Get-Content (Join-Path $keepOut 'UNCORE_G1_HRY_Map.json') -Raw | ConvertFrom-Json
    Assert ($kept.HRYLength -eq 3 -and $kept.Patterns[0].Groups[0].Pins[2].PinRegex -eq '(?!)') 'NeverMatch keeps empty blocks safely'
    $passed++
    Expect-Failure { & $script -WorkbookPath $workbook -TemplatePath $template -SheetName 'missing' -DryRun } 'Worksheet not found'
    foreach ($case in @(
        @{ Name = 'direction'; Rows = ,@('G1','B1','P1','Other'); Error = 'Unknown direction' },
        @{ Name = 'incomplete'; Rows = ,@('G1','B1',$null,'ScanOut'); Error = 'Incomplete' },
        @{ Name = 'unsafe'; Rows = ,@('../G1','B1','P1','ScanOut'); Error = 'Unsafe group filename' },
        @{ Name = 'collision'; Rows = @(@('G1','B1','P1','ScanOut'), @('g1','B1','P2','ScanOut')); Error = 'filename collision' },
        @{ Name = 'empty'; Rows = ,@('G1','B1','P1','ScanIn'); Error = 'No outputs' }
    )) {
        $file = Join-Path $root ($case.Name + '.xlsx')
        New-Workbook $file $case.Rows
        $badOut = Join-Path $root ($case.Name + '-output')
        Expect-Failure { & $script -WorkbookPath $file -TemplatePath $template -OutputDirectory $badOut -WarningAction SilentlyContinue } $case.Error
        Assert (-not (Test-Path $badOut)) 'Validation must finish before any output is written'
    }
    $merged = Join-Path $root 'merged.xlsx'
    New-Workbook $merged $rows -Merge 'M3:M4'
    Expect-Failure { & $script -WorkbookPath $merged -TemplatePath $template -DryRun } 'Merged mapping cells'
    $formula = Join-Path $root 'formula.xlsx'
    New-Workbook $formula $rows -Formula
    Expect-Failure { & $script -WorkbookPath $formula -TemplatePath $template -DryRun } 'Formula in mapping cell'
    $ambiguous = Join-Path $root 'ambiguous.json'
    $g1.Patterns += $g1.Patterns[0]
    Write-Text $ambiguous ($g1 | ConvertTo-Json -Depth 100)
    Expect-Failure { & $script -WorkbookPath $workbook -TemplatePath $ambiguous -DryRun } 'exactly one pattern'
    $different = Join-Path $root 'different.json'
    $g1 = Get-Content $g1Path -Raw | ConvertFrom-Json
    $g1.Patterns[0].Groups[0].Pins[1].Shifts[0].ShiftEnd = 42
    Write-Text $different ($g1 | ConvertTo-Json -Depth 100)
    Expect-Failure { & $script -WorkbookPath $workbook -TemplatePath $different -DryRun } 'differing non-mapping metadata'
    Expect-Failure { & $script -WorkbookPath $workbook -TemplatePath $g1Path -OutputDirectory $out -Force -WarningAction SilentlyContinue } 'Cannot overwrite input'
    Write-Output "PASS: $passed regression cases. Synthetic fixtures only; no external dependencies."
} finally {
    Remove-Item -LiteralPath $root -Recurse -Force
}
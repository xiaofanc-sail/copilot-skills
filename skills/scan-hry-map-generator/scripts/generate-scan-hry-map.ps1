#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$WorkbookPath,
    [Parameter(Mandatory = $true)][string]$TemplatePath,
    [string]$OutputDirectory,
    [string]$SheetName = ('scanin scanout io' + [char]0x6620 + [char]0x5c04 + [char]0x5173 + [char]0x7cfb),
    [ValidateRange(1, 1048576)][int]$FirstDataRow = 3,
    [ValidateSet('Drop', 'NeverMatch')][string]$EmptyBlockPolicy = 'Drop',
    [switch]$DryRun,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression.FileSystem

function Read-ZipXml($Zip, [string]$Name) {
    $entry = $Zip.GetEntry($Name)
    if ($null -eq $entry) { throw "Missing XLSX part: $Name" }
    $stream = $entry.Open()
    $settings = New-Object System.Xml.XmlReaderSettings
    $settings.DtdProcessing = [System.Xml.DtdProcessing]::Prohibit
    $settings.XmlResolver = $null
    $reader = [System.Xml.XmlReader]::Create($stream, $settings)
    try {
        $xml = New-Object System.Xml.XmlDocument
        $xml.XmlResolver = $null
        $xml.Load($reader)
        return ,$xml
    } finally { $reader.Dispose(); $stream.Dispose() }
}

function Get-CellText($Cell, $Strings) {
    if ($null -ne $Cell.SelectSingleNode('./*[local-name()="f"]')) {
        throw "Formula in mapping cell $($Cell.r); supply literal values instead."
    }
    $value = $Cell.SelectSingleNode('./*[local-name()="v"]')
    switch ($Cell.GetAttribute('t')) {
        's' {
            if ($null -eq $value -or $value.InnerText -notmatch '^\d+$') {
                throw "Invalid shared string in cell $($Cell.r)"
            }
            $index = [int]$value.InnerText
            if ($index -ge $Strings.Count) { throw "Shared string index out of range: $($Cell.r)" }
            return $Strings[$index]
        }
        'inlineStr' {
            return (($Cell.SelectNodes('./*[local-name()="is"]//*[local-name()="t"]') |
                ForEach-Object { $_.InnerText }) -join '')
        }
        'e' { throw "Excel error in mapping cell $($Cell.r)" }
        default { if ($null -ne $value) { return $value.InnerText }; return '' }
    }
}

function ConvertTo-CanonicalJson($Value) {
    return ConvertTo-Json -InputObject $Value -Depth 100 -Compress
}

function Copy-JsonObject($Value) {
    return (ConvertTo-CanonicalJson $Value) | ConvertFrom-Json
}

function Require-Properties($Value, [string[]]$Names, [string]$Label) {
    foreach ($name in $Names) {
        if ($null -eq $Value -or $null -eq $Value.PSObject.Properties[$name]) {
            throw "Template $Label is missing $name"
        }
    }
}

$WorkbookPath = (Resolve-Path -LiteralPath $WorkbookPath).ProviderPath
$TemplatePath = (Resolve-Path -LiteralPath $TemplatePath).ProviderPath
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) { $OutputDirectory = Split-Path $TemplatePath -Parent }
$OutputDirectory = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputDirectory)
$sourceHashes = @((Get-FileHash -LiteralPath $WorkbookPath).Hash, (Get-FileHash -LiteralPath $TemplatePath).Hash)
$raw = [System.IO.File]::ReadAllText($TemplatePath)
# Match complete strings first, so comma/bracket text inside a string is never altered.
$raw = [regex]::Replace($raw, '"(?:[^"\\]|\\.)*"|,\s*(?=[}\]])', [System.Text.RegularExpressions.MatchEvaluator]{
    param($match)
    if ($match.Value.StartsWith('"')) { return $match.Value }
    return ''
})
$template = $raw | ConvertFrom-Json
Require-Properties $template @('HRYLength', 'GroupCount', 'Patterns') 'root'
if ($template.Patterns -isnot [array] -or $template.Patterns.Count -ne 1) {
    throw 'Template must contain exactly one pattern in a Patterns array.'
}
$pattern = $template.Patterns[0]
Require-Properties $pattern @('PatternRegex', 'Groups') 'pattern'
if ($pattern.Groups -isnot [array] -or $pattern.Groups.Count -ne 1) {
    throw 'Template must contain exactly one group in a Groups array.'
}
$groupTemplate = $pattern.Groups[0]
Require-Properties $groupTemplate @('GroupNumber', 'Pins') 'group'
if ($groupTemplate.Pins -isnot [array] -or $groupTemplate.Pins.Count -eq 0) {
    throw 'Template Pins must be a nonempty array.'
}
$pinShape = $null
foreach ($pin in $groupTemplate.Pins) {
    Require-Properties $pin @('PinRegex', 'Shifts') 'pin'
    if ($pin.Shifts -isnot [array] -or $pin.Shifts.Count -ne 1) {
        throw 'Each template pin must have exactly one shift in a Shifts array.'
    }
    Require-Properties $pin.Shifts[0] @('ShiftStart', 'ShiftEnd', 'HRYIndex', 'HRYPrint') 'shift'
    $normalized = Copy-JsonObject $pin
    $normalized.PinRegex = ''
    $normalized.Shifts[0].HRYIndex = 0
    $normalized.Shifts[0].HRYPrint = ''
    $shape = ConvertTo-CanonicalJson $normalized
    if ($null -ne $pinShape -and $shape -cne $pinShape) {
        throw 'Template pins have differing non-mapping metadata; cannot choose a prototype safely.'
    }
    $pinShape = $shape
}

$mapping = [System.Collections.Specialized.OrderedDictionary]::new([System.StringComparer]::Ordinal)
$zip = [System.IO.Compression.ZipFile]::OpenRead($WorkbookPath)
try {
    $workbook = Read-ZipXml $zip 'xl/workbook.xml'
    $relationships = Read-ZipXml $zip 'xl/_rels/workbook.xml.rels'
    $sheets = @($workbook.SelectNodes('//*[local-name()="sheet"]') |
        Where-Object { $_.GetAttribute('name') -ceq $SheetName })
    if ($sheets.Count -ne 1) { throw "Worksheet not found or ambiguous: $SheetName" }
    $rid = $sheets[0].GetAttribute('id', 'http://schemas.openxmlformats.org/officeDocument/2006/relationships')
    $rels = @($relationships.SelectNodes('//*[local-name()="Relationship"]') |
        Where-Object { $_.GetAttribute('Id') -ceq $rid })
    if ($rels.Count -ne 1 -or $rels[0].GetAttribute('TargetMode') -eq 'External') {
        throw 'Invalid or external worksheet relationship.'
    }
    $uri = [uri]::new([uri]'http://xlsx.local/xl/workbook.xml', $rels[0].GetAttribute('Target'))
    if ($uri.Host -ne 'xlsx.local') { throw 'External worksheet target is unsupported.' }
    $sheet = Read-ZipXml $zip ([uri]::UnescapeDataString($uri.AbsolutePath.TrimStart('/')))
    $strings = @()
    if ($null -ne $zip.GetEntry('xl/sharedStrings.xml')) {
        $shared = Read-ZipXml $zip 'xl/sharedStrings.xml'
        $strings = @($shared.SelectNodes('//*[local-name()="si"]') | ForEach-Object {
            ($_.SelectNodes('./*[local-name()="t"] | ./*[local-name()="r"]/*[local-name()="t"]') |
                ForEach-Object { $_.InnerText }) -join ''
        })
    }
    foreach ($merge in $sheet.SelectNodes('//*[local-name()="mergeCell"]')) {
        if ($merge.ref -match '^([A-Z]+)(\d+):([A-Z]+)(\d+)$') {
            $left = 0; foreach ($char in $Matches[1].ToCharArray()) { $left = $left * 26 + [int]$char - 64 }
            $right = 0; foreach ($char in $Matches[3].ToCharArray()) { $right = $right * 26 + [int]$char - 64 }
            if ([int]$Matches[4] -ge $FirstDataRow -and @((13, 14, 16, 17) | Where-Object { $_ -ge $left -and $_ -le $right }).Count) {
                throw "Merged mapping cells are unsupported: $($merge.ref). Unmerge and fill explicit values."
            }
        }
    }
    foreach ($row in ($sheet.SelectNodes('//*[local-name()="sheetData"]/*[local-name()="row"]') | Sort-Object { [int]$_.r })) {
        if ([int]$row.r -lt $FirstDataRow) { continue }
        $values = @{}
        foreach ($cell in $row.SelectNodes('./*[local-name()="c"]')) {
            if ($cell.r -match '^(M|N|P|Q)\d+$') {
                $values[$Matches[1]] = ([string](Get-CellText $cell $strings)).Trim()
            }
        }
        if (-not ($values.M -or $values.N -or $values.P -or $values.Q)) { continue }
        if (-not ($values.M -and $values.N -and $values.P -and $values.Q)) {
            throw "Incomplete M/N/P/Q mapping at row $($row.r)"
        }
        if ($values.Q -notin @('ScanIn', 'ScanOut')) { throw "Unknown direction at row $($row.r): $($values.Q)" }
        $g = $values.M; $b = $values.N
        if ($g -match '[<>:"/\\|?*\x00-\x1f]' -or $g.EndsWith('.')) { throw "Unsafe group filename: $g" }
        if (-not $mapping.Contains($g)) {
            $mapping.Add($g, [System.Collections.Specialized.OrderedDictionary]::new([System.StringComparer]::Ordinal))
        }
        if (-not $mapping[$g].Contains($b)) { $mapping[$g].Add($b, [System.Collections.Generic.List[string]]::new()) }
        if ($values.Q -eq 'ScanOut') { $mapping[$g][$b].Add($values.P) }
    }
} finally { $zip.Dispose() }
if ($mapping.Count -eq 0) { throw 'No mapping rows found.' }

$plans = @()
$paths = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
foreach ($g in $mapping.Keys) {
    $document = Copy-JsonObject $template
    $document.Patterns[0].PatternRegex = "CCD_$g"
    $pins = @(); $emptyBlocks = @(); $pinCount = 0
    foreach ($b in $mapping[$g].Keys) {
        $names = $mapping[$g][$b]
        if ($names.Count -eq 0) {
            $emptyBlocks += $b
            if ($EmptyBlockPolicy -eq 'Drop') { continue }
        }
        $pin = Copy-JsonObject $groupTemplate.Pins[0]
        $pin.PinRegex = if ($names.Count) { $names -join '|' } else { '(?!)' }
        $pin.Shifts[0].HRYIndex = $pins.Count
        $pin.Shifts[0].HRYPrint = $b
        $pins += $pin
        $pinCount += $names.Count
    }
    if ($emptyBlocks.Count) { Write-Warning "$g has no ScanOut for: $($emptyBlocks -join ', '); policy=$EmptyBlockPolicy" }
    if ($pins.Count -eq 0) { Write-Warning "Omitted group $g (no retained blocks); existing files, if any, are not deleted."; continue }
    $document.HRYLength = $pins.Count
    $document.Patterns[0].Groups[0].Pins = @($pins)
    $path = Join-Path $OutputDirectory "UNCORE_${g}_HRY_Map.json"
    if (-not $paths.Add($path)) { throw "Case-insensitive output filename collision: $path" }
    if ($path -ieq $TemplatePath -or $path -ieq $WorkbookPath) { throw "Cannot overwrite input: $path" }
    if (Test-Path -LiteralPath $path) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Output is not a file: $path" }
        if (-not $Force -and -not $DryRun) { throw "Output exists; review then use -Force: $path" }
        Write-Warning "Existing output: $path"
    }
    $plans += [pscustomobject]@{ Group = $g; Path = $path; Document = $document; Blocks = $pins.Count; ScanOutPins = $pinCount }
}
if ($plans.Count -eq 0) { throw 'No outputs: all groups have no ScanOut blocks.' }
if (-not $DryRun) {
    [void][System.IO.Directory]::CreateDirectory($OutputDirectory)
    foreach ($plan in $plans) {
        $json = ConvertTo-Json -InputObject $plan.Document -Depth 100
        [System.IO.File]::WriteAllText($plan.Path, $json + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
        $check = [System.IO.File]::ReadAllText($plan.Path) | ConvertFrom-Json
        if ((ConvertTo-CanonicalJson $check) -cne (ConvertTo-CanonicalJson $plan.Document)) {
            throw "Output verification failed: $($plan.Path)"
        }
    }
}
if ((Get-FileHash -LiteralPath $WorkbookPath).Hash -ne $sourceHashes[0] -or
    (Get-FileHash -LiteralPath $TemplatePath).Hash -ne $sourceHashes[1]) { throw 'An input file changed during generation.' }
$plans | Select-Object Group, Path, Blocks, ScanOutPins, @{ Name = 'DryRun'; Expression = { [bool]$DryRun } }
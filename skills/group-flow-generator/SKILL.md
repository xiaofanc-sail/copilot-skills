---
name: group-flow-generator
description: Generate a new MTPL group flow by copying an existing group flow/composite, changing source group names to a target group name, and applying configurable counter/bin digit replacement rules. Use when the user asks to copy GROUP2 to GROUP1, clone a group flow, generate a new group composite, or change group names and counter digit positions/values in MTPL.
---

# Group Flow Generator

Generate a new MTPL group flow/composite by copying an existing flow, changing group names, and applying configurable numeric transformations to `SetBin` and `IncrementCounters` identifiers.

## When to Use

Use this skill when the user asks to:

- Copy one MTPL group flow/composite to another group.
- Generate a new `PREHVQK_VNOM_G1`-style flow from an existing `PREHVQK_VNOM_G2`-style flow.
- Replace group names such as `GROUP2 -> GROUP1` or any other source/target group pair.
- Transform counter/bin numeric digits by configurable positions and values.
- Add the copied flow into a subflow.
- Copy supporting `CSharpTest` definitions and counter declarations for the new group.

Do not use this skill for unrelated refactoring, performance work, or non-MTPL files unless the user explicitly requests a similar transformation pattern.

## Required Inputs

Ask for any missing required input before editing.

| Input | Meaning | Example |
|---|---|---|
| `filePath` | MTPL file to edit | `Modules/SCN_UNCORE_DRD/SCN_UNCORE_DRD.mtpl` |
| `sourceFlowName` | Existing flow/composite to copy | `PREHVQK_VNOM_G2` |
| `targetFlowName` | New flow/composite name | `PREHVQK_VNOM_G1` |
| `sourceGroupName` | Group token to replace | `GROUP2` |
| `targetGroupName` | New group token | `GROUP1` |
| `subflowName` | Subflow to add the copied flow into, if needed | `SCN_UNCORE_DRD_PREHVQK` |
| `insertPositionInSubflow` | Where to insert target flow relative to source flow | `after sourceFlowName` |
| `binDigitRules` | Digit replacement rules for `SetBin b########...` IDs | Replace digits 5-6 with `01` |
| `counterDigitRules` | Digit replacement rules for `IncrementCounters ...::n########...` IDs | Replace digits 3-4 with `01` |

## Digit Rule Format

Use 1-based inclusive positions.

Example:

```text
binDigitRules:
  identifierPrefix: b
  digitCount: 8
  replacements:
	- start: 5
	  end: 6
	  value: "01"

counterDigitRules:
  identifierPrefix: n
  digitCount: 8
  replacements:
	- start: 3
	  end: 4
	  value: "01"
```

For an 8-digit identifier:

- `b99410000` with digits 5-6 set to `01` becomes `b99410100`.
- `n41000000` with digits 3-4 set to `01` becomes `n41010000`.

Only transform identifiers explicitly covered by the rules:

- Apply `binDigitRules` to `SetBin b########...` names.
- Apply `counterDigitRules` to `IncrementCounters <module>::n########...` names.
- Do not transform unrelated numeric values, comments, voltages, pattern names, or configuration values.

## Transformation Rules

### 1. Copy Flow Block

Find the complete source flow block:

```text
Flow <sourceFlowName>
{
  ...
}
```

Use brace depth to locate the matching closing brace. Do not rely only on fixed line counts.

Create a copied block with:

- `sourceFlowName -> targetFlowName`
- `sourceGroupName -> targetGroupName`
- `binDigitRules` applied to `SetBin b########...`
- `counterDigitRules` applied to `IncrementCounters ...::n########...`

Keep the source flow unchanged.

### 2. Preserve Internal Naming Consistency

For every copied `FlowItem`, ensure both instance names match:

```text
FlowItem <targetInstance> <targetInstance> @EDC
```

For copied counters, the suffix after `_fail_` should match the copied FlowItem when applicable:

```text
n########_fail_<targetInstance>_0
n########_fail_<targetInstance>_2
```

For copied bins, the suffix after `_fail_<module>_` should match the copied FlowItem when applicable:

```text
b########_fail_<module>_<targetInstance>_n2
b########_fail_<module>_<targetInstance>_n1
```

### 3. Copy Supporting CSharpTest Definitions

For every copied `FlowItem <targetInstance>`, verify a corresponding test/flow definition exists.

If missing, locate the source definition matching the source instance:

```text
CSharpTest <method> <sourceInstance>
{
  ...
}
```

Copy that complete block and transform:

- `sourceGroupName -> targetGroupName`
- Any source instance tokens to target instance tokens

Insert copied `CSharpTest` definitions near the source definitions, usually before the source group definitions or in the same section.

Do not create duplicate definitions if they already exist.

### 4. Add Counter Declarations

Collect every transformed counter used in the target flow:

```text
IncrementCounters <module>::n########_fail_...;
```

In the top `Counters` block, add any missing transformed counters.

Rules:

- Do not duplicate existing declarations.
- Preserve indentation and comma style.
- Keep final counter syntax valid: every declaration except the last should end with `,`.
- Prefer inserting near related source/target group counters.

### 5. Add Target Flow to Subflow

If `subflowName` is provided, find:

```text
Flow <subflowName>
{
  ...
}
```

Add:

```text
FlowItem <targetFlowName> <targetFlowName>
{
  Result -2
  {
	Property PassFail = "Fail";
	Return -2;
  }
  Result -1
  {
	Property PassFail = "Fail";
	Return -1;
  }
  Result 0
  {
	Property PassFail = "Fail";
	Return 1;
  }
  Result 1
  {
	Property PassFail = "Pass";
	Return 1;
  }
}
```

Place it according to `insertPositionInSubflow`, for example after `sourceFlowName`.

If the target FlowItem already exists in the subflow, reorder it rather than duplicating it.

## Validation Checklist

After edits, validate all of the following:

1. Target flow exists exactly once.
2. Source flow remains unchanged.
3. Target flow contains no old `sourceFlowName` tokens.
4. Target flow contains no old `sourceGroupName` tokens unless intentionally present in comments or unrelated values.
5. Every target `FlowItem` has a matching definition or flow.
6. Every transformed counter used by the target flow is declared in `Counters`.
7. `SetBin` numeric identifiers follow `binDigitRules`.
8. `IncrementCounters` numeric identifiers follow `counterDigitRules`.
9. The target FlowItem appears in the requested subflow position.
10. Run file diagnostics and fix only issues introduced by this transformation.

## Suggested PowerShell Validation Snippets

### Find Flow Range

```powershell
$path = '<filePath>'
$lines = Get-Content $path
$flowName = '<targetFlowName>'
$start = ($lines | Select-String "^Flow $flowName`$").LineNumber
$depth = 0
$end = 0
for ($i = $start - 1; $i -lt $lines.Count; $i++) {
  if ($lines[$i] -match '\{') { $depth++ }
  if ($lines[$i] -match '\}') {
	$depth--
	if ($i -gt $start -and $depth -eq 0) { $end = $i + 1; break }
  }
}
"$flowName lines $start-$end"
```

### Validate Target Flow Tokens and Digit Rules

```powershell
$block = $lines[($start - 1)..($end - 1)]
$oldTokens = ($block | Select-String '<sourceFlowName>|<sourceGroupName>').Count
$badBins = @()
$badCounters = @()

foreach ($line in $block) {
  if ($line -match 'SetBin\s+b(\d{8})_') {
	$digits = $matches[1]
	# Example: digits 5-6 must be 01. Convert input positions from 1-based to 0-based.
	if ($digits.Substring(4, 2) -ne '01') { $badBins += $line.Trim() }
  }
  if ($line -match 'IncrementCounters\s+\S+::n(\d{8})_') {
	$digits = $matches[1]
	# Example: digits 3-4 must be 01.
	if ($digits.Substring(2, 2) -ne '01') { $badCounters += $line.Trim() }
  }
}

"Old tokens: $oldTokens"
"Bad SetBin transforms: $($badBins.Count)"
"Bad counter transforms: $($badCounters.Count)"
```

## Reporting Back

Summarize briefly:

- Source flow copied.
- Target flow created.
- Group replacement applied.
- Counter/bin digit rules applied.
- CSharpTest definitions and counter declarations added if needed.
- Subflow insertion/reorder completed.
- Diagnostics result.

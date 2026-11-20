---
name: scan-yield-analyzer
description: 'Analyze lot-level scan yield from AQUA tracking, raw iTUFF and TP MTPL/bin definitions. Use when users ask to analyze or compare scan yield for one or more lots, investigate scan IB 41/42, list FB/test names, collect KILL instance die locations, find non-killing scan failures or overlapping fail counters, or generate/update separate KILL and EDC yield reports. Supports combined lots, actual tested-die denominators, final-kill yield, counter yield, and HRY/SPOFI exclusion. Not for single-unit CLASS failure analysis.'
---

# Scan Yield Analyzer

Run the bundled [Python script](./scripts/scan_yield_analyzer.py) to create a standalone HTML report, CSV exports and a reusable local JSON cache. Python 3.10+ and its standard library are sufficient; no pip dependencies.

## Required Behavior

- Combine selected lots by exact `module::instance`. Retain lot, wafer, X and Y in die identity.
- Select the latest die attempt within one operation per lot and validate it against AQUA tracking. Do not combine different TP versions or multiple operations for the same lot.
- Include all final dies, including final PASS dies with intermediate failure counters. Do not analyze only final-bin failures.
- Exclude instance names containing HRY or SPOFI, case-insensitively, from item rows and mapped other-failure entries.
- Use distinct dies with exact `testtime_MODULE::INSTANCE` records as the execution denominator. Do not substitute the total lot population.
- Add `First Scan Fail Dies` (`first_scan_fail_dies`) immediately after `Counter Fail Dies` in both tables. For each die, retain only the earliest failing included scan instance within each module, following its actual `testtime` execution sequence. This subtracts the union of earlier same-module scan failures, not their summed counts.
- Compute this column across all included scan items before any display filtering, including chain/stuck-at and both KILL/EDC modes. HRY/SPOFI remain excluded and do not suppress later items. Different modules are independent; do not infer order from names, counter IDs, declaration order or yield sorting.
- Keep `counter_fail_dies`, both existing yield formulas and their denominators unchanged. Unexecuted tests, incomplete execution evidence, unavailable order or repeated execution of a failing instance give N/A for the new count, not a guessed first failure. Unknown numeric counters cannot be assigned a module or used as earlier mapped scan failures.
- Display `Fail on Final PASS Dies` (`fail_on_final_pass_dies`) after `First Scan Fail Dies` in both tables. Count distinct dies in the intersection of this instance's counter-failure set and final PASS dies (`3_trslt`, case-insensitive), regardless of final IB number. Exclude final non-PASS dies even when the instance did not kill them. Do not subtract earlier scan failures or change any yield formula or denominator.
- Keep only `fail_on_final_pass_dies` for this metric in HTML/CSV/JSON; do not add a duplicate good-die counter-failure column. Repeated failures count once per die; no observed failures in good dies means zero, not a good-die yield estimate.
- Separate KILL and EDC tables. Preserve zero-final-kill items and zero-counter-failure items in their proper TP group.
- Collect `final_kill_locations` for each KILL instance from final dies whose exact `primary_test` matches that instance, not from all counter failures. Retain `lot`, `wafer`, `x`, `y`, `final_ib`, `final_fb` and `final_db`; preserve signed coordinates and distinct lot/wafer identities. The location count must equal `final_kill_dies`. Zero-kill instances have an empty list, including unexecuted KILL instances. Do not add this field to EDC rows or change existing counts or yields.
- Show expandable Final Kill Die Locations in the HTML KILL table and export one record per final killed die in a separate `_kill_locations.csv`. Keep the initial KILL columns and EDC layout unchanged. Reject duplicate or incomplete location evidence and count mismatches rather than guessing locations.
- KILL yield is `100 * (1 - Final Kill Dies / Tested Dies)`, sorted ascending.
- EDC yield is `100 * (1 - Counter Fail Dies / Tested Dies)`, sorted descending.
- Put N/A last in both groups. Unexecuted tests and incomplete execution evidence are N/A, not 100%.
- Obtain FB from the explicit LeafBin parent in `BinDefinitions.bdefs`, including for zero-kill tests. Never infer FB arithmetically from DB or assume one FB identifies one instance.
- Map other failures by exact counter declarations in all TP module MTPL files. Exclude the entire current instance, not only its final DB. Preserve unknown counter IDs numerically without guessed names.

## Workflow

### 1. Resolve Scope and Prerequisites

Extract the requested lot IDs and interface bins. Default scan IBs are 41 and 42; override only when requested. If a current report/cache already exists and only presentation changes are needed, go to step 5.

Run `python --version`; require Python 3.10+. Resolve the installed skill folder from this SKILL.md location, not a hardcoded user profile. Resolve the local AQUA CLI and region/server from the user's environment or existing tooling. Do not assume a particular internal hostname, drive mapping or region.

If scope is ambiguous, ask only for the missing lot/operation/TP information. Do not guess a TP from a similar product or nearby share.

### 2. Obtain AQUA Tracking and Locate Original Files

Reuse already-fetched local data when available. Otherwise query AQUA using resolved runtime values. In PowerShell, `$aqua`, `$server`, `$lots`, `$yieldCsv` and `$trackingCsv` must be set to actual verified values first:

```powershell
& $aqua -aquaserver $server -analysisType 'YIELD_&_BINS' -lots ($lots -join ',') -outputfilename $yieldCsv
& $aqua -aquaserver $server -analysisType DEVICE_TRACKING -lots ($lots -join ',') -outputfilename $trackingCsv
```

Use the summary to establish lot/bin context and tracking to determine the selected operation, wafer, TP and final population. If the regional server is unavailable, use a verified reachable region; do not loop indefinitely on a failed server. The analyzer consumes DEVICE_TRACKING CSV and raw iTUFF, not the YIELD_&_BINS summary as a substitute for per-die records.

Locate the exact production TP containing `BinDefinitions.bdefs` and `Modules/*/*.mtpl`. A tester-local drive letter is not necessarily the analyst's drive mapping. On Windows, check `Get-SmbMapping` as well as `Get-PSDrive`; use environment-provided mappings instead of guessed share roots. A file-reader tool can reject a valid UNC path; verify with `Test-Path`/`Get-Content` in the terminal before declaring it unavailable.

Find merged raw `.ITF.gz` or `.ITF` files for the selected operation and wafer(s). Supply all selected wafer files, but do not mix a merged file with its T01/T02/T03 shards. Python's gzip reader handles concatenated gzip members; a reader that stops after the header member is insufficient.

If files, tracking or exact TP definitions cannot be obtained, report the missing dependency. Do not generate a counter-yield report using estimated counters or fabricated execution counts.

### 3. Run the Analyzer

Resolve `$skillRoot`, `$ituffPaths` (an array), `$trackingCsv`, `$tpRoot` and `$reportPath`. Keep data and report output outside the skill repository.

```powershell
python "$skillRoot\scripts\scan_yield_analyzer.py" @ituffPaths --tracking $trackingCsv --tp-root $tpRoot --output $reportPath
```

The default expected program name is the TP directory name. Use `--program` only when a copied/renamed directory has a different basename and the original program name has been verified. Use repeated `--scan-bin` arguments to replace the default IB set, for example `--scan-bin 41 --scan-bin 42`.

The script rejects missing/extra final dies, bin mismatches, TP mismatches, ambiguous counter mappings, unknown scan execution names and missing/mixed FlowItem modes. It requires a verified regular failure SetBin for KILL rows. Resolve these failures at the source rather than disabling validation or forcing a report through.

### 4. Verify and Present Results

Check the command exit code and JSON console output. Confirm:

1. The tracking population agrees with final iTUFF die records and no unrequested operation or TP was included.
2. KILL yield uses final kills; EDC yield uses counter failures. Both use Tested Dies.
3. KILL is ascending, EDC descending, and N/A is last. Zero-kill KILL rows with complete execution evidence are 100%.
4. KILL begins `FB, Test, TP Flow Mode, Final Kill Dies, Final Kill Yield`, followed by population and counter details. EDC has no FB column and retains Counter Yield.
5. Final Kill Dies is a final DB attribution, not a synonym for counter failures. Overlapping failures must not be summed as lot yield loss.
6. First Scan Fail Dies is no greater than Counter Fail Dies and subtracts failures in earlier included scan items of the same module, even when those items are hidden by an atspeed-only display filter. Confirm the new column in HTML, KILL/EDC CSV and JSON; retain N/A when ordering evidence is insufficient.
7. Fail on Final PASS Dies equals the number of `failed_die_indexes` whose evidence has `final_result=PASS` (case-insensitive), and never exceeds Counter Fail Dies. Check both table groups and exports; verify that original yields and First Scan Fail Dies are unchanged.
8. Each KILL row's `final_kill_locations` matches the evidence whose `primary_test` equals that exact instance, and its length equals `final_kill_dies`. Check lot/wafer/X/Y and final bins against evidence, HTML details and the location CSV. Counter-only failures and failures finally attributed to another instance must not appear. Same coordinates on different lots or wafers remain separate dies.

Open the generated local HTML using the system browser, such as `Start-Process -FilePath $reportPath`. If the integrated browser rejects a non-trusted local folder, use the system browser; do not change trust settings or claim visual verification without access. No web server is needed.

Summarize the combined die population, final scan kills, major low-yield items and relevant EDC caveats in the user's language. Mention incomplete execution evidence and unresolved numeric counters where applicable.

### 5. Reuse the Cache for Presentation Changes

```powershell
python "$skillRoot\scripts\scan_yield_analyzer.py" --render $cachePath --output $reportPath
```

The cache must have been generated by this bundled script. This mode requires neither TP network access nor raw-log rereading. It is for re-rendering the same analysis, not silently changing the lot selection, TP, exclusions or yield definition. Fresh analysis is required when those inputs change.

Older caches without `first_scan_fail_dies` render the new column as N/A. Fresh analysis from raw logs is required to populate it; never reconstruct execution order from the previously alphabetically sorted execution set or the yield-sorted rows.

Older caches containing `good_die_counter_fail_dies` can be re-rendered directly: retain `fail_on_final_pass_dies` when present, otherwise migrate the alias value to it, then remove the alias from the output cache. If neither field exists, show N/A (null in JSON, blank in CSV), not zero. No raw-log rereading is needed when either saved value is present.

Final kill locations are rebuilt from saved per-die evidence during rendering, including for older caches without `final_kill_locations`. No raw-log rereading is needed when the cache retains exact primary-instance attribution and location/bin fields. Missing evidence, duplicate die identities or a location count differing from `final_kill_dies` stops rendering and requires fresh analysis; do not infer locations from FB alone or from `failed_die_indexes`.

## Artifacts

For an output named `scan.html`, the script writes:

| Artifact | Contents |
|---|---|
| `scan.html` | Separate observed KILL/EDC tables and expandable other-failure details |
| `scan_statistics.csv` | All retained tests, including unexecuted tests and yield basis |
| `scan_kill.csv` | KILL rows, FB first, final-kill yield ascending |
| `scan_kill_locations.csv` | One row per KILL instance's final killed die: test, flow_mode, lot, wafer, x, y, final_ib, final_fb, final_db |
| `scan_edc.csv` | EDC rows, counter yield descending, unexecuted rows last |
| `scan_overlap.csv` | Per-test other-failure names/counters and affected-die counts |
| `scan.json` | Re-renderable statistics and per-die evidence; failed-die indexes reference its evidence array |

Counter Fail Dies and Final Kill Dies are distinct. Do not add the redundant "Fail Without This Final Kill" column. Both groups use `yield_percent` in CSV, with `yield_basis` making its definition explicit; `counter_yield_percent` remains an audit metric.

`first_scan_fail_dies` is an additional count, not a replacement yield. JSON retains `scan_execution_order` per die and `first_scan_fail_die_indexes` per item for audit. `uncertain_scan_order_dies` counts observed dies whose same-module first-failure attribution is uncertain. N/A counts are null in JSON, blank in CSV and shown as N/A in HTML.

`fail_on_final_pass_dies` is a count restricted to final good dies, not a percentage. Audit it using `failed_die_indexes` and per-die `final_result`. Unexecuted items have zero observed good-die failures, while their yield remains N/A.

`final_kill_locations` is a structured list on KILL JSON rows. HTML shows the list in expandable details; the separate location CSV is flat for coordinate filtering and downstream wafer analysis. Zero-kill instances produce no location CSV records, while their original summary rows remain present. Existing KILL/EDC summary CSV schemas and all yield definitions are unchanged.

## Interpretation and Limits

- Static MTPL counters are matched to declared local `CSharpTest`/`Test` instances, then qualified with the module. FlowItem `@EDC` determines EDC; regular non-EDC flow is displayed as KILL only after verifying regular SetBin metadata.
- This is not a TP interpreter. Mixed EDC/non-EDC use of one instance or unsupported/missing metadata requires resolving the execution context, not guessing a mode. DB IDs starting with 98/99 are excluded from regular KILL-bin metadata checks as exceptional bins.
- Fail/pass counters within one final die record can overlap because of repeated calls, recovery or intermediate outcomes. Counter yield is not the last-attempt pass rate and is not lot final-bin yield.
- First Scan Fail Dies uses per-die actual scan-module flow order rather than statically interpreting MTPL branches. A repeated failing instance is ambiguous because aggregate fail counters do not identify which call failed; affected module counts stay N/A. The metric does not claim cycle-level failure timing or combine different modules into one global first failure.
- `3_binn`, `3_curfbin` and `3_curibin` are the actual final DB/FB/IB tokens. Do not substitute `3_curdtbin`, `3_curftbin` or `3_curitbin`.
- Unmapped numeric counters cannot be classified as HRY/SPOFI and therefore remain visible with that limitation.
- This skill is for lot-level scan analysis, not the separate single-unit CLASS analysis workflow.

## Maintenance and Privacy

Run the bundled synthetic tests after changes:

```powershell
python -m unittest discover -s "$skillRoot\tests" -v
```

Never commit raw logs, tracking exports, TP sources, generated reports/cache, real lot examples, credentials or internal host paths when distributing this skill. Tests generate synthetic fixtures in temporary directories. Treat all real-data artifacts as local analysis evidence unless the user separately authorizes sharing them.
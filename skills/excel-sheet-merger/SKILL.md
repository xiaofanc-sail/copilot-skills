---
name: excel-sheet-merger
description: 'Merge selected Excel workbooks into one workbook by sheet name, deduplicate repeated row content, preserve formatting, and optionally append date suffixes like _1008. Use when user asks: merge Excel, combine xlsx, 合并excel, 合并工作簿, 每个sheet合并, 重复内容只保留一遍, 文件名加日期.'
argument-hint: 'Excel files to merge, optional output name/date suffix'
---

# Excel Sheet Merger

## When to Use

Use this skill when the user wants to combine multiple `.xlsx`/`.xlsm` Excel workbooks into one output workbook and says any of the following:

- Merge selected Excel files or combine workbooks.
- Merge every sheet by sheet name.
- Keep duplicate content only once.
- Add a date suffix to the merged workbook name, such as `_1008`.

## Inputs

Prefer using the files the user selected or explicitly named. If no files are named, search the current workspace for Excel files and choose the most likely non-temporary workbooks. Ignore lock/temp files whose names start with `~$`.

Default output naming:

- If the user gives a suffix, append it before the extension.
- If the user asks for today's date, use `MMdd`, for example `_1008` on October 8.
- Otherwise use `<first-input-basename>_merged.xlsx`.

## Procedure

1. Identify the input workbooks and confirm they exist.
2. Use the first workbook as the base/template so workbook styles and existing sheet formatting are preserved.
3. Run [merge-excel-sheets.ps1](./scripts/merge-excel-sheets.ps1) with the selected files.
4. For each source sheet:
   - If the target workbook does not have the sheet, copy the whole sheet into the output workbook.
   - If the target workbook already has the sheet, append only source rows whose non-empty cell values do not already appear as an identical row in that target sheet.
   - Treat a row as duplicate only when the populated column positions and values match.
5. Save the merged workbook.
6. Reopen the output workbook with Excel COM to verify it is readable and report the output path.

## Command Template

Run from the workspace folder:

```powershell
powershell -ExecutionPolicy Bypass -Command "& '.\.github\skills\excel-sheet-merger\scripts\merge-excel-sheets.ps1' -Files 'file1.xlsx','file2.xlsx','file3.xlsx' -DateSuffix '_1008'"
```

To specify the exact output path:

```powershell
powershell -ExecutionPolicy Bypass -Command "& '.\.github\skills\excel-sheet-merger\scripts\merge-excel-sheets.ps1' -Files 'file1.xlsx','file2.xlsx','file3.xlsx' -OutputPath 'merged_1008.xlsx'"
```

## Notes

- This workflow requires Microsoft Excel to be installed because it uses Excel COM automation.
- The script overwrites an existing output file of the same name.
- After creating the file, give the user a clickable workspace-relative link to the merged workbook.

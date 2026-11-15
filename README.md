# copilot-skills

## Scan Yield Analyzer

[scan-yield-analyzer](skills/scan-yield-analyzer/SKILL.md) analyzes lot-level scan yield using local AQUA tracking, raw iTUFF and verified TP definitions. It produces separate KILL and EDC tables, FB mapping, other-failure counter details, CSV exports and a reusable JSON cache.

To install for personal use in Copilot, copy `skills/scan-yield-analyzer` into `~/.copilot/skills/scan-yield-analyzer`. To use it in a project, copy it into `.github/skills/scan-yield-analyzer`. Reload or start a new agent session to discover the skill. The script requires Python 3.10+ and no third-party packages.

Run its synthetic tests from the repository root:

```powershell
python -m unittest discover -s skills/scan-yield-analyzer/tests -v
```

Real analysis inputs and generated reports should remain outside this repository. See the skill for acquisition steps, supported input formats, validation rules and command examples.

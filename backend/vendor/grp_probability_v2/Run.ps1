param(
    [string]$Python = 'python',
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$ScriptArguments
)
$ErrorActionPreference = 'Stop'
& $Python (Join-Path $PSScriptRoot 'grp_probability.py') @ScriptArguments
exit $LASTEXITCODE

# context-lens-autoload.ps1 — auto-index when entering a project directory
#
# Add to your PowerShell profile ($PROFILE):
#   . C:\path\to\shell\ctx-autoload.ps1

function Invoke-LensAutoIndex {
    if (-not (Get-Command lens -ErrorAction SilentlyContinue)) { return }
    $markers = @("pyproject.toml", "package.json", "Cargo.toml", "go.mod", ".git", ".ctx")
    foreach ($m in $markers) {
        if (Test-Path $m) {
            Start-Job -ScriptBlock { lens index } -WorkingDirectory (Get-Location) | Out-Null
            return
        }
    }
}

function Set-Location {
    Microsoft.PowerShell.Management\Set-Location @args
    Invoke-LensAutoIndex
}
Set-Alias -Name cd -Value Set-Location -Option AllScope -Force

Invoke-LensAutoIndex

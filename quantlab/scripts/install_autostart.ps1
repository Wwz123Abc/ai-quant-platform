# Starts QuantLab in the background when you sign in to Windows, by adding a shortcut to your Startup folder.
# No administrator rights are needed. Undo with scripts/uninstall_autostart.ps1.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = (& python -c "import sys; print(sys.executable)").Trim()
$pythonw = Join-Path (Split-Path -Parent $python) "pythonw.exe"
if (-not (Test-Path $pythonw)) { throw "pythonw.exe not found next to $python" }
& $python -c "import quantlab" 2>$null
if ($LASTEXITCODE -ne 0) { throw "quantlab is not installed for $python; run: pip install -e .[ai]" }

$lnk = Join-Path ([Environment]::GetFolderPath("Startup")) "QuantLab.lnk"
$shell = New-Object -ComObject WScript.Shell
$s = $shell.CreateShortcut($lnk)
$s.TargetPath = $pythonw                       # pythonw: no console window
$s.Arguments = "-m quantlab serve --log-file data\logs\serve.log"
$s.WorkingDirectory = $root                    # .env and data\ are read from here
$s.Description = "QuantLab research workstation (background service)"
$s.Save()
Write-Output "Installed: $lnk"
Write-Output "It starts at your next sign-in. Open the workbench with: python -m quantlab open"

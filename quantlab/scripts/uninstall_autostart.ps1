# Stops QuantLab from starting at sign-in. A service that is already running keeps running until you sign out.
$lnk = Join-Path ([Environment]::GetFolderPath("Startup")) "QuantLab.lnk"
if (Test-Path $lnk) { Remove-Item $lnk; Write-Output "Removed: $lnk" } else { Write-Output "Not installed." }

# Keeps the app running: the tracker and discovery only work while the server is up.
# Restarts the server if it stops; close this window to stop it for good.
Set-Location (Split-Path $PSScriptRoot -Parent)
$Host.UI.RawUI.WindowTitle = "confac app :8000"
while ($true) {
    & .\.venv\Scripts\python.exe -m uvicorn app.main:app
    Write-Host "Server stopped at $(Get-Date). Restarting in 10 seconds..."
    Start-Sleep -Seconds 10
}

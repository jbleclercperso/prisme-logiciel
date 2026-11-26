# Cree le raccourci "Jarvis" sur le Bureau (lancement sans fenetre noire).
$racine = Split-Path -Parent $PSScriptRoot
$bureau = [Environment]::GetFolderPath('Desktop')
$pythonw = Join-Path $racine '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $pythonw)) { $pythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source }
if (-not $pythonw) { Write-Output 'pythonw introuvable : raccourci non cree.'; exit 0 }
$shell = New-Object -ComObject WScript.Shell
$lien = $shell.CreateShortcut((Join-Path $bureau 'Jarvis.lnk'))
$lien.TargetPath = $pythonw
$lien.Arguments = '-m jarvis'
$lien.WorkingDirectory = $racine
$lien.Description = 'Jarvis, assistant vocal'
$lien.IconLocation = "$env:SystemRoot\System32\shell32.dll,13"
$lien.Save()
Write-Output "Raccourci cree : $(Join-Path $bureau 'Jarvis.lnk')"

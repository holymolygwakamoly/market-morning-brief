# 프로젝트 루트에 "시장브리핑 대시보드.lnk" 바로가기를 만든다 (대상: 시장브리핑 대시보드.bat).
# 실행: powershell -ExecutionPolicy Bypass -File scripts/make_shortcut.ps1
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$bat = Join-Path $root "시장브리핑 대시보드.bat"
$lnk = Join-Path $root "시장브리핑 대시보드.lnk"
if (-not (Test-Path $bat)) { throw "bat 파일이 없습니다: $bat" }

$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($lnk)
$sc.TargetPath = $bat
$sc.WorkingDirectory = $root
$sc.IconLocation = "%SystemRoot%\System32\shell32.dll,165"
$sc.WindowStyle = 7
$sc.Description = "시장 아침 브리핑 대시보드 실행"
$sc.Save()
Write-Host "바로가기 생성: $lnk"

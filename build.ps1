# Сборка Windows-версии: PyInstaller + Inno Setup.
#
#   .\build.ps1                 # exe + установщик (если найден ISCC)
#   .\build.ps1 -NoInstaller    # только dist\Music\Music.exe
#   .\build.ps1 -SkipClean      # собрать поверх прошлой сборки
#
# Требования: Python 3.10+, зависимости из requirments.txt,
# PyInstaller, Inno Setup 6.3+ (для установщика).

param(
    [switch]$NoInstaller,
    [switch]$SkipClean,
    [string]$InnoSetupPath = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Fail([string]$Message) {
    Write-Host ""
    Write-Host "ОШИБКА: $Message" -ForegroundColor Red
    exit 1
}

# ---------- версия ----------
$Version = (python -c "import version; print(version.__version__)" 2>$null)
if (-not $Version) { Fail "Не удалось прочитать version.py. Запустите скрипт из папки проекта." }
$Version = $Version.Trim()
Write-Host "Версия: $Version" -ForegroundColor Cyan

# ---------- проверка зависимостей ----------
$check = python -c "import PyInstaller, webview, flask, mutagen, PIL, pystray, pypresence; print('ok')" 2>$null
if ($check -ne "ok") {
    Fail "Не хватает зависимостей. Выполните: python -m pip install -r requirments.txt pyinstaller"
}

# ---------- иконка ----------
if (-not (Test-Path "assets\app.ico")) {
    New-Item -ItemType Directory -Force assets | Out-Null
    python -c "from windows_integration import _make_app_icon, _save_ico; _save_ico(_make_app_icon(256), r'assets/app.ico', [(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])"
    if (-not (Test-Path "assets\app.ico")) { Fail "Не удалось создать assets\app.ico" }
}

# ---------- очистка ----------
if (-not $SkipClean) {
    if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
    if (Test-Path "dist\Music") { Remove-Item -Recurse -Force "dist\Music" }
}

# ---------- PyInstaller ----------
Write-Host ""
Write-Host "Сборка dist\Music\Music.exe ..." -ForegroundColor Cyan
python -m PyInstaller Music.spec --noconfirm
if ($LASTEXITCODE -ne 0) { Fail "PyInstaller завершился с ошибкой." }

$exe = Join-Path $Root "dist\Music\Music.exe"
if (-not (Test-Path $exe)) { Fail "Не найден $exe" }
Write-Host "Готово: $exe" -ForegroundColor Green

# ---------- Inno Setup ----------
if ($NoInstaller) { exit 0 }

function Find-Iscc([string]$Hint) {
    if ($Hint) {
        if (Test-Path $Hint) { return (Resolve-Path $Hint).Path }
        $candidate = Join-Path $Hint "ISCC.exe"
        if (Test-Path $candidate) { return (Resolve-Path $candidate).Path }
    }
    $cmd = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $paths = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    )
    foreach ($p in $paths) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

$iscc = Find-Iscc $InnoSetupPath
if (-not $iscc) {
    Write-Host ""
    Write-Host "Inno Setup не найден — установщик не собран." -ForegroundColor Yellow
    Write-Host "Установите Inno Setup 6.3+ (https://jrsoftware.org/isdl.php) и повторите,"
    Write-Host "или укажите путь: .\build.ps1 -InnoSetupPath 'C:\Program Files (x86)\Inno Setup 6'"
    exit 0
}

Write-Host ""
Write-Host "Сборка установщика ..." -ForegroundColor Cyan
& $iscc "/DAppVersion=$Version" "installer\setup.iss"
if ($LASTEXITCODE -ne 0) { Fail "ISCC завершился с ошибкой." }

$setup = Join-Path $Root "dist\Music-Setup-$Version.exe"
if (Test-Path $setup) {
    Write-Host "Готово: $setup" -ForegroundColor Green
} else {
    Fail "Установщик не найден в dist."
}

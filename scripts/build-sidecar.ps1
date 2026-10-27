# Build the FastAPI backend into a PyInstaller onedir sidecar and copy it into
# apps/desktop/src-tauri/binaries/ (bundle.resources picks it up from there).
# This can take several minutes and produces a large folder (torch +
# transformers + onnxruntime). It is not part of `npm run build`.
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$api = Join-Path $root "services\api"
$pyinstaller = Join-Path $root ".venv\Scripts\pyinstaller.exe"

if (-not (Test-Path $pyinstaller)) {
    Write-Host "未找到 PyInstaller：$pyinstaller" -ForegroundColor Red
    Write-Host "请先执行：.\.venv\Scripts\python.exe -m pip install pyinstaller"
    exit 1
}

Push-Location $api
try {
    & $pyinstaller --clean --noconfirm kb-server.spec
    if ($LASTEXITCODE -ne 0) {
        Write-Host "PyInstaller 构建失败，退出码 $LASTEXITCODE" -ForegroundColor Red
        exit $LASTEXITCODE
    }

    $source = Join-Path $api "dist\kb-server"
    if (-not (Test-Path (Join-Path $source "kb-server.exe"))) {
        Write-Host "未找到 $source\kb-server.exe" -ForegroundColor Red
        exit 1
    }

    $destRoot = Join-Path $root "apps\desktop\src-tauri\binaries"
    New-Item -ItemType Directory -Force -Path $destRoot | Out-Null
    $dest = Join-Path $destRoot "kb-server"
    if (Test-Path $dest) {
        Remove-Item -Recurse -Force $dest
    }
    Copy-Item -Recurse -Force $source $dest
    # Keep the placeholder that makes the Tauri resource glob resolvable before
    # the first build (see .gitignore).
    New-Item -ItemType File -Force -Path (Join-Path $dest ".gitkeep") | Out-Null
    Write-Host "sidecar 已复制到 $dest" -ForegroundColor Green
}
finally {
    Pop-Location
}

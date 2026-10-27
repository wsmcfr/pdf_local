# Prepare local models for Local KB:
#   1. check/start Ollama and pull the chat model
#   2. download the BGE-M3 embedding model into KB_DATA_DIR
# Nothing is downloaded unless you run this script yourself.
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

$ollama = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollama) {
    Write-Host "未检测到 Ollama。" -ForegroundColor Yellow
    Write-Host "请先从 https://ollama.com/download 下载并安装 Ollama，启动后重新运行本脚本。"
    Write-Host "安装完成后可直接执行：ollama pull qwen2.5:7b"
    exit 1
}

try {
    Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 5 | Out-Null
} catch {
    Write-Host "Ollama 已安装但未启动。请先运行 Ollama，再重新执行本脚本。" -ForegroundColor Yellow
    exit 1
}

Write-Host "拉取 LLM 模型 qwen2.5:7b（约 4.7GB）..." -ForegroundColor Cyan
& ollama pull qwen2.5:7b
if ($LASTEXITCODE -ne 0) {
    Write-Host "ollama pull 失败，退出码 $LASTEXITCODE" -ForegroundColor Red
    exit $LASTEXITCODE
}

if (-not (Test-Path $python)) {
    Write-Host "未找到虚拟环境 $python，请先创建 .venv 并安装 requirements.txt" -ForegroundColor Red
    exit 1
}

Write-Host "下载 Embedding 模型 BGE-M3（约 2.2GB）..." -ForegroundColor Cyan
& $python (Join-Path $root "scripts\download_embedding.py")
if ($LASTEXITCODE -ne 0) {
    Write-Host "BGE-M3 下载失败，退出码 $LASTEXITCODE" -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host "模型准备完成，可在应用的「开始使用」页面刷新状态。" -ForegroundColor Green

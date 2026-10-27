# Prepare local models for Local KB:
#   Download the BGE-M3 embedding model into KB_DATA_DIR.
# Chat answers come from a configured external Provider (see the settings page);
# no local LLM (Ollama) is required. Nothing is downloaded unless you run this
# script yourself.
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

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

Write-Host "Embedding 模型准备完成。" -ForegroundColor Green
Write-Host "问答模型请在应用「设置」页添加外部 Provider；调用外部 API 需要网络。" -ForegroundColor Green

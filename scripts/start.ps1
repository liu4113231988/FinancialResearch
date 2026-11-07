#Requires -Version 7.0
<#
.SYNOPSIS
  一键启动本地研究工作台。
.DESCRIPTION
  优先使用项目 .venv 里的 market-research 入口（不经过 uv，省一层进程）；
  没有 .venv 时退回 uv run --locked。若存在 .env，会通过 FIL_ENV_FILE 交给
  应用读取；命令行与已设置的环境变量优先级更高。
.PARAMETER Port
  覆盖监听端口；不传时沿用 FIL_PORT / 默认 8760。
.PARAMETER ListenHost
  覆盖监听地址；不传时沿用 FIL_HOST / 默认 127.0.0.1。
.PARAMETER NoWorker
  不自动拉起 worker（等价 FIL_AUTO_SERVICES=0）。
.PARAMETER EnvFile
  .env 路径，默认项目根目录 .env；传空字符串可禁用。
.EXAMPLE
  .\scripts\start.ps1
.EXAMPLE
  .\scripts\start.ps1 -Port 8877 -NoWorker
#>
param(
    [int]$Port = 0,
    [string]$ListenHost = "",
    [switch]$NoWorker,
    [string]$EnvFile = ".env"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    $envPath = $null
    if ($EnvFile) {
        $envPath = if ([System.IO.Path]::IsPathRooted($EnvFile)) {
            $EnvFile
        } else {
            Join-Path $root $EnvFile
        }
    }
    if ($envPath -and (Test-Path -LiteralPath $envPath -PathType Leaf) -and -not $env:FIL_ENV_FILE) {
        $env:FIL_ENV_FILE = (Resolve-Path -LiteralPath $envPath).Path
    }
    if ($NoWorker) { $env:FIL_AUTO_SERVICES = "0" }

    $forward = @()
    if ($Port -gt 0) { $forward += @("--port", "$Port") }
    if ($ListenHost) { $forward += @("--host", $ListenHost) }

    $exe = Join-Path $root ".venv\Scripts\market-research.exe"
    if (Test-Path -LiteralPath $exe -PathType Leaf) {
        & $exe @forward
    } elseif (Get-Command uv -ErrorAction SilentlyContinue) {
        & uv run --locked market-research @forward
    } else {
        throw "未找到 .venv 或 uv；请先在项目根目录运行 uv sync --locked"
    }
} finally {
    Pop-Location
}

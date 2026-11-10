# 启动脚本（Windows PowerShell）
# 用法： .\scripts\start.ps1              默认端口 18760
#        .\scripts\start.ps1 -Port 18860   指定端口
param(
    [int]$Port = 18760,
    [string]$Device = ""
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

$venvPy = Join-Path $PWD ".venv\Scripts\python.exe"
if (Test-Path $venvPy) {
    $py = $venvPy
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $py = "python"
} else {
    Write-Host "❌ 找不到 Python。先运行： python install.py" -ForegroundColor Red
    exit 1
}

Write-Host "使用解释器： $py" -ForegroundColor Cyan
Write-Host "端口：       $Port" -ForegroundColor Cyan

$args = @("run.py", "--port", "$Port")
if ($Device -ne "") { $args += @("--device", $Device) }

& $py @args

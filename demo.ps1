# =============================================================================
# CareerCopilot 一键 Demo（Windows PowerShell 5+ / pwsh）—— 阶段 11 P1
#
# 用法：
#   .\demo.ps1            # 启动全套：基础设施 + 迁移 + seed + api/worker/beat/前端
#   .\demo.ps1 stop       # 停止 api/worker/beat/前端（基础设施容器保留）
#   .\demo.ps1 status     # 查看状态
#
# 前置依赖：Docker Desktop、uv、Node 20+（npm）。
# 与 demo.sh 等价体验；Windows 差异：Celery worker 使用 --pool solo。
# 脚本幂等：重复执行会先停掉旧进程再拉起，seed 不产生重复数据。
# =============================================================================
param([string]$Command = "up")

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$DemoDir = Join-Path $Root ".demo"
$LogDir = Join-Path $DemoDir "logs"
$PidDir = Join-Path $DemoDir "pids"
$ApiPort = if ($env:DEMO_API_PORT) { $env:DEMO_API_PORT } else { "8000" }
$FrontendPort = if ($env:DEMO_FRONTEND_PORT) { $env:DEMO_FRONTEND_PORT } else { "3000" }

function Info($msg) { Write-Host "[demo] $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "[demo] 失败：$msg" -ForegroundColor Red; exit 1 }

function Require-Cmd($name, $hint) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) { Fail "缺少依赖：$name（$hint）" }
}

function Stop-Proc($name) {
    $pidFile = Join-Path $PidDir "$name.pid"
    if (Test-Path $pidFile) {
        $procId = Get-Content $pidFile
        if (Get-Process -Id $procId -ErrorAction SilentlyContinue) {
            Info "停止旧进程 $name (pid $procId)"
            # /T 杀整个进程树（uv/npm 会派生子进程）
            & taskkill /PID $procId /T /F 2>$null | Out-Null
        }
        Remove-Item $pidFile -Force
    }
}

function Start-Proc($name, $workdir, $exe, $argList) {
    Stop-Proc $name
    Info "启动 $name：$exe $($argList -join ' ')"
    $proc = Start-Process -FilePath $exe -ArgumentList $argList -WorkingDirectory $workdir `
        -RedirectStandardOutput (Join-Path $LogDir "$name.log") `
        -RedirectStandardError (Join-Path $LogDir "$name.err.log") `
        -WindowStyle Hidden -PassThru
    Set-Content -Path (Join-Path $PidDir "$name.pid") -Value $proc.Id
}

function Wait-Http($url, $desc, $maxSeconds) {
    for ($i = 0; $i -lt $maxSeconds; $i++) {
        try {
            $resp = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 3
            if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 400) { return }
        } catch { }
        Start-Sleep -Seconds 1
    }
    Fail "$desc 在 ${maxSeconds}s 内未就绪（$url），日志见 $LogDir"
}

function Cmd-Status {
    Push-Location $Root; docker compose ps; Pop-Location
    foreach ($name in @("api", "worker", "beat", "frontend")) {
        $pidFile = Join-Path $PidDir "$name.pid"
        if ((Test-Path $pidFile) -and (Get-Process -Id (Get-Content $pidFile) -ErrorAction SilentlyContinue)) {
            Write-Host "${name}: running (pid $(Get-Content $pidFile))"
        } else {
            Write-Host "${name}: stopped"
        }
    }
}

function Cmd-Stop {
    foreach ($name in @("frontend", "beat", "worker", "api")) { Stop-Proc $name }
    Info "应用进程已停止；基础设施容器保留（需要时执行：docker compose down）"
}

function Cmd-Up {
    Require-Cmd docker "Docker Desktop：https://docs.docker.com"
    Require-Cmd uv "https://docs.astral.sh/uv/"
    Require-Cmd npm "https://nodejs.org（Node 20+）"

    New-Item -ItemType Directory -Force -Path $LogDir, $PidDir | Out-Null

    docker info *> $null
    if ($LASTEXITCODE -ne 0) { Fail "docker daemon 未运行，请先启动 Docker Desktop" }

    # 1) 环境变量：首次运行从 example 生成
    if (-not (Test-Path (Join-Path $Root ".env"))) {
        Copy-Item (Join-Path $Root ".env.example") (Join-Path $Root ".env")
        Info "已生成 .env（来自 .env.example）"
    }
    if (-not (Test-Path (Join-Path $Root "backend\.env"))) {
        Copy-Item (Join-Path $Root "backend\.env.example") (Join-Path $Root "backend\.env")
        Info "已生成 backend\.env（来自 example）"
    }

    # 2) 基础设施
    Info "启动基础服务（docker compose up -d）..."
    Push-Location $Root; docker compose up -d; Pop-Location
    Info "等待容器健康检查 ..."
    foreach ($c in @("careercopilot-postgres", "careercopilot-redis", "careercopilot-mailpit")) {
        $ok = $false
        for ($i = 0; $i -lt 60; $i++) {
            $state = docker inspect -f "{{.State.Health.Status}}" $c 2>$null
            if ($state -eq "healthy") { $ok = $true; break }
            Start-Sleep -Seconds 1
        }
        if (-not $ok) { Fail "容器 $c 未在 60s 内变为 healthy" }
    }

    # 3) 后端依赖 + 迁移
    Info "安装后端依赖（uv sync）并执行迁移 ..."
    Push-Location (Join-Path $Root "backend")
    uv sync | Out-Null
    uv run alembic upgrade head
    if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "数据库迁移失败" }

    # 4) 种子数据（幂等；演示账号走正常邀请码 + Mailpit magic link 注册，无后门）
    Info "写入演示种子数据（幂等）..."
    uv run python scripts/seed_demo.py
    if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "seed 失败" }
    Pop-Location

    # 5) 应用进程：API + Celery worker + Celery beat + 前端
    $backendDir = Join-Path $Root "backend"
    $frontendDir = Join-Path $Root "frontend"
    Start-Proc "api" $backendDir "uv" @("run", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", $ApiPort)
    # Windows 下 Celery 需要 solo pool
    Start-Proc "worker" $backendDir "uv" @("run", "celery", "-A", "app.tasks.celery_app", "worker", "--loglevel", "info", "--pool", "solo")
    Start-Proc "beat" $backendDir "uv" @("run", "celery", "-A", "app.tasks.celery_app", "beat", "--loglevel", "info", "--schedule", (Join-Path $DemoDir "celerybeat-schedule"))
    # 无条件 install：package.json 变了也能补装新依赖（依赖齐时是秒级 no-op）
    Info "同步前端依赖（npm install，首次较慢）..."
    Push-Location $frontendDir; npm install --no-audit --no-fund | Out-Null; Pop-Location
    Start-Proc "frontend" $frontendDir "npm" @("run", "dev", "--", "--port", $FrontendPort)

    Info "等待 API 与前端就绪 ..."
    Wait-Http "http://localhost:$ApiPort/health/ready" "后端 API" 60
    Wait-Http "http://localhost:$FrontendPort" "前端" 120

    Write-Host @"

============================ CareerCopilot Demo 已就绪 ============================
前端        : http://localhost:$FrontendPort
后端 API    : http://localhost:$ApiPort（健康检查 /health/ready）
Mailpit     : http://localhost:8025（demo 登录邮件在这里收）

演示账号    : demo@careercopilot-demo.dev
登录邀请码  : DEMO-LOGIN-2026

登录步骤（正常流程，无认证后门）：
  1. 打开 http://localhost:$FrontendPort/login，填演示邮箱和邀请码，请求登录链接
  2. 打开 Mailpit http://localhost:8025，点开最新邮件里的登录链接
  3. 登录后到「推荐」页查看种子推荐（岗位均带「合成示例」徽标）

日志        : $LogDir
停止        : .\demo.ps1 stop
===================================================================================
"@
}

switch ($Command) {
    "up" { Cmd-Up }
    "stop" { Cmd-Stop }
    "status" { Cmd-Status }
    default { Fail "未知子命令：$Command（支持 up / stop / status）" }
}

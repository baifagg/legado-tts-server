#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""启动器 —— 一条命令拉起 worker + 主服务，并自动守护。

    python run.py                  # 默认 18760 / 18770
    python run.py --port 18860     # 换端口（worker 会自动 +10）
    python run.py --no-guard       # 关掉崩溃自动重启（调试用）

做了什么：
  1. 选对解释器（优先 .venv，避免装错环境）
  2. 先起 worker（模型进程），轮询 /health 直到就绪
  3. 再起主服务（Web），启动后打印手机端可用的地址
  4. 任一进程退出就自动重启（最多 5 次），Ctrl+C 一起退出
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
VENV = BASE / ".venv"


def pick_python() -> Path:
    """优先用 .venv 里的解释器；没有就用当前解释器（前提是依赖已装）。"""
    if os.name == "nt":
        p = VENV / "Scripts" / "python.exe"
    else:
        p = VENV / "bin" / "python"
    return p if p.exists() else Path(sys.executable)


def lan_ip() -> str:
    """拿本机局域网 IP（不会真的发包）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"
    finally:
        s.close()


def wait_health(url: str, timeout: float = 180.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(1.0)
    return False


def port_free(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


class Proc:
    def __init__(self, name: str, cmd: list[str], cwd: Path):
        self.name = name
        self.cmd = cmd
        self.cwd = cwd
        self.p: subprocess.Popen | None = None
        self.restarts = 0

    def start(self) -> None:
        print(f"[启动] {self.name}: {' '.join(self.cmd)}")
        self.p = subprocess.Popen(self.cmd, cwd=str(self.cwd))

    def alive(self) -> bool:
        return self.p is not None and self.p.poll() is None

    def stop(self) -> None:
        if self.alive():
            try:
                self.p.terminate()
                self.p.wait(timeout=8)
            except Exception:
                try:
                    self.p.kill()
                except Exception:
                    pass


def main() -> None:
    ap = argparse.ArgumentParser(description="OmniVoice 听书 TTS 启动器")
    ap.add_argument("--port", type=int, default=int(os.environ.get("TTS_PORT", 18760)))
    ap.add_argument("--worker-port", type=int, default=None,
                    help="默认 = --port + 10")
    ap.add_argument("--host", default=os.environ.get("TTS_HOST", "::"))
    ap.add_argument("--no-guard", action="store_true", help="进程退出后不自动重启")
    ap.add_argument("--device", default=os.environ.get("TTS_DEVICE"),
                    help="cuda:0 / mps / xpu:0 / cpu，留空自动探测")
    args = ap.parse_args()

    worker_port = args.worker_port or (args.port + 10)
    py = str(pick_python())

    for port, label in ((worker_port, "worker"), (args.port, "主服务")):
        if not port_free(port):
            print(f"[错误] 端口 {port}（{label}）已被占用。")
            print(f"       换个端口： python run.py --port {args.port + 100}")
            print(f"       或查谁占了： Windows → netstat -ano | findstr :{port}")
            sys.exit(1)

    env = dict(os.environ)
    env["OMNI_URL"] = f"http://127.0.0.1:{worker_port}"
    env["TTS_WORKER_PORT"] = str(worker_port)

    worker_cmd = [py, str(BASE / "worker.py"), "--port", str(worker_port)]
    if args.device:
        worker_cmd += ["--device", args.device]
    server_cmd = [py, str(BASE / "server.py"), "--port", str(args.port), "--host", args.host]

    worker = Proc("worker", worker_cmd, BASE)
    server = Proc("server", server_cmd, BASE)

    print("=" * 72)
    print("  OmniVoice 听书 TTS")
    print("=" * 72)

    worker.start()
    print("[等待] 模型加载中（首次约 10–60 秒，取决于显卡）...")
    if not wait_health(f"http://127.0.0.1:{worker_port}/health", timeout=300):
        print("[错误] worker 启动超时。看日志：", BASE / "logs")
        worker.stop()
        sys.exit(1)
    print("[就绪] worker")

    server_env_backup = dict(os.environ)
    os.environ.update(env)
    server.start()

    ip = lan_ip()
    time.sleep(2)
    print()
    print("=" * 72)
    print(f"  电脑访问：   http://127.0.0.1:{args.port}/")
    print(f"  手机访问：   http://{ip}:{args.port}/   （需同一 WiFi）")
    print()
    print(f"  导入链接 A（0~2.0 语速刻度，推荐）：http://{ip}:{args.port}/httpTTS-float.json")
    print(f"  导入链接 B（1~100 语速刻度）：      http://{ip}:{args.port}/httpTTS.json")
    print("=" * 72)
    print("  手机「阅读」→ 我的 → 朗读引擎 → 右上角菜单 → 网络导入 → 粘贴链接")
    print("  按 Ctrl+C 停止服务")
    print("=" * 72)

    def shutdown(*_):
        print("\n[停止] 正在关闭...")
        server.stop()
        worker.stop()
        sys.exit(0)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, shutdown)
        except Exception:
            pass

    try:
        while True:
            time.sleep(1.5)
            if not worker.alive():
                code = worker.p.returncode if worker.p else -1
                print(f"[警告] worker 退出（code={code}）")
                if args.no_guard:
                    break
                worker.restarts += 1
                if worker.restarts > 5:
                    print("[错误] worker 连续崩溃 5 次，停止。请看 logs/ 下的日志。")
                    break
                time.sleep(2)
                worker.start()
                wait_health(f"http://127.0.0.1:{worker_port}/health", timeout=300)
            if not server.alive():
                code = server.p.returncode if server.p else -1
                print(f"[警告] 主服务退出（code={code}）")
                if args.no_guard:
                    break
                server.restarts += 1
                if server.restarts > 5:
                    print("[错误] 主服务连续崩溃 5 次，停止。请看 logs/ 下的日志。")
                    break
                time.sleep(2)
                server.start()
    except KeyboardInterrupt:
        shutdown()

    server.stop()
    worker.stop()


if __name__ == "__main__":
    main()

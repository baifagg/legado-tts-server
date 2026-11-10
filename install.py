#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键安装器 —— 自动探测硬件、建虚拟环境、装依赖、下模型。

用法：
    python install.py                 # 自动探测（推荐）
    python install.py --mirror        # 国内网络：用 hf-mirror.com 下模型 + 清华 pip 源
    python install.py --device cpu    # 强制纯 CPU 安装
    python install.py --skip-model    # 只装依赖，模型稍后手动放

装完执行：
    python run.py                     # 启动服务
    # 或先自检
    .venv/bin/python doctor.py        # Linux/macOS
    .venv\\Scripts\\python.exe doctor.py   # Windows

设计原则
--------
* 只用标准库，**不依赖任何第三方包**（因为这时还没装）。
* 幂等：重复运行会跳过已完成的步骤。
* 任何一步失败都给出「可复制的下一步命令」，不静默失败。
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

BASE = Path(__file__).resolve().parent
VENV = BASE / ".venv"
MODELS = BASE / "models"
MODEL_DIR = MODELS / "OmniVoice"
HF_REPO = "k2-fsa/OmniVoice"
TORCH_VERSION = "2.8.0"
OMNIVOICE_SPEC = "omnivoice==0.2.1"
# 实测通过的依赖基线（omnivoice 0.2.1 要求 transformers>=5.3.0）
PIP_DEPS = [
    "fastapi>=0.115",
    "uvicorn>=0.30",
    "httpx>=0.27",
    "soundfile>=0.12",
    "numpy>=1.26",
    "imageio-ffmpeg>=0.5",   # 自带 ffmpeg 二进制，用户无需单独安装
    "huggingface_hub>=0.26",
]

OK = "✅"
WARN = "⚠️ "
FAIL = "❌"


def run(cmd: list[str], desc: str, check: bool = True) -> int:
    print(f"\n>>> {desc}")
    print("    " + " ".join(cmd))
    try:
        rc = subprocess.call(cmd)
    except FileNotFoundError:
        print(f"{FAIL} 找不到命令：{cmd[0]}")
        return 127
    if check and rc != 0:
        print(f"{FAIL} 失败（退出码 {rc}）")
        print("    请把上面的完整输出发给 AI agent 排查。")
        sys.exit(rc)
    return rc


def venv_python() -> Path:
    if os.name == "nt":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def detect_hardware() -> dict:
    """不 import torch（可能还没装），只用系统命令探测。"""
    info = {
        "os": platform.system(),
        "os_version": platform.version(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "vendor": "unknown",
        "gpu": None,
        "cuda": None,
        "vram_gb": None,
        "torch_index": None,
        "note": "",
    }
    # NVIDIA
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
                 "--format=csv,noheader"], text=True, timeout=15, stderr=subprocess.DEVNULL)
            first = out.strip().splitlines()[0]
            name, mem, drv = [x.strip() for x in first.split(",")]
            info.update(vendor="nvidia", gpu=name, cuda=drv,
                        vram_gb=round(int(re.sub(r"\D", "", mem)) / 1024, 1))
        except Exception as exc:
            info["note"] = f"nvidia-smi 存在但读取失败：{exc}"
    # Apple Silicon
    elif info["os"] == "Darwin" and info["machine"] == "arm64":
        info.update(vendor="apple", gpu="Apple Silicon (MPS)", vram_gb=None)
    # Intel / AMD / 其他
    else:
        info["vendor"] = "unknown"
    return info


def choose_torch_index(hw: dict, force_device: str | None) -> tuple[str | None, str]:
    """返回 (--index-url 或 None, 说明)。"""
    if force_device == "cpu":
        if hw["os"] == "Darwin":
            return None, "macOS 用 PyPI 默认 CPU/MPS 构建"
        return "https://download.pytorch.org/whl/cpu", "强制 CPU 构建"

    if hw["vendor"] == "nvidia":
        return "https://download.pytorch.org/whl/cu128", "NVIDIA CUDA 12.8 构建（适配 sm_75~sm_120）"

    if hw["vendor"] == "apple":
        return None, "macOS 默认构建，内含 MPS 后端"

    if hw["os"] == "Darwin":
        return None, "Intel Mac：只能用 CPU 构建"
    # Linux/Windows 上的 AMD / Intel 核显
    return "https://download.pytorch.org/whl/cpu", (
        "未检测到 NVIDIA，装 CPU 构建。"
        "AMD 显卡（Linux）可改用 ROCm 索引：--torch-index https://download.pytorch.org/whl/rocm6.2"
    )


def step_venv() -> None:
    if venv_python().exists():
        print(f"{OK} 虚拟环境已存在：{VENV}")
        return
    run([sys.executable, "-m", "venv", str(VENV)], "创建虚拟环境 .venv")


def step_pip(mirror: bool) -> None:
    py = str(venv_python())
    args = [py, "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"]
    if mirror:
        args += ["-i", "https://pypi.tuna.tsinghua.edu.cn/simple"]
    run(args, "升级 pip")


def step_torch(hw: dict, force_device: str | None, torch_index: str | None, mirror: bool) -> None:
    py = str(venv_python())
    idx, why = (torch_index, "命令行指定") if torch_index else choose_torch_index(hw, force_device)
    print(f"\n>>> 安装 PyTorch {TORCH_VERSION} —— {why}")
    args = [py, "-m", "pip", "install", f"torch=={TORCH_VERSION}", f"torchaudio=={TORCH_VERSION}"]
    if idx:
        args += ["--index-url", idx]
    elif mirror:
        args += ["-i", "https://pypi.tuna.tsinghua.edu.cn/simple"]
    run(args, "安装 torch / torchaudio")


def step_deps(mirror: bool) -> None:
    py = str(venv_python())
    args = [py, "-m", "pip", "install", *PIP_DEPS, OMNIVOICE_SPEC]
    if mirror:
        args += ["-i", "https://pypi.tuna.tsinghua.edu.cn/simple"]
    run(args, "安装 omnivoice 与 Web 依赖")


def step_model(mirror: bool) -> None:
    if (MODEL_DIR / "model.safetensors").exists():
        size = (MODEL_DIR / "model.safetensors").stat().st_size / 1e9
        print(f"{OK} 模型已存在：{MODEL_DIR}（{size:.2f} GB）")
        return
    print(f"\n>>> 下载模型 {HF_REPO}（约 3.1 GB，首次较慢）")
    env = dict(os.environ)
    if mirror:
        env["HF_ENDPOINT"] = "https://hf-mirror.com"
        print("    使用镜像 HF_ENDPOINT=https://hf-mirror.com")
    code = (
        "from huggingface_hub import snapshot_download;"
        f"p=snapshot_download('{HF_REPO}', local_dir=r'{MODEL_DIR}');"
        "print('downloaded to', p)"
    )
    print("    " + f"{venv_python()} -c \"{code[:70]}...\"")
    rc = subprocess.call([str(venv_python()), "-c", code], env=env)
    if rc != 0:
        print(f"{FAIL} 模型下载失败。可手动下载：")
        print(f"     https://huggingface.co/{HF_REPO}/tree/main")
        print(f"     把 model.safetensors / config.json / tokenizer*.json / audio_tokenizer/ 放到：{MODEL_DIR}")
        print("     然后重新运行：python install.py --skip-model")
        sys.exit(rc)


def step_verify() -> None:
    print("\n>>> 验证安装")
    code = (
        "import torch, omnivoice, fastapi, soundfile, numpy;"
        "print('torch', torch.__version__);"
        "print('omnivoice', omnivoice.__version__);"
        "print('cuda', torch.cuda.is_available());"
        "print('mps', getattr(torch.backends,'mps',None) and torch.backends.mps.is_available())"
    )
    run([str(venv_python()), "-c", code], "导入自检")


def main() -> None:
    ap = argparse.ArgumentParser(description="OmniVoice 听书 TTS 一键安装")
    ap.add_argument("--mirror", action="store_true", help="国内网络：pip 用清华源、模型用 hf-mirror")
    ap.add_argument("--device", choices=["auto", "cuda", "cpu", "mps"], default="auto")
    ap.add_argument("--torch-index", default=None,
                    help="自定义 torch 安装索引（如 ROCm: https://download.pytorch.org/whl/rocm6.2）")
    ap.add_argument("--skip-model", action="store_true", help="跳过模型下载")
    args = ap.parse_args()

    print("=" * 72)
    print("  OmniVoice 听书 TTS · 安装器")
    print("=" * 72)

    # Python 版本
    major, minor = sys.version_info[:2]
    if (major, minor) < (3, 10) or (major, minor) > (3, 12):
        print(f"{FAIL} 需要 Python 3.10–3.12，当前 {platform.python_version()}")
        print("    建议用 3.11 或 3.12。")
        sys.exit(1)
    print(f"{OK} Python {platform.python_version()}")

    hw = detect_hardware()
    print(f"{OK} 系统：{hw['os']} {hw['machine']}")
    if hw["gpu"]:
        extra = f"，显存 {hw['vram_gb']} GB" if hw["vram_gb"] else ""
        print(f"{OK} 显卡：{hw['gpu']}{extra}")
        if hw["vram_gb"] and hw["vram_gb"] < 6:
            print(f"{WARN} 显存 < 6 GB：可以用，但长句会慢，建议用 CPU 或换更短的分句。")
    else:
        print(f"{WARN} 未检测到独立显卡，将使用 CPU（能用，但每句约 5–15 秒）")
    if hw["note"]:
        print(f"    备注：{hw['note']}")

    step_venv()
    step_pip(args.mirror)
    step_torch(hw, None if args.device == "auto" else args.device, args.torch_index, args.mirror)
    step_deps(args.mirror)
    if not args.skip_model:
        step_model(args.mirror)
    step_verify()

    print("\n" + "=" * 72)
    print(f"{OK} 安装完成")
    print("=" * 72)
    print("\n下一步：")
    print(f"  1) 自检环境：{venv_python()} doctor.py")
    print(f"  2) 启动服务：{venv_python()} run.py")
    print("  3) 手机「阅读」导入：http://<本机局域网IP>:18760/httpTTS-float.json")
    print("\n若模型未下载，可手动放到：", MODEL_DIR)


if __name__ == "__main__":
    main()

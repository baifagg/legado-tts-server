#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""环境诊断 —— 部署前 / 出问题时第一个该跑的命令。

    python doctor.py            # 人类可读报告
    python doctor.py --json     # 机器可读（给 agent 解析）
    python doctor.py --smoke    # 额外做一次真实合成（会加载模型，约 30–90 秒）

退出码：
    0 = 全部关键项通过
    1 = 有致命问题（服务起不来）
    2 = 有警告（能跑但有风险）

设计目标：**任何 agent 拿到这个输出，就能判断能不能跑、缺什么、下一步做什么。**
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
VENV = BASE / ".venv"
REFS = Path(os.environ.get("TTS_REFS_DIR", BASE / "refs"))
MODELS = BASE / "models"
MODEL_DIR = MODELS / "OmniVoice"

RESULTS: list[dict] = []


def add(item: str, status: str, detail: str, fix: str = "") -> None:
    """status: ok / warn / fail"""
    RESULTS.append(dict(item=item, status=status, detail=detail, fix=fix))


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


# --------------------------------------------------------------------------- #
def check_python() -> None:
    v = sys.version_info
    ver = f"{v.major}.{v.minor}.{v.micro}"
    in_venv = sys.prefix != sys.base_prefix
    detail = f"Python {ver}（{'venv' if in_venv else '系统环境'}：{sys.prefix}）"
    if not (3, 10) <= (v.major, v.minor) <= (3, 12):
        add("Python 版本", "fail", detail, "需要 3.10–3.12；3.13 目前 torch 轮子不全")
    elif not in_venv:
        add("Python 版本", "warn", detail, "建议用 install.py 创建 .venv，避免污染系统环境")
    else:
        add("Python 版本", "ok", detail)


def check_torch() -> dict:
    info: dict = {}
    try:
        import torch
    except Exception as exc:
        add("PyTorch", "fail", f"import torch 失败：{type(exc).__name__}: {exc}",
            "运行 python install.py 重新安装")
        return info

    info["torch"] = torch.__version__
    info["cuda_built"] = torch.version.cuda
    info["cuda_available"] = bool(torch.cuda.is_available())
    info["mps"] = bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available())
    info["xpu"] = bool(hasattr(torch, "xpu") and torch.xpu.is_available())

    if info["cuda_available"]:
        info["device_name"] = torch.cuda.get_device_name(0)
        info["capability"] = ".".join(map(str, torch.cuda.get_device_capability(0)))
        try:
            props = torch.cuda.get_device_properties(0)
            info["vram_gb"] = round(props.total_memory / 1e9, 1)
        except Exception:
            info["vram_gb"] = None
        vram = info.get("vram_gb") or 0
        detail = (f"torch {torch.__version__} | CUDA {torch.version.cuda} | "
                  f"{info['device_name']} | sm_{info['capability'].replace('.', '')} | {vram} GB")
        if vram and vram < 6:
            add("PyTorch / CUDA", "warn", detail,
                "显存 < 6 GB：模型要 ~3.6 GB，长句可能触发换页变慢；建议分句更短或改用 CPU")
        else:
            add("PyTorch / CUDA", "ok", detail)
    elif info["mps"]:
        add("PyTorch / MPS", "ok", f"torch {torch.__version__} | Apple Silicon MPS 可用")
    elif info["xpu"]:
        add("PyTorch / XPU", "ok", f"torch {torch.__version__} | Intel XPU 可用")
    else:
        add("PyTorch / 设备", "warn",
            f"torch {torch.__version__} | 无 CUDA/MPS/XPU，将用 CPU",
            "CPU 可运行但每句约 5–15 秒；如需加速请装对应厂商的 torch 构建")
    return info


def check_omnivoice() -> None:
    try:
        import omnivoice
        add("omnivoice 包", "ok", f"版本 {getattr(omnivoice, '__version__', 'unknown')}")
    except Exception as exc:
        add("omnivoice 包", "fail", f"import 失败：{type(exc).__name__}: {exc}",
            "运行 python install.py，或 pip install omnivoice==0.2.1")


def check_model() -> None:
    main_w = MODEL_DIR / "model.safetensors"
    tok_w = MODEL_DIR / "audio_tokenizer" / "model.safetensors"
    cfg = MODEL_DIR / "config.json"
    if not MODEL_DIR.exists():
        add("模型权重", "fail", f"目录不存在：{MODEL_DIR}",
            "运行 python install.py（会自动下载约 3.1 GB），或手动放到该目录")
        return
    missing = [str(p.relative_to(MODEL_DIR)) for p in (main_w, tok_w, cfg) if not p.exists()]
    if missing:
        add("模型权重", "fail", f"缺少文件：{', '.join(missing)}",
            "重新运行 python install.py；或从 https://huggingface.co/k2-fsa/OmniVoice 手动补齐")
        return
    total = sum(p.stat().st_size for p in MODEL_DIR.rglob("*") if p.is_file())
    add("模型权重", "ok", f"{MODEL_DIR}（共 {human_bytes(total)}）")


def check_refs() -> None:
    man = REFS / "manifest.json"
    if not man.exists():
        add("参考音色", "fail", f"缺少清单：{man}", "从仓库重新拉取 refs/ 目录")
        return
    try:
        data = json.loads(man.read_text(encoding="utf-8"))
    except Exception as exc:
        add("参考音色", "fail", f"manifest.json 解析失败：{exc}", "文件损坏，重新拉取 refs/")
        return
    bad = []
    for d in data:
        f = REFS / d.get("file", "")
        if not f.exists() or f.stat().st_size < 10000:
            bad.append(d.get("key", "?"))
        elif not (d.get("text") or "").strip():
            bad.append(d.get("key", "?") + "(无参考文本)")
    if bad:
        add("参考音色", "fail", f"{len(data)} 个音色中 {len(bad)} 个有问题：{bad}",
            "重新拉取 refs/ 目录")
    else:
        add("参考音色", "ok", f"{len(data)} 个音色，wav 与参考文本齐全")


def check_ffmpeg() -> None:
    p = shutil.which("ffmpeg")
    if p:
        add("ffmpeg", "ok", p)
        return
    try:
        import imageio_ffmpeg
        p = imageio_ffmpeg.get_ffmpeg_exe()
        if p and os.path.exists(p):
            add("ffmpeg", "ok", f"{p}（来自 imageio-ffmpeg）")
            return
    except Exception:
        pass
    add("ffmpeg", "warn", "未找到",
        "变速需要 ffmpeg。pip install imageio-ffmpeg（自带二进制）或系统安装 ffmpeg。"
        "缺了不会崩，但高倍速会失效")


def check_ports(base_port: int) -> None:
    for port, label in ((base_port, "主服务"), (base_port + 10, "worker")):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("0.0.0.0", port))
            add(f"端口 {port}（{label}）", "ok", "空闲")
        except OSError:
            add(f"端口 {port}（{label}）", "warn", "已被占用",
                f"换端口：python run.py --port {base_port + 100}；或先停掉占用进程")
        finally:
            s.close()


def check_lan() -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    add("局域网地址", "ok", f"http://{ip}:18760/  （手机需在同一 WiFi）")


def check_smoke() -> None:
    """真实加载模型合成一句，验证端到端。"""
    print("\n[smoke] 正在加载模型并合成一句，请稍候（30–90 秒）...")
    try:
        sys.path.insert(0, str(BASE))
        import numpy as np
        import torch
        from omnivoice import OmniVoice
        from worker import detect_device, pick_dtype

        dev = detect_device()
        dt = pick_dtype(dev)
        model = OmniVoice.from_pretrained(str(MODEL_DIR), device_map=dev, dtype=dt)
        man = json.loads((REFS / "manifest.json").read_text(encoding="utf-8"))
        ref = man[0]
        out = model.generate(
            text="这是一次自检，用来确认语音合成可以正常工作。",
            ref_audio=str(REFS / ref["file"]),
            ref_text=ref["text"],
            num_step=16,
        )[0]
        arr = np.asarray(out, dtype=np.float32)
        secs = len(arr) / 24000
        add("端到端合成", "ok", f"device={dev} dtype={dt} 输出 {secs:.2f}s 音频")
        del model
        torch.cuda.empty_cache() if torch.cuda.is_available() else None
    except Exception as exc:
        add("端到端合成", "fail", f"{type(exc).__name__}: {exc}",
            "看 logs/worker_*.log；常见原因：显存不足、模型文件不全、refs 文本不匹配")


# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description="OmniVoice 听书 TTS 环境诊断")
    ap.add_argument("--json", action="store_true", help="输出 JSON（给 agent 解析）")
    ap.add_argument("--smoke", action="store_true", help="额外做一次真实合成")
    ap.add_argument("--port", type=int, default=18760)
    args = ap.parse_args()

    check_python()
    torch_info = check_torch()
    check_omnivoice()
    check_model()
    check_refs()
    check_ffmpeg()
    check_ports(args.port)
    check_lan()
    if args.smoke:
        check_smoke()

    fails = [r for r in RESULTS if r["status"] == "fail"]
    warns = [r for r in RESULTS if r["status"] == "warn"]

    if args.json:
        print(json.dumps(dict(
            os=f"{platform.system()} {platform.machine()}",
            python=platform.python_version(),
            torch=torch_info,
            checks=RESULTS,
            summary=dict(ok=len(RESULTS) - len(fails) - len(warns),
                         warn=len(warns), fail=len(fails)),
            verdict="fail" if fails else ("warn" if warns else "ok"),
        ), ensure_ascii=False, indent=2))
    else:
        icon = {"ok": "✅", "warn": "⚠️ ", "fail": "❌"}
        print("=" * 78)
        print("  OmniVoice 听书 TTS · 环境诊断")
        print("=" * 78)
        for r in RESULTS:
            print(f"{icon[r['status']]} {r['item']}")
            print(f"    {r['detail']}")
            if r["status"] != "ok" and r["fix"]:
                print(f"    → 修复：{r['fix']}")
        print("-" * 78)
        print(f"结果：{len(RESULTS)-len(fails)-len(warns)} 项正常，"
              f"{len(warns)} 项警告，{len(fails)} 项致命")
        if fails:
            print("\n❌ 有致命问题，服务无法启动。按上面的「→ 修复」逐条处理。")
        elif warns:
            print("\n⚠️  能跑，但有风险项。建议先处理再启动。")
        else:
            print("\n✅ 环境就绪。启动：python run.py")

    sys.exit(1 if fails else (2 if warns else 0))


if __name__ == "__main__":
    main()

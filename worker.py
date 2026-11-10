# -*- coding: utf-8 -*-
"""OmniVoice TTS worker —— 模型常驻推理进程。

设计要点
--------
1. **独立进程**：模型加载一次要 5–10 秒、常驻 2–4 GB 显存。放在独立进程里，
   主服务（server.py）不 import torch，启动只要 1 秒，重启也不影响模型。
2. **硬件自适应**：自动探测 CUDA / Apple MPS / Intel XPU / CPU，并选择对应 dtype。
3. **变速策略**：模型侧语速上限 1.6x（关键！见 MODEL_SPEED_MAX 注释），
   更快的部分交给 ffmpeg atempo，保证高倍速下不漏字。

接口
----
    POST /tts    {"text": "...", "ref": "王宝", "speed": 2.57}
                 -> audio/wav 字节
    GET  /health -> {"engine":"omni","loaded":true,"device":"cuda:0",...}

用法
----
    python worker.py --port 18770
    python worker.py --model ./models/OmniVoice --device cuda:0 --dtype float16
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
import uvicorn

# --------------------------------------------------------------------------- #
# 路径与配置
# --------------------------------------------------------------------------- #
BASE_DIR = Path(__file__).resolve().parent
REFS_DIR = Path(os.environ.get("TTS_REFS_DIR", BASE_DIR / "refs"))
LOG_DIR = Path(os.environ.get("TTS_LOG_DIR", BASE_DIR / "logs"))
LOG_DIR.mkdir(parents=True, exist_ok=True)

# 模型目录优先级：--model 参数 > 环境变量 > 本地 ./models/OmniVoice > HF 仓库 id
DEFAULT_MODEL_DIR = Path(os.environ.get("TTS_MODEL_DIR", BASE_DIR / "models" / "OmniVoice"))
HF_MODEL_ID = os.environ.get("TTS_HF_MODEL", "k2-fsa/OmniVoice")

# 这些是「标定过的生产参数」，改动前必读 docs/TUNING.md
MODEL_SPEED_MAX = 1.6   # 模型侧语速上限（**不要调回 2.0**，会丢句首字，见 docs/TUNING.md）
MODEL_SPEED_MIN = 0.75  # 更慢的部分交给 ffmpeg
NUM_STEP = 16           # 采样步数（32 步质量提升不明显，耗时 +81%）
SAMPLE_RATE = 24000

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / f"worker_{os.getpid()}.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("worker")

app = FastAPI(docs_url=None, redoc_url=None)

_state: dict = {
    "engine": "omni",
    "loaded": False,
    "load_seconds": None,
    "device": None,
    "dtype": None,
    "model": None,
    "error": None,
}
_model = None
_lock = threading.RLock()      # 推理锁（模型非线程安全，必须串行；用 RLock 因为内部会重入）
_ref_lock = threading.Lock()
_ref_cache: dict[str, tuple[str, str]] = {}
_manifest: dict[str, dict] = {}
DEFAULT_REF = os.environ.get("TTS_DEFAULT_REF", "王宝")


# --------------------------------------------------------------------------- #
# 音色（参考音频）
# --------------------------------------------------------------------------- #
def _load_manifest() -> dict[str, dict]:
    global _manifest
    if _manifest:
        return _manifest
    p = REFS_DIR / "manifest.json"
    if not p.exists():
        raise FileNotFoundError(f"音色清单不存在：{p}")
    data = json.loads(p.read_text(encoding="utf-8"))
    _manifest = {d["key"]: d for d in data}
    log.info("已载入 %d 个参考音色：%s", len(_manifest), "、".join(_manifest))
    return _manifest


def _load_ref(ref_name: str) -> tuple[str, str]:
    """返回 (参考音频路径, 参考文本)。带缓存，避免每句读盘。"""
    with _ref_lock:
        if ref_name in _ref_cache:
            return _ref_cache[ref_name]
        man = _load_manifest()
        if ref_name not in man:
            log.warning("未知音色 %s，改用 %s", ref_name, DEFAULT_REF)
            ref_name = DEFAULT_REF if DEFAULT_REF in man else next(iter(man))
        entry = man[ref_name]
        wav = REFS_DIR / entry["file"]
        if not wav.exists():
            raise FileNotFoundError(f"参考音频不存在：{wav}")
        _ref_cache[ref_name] = (str(wav), entry.get("text", "").strip())
        return _ref_cache[ref_name]


# --------------------------------------------------------------------------- #
# 硬件探测
# --------------------------------------------------------------------------- #
def detect_device(preferred: str | None = None) -> str:
    """按 CUDA -> Apple MPS -> Intel XPU -> CPU 的顺序选设备。"""
    if preferred:
        return preferred
    import torch

    if torch.cuda.is_available():
        return "cuda:0"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    if hasattr(torch, "xpu") and torch.xpu.is_available():
        return "xpu:0"
    return "cpu"


def pick_dtype(device: str):
    """不同后端的最优 dtype；CPU 必须 float32（fp16 在 CPU 上极慢且可能不支持）。"""
    import torch

    if device.startswith("cuda") or device.startswith("xpu"):
        return torch.float16
    if device == "mps":
        return torch.float16
    return torch.float32


def resolve_ffmpeg() -> str | None:
    """找 ffmpeg：系统 PATH -> 常见安装路径 -> imageio-ffmpeg 自带二进制。"""
    p = shutil.which("ffmpeg")
    if p:
        return p
    candidates = [
        r"C:\Program Files\FFmpeg\bin\ffmpeg.exe",
        r"C:\Program Files (x86)\FFmpeg\bin\ffmpeg.exe",
        "/opt/homebrew/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "/usr/bin/ffmpeg",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    try:  # 兜底：imageio-ffmpeg 会自带一份可执行文件
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            return exe
    except Exception:
        pass
    return None


FFMPEG = resolve_ffmpeg()


# --------------------------------------------------------------------------- #
# 模型加载
# --------------------------------------------------------------------------- #
def load_model(model_path: str | None = None, device: str | None = None,
               dtype: str | None = None) -> None:
    global _model
    import torch

    t0 = time.time()
    dev = detect_device(device)
    dt = {"float16": torch.float16, "bfloat16": torch.bfloat16, "float32": torch.float32}.get(
        (dtype or "").lower()) or pick_dtype(dev)

    # 模型路径：显式 > 本地目录 > HF 仓库 id（会自动下载）
    if model_path:
        src = model_path
    elif (DEFAULT_MODEL_DIR / "model.safetensors").exists():
        src = str(DEFAULT_MODEL_DIR)
    elif (DEFAULT_MODEL_DIR / "config.json").exists():
        src = str(DEFAULT_MODEL_DIR)
    else:
        src = HF_MODEL_ID
        log.info("本地未找到模型，将从 HuggingFace 下载：%s（首次约 3 GB）", src)

    from omnivoice import OmniVoice

    log.info("加载 OmniVoice device=%s dtype=%s src=%s", dev, dt, src)
    _model = OmniVoice.from_pretrained(src, device_map=dev, dtype=dt)

    _state.update(
        loaded=True,
        load_seconds=round(time.time() - t0, 2),
        device=dev,
        dtype=str(dt).replace("torch.", ""),
        model=str(src),
        error=None,
    )
    log.info("OmniVoice 加载完成 %.1fs (%s / %s)", _state["load_seconds"], dev, _state["dtype"])


# --------------------------------------------------------------------------- #
# 合成
# --------------------------------------------------------------------------- #
def _speed_compensate(target_ratio: float) -> float:
    """把「期望的实际倍速」换算成应传给模型的 speed 参数。

    模型对 speed 有衰减，实测拟合：实际 y ≈ 0.916 * x + 0.121
    反解：x = (y - 0.121) / 0.916
    """
    if target_ratio <= 1.0:
        return target_ratio
    x = (target_ratio - 0.121) / 0.916
    return float(min(5.0, max(1.0, x)))


def synth_omni(text: str, ref_name: str, speed: float) -> tuple[np.ndarray, int]:
    ref_audio, ref_text = _load_ref(ref_name)
    kwargs: dict = {"text": text, "ref_audio": ref_audio, "ref_text": ref_text}

    want = float(speed) if speed else 1.0
    model_speed = max(MODEL_SPEED_MIN, min(MODEL_SPEED_MAX, want))

    if abs(model_speed - 1.0) > 0.03:
        # 1.0 附近绝不传 speed：传 1.0 会比不传慢 75%，音频完全一样
        kwargs["speed"] = _speed_compensate(model_speed)
    kwargs["num_step"] = NUM_STEP

    audio = _model.generate(**kwargs)
    wav = np.asarray(audio[0] if isinstance(audio, (list, tuple)) else audio, dtype=np.float32)
    if wav.ndim > 1:
        wav = wav.squeeze()

    # 模型没覆盖到的倍速，用 ffmpeg atempo 补齐
    if model_speed > 0:
        remaining = want / model_speed
        if abs(remaining - 1.0) > 0.02:
            wav = _time_stretch(wav, remaining)

    return wav, SAMPLE_RATE


def _time_stretch(wav: np.ndarray, speed: float) -> np.ndarray:
    """变速不变调，用 ffmpeg atempo（工业级实现，实测几乎无损）。

    历史：曾手写 numpy 相位声码器，能量掉 88%、噪声感高 22 倍（"破音"）。
    atempo 单次只支持 0.5~2.0，超出由多段滤镜串联。
    """
    speed = float(speed)
    if abs(speed - 1.0) < 0.02:
        return wav
    if not FFMPEG:
        log.warning("未找到 ffmpeg，跳过变速（保持原速）。请安装 ffmpeg 或 pip install imageio-ffmpeg")
        return wav

    speed = max(0.25, min(8.0, speed))
    filters: list[str] = []
    rem = speed
    while rem > 2.0:
        filters.append("atempo=2.0")
        rem /= 2.0
    while rem < 0.5:
        filters.append("atempo=0.5")
        rem /= 0.5
    filters.append(f"atempo={rem:.6f}")

    try:
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "in.wav")
            dst = os.path.join(td, "out.wav")
            sf.write(src, np.asarray(wav, dtype=np.float32), SAMPLE_RATE, subtype="PCM_16")
            cmd = [FFMPEG, "-y", "-loglevel", "error", "-i", src,
                   "-filter:a", ",".join(filters), "-ar", str(SAMPLE_RATE), "-ac", "1", dst]
            subprocess.run(cmd, check=True, capture_output=True, timeout=120)
            out, _ = sf.read(dst, dtype="float32")
        out = np.asarray(out, dtype=np.float32)
        if out.ndim > 1:
            out = out.mean(axis=1)
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("ffmpeg 变速失败（%s），返回原始音频", exc)
        return wav


def _run_synth(text: str, ref_name: str, speed: float):
    with _lock:
        return synth_omni(text, ref_name, speed)


def _finalize_audio(wav: np.ndarray, sr: int) -> np.ndarray:
    """输出前统一处理，专治"破音"：

    模型原始输出峰值常顶到 0.9~1.0，手机再开点音量就超过 0 dBFS 被硬削波。
    1) 去直流 2) 峰值归一化到 -2 dBFS 3) tanh 软限幅
    """
    y = np.asarray(wav, dtype=np.float32)
    if y.ndim > 1:
        y = y.squeeze()
    if y.size == 0:
        return y
    y = y - float(np.mean(y))
    peak = float(np.abs(y).max())
    if peak > 1e-6:
        y = y * (0.79 / peak)
    k = 0.85
    y = np.tanh(y / k) * k
    return y.astype(np.float32)


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
@app.get("/health")
async def health():
    info = dict(_state)
    info["engine_ok"] = bool(info["loaded"])
    info["num_step"] = NUM_STEP
    info["model_speed_max"] = MODEL_SPEED_MAX
    info["ffmpeg"] = bool(FFMPEG)
    return info


@app.post("/tts")
async def tts(request: Request):
    data = await request.json()
    text = (data.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "text 为空"}, status_code=400)
    if not _state["loaded"]:
        return JSONResponse({"error": f"模型未加载：{_state['error']}"}, status_code=503)

    ref_name = data.get("ref") or DEFAULT_REF
    speed = float(data.get("speed") or 1.0)

    t0 = time.time()
    try:
        # 必须 to_thread：同步推理放进 async def 会堵死事件循环，连 /health 都不响应
        wav, sr = await asyncio.to_thread(_run_synth, text, ref_name, speed)
    except Exception as exc:  # noqa: BLE001
        log.exception("合成失败")
        return JSONResponse({"error": str(exc)}, status_code=500)

    buf = io.BytesIO()
    out = _finalize_audio(wav, sr)
    sf.write(buf, out, sr, format="WAV", subtype="PCM_16")
    cost = int((time.time() - t0) * 1000)
    log.info("合成 %d 字 ref=%s speed=%.2f -> %.2fs 音频(峰值%.2f) %dms",
             len(text), ref_name, speed, len(out) / sr, float(np.abs(out).max()), cost)

    return Response(
        content=buf.getvalue(),
        media_type="audio/wav",
        headers={
            "X-Engine": "omni",
            "X-Sample-Rate": str(sr),
            "X-Audio-Seconds": f"{len(wav) / sr:.2f}",
            "X-Cost-Ms": str(cost),
            "X-Device": str(_state["device"]),
        },
    )


@app.on_event("startup")
async def startup():
    try:
        load_model(ARGS.model, ARGS.device, ARGS.dtype)
    except Exception as exc:  # noqa: BLE001
        _state["error"] = str(exc)
        _state["loaded"] = False
        log.exception("模型加载失败")


def main() -> None:
    global ARGS
    ap = argparse.ArgumentParser(description="OmniVoice TTS worker")
    ap.add_argument("--port", type=int, default=int(os.environ.get("TTS_WORKER_PORT", 18770)))
    ap.add_argument("--host", default=os.environ.get("TTS_WORKER_HOST", "127.0.0.1"))
    ap.add_argument("--model", default=os.environ.get("TTS_MODEL_PATH"))
    ap.add_argument("--device", default=os.environ.get("TTS_DEVICE"),
                    help="cuda:0 / mps / xpu:0 / cpu；留空自动探测")
    ap.add_argument("--dtype", default=os.environ.get("TTS_DTYPE"),
                    choices=["float16", "bfloat16", "float32", None] if False else None,
                    help="float16 / bfloat16 / float32；留空按设备自动选")
    ARGS = ap.parse_args()
    log.info("worker 启动 port=%s device=%s ffmpeg=%s", ARGS.port, ARGS.device or "auto", FFMPEG)
    uvicorn.run(app, host=ARGS.host, port=ARGS.port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()

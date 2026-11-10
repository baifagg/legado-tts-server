# -*- coding: utf-8 -*-
"""
局域网听书 TTS 主服务 —— 给 Legado（阅读 3.0）的「在线朗读」用。

架构：
    手机 Legado ──HTTP──> 本服务(18760) ──> Qwen3-TTS worker (18771)  音色最贴
                                        └─> OmniVoice worker  (18770)  速度最快
                                        └─> 本机 SAPI        离线兜底

主服务只做路由/缓存/保守文本处理，不加载 torch，所以它自己启动只要 1 秒。
展示给用户的地址永远是本服务的局域网地址。

为什么要缓存：听书时来回翻页、切章节，同一段文字会被反复请求多次。
命中缓存时直接返回文件，手机端几乎零等待。

启动： python server.py --port 18760
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, unquote_to_bytes

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
import uvicorn

BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "cache"
LOG_DIR = BASE_DIR / "logs"
for _d in (CACHE_DIR, LOG_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# 数字/日期/金额/百分比等中文读法前端（2026-11-10 引入，替代原 digits_guard）
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
from text_front import digits_guard_pro  # noqa: E402

MAX_TEXT_LEN = 2000
CACHE_TTL_DAYS = 30

# ------------------------------------------------------------------ 公网模式
# 设了 TTS_TOKEN 就启用鉴权：除了 /health，所有接口都要求带 token。
# 暴露到公网时【必须】设置，否则任何扫到你 IPv6 的人都能免费调用你的 GPU。
# 用法：  $env:TTS_TOKEN="一串随机字符"   再启动
AUTH_TOKEN = os.environ.get("TTS_TOKEN", "").strip()

# MP3 输出：听书用 48kbps 单声道完全够，比 WAV 省 87% 流量。
# 通过请求参数 fmt=mp3 启用；默认仍返回 WAV 保持兼容。
MP3_BITRATE = os.environ.get("MP3_BITRATE", "48k")

# ffmpeg：转 MP3 用。找不到就依次试常见路径、再退到 imageio-ffmpeg 自带二进制。
def _resolve_ffmpeg() -> str | None:
    p = shutil.which("ffmpeg")
    if p:
        return p
    for c in (
        r"C:\Program Files\FFmpeg\bin\ffmpeg.exe",
        r"C:\Program Files (x86)\FFmpeg\bin\ffmpeg.exe",
        "/opt/homebrew/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "/usr/bin/ffmpeg",
    ):
        if os.path.exists(c):
            return c
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            return exe
    except Exception:
        pass
    return None


FFMPEG = _resolve_ffmpeg()

# SAPI 兜底（本机离线语音）只有 Windows 有。其他平台自动禁用，不影响主流程。
SAPI_AVAILABLE = sys.platform == "win32"

# 引擎开关：只保留 omni。
# qwen 一起常驻要多吃约 4.3GB 显存，且在 12GB 卡上会和 omni 抢资源，
# 对持续听书没有实际收益（omni 已足够）。代码保留，需要时把开关改成
# ENABLE_QWEN = os.environ.get("ENABLE_QWEN", "1") == "1" 即可恢复。
ENABLE_QWEN = os.environ.get("ENABLE_QWEN", "0") == "1"

WORKERS = {
    "omni": os.environ.get("OMNI_URL", "http://127.0.0.1:18770"),
}
if ENABLE_QWEN:
    WORKERS["qwen"] = os.environ.get("QWEN_URL", "http://127.0.0.1:18771")

DEFAULT_ENGINE = os.environ.get("DEFAULT_ENGINE", "omni")

REFS_DIR = BASE_DIR / "refs"
DEFAULT_REF = "王宝"


def load_refs() -> dict[str, dict]:
    """读取参考音色清单（二字人名 -> 说明）。"""
    p = REFS_DIR / "manifest.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return {d["key"]: d for d in data}
    except Exception as exc:  # noqa: BLE001
        log.warning("音色清单读取失败：%s", exc)
        return {}


REFS = load_refs()

log = logging.getLogger("tts-main")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "server.log", encoding="utf-8"),
    ],
)

app = FastAPI(title="LAN TTS for Legado", docs_url=None, redoc_url=None)
_cache_lock = asyncio.Lock()


@app.middleware("http")
async def _auth_middleware(request, call_next):
    """
    公网鉴权。只有设置了 TTS_TOKEN 才生效。

    /health 放行（只返回引擎状态，不含敏感信息），方便手机端排查连通性；
    其余所有接口（含控制台页面、/api/tts、JSON 清单）都要求 token。
    token 可以放在 query（?token=xxx）或 header（X-TTS-Token）。
    """
    if AUTH_TOKEN:
        path = request.url.path
        if path not in ("/health", "/favicon.ico"):
            supplied = (request.query_params.get("token")
                        or request.headers.get("x-tts-token", ""))
            if supplied != AUTH_TOKEN:
                return JSONResponse(
                    {"ok": False, "error": "unauthorized: token 缺失或不匹配"},
                    status_code=403,
                )
    return await call_next(request)


def _to_mp3(wav_path: Path) -> Path | None:
    """把缓存里的 WAV 转成 MP3（同目录同名 .mp3，存在则直接复用）。"""
    if not FFMPEG:
        return None
    mp3 = wav_path.with_suffix(".mp3")
    if mp3.exists() and mp3.stat().st_size > 0:
        return mp3
    tmp = mp3.with_suffix(".mp3.part")
    try:
        # 必须显式 -f mp3：临时文件后缀是 .part，ffmpeg 无法从扩展名
        # 推断封装格式，会报 EINVAL（实测 exit 4294967274 = -22）
        subprocess.run(
            [FFMPEG, "-y", "-loglevel", "error", "-i", str(wav_path),
             "-c:a", "libmp3lame", "-b:a", MP3_BITRATE, "-ac", "1",
             "-f", "mp3", str(tmp)],
            check=True, capture_output=True, timeout=60,
        )
        tmp.replace(mp3)
        return mp3
    except Exception as exc:  # noqa: BLE001
        log.warning("MP3 转码失败：%s", exc)
        tmp.unlink(missing_ok=True)
        return None


# --------------------------------------------------------------------------- #
# 文本保守处理
# --------------------------------------------------------------------------- #
_DIGITS = "零一二三四五六七八九"


def _int_to_cn(v: int) -> str:
    """0-99999 的整数中文读法。"""
    if v == 0:
        return "零"
    if v < 10:
        return _DIGITS[v]
    if v < 20:
        return "十" + (_DIGITS[v % 10] if v % 10 else "")
    parts: list[str] = []
    for name, base in (("万", 10000), ("千", 1000), ("百", 100), ("十", 10)):
        q, v = divmod(v, base)
        if q:
            parts.append(_DIGITS[q] + name)
        elif parts and v:
            parts.append("零")
    if v:
        parts.append(_DIGITS[v])
    return "".join(parts).replace("零零", "零").rstrip("零") or "零"


def digits_guard(text: str) -> str:
    """
    听书最怕数字读错。把阿拉伯数字转成中文读法，减少模型自由发挥：
      - 年份逐位读：2024年 -> 二零二四年
      - 其它整数按数值读：第15章 -> 第十五章
      - 小数保留"点"：3.5 -> 三点五
    只处理中文语境下的裸数字，不动英文/URL 里的数字。
    """
    if not re.search(r"\d", text):
        return text

    def year(m: re.Match) -> str:
        # 把 "2024年" 整体替换为 "二零二四年"，避免再套一层"年"
        return "".join(_DIGITS[int(c)] for c in m.group(1)) + "年"

    # 年份（连同后面的"年"一起吃掉）
    text = re.sub(r"((?:19|20)\d{2})(?=年)年?", year, text)

    # 小数
    def dec(m: re.Match) -> str:
        return _int_to_cn(int(m.group(1))) + "点" + "".join(
            _DIGITS[int(c)] for c in m.group(2))

    text = re.sub(r"(?<![\w.])(\d{1,5})\.(\d{1,4})(?![\w.])", dec, text)

    # 普通整数（跳过英文单词/URL 里的数字）
    text = re.sub(r"(?<![A-Za-z0-9_.])(\d{1,5})(?![A-Za-z0-9_.])",
                  lambda m: _int_to_cn(int(m.group(1))), text)
    return text


# --------------------------------------------------------------------------- #
# 文本清洗：把 App 强行切碎的句子修干净
# --------------------------------------------------------------------------- #
# 颜文字 / 表情：送进模型会被念成怪音或直接产生杂音
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"   # 各类 emoji
    "\U00002600-\U000027BF"   # 杂项符号、装饰符号
    "\U0001F000-\U0001F02F"   # 麻将等
    "\uFE0F\u200D\u20E3"      # 变体选择符/零宽连接符
    "\u2190-\u21FF"           # 箭头
    "\u2500-\u257F"           # 制表符（那些 ┗|｀O′|┛）
    "\u2580-\u25FF"           # 方块/几何
    # 【2026-11-10 修复】原来这里还有 "\u3000-\u303F"（CJK 标点区），
    # 会把「。、〈〉《》「」『』【】〔〕」全部删掉 —— 实测生产日志里 63,727 条
    # 请求中「。」只出现 295 次（0.46%），15,200 条完整碎片里以「。」结尾的只有 1 条。
    # 语义标点被吃光后模型根本不知道哪里是句末，是「断句怪」的主要成因。
    # 现在只删真正的空白（U+3000 由后面的 \s+ 处理），标点全部保留。
    "]",
    flags=re.UNICODE,
)
# 俄文/希腊等非中日英文字符，常是乱码或拟声符号
# 【2026-11-10 修复】原白名单只放行「，。！？、；：（）《》…—·」，把
# 「“”‘’」和「「」『』【】〔〕〈〉」当作乱码删掉了。这些是中文对话/书名号，
# 删掉会让对白与叙述无法区分，也让模型看不到句末。现改为放行整个
# CJK 标点区（\u3000-\u303F）与中文弯引号（\u2018-\u201D），只保留真正的乱码过滤。
_KAOMOJI_RE = re.compile(
    r"[^\u4e00-\u9fff\u3040-\u30ff\u3000-\u303F\u2018-\u201DA-Za-z0-9"
    r"，。！？、；：（）《》…—·,.!?;:'\"()\[\]—\-~～％%]"
)


def clean_text(text: str) -> str:
    """
    清洗 App 送来的碎片文本。

    从实际日志看，Legado 按标点硬切会把一句话拆成这样的碎片：
        "”。…。卧室内，龙墨霆浮空盘坐"
        "“元神归位，嗷——！”。┗|｀O′|┛ 嗷~"
        "”。她这个身份的女子..."

    这里做五件事：
      1. 去掉整段颜文字/符号组合（在去单字符之前做，避免残留字母）
      2. 去掉 emoji / 制表符
      3. 去掉开头的孤立标点（上句残留的 "”。…。 ）
      4. 折叠连续标点
      5. 去掉首尾空白
    """
    if not text:
        return text

    # 1. 先整体干掉颜文字块：由符号+字母组成的短串（如 ┗|｀O′|┛ ^_^ T_T）
    t = re.sub(r"[|｜/\\^~_·`′｀°´\-—=+*<>《》〔〕（）()\[\]{}]+[A-Za-z0-9°´′｀|｜/\\^~_\-]{0,8}"
               r"[|｜/\\^~_·`′｀°´\-—=+*<>《》〔〕（）()\[\]{}]+", "", text)
    # 单独的 ^_^ 这类两段式
    t = re.sub(r"\^[_\-]?\^|T[_\-]?T|>[_\-]?<|o[_\-]?o", "", t)
    # 括号包住的短语颜文字，如 (へ) (｀・ω・´) （・ω・）
    t = re.sub(r"[（(\[〔]\s*[^\u4e00-\u9fff]{0,10}\s*[）)\]〕]", "", t)

    # 2. emoji / 制表符
    t = _EMOJI_RE.sub("", t)
    t = _KAOMOJI_RE.sub("", t)

    # 3. 去掉开头连续的标点（引号保留：正常对话起点）
    t = re.sub(r'^[\s。，、；：…—\-·\.,;:]+', "", t)
    # 4. 折叠标点
    t = re.sub(r'([。！？…])\1{1,}', r"\1", t)
    t = re.sub(r"[。]{2,}", "。", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def merge_fragments(text: str) -> str:
    """
    App 有时会把一句话切成两半送过来（前半句没有句末标点）。
    这种半句单独合成会有明显的不自然停顿。
    这里只做一个弱修正：把句中的句号换成逗号式的连贯处理 —— 实际上
    服务端拿不到下一句，能做的是保证标点不干扰朗读节奏。

    （真正的合并需要在 App 侧调大单句长度；服务端无法跨请求合并，
      因为 HTTP TTS 协议是一次请求一段文本。这里提供的是止损。）
    """
    if not text:
        return text
    # 句子被切在逗号处时，结尾会残留一个逗号，读起来会拖长音
    t = re.sub(r"[，、；：]\s*$", "", text)
    return t


# --------------------------------------------------------------------------- #
# 语速刻度适配
# --------------------------------------------------------------------------- #
# Legado 有两种语速刻度（都真实存在，取决于版本/分支）：
#   scale=speed   滑块 1~100，默认 10 为正常   ← legado / 阅读 3.x
#   scale=float   滑块 0.0~2.0，默认 1.0 为正常 ← 部分分支/魔改版
# 适配方式：请求里加 &scale=float 或 &scale=speed 指定；不指定则自动识别。
SCALE_SPEED = "speed"
SCALE_FLOAT = "float"

# 0~2.0 刻度（0-2.0 调速那个 App）的映射参数
# 2026-10-26 按用户指定锚点标定：滑块 1.7 == 旧滑块 1.2 的速度（2.56x），
#   线性外推得到满格 2.94x，中段因此更平缓、更好微调。
# 2026-11-03 用户连续两次要求「再快 20%」：满格 2.94x -> 3.528x -> 4.2336x。
#   习惯点 1.7（2.56x）始终不变，只把 1.7~2.0 这一段一再拉陡。
FLOAT_SLIDER_MAX = 2.0     # App 滑块的最大值
FLOAT_MIN = 0.4            # 滑块 0 时对应的实际倍速（最慢）
FLOAT_ANCHOR = 1.7         # 用户习惯点（2026-10-26 标定，速度不变）
FLOAT_ANCHOR_RATIO = 2.56  # 习惯点对应的实际倍速
FLOAT_MAX = 4.2336         # 滑块 2.0 时对应的实际倍速（2.94 × 1.2 × 1.2）


def auto_scale(raw: float) -> str:
    """
    没有显式指定 scale 时，靠数值范围自动判断。

    1~100 刻度：正常值是 10，常见波动 1~50
    0~2   刻度：正常值是 1.0，最大值 2.0

    判据：<= 2.5 基本只可能是 0~2.0 刻度（1~100 刻度下 2.5 是极慢，
    但更可能是 float 刻度的 2.0 倍速上限）。用 2.5 做分界最稳。
    """
    if raw <= 2.5:
        return SCALE_FLOAT
    return SCALE_SPEED


def speed_to_ratio(speed: float | None, scale: str | None = None) -> tuple[float, str]:
    """
    把 App 的语速值换算成模型倍率，返回 (倍率, 实际使用的刻度)。

    speed 刻度（1~100，10 为正常）：
        6  -> 0.77    8  -> 0.89    10 -> 1.00
        15 -> 1.22    20 -> 1.41    30 -> 1.73

    float 刻度（滑块 0~2.0，0.4~4.23 倍速，1.7 为习惯点）：
        0.0 -> 0.40   0.5 -> 1.04   1.0 -> 1.67
        1.5 -> 2.31   1.7 -> 2.56   2.0 -> 4.23（满格上限两次 +20%）
    注意：这里 1.0 不再对应原速（用户明确要求"以上限为准均分"），
    原速落在滑块 0.5 附近。
    """
    if speed is None or speed == "":
        return 1.0, (scale or SCALE_SPEED)
    try:
        s = float(speed)
    except Exception:
        return 1.0, (scale or SCALE_SPEED)

    use = scale if scale in (SCALE_SPEED, SCALE_FLOAT) else auto_scale(s)

    # 1~100 刻度下 0 或负数是无效值；但 0~2.0 刻度下 0 是合法的滑块最左端，
    # 必须映射到最慢倍速，不能当无效值退回 1.0。
    if s <= 0 and use == SCALE_SPEED:
        return 1.0, SCALE_SPEED

    if use == SCALE_FLOAT:
        # 刻度错配修正（实测发现）：
        # 某版本阅读界面显示 0~2.0，但传输时数值 ×10，
        # 即界面满格 2.0 实际发的是 20（用户日志里 102 次全是 20）。
        # 所以 > 2.0 的值先除以 10 还原成界面值，再按界面刻度映射。
        s_slider = s
        if s > FLOAT_SLIDER_MAX * 1.05:
            s_slider = s / 10.0
            log.info("speakSpeed=%s 按界面值 %.1f 处理（该版本传输 ×10）", s, s_slider)
            if s_slider > FLOAT_SLIDER_MAX * 1.05:
                s_slider = FLOAT_SLIDER_MAX   # 还原后仍超限，直接按满格

        # 0~2.0 刻度分两段：0~1.7 沿用原斜率（习惯点 2.56x 不变），
        # 1.7~2.0 单独拉陡，只把满格上限抬到 3.528x。
        #     0.0 -> 0.40    0.5 -> 1.04    1.0 -> 1.67
        #     1.2 -> 1.92    1.5 -> 2.31    1.7 -> 2.56
        #     1.8 -> 3.12    1.9 -> 3.68    2.0 -> 4.23
        if s_slider <= FLOAT_ANCHOR:
            ratio = FLOAT_MIN + s_slider * (FLOAT_ANCHOR_RATIO - FLOAT_MIN) / FLOAT_ANCHOR
        else:
            ratio = FLOAT_ANCHOR_RATIO + (s_slider - FLOAT_ANCHOR) * (
                FLOAT_MAX - FLOAT_ANCHOR_RATIO) / (FLOAT_SLIDER_MAX - FLOAT_ANCHOR)
        # 全链路衰减补偿：模型 speed 响应 + 外部拉伸都有损耗，实测偏低约 6%。
        # 用真实小说句子（31 字）标定，以滑块0.5(≈1.04x)为基准：
        #     标称 2.56x -> 实测 2.40x（偏低 6.3%）
        #     标称 2.94x -> 实测 2.75x（偏低 6.5%）
        # 线性反解：标称 = (目标 - 0.04) / 0.88
        if ratio > 1.05:
            ratio = (ratio - 0.04) / 0.88
        return max(FLOAT_MIN, min(FLOAT_MAX * 1.2, ratio)), SCALE_FLOAT

    # 1~100 刻度：以 10 为基准，开方压平。
    # 上限提到 4.0x：部分版本滑块最大值只到 20~30，不放开会撞上限显得"调不动"。
    # 实测用户滑块拉到最大发的就是 20，需要能到 4.0x。
    #     6 -> 0.77    10 -> 1.00    15 -> 1.22
    #     20 -> 1.41    40 -> 2.00    80 -> 2.83
    # 为让 20 能到更高速度，改用更陡的曲线：ratio = (s/10) ** 0.8
    #     10 -> 1.00    15 -> 1.39    20 -> 1.74    30 -> 2.41    40 -> 3.03
    ratio = (s / 10.0) ** 0.8
    return max(0.55, min(4.0, ratio)), SCALE_SPEED


def cache_key(text: str, engine: str, ref: str, ratio: float) -> str:
    raw = f"{engine}|{ref}|{ratio:.3f}|{text}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


def cache_path(key: str) -> Path:
    return CACHE_DIR / key[:2] / f"{key}.wav"


def cleanup_cache() -> None:
    cutoff = time.time() - CACHE_TTL_DAYS * 86400
    n = 0
    for p in CACHE_DIR.rglob("*.wav"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
                n += 1
        except OSError:
            pass
    if n:
        log.info("清理过期缓存 %d 个", n)


def my_lan_ip() -> str:
    """
    取本机对局域网出口的 IPv4 地址。
    注意：默认路由探测可能返回 IPv6（::1 等），直接拼 URL 会变成非法地址，
    所以这里显式只接受 IPv4，并回退到枚举网卡。
    """
    import socket

    # 1) UDP connect 探测（不真的发包），只接受 IPv4
    for target in ("192.168.110.1:80", "8.8.8.8:80"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # 强制 IPv4
            s.settimeout(1.0)
            s.connect((target.split(":")[0], int(target.split(":")[1])))
            ip = s.getsockname()[0]
            s.close()
            if ip and not ip.startswith("127.") and ":" not in ip:
                return ip
        except Exception:
            try:
                s.close()
            except Exception:
                pass

    # 2) 回退：枚举本机 IPv4，优先 192.168 / 10. / 172.16-31
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip.startswith(("192.168.", "10.", "172.")):
                return ip
    except Exception:
        pass
    return "127.0.0.1"


# --------------------------------------------------------------------------- #
# 合成
# --------------------------------------------------------------------------- #
async def call_worker(engine: str, text: str, ref: str, ratio: float) -> tuple[bytes, dict]:
    base = WORKERS.get(engine)
    if not base:
        raise RuntimeError(f"未知引擎 {engine}")
    # 必须显式 trust_env=False：本机设了 HTTP_PROXY 且 NO_PROXY 里含 "[::1]" 写法，
    # httpx 解析该写法会抛 InvalidURL；worker 就在本机，本来也不该走代理。
    async with httpx.AsyncClient(timeout=180.0, trust_env=False) as client:
        r = await client.post(f"{base}/tts",
                              json={"text": text, "ref": ref, "speed": ratio})
        r.raise_for_status()
        return r.content, {
            "engine": engine,
            "audio_seconds": r.headers.get("X-Audio-Seconds"),
            "worker_ms": r.headers.get("X-Cost-Ms"),
        }


def synth_sapi(text: str, out_wav: Path) -> None:
    """离线兜底：Windows 本机语音（其他平台直接抛错，由上层回落逻辑处理）。"""
    if not SAPI_AVAILABLE:
        raise RuntimeError("SAPI 兜底仅在 Windows 可用")
    ps = r"""
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
  $names = @($s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name })
  $hit = $names | Where-Object { $_ -like "*Huihui*" } | Select-Object -First 1
  if ($hit) { $s.SelectVoice($hit) }
  $fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(24000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
  $s.SetOutputToWaveFile($env:TTS_OUT, $fmt)
  $s.Speak($env:TTS_TEXT)
} finally { $s.Dispose() }
"""
    env = dict(os.environ)
    env["TTS_TEXT"] = text
    env["TTS_OUT"] = str(out_wav)
    subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-Command", ps],
        env=env, capture_output=True, timeout=120, check=True,
    )


async def synthesize(text: str, engine: str, ref: str, ratio: float) -> tuple[Path, dict]:
    key = cache_key(text, engine, ref, ratio)
    target = cache_path(key)
    if target.exists() and target.stat().st_size > 0:
        return target, {"engine": engine, "cached": True}

    async with _cache_lock:
        if target.exists() and target.stat().st_size > 0:
            return target, {"engine": engine, "cached": True}

        target.parent.mkdir(parents=True, exist_ok=True)
        meta: dict[str, Any] = {"engine": engine, "cached": False}
        try:
            data, wmeta = await call_worker(engine, text, ref, ratio)
            meta.update(wmeta)
            target.write_bytes(data)
        except Exception as exc:  # noqa: BLE001
            if not SAPI_AVAILABLE:
                # 非 Windows 没有 SAPI 兜底，直接把错误抛给客户端（避免返回空音频）
                log.error("引擎 %s 失败且本平台无离线兜底：%s", engine, exc)
                raise
            log.warning("引擎 %s 失败(%s)，回落本机离线语音", engine, exc)
            meta = {"engine": "sapi", "cached": False, "fallback": str(exc)[:200]}
            tmp = target.with_suffix(".sapi.wav")
            await asyncio.to_thread(synth_sapi, text, tmp)
            tmp.replace(target)
        return target, meta


# --------------------------------------------------------------------------- #
# 参数解析：兼容 Legado 的 query / form / json 三种写法
# --------------------------------------------------------------------------- #
async def read_params(request: Request) -> dict[str, str]:
    params: dict[str, str] = {k: v for k, v in request.query_params.items()}
    if request.method in ("POST", "PUT"):
        body = await request.body()
        if body:
            ctype = request.headers.get("content-type", "").lower()
            if "json" in ctype or body.lstrip().startswith(b"{"):
                try:
                    d = json.loads(body.decode("utf-8", "replace"))
                    if isinstance(d, dict):
                        for k, v in d.items():
                            if isinstance(v, (str, int, float)):
                                params.setdefault(k, str(v))
                except Exception:
                    pass
            else:
                try:
                    for k, v in parse_qs(body.decode("utf-8", "replace"),
                                         keep_blank_values=True).items():
                        params.setdefault(k, v[0])
                except Exception:
                    pass
    return params


# --------------------------------------------------------------------------- #
# 入参文本解码：兼容把 text 多编码一层的 App 版本
# --------------------------------------------------------------------------- #
# 2026-11-05 实测（legado-with-MD3 3.26.23）：App 更新后 HTTP 朗读请求里的
# text 多了一层百分号编码——
#     text=%25E7%259C%258B%25EF%25BC%258C...   （双层，表单解析只解一层）
#     text=%E7%9C%8B%EF%BC%8C...               （JSON body 里的单层）
# 服务端取到的仍是 "%E7%9C%8B..."，而 clean_text() 的字符白名单会把 "%" 吃掉，
# 最后把 "E79C8BEFBC8C..." 的 HEX 串当正文送进模型 —— 听书变成念字母。
# 这里在取 text 时做幂等解码，同时兼容 "%" 已被上游剥掉的纯 HEX 形态。
_PCT_RE = re.compile(r"%[0-9A-Fa-f]{2}")
_HEX_ONLY_RE = re.compile(r"[0-9A-Fa-f]+")
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def _url_decode(t: str) -> str:
    """百分号解码，UTF-8 优先；解出替换字符时回退 GBK（老版本/魔改版）。"""
    text = unquote(t)
    if "\ufffd" in text:
        try:
            return unquote_to_bytes(t).decode("gbk")
        except Exception:  # noqa: BLE001 —— 解不动就保持 UTF-8 结果
            return text
    return text


def _decode_hex_text(raw: str) -> str | None:
    """把纯 HEX（UTF-8 字节的十六进制）还原成文本；还原不出中文则返回 None。"""
    if len(raw) < 12 or len(raw) % 2:
        return None
    if not _HEX_ONLY_RE.fullmatch(raw):
        return None
    try:
        decoded = bytes.fromhex(raw).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    # 必须还原出中文才采用，避免把 "deadbeef" 这类正常英文误判成编码
    return decoded if _CJK_RE.search(decoded) else None


def decode_text_param(raw: str) -> str:
    """
    还原 App 传来的 text（幂等，最多解 3 层）。

    正常版本：parse_qs 已解过一层，text 里没有 %XX，原样返回。
    异常版本：text 仍带一层或多层 %XX，连续解到干净为止；
              连 % 都被剥掉的纯 HEX 形态再用 _decode_hex_text 兜底。
    """
    if not raw:
        return raw

    t = raw
    for _ in range(3):
        hits = _PCT_RE.findall(t)
        if not hits:
            break
        # 百分号转义占比过低时不解码，避免误伤正文里本来就有的 "%"
        if sum(len(h) for h in hits) * 2 < len(t):
            break
        u = _url_decode(t)
        if u == t:
            break
        t = u

    fixed = _decode_hex_text(t)
    return fixed if fixed else t


def pick_text(params: dict[str, str]) -> str:
    for alias in ("text", "speakText", "txt", "content", "q"):
        v = params.get(alias)
        if v and v.strip():
            raw = v.strip()
            text = decode_text_param(raw)
            if text != raw:
                log.info("[入参] text 多层编码已还原：%d 字 -> %d 字", len(raw), len(text))
            return text
    return ""


# --------------------------------------------------------------------------- #
# 路由
# --------------------------------------------------------------------------- #
@app.get("/health")
async def health(request: Request):
    status: dict[str, Any] = {}
    async with httpx.AsyncClient(timeout=6.0, trust_env=False) as client:
        for name, url in WORKERS.items():
            try:
                r = await client.get(f"{url}/health")
                j = r.json()
                status[name] = {"ok": bool(j.get("loaded")),
                                "load_seconds": j.get("load_seconds"),
                                "model": j.get("model"),
                                "error": j.get("error")}
            except Exception as exc:  # noqa: BLE001
                status[name] = {"ok": False, "error": str(exc)[:120]}
    port = request.url.port or 18760
    return {
        "ok": any(v.get("ok") for v in status.values()),
        "engines": status,
        "sapi": SAPI_AVAILABLE,
        "cache_files": sum(1 for _ in CACHE_DIR.rglob("*.wav")),
        "lan_url": f"http://{my_lan_ip()}:{port}",
        "default_engine": DEFAULT_ENGINE,
    }


@app.api_route("/api/tts", methods=["GET", "POST"])
@app.api_route("/api/rap", methods=["GET", "POST"])
@app.api_route("/tts", methods=["GET", "POST"])
async def tts(request: Request):
    params = await read_params(request)
    text = pick_text(params)
    if not text:
        return PlainTextResponse("缺少文本参数（text 或 speakText）", status_code=400)
    if len(text) > MAX_TEXT_LEN:
        text = text[:MAX_TEXT_LEN]

    engine = (params.get("engine") or DEFAULT_ENGINE).lower()
    if engine not in ("omni", "qwen", "sapi"):
        engine = DEFAULT_ENGINE
    # qwen 已停用：客户端若还带着 engine=qwen（旧配置），静默回落到 omni，
    # 不让它直接 500，避免用户还得回去改配置。
    if engine == "qwen" and "qwen" not in WORKERS:
        engine = "omni"
    ref = params.get("ref") or DEFAULT_REF
    if REFS and ref not in REFS:
        log.warning("未知音色 %s，改用 %s", ref, DEFAULT_REF)
        ref = DEFAULT_REF

    speed_raw = params.get("speakSpeed") or params.get("speed") or ""
    try:
        speed_val = float(speed_raw) if speed_raw else None
    except Exception:
        speed_val = None

    # 刻度：请求里显式指定，或按数值自动识别
    scale_req = (params.get("scale") or "").lower().strip()
    ratio, scale_used = speed_to_ratio(speed_val, scale_req or None)

    log.info("[参数] speakSpeed=%r scale=%s -> %.2fx",
             speed_raw, scale_used, ratio)

    # 文本处理（2026-11-10 修正顺序）
    # 旧顺序：clean_text -> digits_guard，问题在于 clean_text 会先删掉 "％ / ² ℃ -" 等符号，
    #         digit 规则再看到时就已失去语义（15% -> 十五、1/3 -> 13、3,500 -> 三,五百）。
    # 新顺序：digits_guard_pro（数字/日期/金额/百分比等 → 中文读法）→ clean_text（清颜文字）→
    #         merge_fragments（去句尾残逗号）。
    if engine == "sapi":
        clean = clean_text(text)
    else:
        clean = clean_text(merge_fragments(digits_guard_pro(text)))

    if not clean:
        # 整句都是表情/符号，没有可读内容，返回极短静音避免客户端报错
        clean = "。"

    t0 = time.time()
    try:
        if engine == "sapi":
            key = cache_key(text, "sapi", "default", 1.0)
            target = cache_path(key)
            if not target.exists() or target.stat().st_size == 0:
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = target.with_suffix(".tmp.wav")
                await asyncio.to_thread(synth_sapi, text, tmp)
                tmp.replace(target)
            meta = {"engine": "sapi", "cached": False}
        else:
            target, meta = await synthesize(clean, engine, ref, ratio)
    except Exception as exc:  # noqa: BLE001
        log.exception("合成失败")
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    cost = int((time.time() - t0) * 1000)
    secs = target.stat().st_size / 48000  # 24kHz 16bit mono
    log.info("[%s] %d 字 ≈%.1fs 音频 %s %dms | %s",
             meta.get("engine"), len(clean), secs,
             "缓存" if meta.get("cached") else "合成", cost,
             clean[:22].replace("\n", " "))

    # 输出格式：默认 WAV；传 fmt=mp3 走 MP3（省 87% 流量，适合公网/手机流量）
    fmt = (params.get("fmt") or params.get("format") or "wav").lower()
    if fmt in ("mp3", "mpeg") and FFMPEG:
        mp3 = await asyncio.to_thread(_to_mp3, target)
        if mp3:
            return FileResponse(
                mp3, media_type="audio/mpeg",
                headers={
                    "Cache-Control": "public, max-age=86400",
                    "X-TTS-Engine": str(meta.get("engine")),
                    "X-TTS-Cached": "1" if meta.get("cached") else "0",
                    "X-TTS-Ms": str(cost),
                    "X-TTS-Voice": quote(ref),
                    "X-TTS-Format": "mp3",
                },
                filename="tts.mp3",
            )
        # MP3 转码失败就回落 WAV，不让客户端拿到空响应
        log.warning("MP3 不可用，回落 WAV")

    return FileResponse(
        target, media_type="audio/wav",
        headers={
            "Cache-Control": "public, max-age=86400",
            "X-TTS-Engine": str(meta.get("engine")),
            "X-TTS-Cached": "1" if meta.get("cached") else "0",
            "X-TTS-Ms": str(cost),
            "X-TTS-Voice": quote(ref),
            "X-TTS-Format": "wav",
        },
        filename="tts.wav",
    )


@app.get("/api/voices")
async def voices(request: Request):
    port = request.url.port or 18760
    base = f"http://{my_lan_ip()}:{port}"
    engines = ["omni"] + (["qwen"] if "qwen" in WORKERS else []) + (["sapi"] if SAPI_AVAILABLE else [])
    desc = {
        "omni": "OmniVoice 0.8B · 约 2.5 秒/句 · 合成快于播放",
        "qwen": "Qwen3-TTS 1.7B · 音色最贴参考、中文最准 · 约 10 秒/句",
        **({"sapi": "本机离线语音 · 断网可用 · 音质一般"} if SAPI_AVAILABLE else {}),
    }
    return {
        "engines": engines,
        "engine_desc": {k: desc[k] for k in engines},
        "default_engine": DEFAULT_ENGINE,
        "default_ref": DEFAULT_REF,
        "refs": [
            {"key": k, "desc": v.get("desc", ""), "usage": v.get("usage", ""),
             "seconds": v.get("seconds")}
            for k, v in REFS.items()
        ],
        "endpoints": {"tts": f"{base}/api/tts", "health": f"{base}/health"},
    }


@app.get("/", response_class=HTMLResponse)
async def console(request: Request):
    port = request.url.port or 18760
    base = f"http://{my_lan_ip()}:{port}"
    ref_opts = "".join(
        f'<option value="{k}">{k} · {v.get("desc","")}</option>'
        for k, v in REFS.items()
    )
    html = (CONSOLE_HTML
            .replace("__BASE__", base)
            .replace("__DEFAULT_REF__", DEFAULT_REF)
            .replace("__REF_OPTIONS__", ref_opts)
            .replace("__SAPI_OPTION__",
                     '<option value="sapi">本机离线 · 断网用</option>' if SAPI_AVAILABLE else ""))
    return HTMLResponse(html)


@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)


def _request_host(request: Request) -> str:
    """
    取客户端实际用来访问的主机名（含 IPv6 字面量）。

    关键：JSON 里必须写入**访问者真正连通的那个地址**。
    之前 JSON 是静态文件、地址硬编码 192.168.110.57，导致外网通过 IPv6
    拿到 JSON 后，导入的引擎仍指向局域网 IP，朗读必然失败。
    """
    # X-Forwarded-Host 优先（将来若挂反代）
    fwd = request.headers.get("x-forwarded-host")
    if fwd:
        return fwd.split(",")[0].strip()
    host = request.headers.get("host", "")
    if host:
        # Host 头形如 [2408:...]:18760 或 192.168.110.57:18760
        if host.startswith("["):
            return host[1:host.index("]")] if "]" in host else host
        if host.count(":") == 1:
            return host.split(":")[0]
        # 裸 IPv6 没加括号（不规范但兼容）
        return host
    return my_lan_ip()


def _is_public_host(host: str) -> bool:
    """判断是不是公网地址（IPv6 全球单播，或非私有 IPv4）。"""
    if ":" in host:               # IPv6
        return not host.startswith(("fe80", "fd", "fc", "::1"))
    return not (host.startswith(("127.", "192.168.", "10."))
                or host.startswith("172.16.") or host.startswith("172.17.")
                or host.startswith("172.18.") or host.startswith("172.19.")
                or host.startswith("172.2") or host.startswith("172.30.")
                or host.startswith("172.31."))


def _tts_json_response(request: Request, scale: str | None) -> Response:
    """按访问 Host 动态生成朗读引擎 JSON。"""
    try:
        import build_tts_json as btj
    except Exception as exc:  # noqa: BLE001
        log.exception("加载 build_tts_json 失败：%s", exc)
        return JSONResponse({"error": f"生成器不可用：{exc}"}, status_code=500)

    host = _request_host(request)
    public = _is_public_host(host)
    # 公网访问自动用 MP3：省 87% 流量。局域网保持 WAV（音质优先）
    items = btj.build_items(host, request.url.port or 18760,
                            scale=scale, use_mp3=public)
    payload = json.dumps(items, ensure_ascii=False, indent=2)
    log.info("生成 httpTTS.json host=%s scale=%s mp3=%s 共%d条",
             host, scale or "auto", public, len(items))
    return Response(content=payload, media_type="application/json; charset=utf-8",
                    headers={
                        "Cache-Control": "no-cache",
                        "X-TTS-Json-Host": host,
                        "X-TTS-Json-Mp3": "1" if public else "0",
                    })


@app.get("/httpTTS.json")
async def http_tts_json(request: Request):
    """
    「阅读 / Legado」朗读引擎可导入 JSON —— 语速 1~100 刻度版（10 为正常）。
    地址按访问方式自动适配：公网 IPv6 访问 → 写 IPv6 地址并带 fmt=mp3。
    """
    return _tts_json_response(request, None)


@app.get("/httpTTS-float.json")
async def http_tts_json_float(request: Request):
    """语速 0~2.0 刻度版（1.0 为正常），显式带 scale=float。"""
    return _tts_json_response(request, "float")


CONSOLE_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>听书 TTS</title>
<style>
  :root{--bg:#0e1014;--card:#161a21;--line:#252b36;--fg:#e8eaef;--dim:#8a93a6;--ok:#57d99b;--warn:#f0b849;--brand:#d9a05b}
  *{box-sizing:border-box}
  body{margin:0;padding:16px;background:var(--bg);color:var(--fg);
       font:15px/1.65 -apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
  .wrap{max-width:680px;margin:0 auto}
  h1{font-size:19px;margin:6px 0 2px;letter-spacing:.2px}
  .sub{color:var(--dim);font-size:13px;margin-bottom:14px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:13px;padding:15px;margin-bottom:12px}
  label{display:block;font-size:12.5px;color:var(--dim);margin:11px 0 5px}
  textarea,input,select{width:100%;background:#0b0d11;color:var(--fg);border:1px solid var(--line);
       border-radius:9px;padding:10px;font:inherit;font-size:14px}
  textarea{min-height:92px;resize:vertical;line-height:1.6}
  .row{display:flex;gap:10px}.row>*{flex:1}
  button{margin-top:13px;width:100%;padding:12px;border:0;border-radius:10px;
       background:var(--brand);color:#0b0d10;font-weight:650;font-size:15px}
  button:active{opacity:.82}
  button.ghost{background:#242a35;color:#d3d9e6}
  audio{width:100%;margin-top:12px}
  .pill{display:inline-block;padding:2px 9px;border-radius:99px;background:#1e242f;font-size:11.5px;margin:0 5px 4px 0}
  .ok{color:var(--ok)}.warn{color:var(--warn)}
  code{background:#0b0d11;border:1px solid var(--line);border-radius:7px;padding:8px 10px;
       font:12.5px ui-monospace,Consolas,monospace;word-break:break-all;display:block;line-height:1.7;margin:0}
  .hint{font-size:12.5px;color:var(--dim);margin:9px 0}
  .log{font:12px ui-monospace,Consolas,monospace;color:var(--dim);margin-top:9px;min-height:18px}
  .lnk{display:flex;gap:8px;align-items:stretch}
  .lnk code{flex:1;min-width:0}
  .lnk .copy{margin:0;width:auto;flex:0 0 auto;padding:0 14px;border-radius:7px;
       background:#242a35;color:#d3d9e6;font-size:13px;font-weight:600}
  .lnk .copy:active{opacity:.82}
  .card h2{font-size:13.5px;margin:0 0 2px;color:#cfd6e4;letter-spacing:.2px}
</style></head><body><div class="wrap">
  <h1>听书 TTS</h1>
  <div class="sub"><span id="engines">检测中…</span></div>

  <div class="card">
    <label>试听文本</label>
    <textarea id="t">夜色一点一点沉下来，巷口的灯还亮着。他站在那儿，忽然觉得这些年像是白走了一趟。</textarea>
    <div class="row">
      <div><label>引擎</label>
        <select id="engine">
          <option value="omni">OmniVoice · 默认</option>
          __SAPI_OPTION__
        </select></div>
      <div><label>语速（阅读内 2.5 为正常）</label>
        <input id="speed" type="number" step="0.1" value="2.5"></div>
    </div>
    <label>音色</label>
    <select id="ref">__REF_OPTIONS__</select>
    <button id="go">合成并播放</button>
    <audio id="au" controls></audio>
    <div class="log" id="log"></div>
  </div>

  <div class="card">
    <h2>导入到「阅读」</h2>
    <label>局域网地址 · 手机和电脑连同一个 Wi-Fi</label>
    <div class="lnk"><code id="lan">__BASE__</code><button class="copy" data-copy="__BASE__">复制</button></div>

    <label>链接 A · 语速 1~100 刻度（10 为正常）</label>
    <div class="lnk"><code id="j1">__BASE__/httpTTS.json</code><button class="copy" data-copy="__BASE__/httpTTS.json">复制</button></div>

    <label>链接 B · 语速 0~2.0 刻度（1.0 为正常）</label>
    <div class="lnk"><code id="j2">__BASE__/httpTTS-float.json</code><button class="copy" data-copy="__BASE__/httpTTS-float.json">复制</button></div>

    <p class="hint">我的 → 朗读引擎 → 右上角菜单 → 网络导入，粘贴上面地址。</p>
  </div>

  <div class="card">
    <label>① 「阅读」朗读引擎里填这个 URL</label>
    <code id="api">__BASE__/api/tts</code>
    <p class="hint">我的 → 朗读引擎 → 右上角 <b>+</b> → 选「自定义 / 在线朗读」。</p>
    <label>② 若用「在线朗读」，粘这一整行</label>
    <code id="tpl"></code>
    <button class="ghost" id="copy">复制这一行</button>
  </div>

  <div class="card">
    <label>离线备用地址（断网也能听）</label>
    <code>__BASE__/api/tts?engine=sapi&amp;text={{encodeURIComponent(speakText)}}</code>
  </div>
</div>
<script>
const $=s=>document.querySelector(s);
const tpl=()=>{
  const b=$("#api").textContent, v=$("#engine").value, r=$("#ref").value;
  $("#tpl").textContent=`${b},{"method":"POST","body":"text={{encodeURIComponent(speakText)}}&engine=${v}&ref=${encodeURIComponent(r)}&speakSpeed={{speakSpeed}}"}`;
};
$("#engine").onchange=tpl; $("#ref").onchange=tpl; tpl();
$("#go").onclick=()=>{
  const u=new URL($("#api").textContent);
  u.searchParams.set("text",$("#t").value);
  u.searchParams.set("engine",$("#engine").value);
  u.searchParams.set("ref",$("#ref").value);
  u.searchParams.set("speakSpeed",$("#speed").value);
  const t0=Date.now();
  $("#log").textContent="合成中…";
  $("#au").src=u.toString()+"&_="+Date.now();
  $("#au").onloadeddata=()=>{ $("#log").textContent=`就绪 ${Date.now()-t0}ms`; };
  $("#au").play().catch(()=>{});
};
$("#copy").onclick=async()=>{
  const ok=await copyText($("#tpl").textContent);
  $("#copy").textContent=ok?"已复制":"长按上方地址手动复制";
  if(ok)setTimeout(()=>{$("#copy").textContent="复制这一行";},1400);
};
document.addEventListener("click",async e=>{
  const b=e.target.closest(".copy[data-copy]"); if(!b) return;
  const old=b.textContent;
  const ok=await copyText(b.dataset.copy);
  b.textContent=ok?"已复制":"长按复制";
  if(!ok){
    const c=b.parentElement.querySelector("code");
    if(c){const r=document.createRange();r.selectNodeContents(c);
          const s=getSelection();s.removeAllRanges();s.addRange(r);}
  }
  if(ok)setTimeout(()=>{b.textContent=old;},1200);
});
// 局域网是 http（非安全上下文），navigator.clipboard 为 undefined，
// 必须先走 execCommand 兜底，否则手机上点复制永远失败。
const copyText=t=>new Promise(res=>{
  const fallback=()=>{
    let ok=false;
    try{
      const ta=document.createElement("textarea");
      ta.value=t; ta.setAttribute("readonly","");
      ta.style.cssText="position:fixed;top:0;left:0;opacity:0;pointer-events:none";
      document.body.appendChild(ta); ta.focus(); ta.select();
      ok=document.execCommand("copy"); ta.remove();
    }catch(err){ ok=false; }
    res(ok);
  };
  if(navigator.clipboard&&window.isSecureContext){
    navigator.clipboard.writeText(t).then(()=>res(true),()=>fallback());
  }else{
    fallback();
  }
});
fetch("/health").then(r=>r.json()).then(j=>{
  const p=[];
  for(const [k,v] of Object.entries(j.engines||{})){
    p.push(`<span class="pill ${v.ok?'ok':'warn'}">${k} ${v.ok?'就绪':'离线'}</span>`);
  }
  p.push(`<span class="pill ok">sapi 就绪</span>`);
  p.push(`<span class="pill">缓存 ${j.cache_files}</span>`);
  $("#engines").innerHTML=p.join("");
}).catch(()=>{$("#engines").textContent="服务异常";});
</script></body></html>"""


@app.on_event("startup")
async def _startup() -> None:
    cleanup_cache()
    log.info("主服务启动 | worker=%s | 默认引擎=%s", WORKERS, DEFAULT_ENGINE)


def _make_dualstack_socket(host: str, port: int):
    """
    手动创建双栈监听 socket。

    为什么不能直接 uvicorn.run(host="::")：
    Windows 的 IPv6 socket 默认 IPV6_V6ONLY=1（Linux 默认 0），
    所以绑 "::" 后只能接受 IPv6，IPv4（127.0.0.1 / 192.168.x.x）全部连不上。
    实测：绑 :: 后本地 curl 127.0.0.1:18760 直接被拒绝。
    这里显式关掉 V6ONLY，再交给 uvicorn。
    """
    import socket
    infos = socket.getaddrinfo(host, port, socket.AF_INET6,
                               socket.SOCK_STREAM, 0, socket.AI_PASSIVE)
    family, socktype, proto, _, sockaddr = infos[0]
    sock = socket.socket(family, socktype, proto)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    except (AttributeError, OSError):
        log.warning("无法关闭 IPV6_V6ONLY，IPv4 可能不通")
    sock.bind(sockaddr)
    sock.set_inheritable(True)
    return sock


def main() -> None:
    ap = argparse.ArgumentParser(description="局域网/公网听书 TTS 主服务")
    # 默认 ::（双栈），同时接受 IPv4 和 IPv6。想只跑 IPv4 就传 --host 0.0.0.0
    ap.add_argument("--host", default="::")
    ap.add_argument("--port", type=int, default=18760)
    args = ap.parse_args()

    if AUTH_TOKEN:
        log.info("鉴权已启用（TTS_TOKEN 已设置）")
    else:
        log.warning("未设置 TTS_TOKEN —— 仅建议在局域网使用；暴露公网前请务必设置")

    if ":" in args.host:
        # 双栈：自己建 socket 并显式关闭 IPV6_V6ONLY，否则 Windows 下 IPv4 不通
        try:
            sock = _make_dualstack_socket(args.host, args.port)
            config = uvicorn.Config(app, log_level="warning", access_log=False)
            server = uvicorn.Server(config)
            log.info("监听 http://[%s]:%d （IPv4+IPv6 双栈）", args.host, args.port)
            server.run(sockets=[sock])
        except OSError as exc:
            # 系统没开 IPv6 / 端口被占：退回纯 IPv4，别让整个服务起不来
            log.warning("双栈监听失败（%s），退回纯 IPv4 0.0.0.0:%d", exc, args.port)
            config = uvicorn.Config(app, host="0.0.0.0", port=args.port,
                                    log_level="warning", access_log=False)
            uvicorn.Server(config).run()
    else:
        log.info("监听 http://%s:%d （仅 IPv4）", args.host, args.port)
        # 注意：必须把 host/port 传给 Config，否则 uvicorn 会用默认 127.0.0.1:8000
        config = uvicorn.Config(app, host=args.host, port=args.port,
                                log_level="warning", access_log=False)
        uvicorn.Server(config).run()


if __name__ == "__main__":
    main()

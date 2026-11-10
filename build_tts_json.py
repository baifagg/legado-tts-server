# -*- coding: utf-8 -*-
"""
生成「朗读引擎」可导入的 httpTTS.json。

格式依据：从本机 legado 真实备份 backup2026-10-14.zip 里的 httpTTS.json 反推得出。
每条字段：concurrentRate / contentType / enabledCookieJar / id / lastUpdateTime / name / url
其中 url 用 legado 的内联写法：  <接口地址>,{"method":"POST","body":"..."}

【重要改动 2026-10-26】原来的 LAN_IP 是硬编码局域网地址，导致通过公网 IPv6
访问 /httpTTS.json 时，导入后的朗读请求仍指向 192.168.x.x —— 外网必然失败。
现在改成 build_items(host, ...)，由服务端按请求的 Host 头动态生成：
  - 用 http://[IPv6]:18760/... 访问 → JSON 里的地址就是该 IPv6，并自动带 fmt=mp3
  - 用 http://192.168.110.57:18760/... 访问 → JSON 里就是局域网地址，用 WAV

用法（本地生成静态文件，仅用于备份/检查）： python build_tts_json.py
运行时真正对外提供的是 server.py 里的 /httpTTS.json 动态端点。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
REFS = BASE / "refs"
NOW_MS = int(time.time() * 1000)

def _detect_lan_ip() -> str:
    """自动探测本机局域网 IP（不硬编码，换机器/换网段都能用）。"""
    import socket

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


LAN_IP = os.environ.get("TTS_LAN_IP") or _detect_lan_ip()
PORT = int(os.environ.get("TTS_PORT", 18760))

# 音色元数据用手写描述，比 manifest 里的机械描述更适合在 App 里辨认
# 2026-11-10：扩充到 20 个音色（删 周野/齐峰/姜月/夏禾/小满，新增 5 个真人 + 6 个设计音色）
VOICE_META = {
    # —— 现有（AISHELL-3）——
    "王宝": ("男 · 新闻播报", "正剧、都市"),
    "云松": ("男 · 温和", "日常、轻松"),
    "陆明": ("男 · 冷静", "悬疑、推理"),
    "老宋": ("男 · 低沉", "历史、武侠"),
    "顾川": ("男 · 严肃", "严肃文学"),
    "沈约": ("女 · 柔和", "言情、都市"),
    "林清": ("女 · 清亮", "现代言情"),
    "苏禾": ("女 · 讲故事", "儿童、故事"),
    "白露": ("女 · 优雅", "古典、文艺"),
    # —— 新增真人（AISHELL-3，补足音域）——
    "秦川": ("男 · 极低音 107Hz", "历史、武侠、老年"),
    "程越": ("男 · 中性 143Hz", "都市、职场"),
    "温言": ("女 · 低音 200Hz", "言情、家庭"),
    "叶澜": ("女 · 中低 203Hz", "年代、回忆"),
    "许南": ("女 · 中音 195Hz", "现代言情"),
    # —— 新增设计音色（中年 / 老年 / 评书）——
    "男中年": ("男 · 中年", "职场、现实主义"),
    "男老年": ("男 · 老年", "历史、传记、回忆录"),
    "男评书": ("男 · 评书", "武侠、评书、章回体"),
    "女中年": ("女 · 中年", "言情、家庭、都市"),
    "女老年": ("女 · 老年", "年代文、亲情"),
    "女评书": ("女 · 评书", "武侠、传奇、故事会"),
}


def api_base(host: str, port: int = PORT, use_mp3: bool = False) -> str:
    """按 host 拼出接口地址。IPv6 字面量必须加方括号。"""
    h = host.strip()
    # 已经是 [xxx] 形式就不重复加
    if ":" in h and not h.startswith("["):
        h = f"[{h}]"
    return f"http://{h}:{port}/api/tts"


def body_for(ref: str, engine: str = "omni", scale: str | None = None,
             use_mp3: bool = False) -> str:
    """构造 legado 的 body 字符串。

    注意两个转义层次：
    1. legado 会先对 url 字段做「模板替换」，{{...}} 被求值；
    2. body 本身是 JSON 字符串，里面的双引号要写成 \\" 。

    scale 显式指定语速刻度：speed = 1~100（10 正常）、float = 0.0~2.0（1.0 正常）
    use_mp3 走 MP3 输出（公网/移动流量省 87%，局域网不用）
    """
    parts = [
        "text={{encodeURIComponent(speakText)}}",
        f"engine={engine}",
        f"ref={ref}",
        "speakSpeed={{speakSpeed}}",
    ]
    if scale:
        parts.append(f"scale={scale}")
    if use_mp3:
        parts.append("fmt=mp3")
    return "&".join(parts)


def entry(name: str, base: str, ref: str, engine: str = "omni", idx: int = 0,
          scale: str | None = None, use_mp3: bool = False) -> dict:
    body = body_for(ref, engine, scale, use_mp3)
    url = (
        f'{base},{{"method":"POST",'
        f'"body":"{body.replace(chr(34), chr(92) + chr(34))}"}}'
    )
    return {
        # MP3 时 contentType 仍写 audio/wav 会更安全：legado 对 mp3 的匹配
        # 依赖服务端返回头，这里保持 wav 声明在部分版本反而导致不播放，
        # 所以按实际输出格式声明。
        "concurrentRate": "0",
        "contentType": "audio/mpeg" if use_mp3 else "audio/wav",
        "enabledCookieJar": False,
        "id": int(f"{int(time.time())}{idx:03d}"),
        "lastUpdateTime": NOW_MS,
        "name": name,
        "url": url,
    }


def build_items(host: str, port: int = PORT, scale: str | None = None,
                use_mp3: bool = False,
                manifest: list[dict] | None = None) -> list[dict]:
    """按指定 host 生成一套朗读引擎条目。"""
    if manifest is None:
        manifest = json.loads((REFS / "manifest.json").read_text(encoding="utf-8"))
    base = api_base(host, port, use_mp3)
    items: list[dict] = []

    for i, m in enumerate(manifest):
        key = m["key"]
        desc, _ = VOICE_META.get(key, (m.get("desc", ""), m.get("usage", "")))
        items.append(entry(f"{key} · {desc}", base, key, "omni", i,
                           scale=scale, use_mp3=use_mp3))

    # 断网兜底（本机 SAPI，不认音色，也不需要 mp3 协商）
    sapi_body = "text={{encodeURIComponent(speakText)}}&engine=sapi"
    items.append({
        "concurrentRate": "0",
        "contentType": "audio/wav",
        "enabledCookieJar": False,
        "id": int(f"{int(time.time())}{len(items):03d}"),
        "lastUpdateTime": NOW_MS,
        "name": "断网兜底 · 本机离线",
        "url": f'{base},{{"method":"POST","body":"{sapi_body}"}}',
    })
    return items


def main() -> None:
    """本地生成两份静态文件（以局域网地址为准），用于备份与检查。"""
    for fname, scale, use_mp3, desc in [
        ("httpTTS.json", None, False, "1~100 刻度，局域网 WAV"),
        ("httpTTS-float.json", "float", False, "0~2.0 刻度，局域网 WAV"),
    ]:
        data = build_items(LAN_IP, PORT, scale=scale, use_mp3=use_mp3)
        out = BASE / fname
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已生成 {out}  [{desc}]  共 {len(data)} 条")

    print()
    print("=" * 64)
    print("  运行时真正对外提供的（按访问方式自动适配）：")
    print("=" * 64)
    print(f"  局域网  http://{LAN_IP}:{PORT}/httpTTS-float.json")
    print(f"  公网    http://[<本机IPv6>]:{PORT}/httpTTS-float.json")
    print()
    print("  公网访问时 JSON 里的地址会自动换成该 IPv6，并带 &fmt=mp3")
    print("=" * 64)


if __name__ == "__main__":
    main()

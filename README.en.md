<div align="center">

<img src="docs/assets/banner.svg" alt="Legado TTS Server" width="100%">

# Legado TTS Server

**Turn your own PC into a private audiobook server.**

by [baifagg](https://github.com/baifagg) · pairs with [开源轻页 qingye-reader](https://github.com/wip3l/qingye-reader)

[简体中文](README.md) · [English](README.en.md)

![Python](https://img.shields.io/badge/Python-3.10–3.12-1f2d3d?style=flat-square)
![License](https://img.shields.io/badge/Code-MIT-1f2d3d?style=flat-square)
![Model](https://img.shields.io/badge/Model-CC--BY--NC-d9a05b?style=flat-square)
![Platform](https://img.shields.io/badge/Windows%20%7C%20macOS%20%7C%20Linux-1f2d3d?style=flat-square)

</div>

---

A local, GPU-accelerated TTS backend for the **Legado / 阅读** Android reading app.
The model runs on your own graphics card — no text leaves your machine, and you don't pay per character.

It ships with **OmniVoice 0.8B**, 20 Chinese voices, and stays smooth up to 4× playback speed.
At the common 2.14× listening speed it synthesizes roughly **twice as fast as playback**.

> Works with the **Legado (阅读) Android app**, **HarmonyOS reading apps built on Legado**,
> and **[开源轻页 qingye-reader](https://github.com/wip3l/qingye-reader)** — plus any other
> reader that implements Legado's TTS engine protocol (test those yourself).
> See [Client compatibility](#client-compatibility).

```
Legado app ──── HTTP ────> localhost:18760 ────> OmniVoice (GPU)
                                ↓
                    text normalizer · cache · 20 voices
```

---

## Why use it

| | |
|---|---|
| **Fully local** | Text and audio never leave your machine. Works offline (Windows SAPI fallback) |
| **20 Chinese voices** | 14 real-speaker clones + 6 designed (middle-aged / elderly / storyteller). Male range spans 107–204 Hz instead of sounding like one person |
| **Keeps up with playback** | 1.1–1.5 s per sentence at 2.14× — about 2× faster than playback |
| **No dropped characters** | Model-side speed capped at 1.6× with ffmpeg compensating. CER ≤ 0.06 across 25 voices |
| **Correct Chinese numerals** | `1/3` → "三分之一", `15%` → "百分之十五", `3500` → "三千五百" |
| **Two commands to run** | `python install.py` → `python run.py` |

## Interface

<div align="center">
<img src="docs/assets/console.png" alt="Web console: audition voices and copy the import link" width="88%">
</div>

Open `http://<your-ip>:18760/` and you get this page: audition any voice, check service status, copy the import link in one click.

---

## Quick start

```bash
git clone <this-repo> legado-tts-server
cd legado-tts-server

python install.py     # detects your GPU, builds a venv, installs deps, downloads the model (~6 GB)
python run.py         # start
```

> Behind the Great Firewall? Add `--mirror` to use Tsinghua PyPI and hf-mirror:
> `python install.py --mirror`

On startup the terminal prints:

```
============================================================
  Desktop:  http://127.0.0.1:18760/
  Phone:    http://192.168.1.23:18760/   (same Wi-Fi required)
  Import link A (0–2.0 speed scale, recommended): http://192.168.1.23:18760/httpTTS-float.json
  Import link B (1–100 speed scale):              http://192.168.1.23:18760/httpTTS.json
============================================================
```

**On the phone**: Legado → Mine → Reading engines → top-right menu → **Network import** → paste link A.
That imports all 20 voices plus one offline fallback in a single step.

---

## Client compatibility

This server implements **Legado's HTTP TTS / reading-engine protocol**, so it isn't tied to one app.

| Client | Status | Notes |
|---|---|---|
| **Legado (阅读) native app**, Android | ✅ Supported | Primary test target; import as described above |
| **HarmonyOS reading apps built on Legado** | ✅ Protocol-compatible | Same reading-engine import flow, same link |
| **[开源轻页 qingye-reader](https://github.com/wip3l/qingye-reader)** (HarmonyOS NEXT) | ✅ Supported | Native HarmonyOS reader, compatible with Legado book-source rules; import the same `httpTTS.json` |
| **Other reading apps** | ⚠️ **Test it yourself** | Not verified here; works only if the app implements Legado's TTS engine protocol |

### Recommended clients

**开源轻页 (qingye-reader)** — the recommended companion on HarmonyOS NEXT devices:
[github.com/wip3l/qingye-reader](https://github.com/wip3l/qingye-reader)

- Native HarmonyOS reading app written in ArkTS / ArkUI, for phones, tablets and PC/2-in-1
- Compatible with Legado (阅读) book-source rules, with its own native book-source format
- Supports local TXT / EPUB / MOBI / PDF, OPDS, WebDAV and LAN libraries
- Built-in TTS that can point straight at this server's reading-engine endpoint
- GPL-3.0, docs at <https://manual.icynol.com/>

The two are complementary: 轻页 decides *what* you read and how you navigate it;
this server decides *how it sounds* — which voice, how accurate, how fast.

**阅读 / Legado (original)** — the veteran Android reader this server was primarily built for:
[github.com/gedoor/legado](https://github.com/gedoor/legado)

**How to tell whether an app will work** — check its reading-engine settings for either of these:

1. **"Network import"** that accepts the `httpTTS.json` format
   → paste `http://<your-ip>:18760/httpTTS-float.json`
2. **"Custom / online TTS"** where you can supply a URL and a request body
   → URL `http://<your-ip>:18760/api/tts`, body
   `text={{encodeURIComponent(speakText)}}&engine=omni&ref=王宝&speakSpeed={{speakSpeed}}&scale=float`
   (replace `ref=王宝` with the voice you want; one entry per voice)

Both supported → just use it. Only the second → create voices manually.

> **What "test it yourself" means**: only the three clients marked ✅ above have been verified.
> Other apps that reuse Legado's reading-engine implementation (most Chinese "阅读" forks do)
> will most likely work, but you need to try. Apps with their own proprietary TTS interface
> won't work with this server.
>
> Quick self-test: after wiring it up, play a passage containing numbers (e.g. "15%", "1/3").
> If you hear audio and it reads "百分之十五" / "三分之一", the integration is good.

---

## Hardware

| Hardware | Works? | Per 25-char sentence | Notes |
|---|---|---|---|
| **NVIDIA, ≥ 6 GB VRAM** | ✅ Recommended | 1.0–1.5 s | RTX 3060 / 4060 / 2060 and up |
| NVIDIA, 4–6 GB | ⚠️ Runs | 2–5 s | May hit VRAM paging; keep sentences short |
| **Apple Silicon (M1–M4)** | ✅ Great | 1.5–3 s | Uses MPS; unified memory removes the VRAM ceiling |
| AMD (Linux + ROCm) | ⚠️ Tinkering | 1.5–4 s | See the compatibility doc |
| Intel Arc / iGPU | ⚠️ Partial | 3–8 s | XPU or CPU |
| **CPU only** | ✅ Runs | 5–15 s | Fine for testing; keep speed under ~1.2× |

**Disk**: about 6 GB (2.5 GB deps + 3.1 GB model), plus an audio cache that expires after 30 days.

Per-GPU support matrix, vendor-specific install notes and measured VRAM usage: [docs/GPU-COMPATIBILITY.md](docs/GPU-COMPATIBILITY.md)

---

## Voices

**Male (10)**: 王宝 (news anchor) · 云松 (warm) · 陆明 (calm) · 老宋 (deep) · 顾川 (serious) · 秦川 (very low, 107 Hz) · 程越 (neutral) · 男中年 · 男老年 · 男评书

**Female (10)**: 沈约 (soft) · 林清 (bright) · 苏禾 (storytelling) · 白露 (elegant) · 温言 (low, 200 Hz) · 叶澜 (mid-low) · 许南 (mid) · 女中年 · 女老年 · 女评书

Which voice fits which genre, and where each one came from: [docs/VOICES.md](docs/VOICES.md)

---

## Commands

| Command | What it does |
|---|---|
| `python install.py` | Install or repair the environment (idempotent) |
| `python install.py --mirror` | Faster installs in mainland China |
| `python run.py` | Start with a supervisor that restarts crashed processes |
| `python run.py --port 18860` | Use a different port |
| `python run.py --device cpu` | Force CPU |
| `python doctor.py` | **Environment check — run this first when something breaks** |
| `python doctor.py --smoke` | Check plus one real synthesis |
| `python doctor.py --json` | Machine-readable output |

---

## Documentation

| Doc | Contents |
|---|---|
| [AGENTS.md](AGENTS.md) | **Deployment and troubleshooting manual written for AI agents**: step-by-step commands, acceptance checks, fault tree, hard "do not touch" list |
| [docs/GPU-COMPATIBILITY.md](docs/GPU-COMPATIBILITY.md) | Vendor support, VRAM thresholds, install differences |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | End-user troubleshooting: phone can't connect, import fails, playback stutters |
| [docs/TUNING.md](docs/TUNING.md) | Where every default came from, and the optimizations that turned out to be dead ends |
| [docs/VOICES.md](docs/VOICES.md) | Voice parameters and selection guide |
| [docs/API.md](docs/API.md) | HTTP API and Legado import format |

> Deploying with an AI agent? Hand it [AGENTS.md](AGENTS.md) — every step lists the expected output and what to do when it fails.

---

## Layout

```
legado-tts-server/
├── install.py              one-shot installer (detect GPU → venv → deps → model)
├── run.py                  launcher (worker + server, with restart supervision)
├── doctor.py               environment diagnostics
├── server.py               main service :18760 (routing / cache / text front-end)
├── worker.py               model process :18770 (OmniVoice inference + speed handling)
├── text_front.py           Chinese numerals, dates, currency → spoken form
├── build_tts_json.py       generates the Legado import JSON
├── refs/                   20 reference clips + manifest.json
├── tests/                  40 unit tests (no GPU needed)
├── docs/                   documentation and assets
└── models/                 model weights (downloaded by install.py, not committed)
```

---

## License and compliance

- **This project's code**: [MIT](LICENSE)
- **OmniVoice weights**: **CC-BY-NC — non-commercial**. Personal use, research and internal tooling are fine; selling access or embedding it in a commercial product requires a separate license
- **Reference voices**: from [AISHELL-3](https://www.openslr.org/93/) (Apache-2.0), redistributable
- **The 6 voice-design voices**: model outputs, therefore also bound by CC-BY-NC

Full attribution in [NOTICE](NOTICE). Read it before any commercial use.

---

## Credits

- [k2-fsa/OmniVoice](https://github.com/k2-fsa/OmniVoice) — 0.8B multilingual zero-shot TTS model
- [AISHELL-3](https://www.openslr.org/93/) — Mandarin multi-speaker corpus (source of the reference voices)
- [gedoor/legado](https://github.com/gedoor/legado) — the open-source reader whose TTS engine protocol this project implements
- [wip3l/qingye-reader](https://github.com/wip3l/qingye-reader) — 开源轻页, a native HarmonyOS NEXT reading app

<div align="center">
<sub>If this saved you a subscription, a star is appreciated.</sub>
</div>

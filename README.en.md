<div align="center">

<img src="docs/assets/banner.svg" alt="Legado TTS Server" width="100%">

# Legado TTS Server

by [baifagg](https://github.com/baifagg)

[简体中文](README.md) · [English](README.en.md)

</div>

---

## What it is

A Chinese text-to-speech service that runs on your own computer. A reading app on your phone sends text over Wi-Fi, the computer synthesizes it on the GPU and sends the audio back. About a second per sentence.

The model is OmniVoice 0.8B with 20 Chinese voices. The server implements Legado's reading-engine protocol, so any reader that speaks that protocol can use it.

## Install

Python 3.10 to 3.12.

```bash
git clone https://github.com/baifagg/legado-tts-server
cd legado-tts-server
python install.py
```

The script checks your GPU, installs the matching PyTorch build, then downloads the model (about 3 GB). Add `--mirror` if you're in mainland China.

Then run the check:

```bash
python doctor.py
```

Fix anything marked red before moving on. Run it again whenever something breaks; the output usually says what to do. Longer troubleshooting notes are in [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md), and the agent-facing deployment manual is [AGENTS.md](AGENTS.md).

On the GPU side: NVIDIA with 6 GB or more VRAM works best. Apple Silicon runs on MPS. AMD works through ROCm on Linux only; on Windows it falls back to CPU. CPU-only is usable but slow, five to fifteen seconds per sentence. Per-model details in [docs/GPU-COMPATIBILITY.md](docs/GPU-COMPATIBILITY.md).

## Usage

```bash
python run.py
```

The terminal prints two import links. Put the phone on the same Wi-Fi, open the reading app's reading-engine settings, choose network import, paste a link.

- Link A: `http://<your-ip>:18760/httpTTS-float.json`, speed scale 0–2.0
- Link B: `http://<your-ip>:18760/httpTTS.json`, speed scale 1–100

Most recent builds of Legado use A. **开源轻页 also uses A, not B.**

One import gives you 20 voices, switchable in the reading-engine list. Ten male and ten female, including middle-aged, elderly and storyteller voices. Genre suggestions are in [docs/VOICES.md](docs/VOICES.md).

Changing ports or forcing CPU:

```bash
python run.py --port 18860
python run.py --device cpu
```

Open `http://127.0.0.1:18760/` in a browser to audition each voice before picking one:

<img src="docs/assets/console.png" alt="Audition page" width="620">

API parameters and error codes are in [docs/API.md](docs/API.md).

## Related projects

[Legado (阅读)](https://github.com/gedoor/legado) is the original implementation of the protocol, a long-standing Android reader. This service started as a way to listen to books in it.

[开源轻页 / qingye-reader](https://github.com/wip3l/qingye-reader) is a native HarmonyOS NEXT reading app written in ArkTS and ArkUI. It's compatible with Legado book sources and handles local TXT, EPUB, MOBI, PDF, plus OPDS and WebDAV. It has built-in TTS and uses link A with this server.

Other readers work too if they implement Legado's reading-engine protocol. Try it and see: after importing, play a passage with numbers, and if you hear "百分之十五" rather than "十五", it's wired up correctly.

## Credits

Model by [k2-fsa/OmniVoice](https://github.com/k2-fsa/OmniVoice). Reference voices come from [AISHELL-3](https://www.openslr.org/93/). The protocol follows [gedoor/legado](https://github.com/gedoor/legado).

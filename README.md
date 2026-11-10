<div align="center">

<img src="docs/assets/banner.svg" alt="Legado TTS Server" width="100%">

# Legado TTS Server

**把你自己的电脑，变成一台私有有声书服务器。**

[简体中文](README.md) · [English](README.en.md)

![Python](https://img.shields.io/badge/Python-3.10–3.12-1f2d3d?style=flat-square)
![License](https://img.shields.io/badge/代码-MIT-1f2d3d?style=flat-square)
![Model](https://img.shields.io/badge/模型-CC--BY--NC-d9a05b?style=flat-square)
![Platform](https://img.shields.io/badge/Windows%20%7C%20macOS%20%7C%20Linux-1f2d3d?style=flat-square)

</div>

---

给手机「阅读 / Legado」提供**本地大模型 TTS**：模型跑在你自己的显卡上，文本不出网，不按字数计费。

内置 **OmniVoice 0.8B**，20 个中文音色，支持到 4 倍速听书，实测 **2.14× 时合成速度是播放的 2 倍**。

```
手机「阅读」 ──── HTTP ────> 本机 :18760 ────> OmniVoice（GPU）
                              ↓
                        文本前端 · 缓存 · 20 音色
```

---

## 为什么值得用

| | |
|---|---|
| **完全本地** | 文本和音频都不离开你的电脑。断网也能听（有 Windows SAPI 兜底） |
| **20 个中文音色** | 14 个真人克隆 + 6 个中年/老年/评书，男声覆盖 107–204 Hz，不再是"一个音" |
| **真的跟得上** | 2.14× 听书时单句 1.1–1.5 秒，合成比播放快约 1 倍 |
| **不漏字** | 模型侧限速 1.6× + ffmpeg 补速，25 音色实测 CER ≤ 0.06 |
| **中文读法正确** | `1/3` 读"三分之一"、`15%` 读"百分之十五"、`3500` 读"三千五百" |
| **两条命令跑起来** | `python install.py` → `python run.py` |

## 界面

<div align="center">
<img src="docs/assets/console.png" alt="Web 控制台：试听 + 一键复制导入链接" width="88%">
</div>

浏览器打开 `http://<本机IP>:18760/` 就是这个页面：试听任意音色、看服务状态、一键复制导入链接。

---

## 快速开始

```bash
git clone <本仓库地址> legado-tts-server
cd legado-tts-server

python install.py     # 自动探测显卡 → 建环境 → 装依赖 → 下模型（约 6 GB）
python run.py         # 启动
```

> 国内网络建议加 `--mirror`（清华 pip 源 + hf-mirror 模型源）：
> `python install.py --mirror`

启动后终端会打印：

```
============================================================
  电脑访问：   http://127.0.0.1:18760/
  手机访问：   http://192.168.1.23:18760/   （需同一 WiFi）
  导入链接 A（0~2.0 语速刻度，推荐）：http://192.168.1.23:18760/httpTTS-float.json
  导入链接 B（1~100 语速刻度）：      http://192.168.1.23:18760/httpTTS.json
============================================================
```

**手机端**：阅读 → 我的 → 朗读引擎 → 右上角菜单 → **网络导入** → 粘贴链接 A。一次导入 20 个音色 + 1 条断网兜底。

---

## 硬件要求

| 硬件 | 能不能跑 | 单句 25 字 | 说明 |
|---|---|---|---|
| **NVIDIA ≥ 6 GB 显存** | ✅ 推荐 | 1.0–1.5 s | RTX 3060 / 4060 / 2060 及以上 |
| NVIDIA 4–6 GB | ⚠️ 能跑 | 2–5 s | 可能触发显存换页，建议分句调短 |
| **Apple Silicon（M1–M4）** | ✅ 很好 | 1.5–3 s | 走 MPS，统一内存不挑显存 |
| AMD（Linux + ROCm） | ⚠️ 需折腾 | 1.5–4 s | 见兼容性文档 |
| Intel Arc / 核显 | ⚠️ 部分可用 | 3–8 s | 走 XPU 或 CPU |
| **纯 CPU** | ✅ 能跑 | 5–15 s | 只适合试听，建议 1.2× 以内 |

**磁盘**约 6 GB（依赖 2.5 GB + 模型 3.1 GB），另加音频缓存（30 天自动过期）。

完整的显卡型号对照、各品牌安装差异、显存实测：[docs/GPU-COMPATIBILITY.md](docs/GPU-COMPATIBILITY.md)

---

## 音色

**男声 10**：王宝（新闻播报）· 云松（温和）· 陆明（冷静）· 老宋（低沉）· 顾川（严肃）· 秦川（极低音 107 Hz）· 程越（中性）· 男中年 · 男老年 · 男评书

**女声 10**：沈约（柔和）· 林清（清亮）· 苏禾（讲故事）· 白露（优雅）· 温言（低音 200 Hz）· 叶澜（中低）· 许南（中音）· 女中年 · 女老年 · 女评书

按题材怎么选、每个音色怎么来的：[docs/VOICES.md](docs/VOICES.md)

---

## 常用命令

| 命令 | 作用 |
|---|---|
| `python install.py` | 一键安装 / 修复环境（幂等，可重复跑） |
| `python install.py --mirror` | 国内网络加速 |
| `python run.py` | 启动（自动守护，崩了会重启） |
| `python run.py --port 18860` | 换端口 |
| `python run.py --device cpu` | 强制用 CPU |
| `python doctor.py` | **环境诊断，出问题先跑这个** |
| `python doctor.py --smoke` | 诊断 + 真跑一次合成 |
| `python doctor.py --json` | 机器可读输出 |

---

## 文档

| 文档 | 内容 |
|---|---|
| [AGENTS.md](AGENTS.md) | **给 AI Agent 的部署与排查手册**：分步命令、验收清单、故障树、禁止事项 |
| [docs/GPU-COMPATIBILITY.md](docs/GPU-COMPATIBILITY.md) | 各家显卡支持情况、显存门槛、安装差异 |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | 用户向排障：手机连不上、导入失败、变慢 |
| [docs/TUNING.md](docs/TUNING.md) | 每个默认参数怎么来的，以及试过但没用的优化 |
| [docs/VOICES.md](docs/VOICES.md) | 20 个音色的参数与选型建议 |
| [docs/API.md](docs/API.md) | HTTP 接口、Legado 导入格式 |

> 用 AI Agent 部署？直接把 [AGENTS.md](AGENTS.md) 丢给它，里面写了每一步的预期输出和出错了怎么办。

---

## 目录结构

```
legado-tts-server/
├── install.py              一键安装（探测硬件 → 建 venv → 装依赖 → 下模型）
├── run.py                  启动器（拉起 worker + server，自动守护）
├── doctor.py               环境诊断
├── server.py               主服务 :18760（路由 / 缓存 / 文本前端）
├── worker.py               模型进程 :18770（OmniVoice 推理 + 变速）
├── text_front.py           中文数字/日期/金额 → 口语化
├── build_tts_json.py       生成 Legado 导入 JSON
├── refs/                   20 个音色参考音频 + manifest.json
├── tests/                  40 个单元测试（不需要显卡）
├── docs/                   文档与素材
└── models/                 模型权重（install.py 下载，不入库）
```

---

## 许可与合规

- **本项目代码**：[MIT](LICENSE)
- **OmniVoice 权重**：**CC-BY-NC —— 不可商用**。个人听书、研究、自用没问题；对外收费或嵌入商业产品需另行授权
- **参考音色**：来自 [AISHELL-3](https://www.openslr.org/93/)（Apache-2.0），可再分发
- **voice-design 生成的 6 个音色**：随模型许可受 CC-BY-NC 约束

完整声明见 [NOTICE](NOTICE)。商用前请务必读一遍。

---

## 致谢

[k2-fsa/OmniVoice](https://github.com/k2-fsa/OmniVoice) · [AISHELL-3](https://www.openslr.org/93/) · [Legado 阅读](https://github.com/gedoor/legado)

<div align="center">
<sub>如果这个项目帮你省下了订阅费，欢迎点个 Star。</sub>
</div>

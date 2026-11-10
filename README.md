# Legado TTS Server · 本地听书语音服务

给手机「阅读 / Legado」提供**本地大模型 TTS**：不联网、不花钱、20 个中文音色、支持 4 倍速听书。

```
手机「阅读」 ──HTTP──> 本机服务 :18760 ──> OmniVoice 0.8B（GPU）
```

- **完全本地**：模型在你自己的电脑上跑，文本不出网
- **20 个中文音色**：真人克隆 + 中年/老年/评书
- **速度跟得上**：2.14× 听书时，合成比播放快约 1 倍
- **不漏字**：模型侧限速 + ffmpeg 补速，实测 25 音色 CER ≤ 0.06
- **中文读法正确**：`1/3` 读"三分之一"、`15%` 读"百分之十五"、`3500` 读"三千五百"

---

## 30 秒开始

```bash
git clone <本仓库地址> legado-tts-server
cd legado-tts-server

python install.py          # 自动探测显卡 → 建环境 → 装依赖 → 下模型（约 6 GB，首次 10–30 分钟）
python run.py              # 启动

# 国内网络建议：
# python install.py --mirror
```

启动后终端会打印：

```
导入链接 A（0~2.0 语速刻度，推荐）：http://192.168.1.23:18760/httpTTS-float.json
导入链接 B（1~100 语速刻度）：      http://192.168.1.23:18760/httpTTS.json
```

**手机端**：「阅读」→ 我的 → 朗读引擎 → 右上角菜单 → **网络导入** → 粘贴链接 A。
一次导入 **20 个音色 + 1 条断网兜底**。

> 手机和电脑必须在**同一个 WiFi**。第一次导不进去，先看 [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md#导入失败)。

---

## 硬件要求

| 硬件 | 能不能跑 | 单句耗时（25 字） | 说明 |
|---|---|---|---|
| **NVIDIA 显卡 ≥ 6 GB 显存** | ✅ 推荐 | 1.0–1.5 s | RTX 3060 / 4060 / 2060 及以上都行 |
| NVIDIA 显卡 4–6 GB | ⚠️ 能跑 | 2–5 s | 可能触发显存换页，建议把分句调短 |
| **Apple Silicon（M1–M4）** | ✅ 很好 | 1.5–3 s | 走 MPS，内存统一编址不挑显存 |
| AMD 显卡（Linux + ROCm） | ⚠️ 需折腾 | 1.5–4 s | 见 [docs/GPU-COMPATIBILITY.md](docs/GPU-COMPATIBILITY.md) |
| Intel Arc / 核显 | ⚠️ 部分可用 | 3–8 s | 走 XPU 或 CPU |
| **纯 CPU（无独显）** | ✅ 能跑 | 5–15 s | 听得下去但会明显落后于播放，建议 1.5× 以内 |

完整兼容性矩阵、各品牌安装差异、显存实测数据：[docs/GPU-COMPATIBILITY.md](docs/GPU-COMPATIBILITY.md)

**磁盘**：约 6 GB（依赖 2.5 GB + 模型 3.1 GB + 音色 3.5 MB），另加缓存（听过的句子会缓存，30 天过期）。

---

## 常用命令

| 命令 | 作用 |
|---|---|
| `python install.py` | 一键安装 / 修复环境（幂等，可重复跑） |
| `python install.py --mirror` | 国内网络（清华 pip 源 + hf-mirror 模型源） |
| `python run.py` | 启动服务（自动守护，崩了会重启） |
| `python run.py --port 18860` | 换端口（worker 自动用 +10） |
| `python doctor.py` | **环境诊断**，出问题第一个跑这个 |
| `python doctor.py --smoke` | 诊断 + 真跑一次合成（约 1 分钟） |
| `python doctor.py --json` | 机器可读输出（给 AI agent 用） |

---

## 音色

**男声（10）**：王宝（新闻播报）、云松（温和）、陆明（冷静）、老宋（低沉）、顾川（严肃）、秦川（极低音 107Hz）、程越（中性 143Hz）、男中年、男老年、男评书

**女声（10）**：沈约（柔和）、林清（清亮）、苏禾（讲故事）、白露（优雅）、温言（低音 200Hz）、叶澜（中低 203Hz）、许南（中音 195Hz）、女中年、女老年、女评书

按题材怎么选、每个音色的客观参数：[docs/VOICES.md](docs/VOICES.md)

---

## 服务接口

| 端点 | 用途 |
|---|---|
| `GET /` | 网页控制台（试听、健康状态、导入链接） |
| `POST /api/tts` | 合成（Legado 调的就是这个） |
| `GET /api/voices` | 音色清单 JSON |
| `GET /httpTTS-float.json` | Legado 导入用（0~2.0 语速刻度） |
| `GET /httpTTS.json` | Legado 导入用（1~100 语速刻度） |
| `GET /health` | 健康检查 |

参数、返回头、错误码：[docs/API.md](docs/API.md)

---

## 给 AI Agent 的部署手册

本项目自带一份**专门写给 agent 的**操作手册：

👉 **[AGENTS.md](AGENTS.md)**

里面有：可直接复制执行的分步部署命令、每步的预期输出、故障树（症状 → 根因 → 修复）、以及**绝对不能改的参数**。

---

## 目录结构

```
legado-tts-server/
├── install.py          一键安装（探测硬件 → 建 venv → 装依赖 → 下模型）
├── run.py              启动器（拉起 worker + server，守护重启）
├── doctor.py           环境诊断（部署前/出问题先跑这个）
├── server.py           主服务 :18760（路由 / 缓存 / 文本处理）
├── worker.py           模型进程 :18770（OmniVoice 推理）
├── text_front.py       中文数字/日期/金额 → 口语化文本
├── build_tts_json.py   生成 Legado 导入 JSON
├── refs/               20 个音色的参考音频 + manifest.json
├── tests/              单元测试（python -m unittest discover -s tests）
├── docs/               兼容性 / 调参 / 排查 / 音色 / API
└── models/             模型权重（install.py 下载，不入库）
```

---

## 许可与合规（重要）

- **本项目代码**：MIT（见 [LICENSE](LICENSE)）
- **模型权重 OmniVoice**：**CC-BY-NC** —— **不可商用**。个人听书、研究、自用没问题；如果要用于商业产品或对外收费服务，必须换模型或取得授权。
- **参考音色**：来自 [AISHELL-3](https://www.openslr.org/93/)（Apache-2.0），可再分发，需保留署名。
- **voice-design 生成的 6 个音色**（男中年/男老年/男评书/女中年/女老年/女评书）：是 OmniVoice 的输出，随模型许可一并受 CC-BY-NC 约束。

详见 [NOTICE](NOTICE)。

---

## 致谢

- [k2-fsa/OmniVoice](https://github.com/k2-fsa/OmniVoice) —— 0.8B 多语言零样本 TTS 模型（Apache-2.0 代码）
- [AISHELL-3](https://www.openslr.org/93/) —— 中文多说话人语料（Apache-2.0）
- [Legado / 阅读](https://github.com/gedoor/legado) —— 开源阅读器

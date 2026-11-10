<div align="center">
<sub><a href="../README.md">← 返回主页</a> · <a href="../README.en.md">English</a></sub>
</div>

# 显卡兼容性 / 性能对照

> 一句话：**有 NVIDIA 显卡最省事；Apple Silicon 体验很好；AMD 要折腾；Intel 和纯 CPU 能跑但慢。**
>
> 本页给的是**判断标准 + 安装命令**，不是营销数字。实测数据都标了测试条件。

---

## 0. 30 秒判断你的机器

```bash
# Windows
nvidia-smi
# Linux
nvidia-smi  ||  lspci | grep -i vga
# macOS
system_profiler SPDisplaysDataType | grep -E "Chipset|VRAM|Cores"
```

| 输出 | 你的情况 | 跳到 |
|---|---|---|
| 有 `NVIDIA GeForce/RTX/Quadro` + 显存 ≥ 6 GB | ✅ 最舒服 | [§1](#1-nvidia--cuda推荐) |
| 有 NVIDIA 但显存 4–6 GB | ⚠️ 能跑，注意分句 | [§1.3](#13-显存偏小46-gb) |
| 有 NVIDIA 但显存 < 4 GB | ⚠️ 建议 CPU | [§5](#5-纯-cpu--核显) |
| Apple M1/M2/M3/M4 | ✅ 很好 | [§2](#2-apple-silicon--mps) |
| AMD Radeon（Windows） | ⚠️ 只能用 CPU | [§5](#5-纯-cpu--核显) |
| AMD Radeon（Linux） | ⚠️ ROCm 可试 | [§3](#3-amd--rocmlinux-才有) |
| Intel Arc / 核显 | ⚠️ XPU 或 CPU | [§4](#4-intel--xpu--核显) |
| 没有独显 | ✅ 能跑，慢 | [§5](#5-纯-cpu--核显) |

**显存最低要求**：模型常驻约 **3.6 GB**（fp16）。低于这个数会触发显存换页，速度掉一个数量级。

---

## 1. NVIDIA + CUDA（推荐）

### 1.1 支持范围

| 架构 | 代表型号 | 计算能力 | 支持 |
|---|---|---|---|
| Ada Lovelace | RTX 4090 / 4080 / 4070 / 4060 | sm_89 | ✅ 最佳 |
| Ampere | RTX 3090 / 3080 / 3070 / 3060 / A100 | sm_80 / sm_86 | ✅ 实测环境（RTX 3060 Laptop） |
| Turing | RTX 2080 / 2070 / 2060 / T4 | sm_75 | ✅ |
| Volta | V100 | sm_70 | ✅ |
| Pascal | GTX 1080 Ti / 1080 / 1070 | sm_61 | ⚠️ 能用，fp16 慢，建议 `--dtype float32` |
| Maxwell 及更老 | GTX 9xx | sm_5x | ❌ 不支持 |

### 1.2 安装

```bash
python install.py
```

会自动检测到 NVIDIA 并用 **CUDA 12.8** 索引：

```
pip install torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
```

驱动要求：**≥ 525**（CUDA 12.x）。驱动太老会装成功但 `cuda_available=False`。

### 1.3 显存偏小（4–6 GB）

能跑，但注意：

| 症状 | 原因 | 缓解 |
|---|---|---|
| 长句突然变很慢 | 显存不够，权重换页到内存 | Legado 里把「单句长度」调小（20–30 字） |
| `CUDA out of memory` | 峰值超了 | 关掉浏览器硬件加速、其他 AI 程序 |
| 每句都要 5 s+ | 持续换页 | 改用 CPU（`python run.py --device cpu`），反而更稳定 |

**实测参考**（RTX 3060 Laptop 12 GB，25 字句，2.14× 倍速）：

| 指标 | 值 |
|---|---|
| 单句合成 | 1.0–1.6 s |
| 模型加载 | 5–10 s |
| 常驻显存 | 3.5–4.0 GB |
| 满载功耗 | 约 70–80 W（笔记本卡） |

---

## 2. Apple Silicon + MPS

### 2.1 支持范围

| 芯片 | 统一内存 | 体验 |
|---|---|---|
| M4 / M4 Pro / M4 Max | 16–128 GB | ✅ 最快 |
| M3 / M3 Pro / M3 Max | 8–128 GB | ✅ 很好 |
| M2 / M2 Pro / M2 Max | 8–96 GB | ✅ 很好 |
| M1 / M1 Pro / M1 Max | 8–64 GB | ✅ 可用 |

**关键优势**：Apple Silicon 是统一内存架构，**没有"显存不足"概念**——只要系统内存够（≥ 8 GB 空闲），模型就能跑。

### 2.2 安装

```bash
python install.py
```

在 `Darwin arm64` 上会自动用 PyPI 默认 torch（内含 MPS 后端），不指定 CUDA 索引。

### 2.3 已知限制

| 项 | 说明 |
|---|---|
| fp16 支持 | M1 及以后支持；`pick_dtype()` 会自动选 fp16 |
| 首次运行慢 | MPS 内核编译，第一句可能 10–20 s，之后正常 |
| 部分算子回落 CPU | 偶发警告 `MPS backend does not support ...`，不影响结果，略慢 |
| Intel Mac | ❌ 无 MPS，只能 CPU，会很慢 |

---

## 3. AMD + ROCm（Linux 才有）

**Windows 上的 AMD 显卡用不了 ROCm**，只能走 CPU（DirectML 未接入本项目）。

### 3.1 Linux 安装

```bash
# 先确认 ROCm 本身可用
rocm-smi

# 用 ROCm 索引装 torch（版本按你的 ROCm 版本选）
python install.py --torch-index https://download.pytorch.org/whl/rocm6.2
```

支持的卡（ROCm 6.x 官方列表）：RX 7900 XTX/XT、RX 7800 XT、RX 7700 XT、RX 6900 XT、MI 系列等。

### 3.2 已知问题

| 现象 | 原因 | 处理 |
|---|---|---|
| `torch.cuda.is_available()` 返回 True 但报 HIP 错误 | ROCm 与 torch 版本不匹配 | 对齐版本后重装 |
| 生成结果有噪声 | 部分算子 ROCm 实现差异 | 试 `--dtype float32` |
| 速度只有同档 NVIDIA 的 40–60% | ROCm 生态成熟度 | 正常，接受或换卡 |

> 本项目**没有在 AMD 上做过端到端实测**。上面的路径来自 PyTorch 官方 ROCm 支持范围，
> 如果你跑通了/踩到坑，欢迎提 issue 补充实测数据。

---

## 4. Intel（XPU / 核显）

### 4.1 Intel Arc 独显

PyTorch 2.5+ 自带 XPU 后端（Windows/Linux）：

```bash
# 确认 torch 认到 XPU
python -c "import torch;print(torch.xpu.is_available(), torch.xpu.get_device_name(0))"

# 指定用 XPU
python run.py --device xpu:0
```

⚠️ **未实测**。OmniVoice 依赖的部分算子可能没有 XPU 实现，会自动回落到 CPU（更慢但不报错）。

### 4.2 Intel 核显（UHD / Iris Xe）

**不建议**。核显算力不足以跑 0.8B 模型，会退化成 CPU 速度但多占内存。直接用 CPU。

---

## 5. 纯 CPU / 核显

### 5.1 能力

能跑，就是慢。

| CPU 档位 | 单句 25 字耗时 | 能不能听 |
|---|---|---|
| 高端桌面（i9 / Ryzen 9，16 核+） | 3–6 s | ⚠️ 1.2× 以内勉强 |
| 主流桌面（i5 / Ryzen 5，6–8 核） | 5–10 s | ❌ 只能用 1.0–1.2× |
| 笔记本低压 U | 10–20 s | ❌ 不实用 |

**结论**：CPU 模式适合"验证功能 / 偶尔听"，不适合长时间听书。

### 5.2 安装与运行

```bash
python install.py --device cpu
python run.py --device cpu
```

CPU 模式会自动用 `float32`（fp16 在 CPU 上极慢甚至不支持）。

### 5.3 提速手段

| 手段 | 效果 |
|---|---|
| 减少线程争抢（关掉浏览器/IDE） | +10–30% |
| Legado 里把单句调到 15–20 字 | 感知明显（短句固定开销占比高，但更不容易"卡住等"） |
| 把语速降到 1.2× 以下 | 合成能跟上播放 |
| 换有独显的机器 | 10–30 倍 |

---

## 6. 多显卡 / 多实例

### 6.1 指定用哪张卡

```bash
# 用第二张卡
CUDA_VISIBLE_DEVICES=1 python run.py          # Linux
$env:CUDA_VISIBLE_DEVICES="1"; python run.py  # Windows PowerShell
```

### 6.2 ⚠️ 不要在同一张卡上跑两个 TTS 实例

这是本机踩过的真实坑：

| 现象 | 数据 |
|---|---|
| 正常单实例 | 单句中位 **1.42–1.49 s** |
| 同卡再跑一个 OmniVoice | 单句中位 **1.82 s（+24%）**，p90 **4.07 s（+117%）** |
| 显存被挤到 89% 时 | 听书模型常驻从 3.6 GB 掉到 2.4 GB，**1.2 GB 权重被换页** |

**结论**：一张 12 GB 卡跑一个 TTS 就够。要跑第二个，要么换卡，要么用完就卸。

---

## 7. 快速对照表（打印版）

| 你的硬件 | 命令 | 预期单句 | 建议 |
|---|---|---|---|
| RTX 3060 12G 及以上 | `python install.py` | 1.0–1.6 s | 默认用 |
| RTX 2060 6G | `python install.py` | 1.5–2.5 s | 默认用 |
| GTX 1650 4G | `python install.py` | 3–6 s（会换页） | 分句调短，或 CPU |
| M1 8G | `python install.py` | 2–4 s | 默认用 |
| M4 16G | `python install.py` | 1.5–2.5 s | 默认用 |
| AMD RX 7900（Linux） | `--torch-index .../rocm6.2` | 2–4 s | 未实测 |
| Intel Arc | `python run.py --device xpu:0` | 3–8 s | 未实测 |
| i5-12400 无独显 | `python install.py --device cpu` | 5–10 s | 只适合试听 |

---

## 8. 上报问题时请带上

```bash
python doctor.py --json > doctor.json
```

这个输出包含：OS、Python、torch 版本、CUDA 可用性、显卡型号、计算能力、显存、
模型文件完整性、音色数量、ffmpeg 状态、端口占用。

有了它，维护者不需要再问"你什么显卡"。

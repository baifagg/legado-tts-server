# AGENTS.md · 给 AI Agent 的部署与排查手册

> 这份文件是写给**自动化 agent** 的，不是写给人看的。目标是：你（agent）读完之后，
> 能在陌生机器上把服务拉起来、验证它能用、并在出问题时定位到具体原因。
>
> 人看的文档在 [README.md](README.md)。技术细节在 [docs/](docs/)。
>
> **执行原则**：每一步都要看输出再往下走。不要一口气跑完所有命令然后假设成功。

---

## 0. 三个命令（90% 的情况够用）

```bash
python install.py      # 装环境（幂等，可重复跑；失败就重跑）
python doctor.py       # 诊断（必须 0 fail）
python run.py          # 启动
```

如果这三步都过了 → 服务已在 `http://<LAN_IP>:18760`，把 `http://<LAN_IP>:18760/httpTTS-float.json` 交给用户导入。

如果哪一步失败 → 跳到 [§6 故障目录](#6-故障目录症状--根因--修复)，按报错关键词查。

---

## 1. 项目事实（先读，避免改错）

### 1.1 它是什么

本地 TTS 服务，给手机「阅读 / Legado」App 提供合成语音。双进程架构：

```
Legado App
   │  POST /api/tts  (form: text / engine=omni / ref=王宝 / speakSpeed=12 / scale=float)
   ▼
server.py  :18760          ← 只做路由/缓存/文本处理，不 import torch，1 秒启动
   │  POST /tts  (JSON: {"text","ref","speed"})
   ▼
worker.py  :18770          ← 常驻 GPU 进程，OmniVoice 0.8B
   │
   └─> 可选：Windows SAPI 断网兜底（非 Windows 自动禁用）
```

**为什么拆两个进程**：模型加载要 5–10 秒、常驻 2–4 GB 显存。拆开后，改 Web 逻辑只重启 server，模型不用重新加载。

### 1.2 不能改的东西（改了会静默变差，不报错）

| 位置 | 值 | 为什么不能动 |
|---|---|---|
| `worker.py` `MODEL_SPEED_MAX` | **1.6** | 改回 2.0 会让 **22/25 个音色丢句首 1–3 个字**。这是用 Whisper 逐字回读 25 音色×3 句测出来的，人耳听不出来但听感会"跳"。改之前必须重跑 §7 的 CER 回归。 |
| `worker.py` `NUM_STEP` | **16** | 32 步质量提升不明显，耗时 +81%。改成 12 以下 CER 会明显变差。 |
| `server.py` 文本链顺序 | `digits_guard_pro → merge_fragments → clean_text` | 顺序反过来会让 `% / ² ℃ -` 在数字规范化之前被删掉，`15%` 变成"十五"、`1/3` 变成"13"。 |
| `refs/manifest.json` 的 `text` | 必须与对应 wav **逐字一致** | 错一个字，克隆音色就失真（不报错）。新增音色必须做 ASR 回读校验。 |
| `server.py` 的 `_cache_lock` | 包住 `call_worker` | 不要在这里加并发。模型非线程安全，worker 侧已有 `RLock`。 |
| `worker.py` 的 `_lock` | `threading.RLock` | **必须是 RLock**。改成普通 Lock 会自死锁（现象：HTTP 挂住不回、CPU 不涨）。 |

### 1.3 关键参数现状

| 参数 | 默认值 | 在哪 |
|---|---|---|
| 主服务端口 | 18760 | `run.py --port` / `server.py --port` |
| worker 端口 | 18770（=主端口+10） | `run.py` 自动算 |
| 采样率 | 24000 Hz 单声道 | 模型固定 |
| 模型侧语速上限 | 1.6× | `worker.py MODEL_SPEED_MAX` |
| 采样步数 | 16 | `worker.py NUM_STEP` |
| 缓存 TTL | 30 天 | `server.py CACHE_TTL_DAYS` |
| 默认音色 | 王宝 | `worker.py DEFAULT_REF` / `server.py DEFAULT_REF` |

---

## 2. 前置检查（30 秒，决定后面走哪条路）

```bash
python --version                  # 必须 3.10–3.12
nvidia-smi                        # 有输出 = NVIDIA；报错 = 没有
python -c "import platform;print(platform.system(), platform.machine())"
df -h .        # Linux/macOS   或   Get-PSDrive .   # Windows：确认 ≥ 10 GB 空闲
```

判定：

| 观察到 | 走哪条 |
|---|---|
| `python 3.10/3.11/3.12` + `nvidia-smi` 正常 | 标准路径，CUDA |
| Apple Silicon（`Darwin arm64`） | 标准路径，MPS（`install.py` 自动选对 torch） |
| 无 `nvidia-smi` | CPU 路径（能跑，每句 5–15 s）。先跟用户确认能否接受 |
| Python 3.13 / 3.9 | **先换 3.11 或 3.12**，torch 轮子不全 |

---

## 3. 部署 Runbook

### Step 1 · 安装

```bash
cd legado-tts-server
python install.py                 # 国内网络加 --mirror
```

**预期输出**（关键行）：

```
✅ Python 3.11.x
✅ 显卡：NVIDIA GeForce RTX 3060 ...，显存 12.0 GB
>>> 安装 PyTorch 2.8.0 —— NVIDIA CUDA 12.8 构建
✅ 安装完成
```

**耗时**：有缓存 3–5 分钟；全新 10–30 分钟（模型 3.1 GB）。

**失败处理**：
- pip 超时 → `python install.py --mirror`
- 模型下载失败 → 见 [§6.2](#62-模型下载失败)
- torch 装成 CPU 版 → 见 [§6.3](#63-装了-cuda-版-torch-但-cuda_available-是-false)

### Step 2 · 诊断（**必须做，不要跳过**）

```bash
.venv/bin/python doctor.py           # Linux/macOS
.venv\Scripts\python.exe doctor.py   # Windows
```

**必须满足**：退出码 `0` 或 `2`（2 = 有警告但能跑），且「模型权重」「参考音色」「omnivoice 包」「PyTorch」四项都是 `✅`。

任何一项 `❌` → 不要继续，先修。

想更保险，加 `--smoke` 真跑一次合成（约 1 分钟，会占显存）：

```bash
python doctor.py --smoke
# 期望最后一行：✅ 端到端合成  device=cuda:0 dtype=torch.float16 输出 2.6s 音频
```

### Step 3 · 启动

```bash
python run.py
```

**预期输出**：

```
✅ worker 就绪（模型加载 7.3s）
============================================
  电脑访问：   http://127.0.0.1:18760/
  手机访问：   http://192.168.1.23:18760/   （需同一 WiFi）
  导入链接 A（0~2.0 语速刻度，推荐）：http://192.168.1.23:18760/httpTTS-float.json
============================================
```

**看到这四行才算成功。** 没看到 → 看 `logs/worker_*.log` 和 `logs/server.log`。

### Step 4 · 验收（4 项，逐条做）

```bash
# ① 健康检查
curl -s http://127.0.0.1:18760/health
# 期望：{"ok":true,"engines":{"omni":{"ok":true,...}},...}

# ② 音色数量（必须是 20）
curl -s http://127.0.0.1:18760/api/voices | python -c "import sys,json;print(len(json.load(sys.stdin)['refs']))"

# ③ 导入 JSON（必须是 21 条 = 20 音色 + 1 断网兜底）
curl -s http://127.0.0.1:18760/httpTTS-float.json | python -c "import sys,json;print(len(json.load(sys.stdin)))"

# ④ 真实合成一句（真跑，不是 mock）
curl -s -X POST http://127.0.0.1:18760/api/tts \
  -d "text=他吃了1/3个西瓜，销售额增长了15%。&engine=omni&ref=白露&speakSpeed=12&scale=float" \
  -o /tmp/t.wav -w "%{http_code} %{size_download}\n"
# 期望：200 且 size > 40000

# ⑤ 文本前端是否生效（看 server.log）
grep "三分之一" logs/server.log | tail -1
# 期望出现：他吃了三分之一个西瓜，销售额增长了百分之十五
```

第 ⑤ 步很关键：如果日志里还是 `他吃了13个西瓜`，说明文本链顺序被改坏了。

### Step 5 · 交给用户

把 `http://<LAN_IP>:18760/httpTTS-float.json` 给用户，说明导入路径：
**阅读 → 我的 → 朗读引擎 → 右上角菜单 → 网络导入**。

⚠️ 手机和电脑必须同一 WiFi。导入失败先看 [§6.1](#61-手机导入失败--打不开网页)。

---

## 4. 端口与网络

| 项 | 默认 | 改法 |
|---|---|---|
| 主服务 | 18760 | `python run.py --port 18860`（worker 自动 18870） |
| 监听地址 | `::`（双栈） | `--host 0.0.0.0` 只跑 IPv4 |

**Windows 双栈坑**：Windows 的 IPv6 socket 默认 `IPV6_V6ONLY=1`，绑 `::` 后 IPv4 全不通。
`server.py` 里 `_make_dualstack_socket()` 已显式关掉；如果系统没有 IPv6，会自动退回 `0.0.0.0`。

**防火墙**：首次启动 Windows 会弹「是否允许 Python 访问网络」→ **必须点「允许」**（勾选专用+公用）。
点过「取消」会在防火墙里留下 Block 规则，之后手机永远连不上。修复见 [§6.1](#61-手机导入失败--打不开网页)。

---

## 5. 架构细节（排查时需要）

### 5.1 请求生命周期

```
Legado
  → POST /api/tts  (form-urlencoded)
  → read_params()          兼容 query / form / json 三种写法
  → decode_text_param()    幂等解 1~3 层 URL 编码 + 纯 HEX 兜底（见 §6.6）
  → speed_to_ratio()       滑块值 → 实际倍率（两套刻度：float 0~2.0 / speed 1~100）
  → digits_guard_pro()     数字/日期/金额/百分比 → 中文读法
  → merge_fragments()      去掉句尾残逗号
  → clean_text()           去颜文字/emoji，保留中文标点
  → cache_key() = sha1(文本+引擎+音色+倍率)
  → 命中 → 3ms 返回
  → 未命中 → call_worker() → worker /tts → 落盘缓存
  → 返回 WAV（或 fmt=mp3 走 MP3）
```

### 5.2 worker 内部

```
synth_omni()
  → _load_ref()        读 refs/<音色>.wav + manifest 里的 text（带缓存）
  → model.generate(speed=补偿后的值, num_step=16)
  → 若目标倍速 > 1.6 → _time_stretch() 用 ffmpeg atempo 补差额
  → _finalize_audio()  去直流 → 峰值归一化 -2dBFS → tanh 软限幅
```

### 5.3 缓存

- 位置 `cache/<前两位>/<sha1>.wav`
- key = `sha1(engine|ref|ratio|text)`，**不含 num_step / guidance** → 改采样参数不会让缓存失效（调参时注意）
- 30 天 TTL，启动时清理
- 命中 3ms vs 未命中 1.5s，差 500 倍。**改动文本前端会让所有缓存失效**（key 变了），首次听会慢几分钟，属正常。

---

## 6. 故障目录（症状 → 根因 → 修复）

> 按**报错关键词**或**症状**检索。每条都给可执行命令。

### 6.1 手机导入失败 / 打不开网页

**先分层定位，不要瞎猜**：

```bash
# ① 服务自己活着吗
curl -s http://127.0.0.1:18760/health          # 通 → 服务没问题
# ② 局域网 IP 对吗
python -c "import socket;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.connect(('8.8.8.8',80));print(s.getsockname()[0])"
# ③ 从局域网 IP 访问（不是 127.0.0.1）
curl -s http://<LAN_IP>:18760/health
```

| 现象 | 根因 | 修复 |
|---|---|---|
| 127.0.0.1 通、LAN_IP 不通 | 防火墙拦截 | 见下方「Windows 防火墙」 |
| LAN_IP 也通、手机还是不行 | 手机不在同一 WiFi / 路由器开了 AP 隔离 | 让手机连同一个 WiFi；路由器关「AP 隔离」 |
| 全都通、Legado 报解析错误 | JSON 被中间设备改写 / 粘贴不完整 | 手机浏览器直接打开该 URL，应显示 JSON 文本 |
| 手机浏览器也打不开 | 上面 ①②③ 之一 | 按 ①②③ 逐个排除 |

**Windows 防火墙**（最常见）。注意：**Block 规则优先级高于 Allow**，光加放行没用，要删 Block：

```powershell
# 看有没有拦 Python 的 Block 规则
Get-NetFirewallRule -DisplayName "*Python*" -Action Block -ErrorAction SilentlyContinue |
  Select-Object DisplayName,Profile,Enabled

# 加放行（管理员 PowerShell）
New-NetFirewallRule -DisplayName "Legado TTS 18760" -Direction Inbound `
  -Protocol TCP -LocalPort 18760 -Action Allow -Profile Any

# 若上面查到 Block 规则，按名字删掉（管理员）
Remove-NetFirewallRule -DisplayName "Python"
```

> 网络位置是「公用」时最容易被拦。确认：`Get-NetConnectionProfile`。

### 6.2 模型下载失败

**症状**：`install.py` 卡在 `Downloading model.safetensors` / `ConnectionError` / `HTTP 403`。

| 根因 | 修复 |
|---|---|
| 访问不了 huggingface.co | `python install.py --mirror`（走 hf-mirror.com） |
| 公司网络要代理 | 先设 `HTTPS_PROXY`，再跑 install |
| 磁盘满 | 确认 ≥ 10 GB |
| 下到一半断了 | 直接重跑，`snapshot_download` 会断点续传 |

**手动兜底**：从 https://huggingface.co/k2-fsa/OmniVoice/tree/main 下载这些放进 `models/OmniVoice/`：

```
model.safetensors            (2.3 GB)
config.json
tokenizer.json               (11 MB)
tokenizer_config.json
chat_template.jinja
audio_tokenizer/model.safetensors   (0.77 GB)
audio_tokenizer/config.json
audio_tokenizer/preprocessor_config.json
```

然后 `python install.py --skip-model`，再 `python doctor.py` 确认「模型权重 ✅」。

### 6.3 装了 CUDA 版 torch 但 `cuda_available` 是 false

```bash
python -c "import torch;print(torch.__version__, torch.version.cuda, torch.cuda.is_available())"
```

| 输出 | 含义 | 修复 |
|---|---|---|
| `2.8.0+cpu` | 装成 CPU 版了 | 重装：`pip install --force-reinstall torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128` |
| `2.8.0+cu128 False` | 驱动太旧 | 升级 NVIDIA 驱动到支持 CUDA 12.8 |
| 报 `no kernel image` | 显卡太新（sm_120） | 换 cu128 及以上索引重装 |

### 6.4 显存不足（OOM）

**症状**：`torch.OutOfMemoryError` / `CUDA out of memory` / worker 起来又挂。

```bash
nvidia-smi                        # 看已用多少
# 先关掉占显存的其他程序（浏览器硬件加速、游戏、别的 AI 服务）
```

| 显存 | 判断 |
|---|---|
| ≥ 6 GB 可用 | 正常 |
| 4–6 GB | 能跑，但长句会慢。建议 Legado 里把单句调短 |
| < 4 GB | 建议改用 CPU（`python run.py --device cpu`） |

**关键**：本服务常驻约 **3.6 GB**。如果机器上还跑着第二个 TTS / LLM，会互相挤到换页，速度掉一个量级。
排查：`nvidia-smi` 看有几个 python 占着显存。

### 6.5 服务起来了但合成很慢（> 5 s/句）

按顺序查：

```bash
# ① 是不是掉到 CPU 了
curl -s http://127.0.0.1:18770/health    # 看 "device" 字段，应该是 cuda:0
# ② 是不是显存被挤
nvidia-smi
# ③ 是不是首次加载
grep "加载完成" logs/worker_*.log | tail -1
```

| 现象 | 根因 | 修复 |
|---|---|---|
| `device: cpu` | torch 装成 CPU 版 / 驱动问题 | §6.3 |
| 显存 > 90% 且 GPU 利用率低 | 权重被换页到内存 | 关掉其他占显存的程序 |
| GPU 利用率 100% 但慢 | 有别的任务在抢卡 | 错峰/关掉 |
| 只有第一句慢 | 模型懒加载 / 内核编译 | 正常，第二句起就快 |
| 每句都 10 s+ | 句子太长（>60 字） | Legado 里调小单句长度 |

### 6.6 念字母 / 念十六进制乱码

**症状**：日志里合成文本是 `E79C8BEFBC8CE6A59A...` 或 `%E7%9C%8B`。

**根因**：Legado 某些版本把 `text` 多编码了一层，服务端只解一层，`clean_text` 又把 `%` 吃掉。

**现状**：`server.py` 的 `decode_text_param()` 已做幂等还原（最多 3 层 + 纯 HEX 兜底），**正常不该再出现**。

如果仍出现：
```bash
# 看原始入参长度 vs 解码后长度
grep "\[入参\]" logs/server.log | tail -5
```
- 解码后长度没变 → 是第 4 层以上编码，把样本发给维护者
- 解码后是乱码 → 编码不是 UTF-8，检查 App 版本

### 6.7 高倍速漏字 / 跳字

**症状**：听 2.5× 以上时偶尔少一两个字，句首尤其明显。

```bash
# 确认你的参数有没有被改过
curl -s http://127.0.0.1:18770/health    # 应有 "model_speed_max": 1.6 和 "num_step": 16
```

| 情况 | 处理 |
|---|---|
| `model_speed_max` 不是 1.6 | 有人改回 2.0 了，改回来 |
| 已经是 1.6 仍偶尔漏 | 让用户在 Legado 把语速降一档（2.14× → 1.85×） |
| 只有某个音色漏 | 该音色的参考音频语速太快，换一条参考音频（见 §7） |

### 6.8 worker 起不来 / 反复重启

```bash
tail -50 logs/worker_*.log
```

| 报错关键词 | 根因 | 修复 |
|---|---|---|
| `FileNotFoundError: 音色清单不存在` | `refs/manifest.json` 丢了 | 重新拉仓库 |
| `FileNotFoundError: 参考音频不存在` | refs 里少了 wav | 同上 |
| `ImportError: omnivoice` | 依赖没装 | `python install.py` |
| `ModuleNotFoundError: torch` | 用了系统 python 而不是 .venv | 用 `.venv/bin/python run.py` |
| `Address already in use` | 端口被占 | `python run.py --port 18860` |

### 6.9 自死锁（HTTP 挂住不回，CPU 不涨）

**根因**：`worker.py` 的 `_lock` 被改成普通 `threading.Lock`。`synth_omni` 内部 `_load_ref()` 会再取锁，普通 Lock 不可重入。

**修复**：确认 `worker.py` 里是 `_lock = threading.RLock()`。

### 6.10 `InvalidURL: Invalid port: ':1]'`

**根因**：本机 `NO_PROXY` 里有 `[::1]` 写法，httpx 解析失败。

**修复**：确认 `server.py` 的 `call_worker()` 里 `httpx.AsyncClient(..., trust_env=False)` 还在。**不要删这个参数。**

### 6.11 Legado 更新后不能用了

Legado 各分支的请求格式有差异（form / json / 双层编码 / 不同语速刻度）。

排查：
```bash
# 看服务端收到的原始参数
grep "\[参数\]" logs/server.log | tail -3
```

| 现象 | 处理 |
|---|---|
| `speakSpeed='15' scale=float -> 2.57x` | 正常（该版本把 1.5 传成了 15，服务端已兼容） |
| `speakSpeed` 为空 | App 没传，服务端按默认 1.0 处理 |
| `scale` 判断错（该 float 却走 speed） | 在导入 JSON 的 url 里显式加 `&scale=float` |

---

## 7. 改参数前必须做的回归（CER 验证）

**任何影响发音的改动**（`MODEL_SPEED_MAX` / `NUM_STEP` / 文本前端 / 换参考音频）都要跑这个。
**不要用耳朵判断**——丢一两个字人耳常常听不出来，但听感会变"跳"。

### 7.1 原理

用 Whisper 回读合成音频，与原文算 CER（字错率）。CER ≤ 0.10 算干净。

### 7.2 最小回归脚本

```bash
# 需要额外装 whisper（一次性，约 2 GB）
.venv/bin/pip install openai-whisper        # 或 pip install faster-whisper

.venv/bin/python - <<'EOF'
import urllib.parse, urllib.request, re, numpy as np, soundfile as sf, io
from faster_whisper import WhisperModel

SENTS = [
  "许安然站在公司楼下，看着玻璃幕墙上倒映出的自己，忽然觉得这些年拼命往上爬，好像把什么更重要的东西丢在了身后。",
  "她端起茶杯抿了一口，目光越过窗外的梧桐树梢，落在远处那片灰蒙蒙的天际线上，久久没有收回。",
]
VOICES = ["王宝", "白露", "苏禾", "男评书", "女老年"]
BASE = "http://127.0.0.1:18760/api/tts"
asr = WhisperModel("large-v3-turbo", device="cpu", compute_type="int8")
P = re.compile(r"[，。！？、；：“”‘’（）《》…—\-\s,.!?;:\"'()\[\]]")
norm = lambda t: P.sub("", t)

def cer(ref, hyp):
    r, h = norm(ref), norm(hyp)
    d = np.zeros((len(r)+1, len(h)+1)); d[:,0] = range(len(r)+1); d[0,:] = range(len(h)+1)
    for i in range(1,len(r)+1):
        for j in range(1,len(h)+1):
            d[i,j] = min(d[i-1,j]+1, d[i,j-1]+1, d[i-1,j-1] + (r[i-1]!=h[j-1]))
    return d[len(r),len(h)]/len(r)

for v in VOICES:
    cs = []
    for s in SENTS:
        body = urllib.parse.urlencode({"text":s,"engine":"omni","ref":v,"speakSpeed":"12","scale":"float"}).encode()
        data = urllib.request.urlopen(urllib.request.Request(BASE, data=body), timeout=180).read()
        y, sr = sf.read(io.BytesIO(data), dtype="float32")
        segs, _ = asr.transcribe(np.asarray(y, np.float32), language="zh", vad_filter=False)
        hyp = "".join(x.text for x in segs)
        cs.append(cer(s, hyp))
    print(f"{v:<6} CER={np.mean(cs):.3f}")
EOF
```

**判定标准**：
- 每个音色 `CER ≤ 0.10` → 通过
- 任何音色 `> 0.15` → 打回，不要上线
- `0.10–0.15` → 抽听该音色，确认没有句首缺字

### 7.3 换参考音频的额外校验

参考音频的 `text` 必须与音频**逐字一致**。新加音色时：

```python
# 用 ASR 回读参考音频，CER 必须 == 0
hyp = asr.transcribe("refs/新音色.wav")[0]
assert cer(manifest_text, hyp) == 0, "参考文本与音频不一致，克隆会失真"
```

---

## 8. 文件地图

| 文件 | 作用 | 改它会影响 |
|---|---|---|
| `install.py` | 装环境 | 无（只影响安装） |
| `run.py` | 启动 + 守护 | 无（只影响启动方式） |
| `doctor.py` | 诊断 | 无（只读） |
| `server.py` | Web/缓存/文本链 | 全部请求 |
| `worker.py` | 模型推理/变速 | 音质、速度、漏字 |
| `text_front.py` | 数字→中文读法 | 读字准确率 |
| `build_tts_json.py` | 生成导入 JSON | Legado 里显示的音色列表 |
| `refs/manifest.json` | 音色清单 | 音色可用性 |
| `refs/*.wav` | 参考音频 | 克隆音色 |

日志位置：`logs/server.log`（主服务）、`logs/worker_<pid>.log`（每个 worker 实例一个）。

---

## 9. 禁止事项（Don't）

1. **不要**把 `MODEL_SPEED_MAX` 调回 2.0 —— 会漏字，见 §1.2
2. **不要**在没有跑 §7 回归的情况下改 `NUM_STEP` 或文本前端
3. **不要**在 `server.py` 的 `call_worker()` 里去掉 `trust_env=False`
4. **不要**把 `_lock` 改成普通 `Lock`
5. **不要**在 `async def` 里直接调同步推理（必须 `asyncio.to_thread`）
6. **不要**修改 `refs/manifest.json` 的 `text` 而不改对应 wav（或反之）
7. **不要**把 `models/` 提交进 git（3 GB）
8. **不要**在模型没加载完就压测（会全部 503）
9. **不要**假设 `nvidia-smi` 有输出就等于 torch 能用 CUDA —— 一定要看 `doctor.py`
10. **不要**用肉耳代替 CER 回归判断漏字

---

## 10. 向用户汇报时该说什么

成功时，必须包含：

```
✅ 服务已启动
   电脑：http://127.0.0.1:18760/
   手机：http://<LAN_IP>:18760/
   导入链接：http://<LAN_IP>:18760/httpTTS-float.json
   音色：20 个 | 模型：<device> | 单句约 <X> 秒
```

失败时，必须包含：
- 卡在哪一步（Step 1/2/3/4）
- **完整报错原文**（不要转述）
- 已经尝试过什么
- 建议用户做的下一个具体动作（一条命令，可复制）

---

## 11. 性能基线（用于判断"是否正常"）

参考环境：RTX 3060 Laptop 12GB，OmniVoice 0.8B，`MODEL_SPEED_MAX=1.6`，`NUM_STEP=16`。

| 指标 | 正常范围 |
|---|---|
| 模型加载 | 5–10 s（首次含编译可能 30 s） |
| 单句合成（25 字，2.14×） | **1.0–1.6 s** |
| 单句合成（50 字） | 2.0–3.0 s |
| 缓存命中 | 3–10 ms |
| 常驻显存 | 3.5–4.0 GB |
| 空闲 GPU 功耗 | < 20 W |

超出上限 2 倍以上 → 按 §6.5 排查。

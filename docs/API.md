<div align="center">
<sub><a href="../README.md">← 返回主页</a> · <a href="../README.en.md">English</a></sub>
</div>

# HTTP API

所有接口默认监听 `:18760`（可用 `python run.py --port` 改）。
默认无鉴权；设了环境变量 `TTS_TOKEN` 后，除 `/health` 外都要求带 token。

---

## 鉴权（可选）

```bash
# 启动时设置
TTS_TOKEN=我的随机串 python run.py            # Linux/macOS
$env:TTS_TOKEN="我的随机串"; python run.py     # Windows
```

带 token 的请求：

```
GET /api/tts?...&token=我的随机串
或
Header: X-TTS-Token: 我的随机串
```

未带 token → `401 {"ok":false,"error":"unauthorized"}`。

> 只在**局域网**用就不必设。要暴露到公网**必须设**，否则任何人都能白嫖你的显卡。

---

## POST /api/tts —— 合成

Legado 调的就是这个。同时接受 `GET/POST`、`/api/rap`、`/tts` 三个路径别名。

### 请求参数

| 参数 | 必填 | 说明 |
|---|---|---|
| `text` | ✅ | 要合成的文本，UTF-8。自动解 1–3 层 URL 编码 |
| `engine` | | `omni`（默认）/ `sapi`（仅 Windows） |
| `ref` | | 音色 key，默认 `王宝`。未知值会回落到默认音色 |
| `speakSpeed` | | 语速。两种刻度见下 |
| `scale` | | `float` 或 `speed`；不传按数值自动判断（≤2.5 当 float） |
| `fmt` | | `wav`（默认）或 `mp3`（省 87% 流量，适合公网） |

**语速刻度**

| scale | 范围 | 正常值 | 例 |
|---|---|---|---|
| `float` | 0.0–2.0 | 1.0（≈1.67×） | `speakSpeed=1.2` → 1.92× |
| `speed` | 1–100 | 10（=1.0×） | `speakSpeed=15` → 1.22× |

⚠️ 某些 Legado 版本会把 `1.5` 传成 `15`（×10），服务端已自动兼容。

### 三种传参方式都支持

```bash
# form
curl -X POST http://127.0.0.1:18760/api/tts \
  -d "text=你好&engine=omni&ref=王宝&speakSpeed=1.2&scale=float" -o out.wav

# query
curl "http://127.0.0.1:18760/api/tts?text=%E4%BD%A0%E5%A5%BD&ref=%E7%8E%8B%E5%AE%9D" -o out.wav

# json
curl -X POST http://127.0.0.1:18760/api/tts \
  -H "Content-Type: application/json" \
  -d '{"text":"你好","ref":"王宝","speakSpeed":1.2,"scale":"float"}' -o out.wav
```

### 成功响应

`200 OK`，`Content-Type: audio/wav`（或 `audio/mpeg`）。

响应头：

| 头 | 含义 |
|---|---|
| `X-TTS-Engine` | `omni` / `sapi` |
| `X-TTS-Cached` | `1` = 命中缓存，`0` = 现场合成 |
| `X-TTS-Ms` | 服务端耗时（毫秒） |
| `X-TTS-Voice` | 实际使用的音色（URL 编码） |
| `X-TTS-Format` | `wav` / `mp3` |

### 错误响应

| 状态码 | 含义 | 排查 |
|---|---|---|
| `400` | 参数错误（引擎不存在等） | 看 `{"error":...}` |
| `401` | 未带 token（设了 TTS_TOKEN 时） | 加 token |
| `500` | 合成失败 | 看 `logs/server.log` 和 `logs/worker_*.log` |
| `503` | worker 未就绪 | 模型还在加载，或加载失败 |

---

## GET /api/voices —— 音色清单

```json
{
  "engines": ["omni", "sapi"],
  "engine_desc": {"omni": "OmniVoice 0.8B · ..."},
  "default_engine": "omni",
  "default_ref": "王宝",
  "refs": [
    {"key": "王宝", "desc": "男 · 新闻播报 · 146Hz", "usage": "正剧、都市", "seconds": 3.13},
    ...
  ]
}
```

---

## GET /httpTTS-float.json —— Legado 导入（0~2.0 刻度）

```json
[
  {
    "concurrentRate": "0",
    "contentType": "audio/wav",
    "enabledCookieJar": false,
    "id": 1794324007000,
    "lastUpdateTime": 1794324007134,
    "name": "王宝 · 男 · 新闻播报",
    "url": "http://192.168.1.23:18760/api/tts,{\"method\":\"POST\",\"body\":\"text={{encodeURIComponent(speakText)}}&engine=omni&ref=王宝&speakSpeed={{speakSpeed}}&scale=float\"}"
  }
]
```

共 **21 条** = 20 音色 + 1 条「断网兜底」（`engine=sapi`，仅 Windows）。

**地址会根据你访问时的 Host 自动生成**：
- 用 `http://192.168.1.23:18760/...` 访问 → JSON 里就是该局域网地址
- 用公网 IPv6 访问 → 自动换成该 IPv6 并带 `&fmt=mp3`

---

## GET /httpTTS.json —— Legado 导入（1~100 刻度）

同结构，`url` 里带 `scale=speed`。

---

## GET /health —— 健康检查

```json
{
  "ok": true,
  "engines": {
    "omni": {"ok": true, "load_seconds": 7.73, "model": "OmniVoice", "error": null}
  },
  "sapi": true,
  "cache_files": 56456,
  "lan_url": "http://192.168.1.23:18760",
  "default_engine": "omni"
}
```

只要 `ok` 为 `true` 且 `engines.omni.ok` 为 `true`，服务就是好的。

---

## GET / —— 网页控制台

浏览器打开可看到：
- 服务/模型/显存状态
- 20 个音色的试听按钮
- 局域网地址 + 两个导入链接（带一键复制）

---

## Worker 内部接口（:18770，仅本机）

主服务通过它调模型。**正常不需要直接访问**，但排查时有用：

```bash
# 健康 + 当前参数
curl -s http://127.0.0.1:18770/health
# {"engine":"omni","loaded":true,"device":"cuda:0","dtype":"float16",
#  "num_step":16,"model_speed_max":1.6,"ffmpeg":true,...}

# 直接合成（绕过 server 的缓存与文本处理）
curl -X POST http://127.0.0.1:18770/tts \
  -H "Content-Type: application/json" \
  -d '{"text":"测试","ref":"王宝","speed":2.57}' -o raw.wav
```

> `speed` 传的是**最终倍速**（如 2.57），worker 内部自己拆成「模型侧 + atempo」。
> 直连 worker 时注意：**不走 server 的文本前端**，数字不会转成中文读法。

---

## 缓存行为

- key = `sha1(engine|ref|ratio|text)`
- 同一个 `(文本, 引擎, 音色, 倍速)` 第二次请求直接返回磁盘缓存（3–10 ms）
- TTL 30 天，服务启动时清理过期文件
- **改文本前端会让所有缓存失效**（key 里的 text 变了）

清缓存：删 `cache/` 下的文件即可，服务运行中也能删。

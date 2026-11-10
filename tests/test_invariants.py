# -*- coding: utf-8 -*-
"""不变量测试：守住那些「改了就静默变差」的参数。

这些断言看起来"多余"，但每一个都对应一次真实踩坑。
如果它们挂了，说明有人动了不该动的东西 —— 先看 docs/TUNING.md 再决定。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


class WorkerInvariantsTest(unittest.TestCase):
    """模型侧参数（改动会让音质/速度静默退化）。"""

    @classmethod
    def setUpClass(cls):
        import worker
        cls.w = worker

    def test_model_speed_max_is_1_6(self):
        """模型侧语速上限必须是 1.6。

        改回 2.0 会让 22/25 个音色丢句首 1-3 个字（Whisper 逐字回读实测），
        人耳听不出来但听感会"跳"。
        """
        self.assertEqual(self.w.MODEL_SPEED_MAX, 1.6,
                         "MODEL_SPEED_MAX 被改动！改之前必须跑 docs/TUNING.md 的 CER 回归")

    def test_num_step_is_16(self):
        """采样步数 16：官方推荐快速档，32 步耗时 +81% 而质量提升不明显。"""
        self.assertEqual(self.w.NUM_STEP, 16,
                         "NUM_STEP 被改动！32 步慢 81%，12 步以下明显变糙")

    def test_sample_rate_is_24000(self):
        self.assertEqual(self.w.SAMPLE_RATE, 24000)

    def test_speed_split_is_consistent(self):
        """任意目标倍速下：模型侧 ≤ 1.6，且 模型侧 × atempo = 目标。"""
        for want in (1.0, 1.3, 1.6, 2.0, 2.57, 3.5, 4.2):
            ms = max(self.w.MODEL_SPEED_MIN, min(self.w.MODEL_SPEED_MAX, want))
            atempo = want / ms
            self.assertLessEqual(ms, 1.6 + 1e-9, f"want={want} 模型侧超限")
            self.assertAlmostEqual(ms * atempo, want, places=6, msg=f"want={want} 倍速不守恒")
            self.assertGreaterEqual(atempo, 0.25, f"want={want} atempo 低于 ffmpeg 下限")

    def test_speed_compensate_monotonic(self):
        """补偿函数必须单调递增，否则高速档会反而变慢。"""
        xs = [1.0, 1.2, 1.4, 1.6, 1.8]
        ys = [self.w._speed_compensate(x) for x in xs]
        self.assertEqual(ys, sorted(ys))


class RefManifestInvariantsTest(unittest.TestCase):
    """音色清单完整性 —— 少一个 wav 或文本为空，克隆就会失真。"""

    @classmethod
    def setUpClass(cls):
        cls.refs_dir = BASE / "refs"
        cls.data = json.loads((cls.refs_dir / "manifest.json").read_text(encoding="utf-8"))

    def test_voice_count_is_20(self):
        self.assertEqual(len(self.data), 20, "音色数量应为 20（男 10 + 女 10）")

    def test_every_voice_has_wav_and_text(self):
        for d in self.data:
            with self.subTest(voice=d.get("key")):
                wav = self.refs_dir / d["file"]
                self.assertTrue(wav.exists(), f"{d['key']} 缺少 {d['file']}")
                self.assertGreater(wav.stat().st_size, 10_000, f"{d['key']} 音频过小")
                self.assertTrue((d.get("text") or "").strip(), f"{d['key']} 参考文本为空")

    def test_gender_balance(self):
        male = [d for d in self.data if d.get("desc", "").startswith("男")]
        female = [d for d in self.data if d.get("desc", "").startswith("女")]
        self.assertEqual(len(male), 10, "男声应为 10 个")
        self.assertEqual(len(female), 10, "女声应为 10 个")

    def test_required_categories_present(self):
        """中年 / 老年 / 评书 三类各男女一套，必须都在。"""
        keys = {d["key"] for d in self.data}
        for need in ("男中年", "男老年", "男评书", "女中年", "女老年", "女评书"):
            self.assertIn(need, keys, f"缺少 {need}")


class ServerConfigTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server
        cls.s = server

    def test_trust_env_disabled_in_worker_call(self):
        """call_worker 必须 trust_env=False，否则 NO_PROXY 里的 [::1] 会让 httpx 崩。"""
        import inspect
        src = inspect.getsource(self.s.call_worker)
        self.assertIn("trust_env=False", src,
                      "call_worker 去掉了 trust_env=False，本机 NO_PROXY 含 [::1] 时会抛 InvalidURL")

    def test_cache_ttl(self):
        self.assertEqual(self.s.CACHE_TTL_DAYS, 30)


if __name__ == "__main__":
    unittest.main()

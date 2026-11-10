# -*- coding: utf-8 -*-
"""给 tts-server 增加回归测试：文本前端 + 音色清单完整性。"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import server  # noqa: E402
import text_front  # noqa: E402


class TextFrontChainTest(unittest.TestCase):
    """生产文本链路：digits_guard_pro -> merge_fragments -> clean_text。"""

    def chain(self, t: str) -> str:
        return server.clean_text(server.merge_fragments(server.digits_guard_pro(t)))

    def test_fraction(self):
        self.assertIn("三分之一", self.chain("他吃了1/3个西瓜。"))

    def test_percent(self):
        self.assertIn("百分之十五", self.chain("销售额增长了15%。"))

    def test_date(self):
        self.assertIn("二零二四年十一月十日", self.chain("合同签于2024-11-10。"))

    def test_thousands(self):
        self.assertIn("三千五百", self.chain("卖了3,500元。"))

    def test_time(self):
        self.assertIn("十二点三十分", self.chain("现在是12:30。"))

    def test_phone(self):
        self.assertIn("一三八零零一三八零零零", self.chain("打13800138000找他。"))

    def test_negative_celsius(self):
        out = self.chain("室温-5℃。")
        self.assertIn("零下五摄氏度", out)

    def test_pinyin_token_protected(self):
        """OmniVoice 拼音控制 token 的声调数字不能被数字规则吃掉。"""
        self.assertIn("ZHE2", self.chain("打ZHE2出售。"))

    def test_keeps_sentence_period(self):
        """句末句号必须保留（旧版 _EMOJI_RE 会把它删掉）。"""
        self.assertTrue(self.chain("他说完了。").endswith("。"))

    def test_keeps_dialogue_quotes(self):
        out = self.chain("“你走吧。”他说。")
        self.assertIn("“", out)
        self.assertIn("”", out)

    def test_keeps_book_title_marks(self):
        self.assertIn("《三体》", self.chain("他打开《三体》第一册。"))

    def test_strips_kaomoji(self):
        out = self.chain("痛啊 ┗|｀O′|┛ 嗷~")
        self.assertNotIn("┗", out)


class RefsManifestTest(unittest.TestCase):
    """音色清单完整性：每个 refs 条目都要有 wav 与非空参考文本。"""

    def setUp(self):
        p = BASE / "refs" / "manifest.json"
        self.data = json.loads(p.read_text(encoding="utf-8"))
        self.refs = BASE / "refs"

    def test_min_voice_count(self):
        self.assertGreaterEqual(len(self.data), 20)

    def test_each_voice_has_wav_and_text(self):
        for d in self.data:
            with self.subTest(voice=d["key"]):
                wav = self.refs / d["file"]
                self.assertTrue(wav.exists(), f"{d['key']} 缺少 {d['file']}")
                self.assertTrue(wav.stat().st_size > 10000, f"{d['key']} 音频过小")
                self.assertTrue((d.get("text") or "").strip(), f"{d['key']} 参考文本为空")

    def test_deleted_voices_absent(self):
        keys = {d["key"] for d in self.data}
        for gone in ("周野", "齐峰", "姜月", "夏禾", "小满"):
            self.assertNotIn(gone, keys)

    def test_required_voices_present(self):
        keys = {d["key"] for d in self.data}
        for need in ("王宝", "秦川", "男中年", "男老年", "男评书", "女中年", "女老年", "女评书"):
            self.assertIn(need, keys)


class BuildJsonTest(unittest.TestCase):
    def test_entry_count_matches_manifest_plus_fallback(self):
        import build_tts_json as btj
        items = btj.build_items("127.0.0.1")
        n_voices = len(json.loads((BASE / "refs" / "manifest.json").read_text(encoding="utf-8")))
        self.assertEqual(len(items), n_voices + 1)
        self.assertEqual(items[-1]["name"], "断网兜底 · 本机离线")


if __name__ == "__main__":
    unittest.main()

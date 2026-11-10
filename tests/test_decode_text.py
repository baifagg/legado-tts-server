# -*- coding: utf-8 -*-
"""入参文本解码的回归测试。

背景：2026-11-05 新版「阅读」(legado-with-MD3 3.26.23) 更新后，HTTP 朗读请求
里的 text 多了一层百分号编码；服务端只解一层，残留的 "%E7%9C%8B..." 又被
clean_text() 吃掉 "%"，HEX 串被当正文合成，听书变成念字母。

运行： python -m unittest discover -s tts-server/tests -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import clean_text, decode_text_param  # noqa: E402

SAMPLE = "看，楚哥这个人，实际上是一个很有意思的人。"


class DecodeTextParamTest(unittest.TestCase):
    def test_plain_text_untouched(self):
        self.assertEqual(decode_text_param(SAMPLE), SAMPLE)

    def test_single_layer_from_json_body(self):
        # JSON body 不走 parse_qs，text 里保留了一层 %XX
        self.assertEqual(decode_text_param("%E7%9C%8B%EF%BC%8C"), "看，")

    def test_double_layer_from_form_body(self):
        # 表单被解析一层后仍是 %XX（新版 App 实测形态）
        double = (
            "%25E7%259C%258B%25EF%25BC%258C%25E6%25A5%259A%25E5%2593%25A5"
            "%25E8%25BF%2599%25E4%25B8%25AA%25E4%25BA%25BA"
        )
        self.assertEqual(decode_text_param(double), "看，楚哥这个人")

    def test_bare_hex_after_percent_stripped(self):
        # 最坏情况：上游把 "%" 全剥了，只剩 UTF-8 字节的 HEX
        bare = "E79C8BEFBC8CE6A59AE593A5E8BF99E4B8AAE4BABA"
        self.assertEqual(decode_text_param(bare), "看，楚哥这个人")

    def test_ascii_hex_word_not_misdecoded(self):
        for word in ("deadbeef", "E79C8B", "C0FFEE", "abcdef"):
            self.assertEqual(decode_text_param(word), word)

    def test_percent_in_plain_text_kept(self):
        for text in ("增长50%的人", "折扣 20%% 起", "50%OFF"):
            self.assertEqual(decode_text_param(text), text)

    def test_empty_and_whitespace(self):
        self.assertEqual(decode_text_param(""), "")
        self.assertEqual(decode_text_param("   "), "   ")

    def test_clean_text_keeps_decoded_sentence(self):
        # 解码之后进 clean_text，标点和汉字都要原样保留
        double = "%25E7%259C%258B%25EF%25BC%258C%25E6%25A5%259A"
        decoded = decode_text_param(double)
        self.assertEqual(decoded, "看，楚")
        self.assertEqual(clean_text(decoded), "看，楚")


if __name__ == "__main__":
    unittest.main()

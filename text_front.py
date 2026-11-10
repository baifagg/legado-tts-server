"""改进版中文文本前端（读字方向的可落地 patch 原型）。

定位：**不修改 tts-server/**。本文件是 `server.py::digits_guard()` 的替代实现原型，
      供 Lead 以「替换一个函数」的最小代价合入生产（diff 思路见 results/pronounce-lab.md）。

相对现有 `digits_guard()`（server.py:187-215）新增/修正的能力：
  | 类别       | 现状                                                   | 本文件 |
  |-----------|--------------------------------------------------------|--------|
  | 日期       | 只处理 `2024年`（`(?=年)`），`2024-11-10`/`2024/11/10` 全错 | YYYY-MM-DD、YYYY/MM/DD、YYYY年M月D日 |
  | 时间       | `12:30` -> `十二:三十`（冒号残留）                        | 十二点三十分 / 十二点三十分秒 |
  | 百分比     | `15%` -> `十五%`（% 残留，模型自由发挥）                  | 百分之十五 |
  | 金额       | `3,500` -> `三,五百`（千分位被拆开，灾难性）              | 三千五百 |
  | 分数       | `1/3` -> `一/三`（斜杠残留）                             | 三分之一 |
  | 序数       | `第15章` -> 第十五章（已 OK）                             | 保留 |
  | 范围       | `5-10个` -> `五-十个`（连字符残留）                       | 五到十个 |
  | 比例       | `3:1` -> `三:一`                                        | 三比一 |
  | 电话号码   | 11 位被切成 5+5+1 段整数 -> `一万三千八百...`（灾难性）    | 逐位：一三八零零一三八零零零 |
  | 年代/代际  | `80后` -> `八十后`（错，应八零后）；`1990年代` 恰好对       | 八零后 / 一九九零年代 |
  | 单位       | `2.5kg` 整个跳过；`37.5℃` 留 `℃` 符号                    | 二点五公斤 / 三十七点五摄氏度 |
  | 大数       | `1,234,567` 被拆三段；`\\d{1,5}` 上限                     | 一百二十三万四千五百六十七 |
  | 万/亿      | `1.5万` -> `一点五万`（错，应一万五千）                   | 一万五千 |
  | 负温度     | `-5℃` -> `五℃`                                        | 零下五摄氏度 |
  | 拉丁+数字  | `5G`/`3D`/`4K` 全部跳过                                   | 五G / 三D / 四K |
  | 拼音控制   | 无保护（`打ZHE2出售` 的 2 会被当数字）                    | 保护 `[A-Z]+[1-5]` 不被数字规则改写 |

命令：
  python digits_guard_pro.py --selftest     # 跑内置用例，打印对照表
  python digits_guard_pro.py "文本"          # 单条转换
"""

from __future__ import annotations

import re

_DIGITS = "零一二三四五六七八九"

# 受保护片段：OmniVoice 行内控制语法 + URL / 邮箱
_PROTECT_RES = [
    re.compile(r"\[[^\[\]]*\]"),          # [laughter] / [B EY1 S]
    re.compile(r"[A-Z]+[1-5]"),           # 中文拼音控制：HANG2 / ZHE2
    re.compile(r"https?://\S+"),          # URL
    re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"),  # 邮箱
]


# --------------------------------------------------------------------------- #
# 整数 / 小数 → 中文
# --------------------------------------------------------------------------- #
def _four_to_cn(n: int) -> str:
    """0-9999 -> 中文（不含万级单位）。"""
    out = ""
    zero_pending = False
    for base, name in ((1000, "千"), (100, "百"), (10, "十"), (1, "")):
        q = (n // base) % 10
        if q == 0:
            if out and not zero_pending:
                zero_pending = True
        else:
            if zero_pending:
                out += "零"
                zero_pending = False
            out += _DIGITS[q] + name
    return out


def int_to_cn(n: int) -> str:
    """任意非负整数（含万/亿进位），输出普通话读法。例：1234567 -> 一百二十三万四千五百六十七。"""
    if n < 0:
        return "负" + int_to_cn(-n)
    if n == 0:
        return "零"
    groups: list[int] = []
    x = n
    while x > 0:
        groups.append(x % 10000)
        x //= 10000
    unit_names = ["", "万", "亿", "万亿"]
    out = ""
    need_zero = False
    for i in range(len(groups) - 1, -1, -1):
        g = groups[i]
        if g == 0:
            need_zero = True
            continue
        if out and (need_zero or g < 1000):
            out += "零"
        need_zero = False
        out += _four_to_cn(g) + unit_names[i]
    if out.startswith("一十"):     # 10 -> 十（不读「一十」）
        out = out[1:]
    return out


def digits_to_cn(s: str) -> str:
    """逐位读：13800138000 -> 一三八零零一三八零零零。"""
    return "".join(_DIGITS[int(c)] for c in s if c.isdigit())


def _number_to_cn(raw: str) -> str:
    """把 '1234' / '3.5' / '1,234,567' 读成中文（支持千分位与小数）。"""
    raw = raw.replace(",", "").replace("，", "")
    if "." in raw:
        ip, _, fp = raw.partition(".")
        head = int_to_cn(int(ip)) if ip else "零"
        return head + "点" + "".join(_DIGITS[int(c)] for c in fp if c.isdigit())
    return int_to_cn(int(raw))


# --------------------------------------------------------------------------- #
# 主函数
# --------------------------------------------------------------------------- #
def digits_guard_pro(text: str) -> str:
    """把中文语境下的数字/日期/时间/金额/单位等转成「该念出来的中文」。"""
    if not text or not re.search(r"\d|\s*[%℃]", text):
        return text

    placeholders: list[str] = []
    # 占位符只用私有区字符（不含数字），否则会被后面的「裸整数」规则二次转换
    _PROT_PREFIX = "\uE000"
    _PROT_BASE = 0xE010

    def _protect(m: re.Match) -> str:
        placeholders.append(m.group(0))
        return _PROT_PREFIX + chr(_PROT_BASE + len(placeholders) - 1)

    for rx in _PROTECT_RES:
        text = rx.sub(_protect, text)

    # 1) 电话号码：11 位手机 / 400-800 / 带连字符座机
    text = re.sub(r"(?<!\d)(1[3-9]\d{9})(?!\d)",
                  lambda m: digits_to_cn(m.group(1)), text)
    text = re.sub(r"(?<!\d)([48]00)[- ]?(\d{3})[- ]?(\d{4})(?!\d)",
                  lambda m: digits_to_cn(m.group(1)) + digits_to_cn(m.group(2)) + digits_to_cn(m.group(3)),
                  text)
    text = re.sub(r"(?<!\d)(0\d{2,3})[- ](\d{7,8})(?!\d)",
                  lambda m: digits_to_cn(m.group(1)) + digits_to_cn(m.group(2)), text)

    # 2) 日期：YYYY-MM-DD / YYYY/MM/DD / YYYY.MM.DD
    def _date_full(m: re.Match) -> str:
        y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
        return digits_to_cn(y) + "年" + int_to_cn(mo) + "月" + int_to_cn(d) + "日"

    text = re.sub(r"(?<!\d)(\d{4})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{1,2})(?!\d)",
                  _date_full, text)

    # 3) 年代 / 代际：1990年代、80后、90后
    text = re.sub(r"(?<!\d)(\d{4})(?=年代)",
                  lambda m: digits_to_cn(m.group(1)), text)
    text = re.sub(r"(?<!\d)(\d{2})(?=后)",
                  lambda m: digits_to_cn(m.group(1)), text)

    # 4) 温度（含负号）：-5℃ / 37.5℃ / 37.5°C
    def _temp(m: re.Match) -> str:
        neg, val = m.group(1), m.group(2)
        v = _number_to_cn(val)
        return ("零下" + v if neg else v) + "摄氏度"

    text = re.sub(r"(-)?\s*(\d+(?:\.\d+)?)\s*(?:℃|°\s*C|摄氏度)", _temp, text)

    # 5) 时间：12:30 / 12:30:45 / 20:00
    def _time(m: re.Match) -> str:
        h, mi, se = int(m.group(1)), int(m.group(2)), m.group(3)
        if h > 23 or mi > 59:
            return m.group(0)          # 不是时间（交给比例规则）
        out = int_to_cn(h) + "点"
        if mi:
            out += int_to_cn(mi) + "分"
        if se is not None:
            out += int_to_cn(int(se)) + "秒"
        return out

    text = re.sub(r"(?<!\d)(\d{1,2}):(\d{2})(?::(\d{2}))?(?!\d)", _time, text)

    # 6) 比例：3:1
    text = re.sub(r"(?<!\d)(\d{1,3}):(\d{1,3})(?!\d)",
                  lambda m: int_to_cn(int(m.group(1))) + "比" + int_to_cn(int(m.group(2))), text)

    # 7) 分数：1/3 -> 三分之一
    text = re.sub(r"(?<!\d)(\d{1,3})/(\d{1,3})(?!\d)",
                  lambda m: int_to_cn(int(m.group(2))) + "分之" + int_to_cn(int(m.group(1))), text)

    # 8) 百分比：15% / 0.5% / 100%
    text = re.sub(r"(\d+(?:\.\d+)?)\s*[%％]",
                  lambda m: "百分之" + _number_to_cn(m.group(1)), text)

    # 9) 千分位金额：3,500 / 1,234,567.89
    text = re.sub(r"(?<!\d)(\d{1,3}(?:,\d{3})+(?:\.\d+)?)(?!\d)",
                  lambda m: _number_to_cn(m.group(1)), text)

    # 10) 万/亿单位：1.5万 -> 一万五千；2.5亿 -> 二亿五千万
    def _wan_yi(m: re.Match) -> str:
        num, unit = m.group(1), m.group(2)
        if "." in num:
            ip, _, fp = num.partition(".")
            head = int_to_cn(int(ip)) if ip else "零"
            out = head + unit
            # 1.5万 -> 一万五千；2.25万 -> 二万二千五百（补足到 4 位）
            frac = (fp + "0000")[:4]
            val = int(frac)
            if val:
                out += int_to_cn(val)
            return out
        return int_to_cn(int(num)) + unit

    text = re.sub(r"(?<![\d.])(\d+(?:\.\d+)?)(万|亿)(?![\d.])", _wan_yi, text)

    # 11) 单位后缀（英文缩写 → 中文）
    unit_map = [
        (r"km²|km2|平方公里", "平方公里"),
        (r"m²|m2|平方米", "平方米"),
        (r"km(?![a-z])", "公里"),
        (r"cm(?![a-z])", "厘米"),
        (r"mm(?![a-z])", "毫米"),
        (r"kg(?![a-z])", "公斤"),
        (r"ml(?![a-z])", "毫升"),
        (r"(?<![a-z])g(?![a-z])", "克"),
        (r"℃|°C", "摄氏度"),
    ]
    for pat, rep in unit_map:
        text = re.sub(pat, rep, text)

    # 12) 数值范围：5-10个 / 3~5天 / 10至20
    def _range(m: re.Match) -> str:
        a, b = m.group(1), m.group(2)
        return _number_to_cn(a) + "到" + _number_to_cn(b)

    text = re.sub(r"(?<![\d.\-A-Za-z])(\d+(?:\.\d+)?)\s*[-~～—–至]\s*(\d+(?:\.\d+)?)(?![\d.])",
                  _range, text)

    # 13) 负号：-5 -> 负五
    text = re.sub(r"(?<![\d.\-A-Za-z])-\s*(\d+(?:\.\d+)?)",
                  lambda m: "负" + _number_to_cn(m.group(1)), text)

    # 14) 拉丁+数字：5G / 3D / 4K / 8K
    text = re.sub(r"(?<![A-Za-z0-9])([2-8])(G|D|K)(?![A-Za-z])",
                  lambda m: _DIGITS[int(m.group(1))] + m.group(2), text)

    # 15) 年份：2024年（不吞后面的「代」，已在规则 3 处理）
    text = re.sub(r"(?<!\d)((?:1[6-9]|20)\d{2})(?=年)",
                  lambda m: digits_to_cn(m.group(1)), text)

    # 16) 小数
    text = re.sub(r"(?<![\d.])(\d+)\.(\d+)(?![\d.])",
                  lambda m: _number_to_cn(m.group(0)), text)

    # 17) 收尾：剩下的裸整数（1~12 位）
    text = re.sub(r"(?<![A-Za-z0-9_.])(\d{1,12})(?![A-Za-z0-9_.])",
                  lambda m: int_to_cn(int(m.group(1))), text)

    # 还原受保护片段
    def _restore(m: re.Match) -> str:
        return placeholders[ord(m.group(1)) - _PROT_BASE]

    text = re.sub("\uE000([\uE010-\uEFFF])", _restore, text)
    return text


# --------------------------------------------------------------------------- #
# 给 ASR 比对用的规范化（把 ASR 转写里的阿拉伯数字也换成「念出来的中文」）
# --------------------------------------------------------------------------- #
_PUNCT_RE = re.compile("[" + re.escape(
    "\u3002\uff0c\uff01\uff1f\u3001\uff1b\uff1a\u201c\u201d\u2018\u2019"
    "\uff08\uff09\u300a\u300b\u2026\u2014\u00b7,.!?;:'\"()[]<>-~ \t\r\n\uff5e") + "]")


def normalize_for_compare(text: str) -> str:
    """把文本规范成「只留读音汉字」串：先过数字前端，再去标点空白。"""
    return _PUNCT_RE.sub("", digits_guard_pro(text or ""))


# --------------------------------------------------------------------------- #
# 自测
# --------------------------------------------------------------------------- #
SELFTEST = [
    # (输入, 期望 (None = 只打印不判定))
    ("会议定在2024年3月15日上午举行。", "会议定在二零二四年三月十五日上午举行。"),
    ("列车在12:30准时出发。", "列车在十二点三十分准时出发。"),
    ("这台电脑卖3,500元。", "这台电脑卖三千五百元。"),
    ("今年的销售额增长了15%。", "今年的销售额增长了百分之十五。"),
    ("水温大约是3.5摄氏度。", "水温大约是三点五摄氏度。"),
    ("他吃了1/3个西瓜。", "他吃了三分之一个西瓜。"),
    ("请翻到第15章。", "请翻到第十五章。"),
    ("他是80后，喜欢听老歌。", "他是八零后，喜欢听老歌。"),
    ("有事打13800138000。", "有事打一三八零零一三八零零零。"),
    ("这次要买5-10个。", "这次要买五到十个。"),
    ("这本书写于1990年代。", "这本书写于一九九零年代。"),
    ("这袋米重2.5kg。", "这袋米重二点五公斤。"),
    ("外面只有-5℃。", "外面只有零下五摄氏度。"),
    ("这片公园占地100km²。", "这片公园占地一百平方公里。"),
    ("双方人数是3:1。", "双方人数是三比一。"),
    ("公司去年营收1,234,567元。", "公司去年营收一百二十三万四千五百六十七元。"),
    ("会议在20:00开始。", "会议在二十点开始。"),
    ("利率只有0.5%。", "利率只有百分之零点五。"),
    ("这个项目投资1.5万元。", "这个项目投资一万五千元。"),
    ("合同签于2024-11-10。", "合同签于二零二四年十一月十日。"),
    ("这里的5G网络很快。", "这里的五G网络很快。"),
    ("这家店做3D打印。", "这家店做三D打印。"),
    ("这台显示器是4K的。", "这台显示器是四K的。"),
    ("这批货物打ZHE2出售后有199块钱。", "这批货物打ZHE2出售后有一百九十九块钱。"),
    ("他有1000块钱。", "他有一千块钱。"),
    ("一共1010个人。", "一共一千零一十个人。"),
    ("这是第10001次。", "这是第一万零一次。"),
]


def _selftest() -> int:
    bad = 0
    for src, want in SELFTEST:
        got = digits_guard_pro(src)
        ok = (want is None) or (got == want)
        if not ok:
            bad += 1
        print(f"[{'OK ' if ok else 'FAIL'}] {src}\n       -> {got}"
              + ("" if ok else f"\n       期望: {want}"))
    print(f"\n{len(SELFTEST) - bad}/{len(SELFTEST)} 通过")
    return bad


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        raise SystemExit(1 if _selftest() else 0)
    if len(sys.argv) > 1:
        print(digits_guard_pro(sys.argv[1]))
    else:
        _selftest()

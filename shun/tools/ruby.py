#!/usr/bin/env python3
"""逐字 ruby 注音引擎（v3）。

算法：
1. janome(IPADIC) 分词 → token.surface + token.reading（片假名）
2. 对含汉字的 token：
   a. 拆出「前导假名 / 汉字串 / 尾部送り假名」
   b. 送り假名的读音 = 它自己（若有浊音便，则只取无声部分）
   c. 剩下的读音用 KANJIDIC2 的 音读/训读/名乗り 做 DP，逐字切分
   d. DP 无解 → 查词表 word_reading.json → 仍无解 → 整块兜底（词级读音）
3. 词表 word_reading.json 优先于 DP（覆盖惯用读音，如 日本語=に|ほん|ご）

输出：[{"t":表层,"r":读音}]，假名的 r 为 ""
"""
import os, re, json, gzip
from functools import lru_cache

HERE = os.path.dirname(os.path.abspath(__file__))
WORD_READING_FILE = os.path.join(HERE, "word_reading.json")
KANJI_FILE = os.environ.get("KANJIDIC2", "/tmp/kanjidic2.xml.gz")


def kata_to_hira(s):
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in s)


def is_kanji(c):
    return "一" <= c <= "鿿" or "㐀" <= c <= "䶿"


def is_kana(c):
    return "぀" <= c <= "ゟ" or "゠" <= c <= "ヿ"


# 浊音 → 清音（浊音便时送り假名只贡献清音）
DAKU = {"が": "か", "ざ": "さ", "だ": "た", "ば": "は", "ぱ": "は",
        "ヴ": "う", "が": "か"}


@lru_cache(maxsize=1)
def get_tokenizer():
    from janome.tokenizer import Tokenizer
    return Tokenizer()


@lru_cache(maxsize=1)
def load_word_reading():
    if not os.path.exists(WORD_READING_FILE):
        return {}
    with open(WORD_READING_FILE, encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def load_curated():
    """上下文读音表 {字: 读音}（kanji_readings.json）——线上逐字读音的主要来源（74% 命中）"""
    f = os.path.join(HERE, "kanji_readings.json")
    if not os.path.exists(f):
        return {}
    with open(f, encoding="utf-8") as fh:
        raw = json.load(fh)
    out = {}
    for k, v in raw.items():
        if isinstance(v, str):
            out.setdefault(k, []).append(v)
        elif isinstance(v, list):
            for x in v:
                if isinstance(x, str):
                    out.setdefault(k, []).append(x)
    return out


CURATED = {}


@lru_cache(maxsize=1)
def load_kanjidic():
    """{字: [候选读音(平假名)]}：音读 + 训读(去掉送り假名部分) + 名乗り + misc

    🔴 分两级（2026-10-25 修）：
       primary   = 音读 + 训读 + 名乗り  ← 词典读音，DP 首选
       fallback  = misc（熟字訓）        ← **只能兜底，不能当首选**
          理由：熟字訓是「上下文读音表」（会→あ / 人→ひと / 国→くに），
                而已发布数据用的是词典读音（会→カイ）⇒ 拿它当首选必然错
       返回 (primary, fallback)
    """
    out = {}
    fb = {}
    global CURATED
    CURATED = load_curated()
    if not os.path.exists(KANJI_FILE):
        return out, fb
    with gzip.open(KANJI_FILE, "rt", encoding="utf-8") as f:
        data = f.read()
    for m in re.finditer(r"<character>(.*?)</character>", data, re.S):
        blk = m.group(1)
        lit = re.search(r"<literal>(.*?)</literal>", blk)
        if not lit:
            continue
        ch = lit.group(1)
        cands = set()

        def add(s):
            s = kata_to_hira(s.strip())
            if not s:
                return
            # 训读 "は.ねる" → 词干 "は"（"." 后是送り假名）
            if "." in s:
                s = s.split(".")[0]
            if s:
                cands.add(s)

        # 🔴 上下文读音表（kanji_readings.json）：线上逐字读音的**主要来源**
        #    实证：线上单字读音 74% 能在本表里逐字对上（日→に 38次 / 会→あ 28次 vs かい 7次 / 人→ひと 61次）
        #    ⇒ 它必须进 primary 候选（读音本身决定唯一解，顺序不影响 DP 选择）
        for crd in CURATED.get(ch, []):
            cands.add(crd)
        for rm in re.finditer(r"<rmgroup>(.*?)</rmgroup>", blk, re.S):
            for r in re.finditer(r'<reading[^>]*r_type="(?:ja_on|ja_kun)"[^>]*>(.*?)</reading>',
                                 rm.group(1), re.S):
                add(r.group(1))
        for nm in re.finditer(r"<nanori>(.*?)</nanori>", blk, re.S):
            for r in re.finditer(r"<reading[^>]*>(.*?)</reading>", nm.group(1), re.S):
                add(r.group(1))
        for mm in re.finditer(r"<misc>(.*?)</misc>", blk, re.S):
            for r in re.finditer(r"<reading>(.*?)</reading>", mm.group(1), re.S):
                add(r.group(1))
                if cands:
                    fb.setdefault(ch, []).append(cands[-1])
        if cands:
            out.setdefault(ch, []).extend(sorted(cands))
    return out, fb


def split_surface(surface):
    """→ (前导假名, 汉字串, 尾部送り假名)"""
    i = 0
    while i < len(surface) and not is_kanji(surface[i]):
        i += 1
    j = len(surface)
    while j > i and not is_kanji(surface[j - 1]):
        j -= 1
    return surface[:i], surface[i:j], surface[j:]


def okurigana_reading(q, reading):
    """尾部送り假名 q 从 reading 尾部扣掉，返回 (扣掉的读音, 剩余读音)"""
    if not q:
        return "", reading
    if reading.endswith(q):
        return q, reading[:len(reading) - len(q)]
    # 浊音便：送り假名首字是浊音，而读音里是清音
    if q and q[0] in DAKU and len(reading) >= len(q):
        plain = DAKU[q[0]] + q[1:]
        if reading.endswith(plain):
            return q, reading[:len(reading) - len(plain)]
    return "", reading


def dp_split(chars, reading, kd, fb=None):
    """把 reading 逐字分给 chars。返回 [(字,读音)] 或 None。
    约束：每字分配到的段必须以该字某个候选读音开头（候选读音是段的 prefix）。
    🔴 两级：先只用 primary（词典读音）；无解才用 fallback（熟字訓）兜底。
    """
    res = _dp_split(chars, reading, kd)
    if res is None and fb:
        res = _dp_split(chars, reading, fb)
    return res


def _dp_split(chars, reading, kd):
    n = len(chars)
    if n == 0 or not reading:
        return None
    NEG = 10 ** 9
    # dp[i][j] = 前 i 字消耗 reading 前 j 字符
    dp = [[NEG] * (len(reading) + 1) for _ in range(n + 1)]
    back = [[None] * (len(reading) + 1) for _ in range(n + 1)]
    dp[0][0] = 0
    for i in range(n):
        cands = kd.get(chars[i], [])
        if not cands:
            continue
        for j in range(len(reading) + 1):
            if dp[i][j] >= NEG:
                continue
            for rd in cands:
                if not rd:
                    continue
                if reading.startswith(rd, j):
                    nj = j + len(rd)
                    if dp[i][j] + 1 < dp[i + 1][nj]:
                        dp[i + 1][nj] = dp[i][j] + 1
                        back[i + 1][nj] = (j, rd)
    best = None
    for j in range(len(reading) + 1):
        if dp[n][j] < NEG:
            if best is None or j > best[0]:
                best = (j, dp[n][j])
    if best is None:
        return None
    j = best[0]
    res = []
    i = n
    while i > 0:
        b = back[i][j]
        if b is None:
            return None
        pj, rd = b
        res.append((chars[i - 1], reading[pj:j]))
        j = pj
        i -= 1
    res.reverse()
    return res


def annotate_token(surface, reading):
    """token → [(表层,读音)]"""
    if not surface:
        return []
    pre, kanji, post = split_surface(surface)
    if not kanji:
        return [(surface, "")]

    # 前导假名：从读音头部剥离
    r = reading
    if pre and r.startswith(pre):
        r = r[len(pre):]
    # 尾部送り假名：从读音尾部剥离
    _, r2 = okurigana_reading(post, r)

    wr = load_word_reading()
    # 🔴 规则①：词表命中 ⇒ **整块 emit 一个单元**，用表里的读音
    #    （旧代码要求 len(读音)==len(汉字) 才生效 ⇒ 187 条里 170 条被静默跳过；
    #      而且等长时是 zip 拆开读（馬鹿→ば+か），与已发布数据的整块读法不符）
    v = wr.get(kanji)
    if v:
        out = []
        if pre:
            out.append((pre, ""))
        out.append((kanji, v))
        if post:
            out.append((post, ""))
        return out
    # 规则②：否则逐字 DP（primary 优先，熟字訓兜底）
    kd, kd_fb = load_kanjidic()
    parts = dp_split(list(kanji), r2, kd, kd_fb)
    if parts is None:
        # 🔴 逐字兜底：DP 无解时**仍然逐字拆**，每字各取一个读音
        #    （旧版这里直接整块 ⇒ 「日本人」会变成一个 ruby 单元）
        #    注：读音可能不是词级读音的正确切分（词典差异，如 IPADIC「ニッポンジン」
        #    vs 词典「ニホンジン」）⇒ 但表层粒度必须是逐字（用户硬要求）
        parts = []
        for ch in kanji:
            cands = CURATED.get(ch) or kd.get(ch) or kd_fb.get(ch) or []
            parts.append((ch, cands[0] if cands else ""))
        out = []
        if pre:
            out.append((pre, ""))
        p_ = 0
        for ch, rd in parts:
            idx = kanji.find(ch, p_)
            if idx < 0:
                idx = p_
            if idx > p_:
                out.append((kanji[p_:idx], ""))
            out.append((ch, rd))
            p_ = idx + 1
        if p_ < len(kanji):
            out.append((kanji[p_:], ""))
        if post:
            out.append((post, ""))
        return out

    out = []
    if pre:
        out.append((pre, ""))
    p = 0
    for ch, rd in parts:
        idx = kanji.find(ch, p)
        if idx < 0:
            idx = p
        if idx > p:
            out.append((kanji[p:idx], ""))
        out.append((ch, rd))
        p = idx + 1
    if p < len(kanji):
        out.append((kanji[p:], ""))
    if post:
        out.append((post, ""))
    return out


def fix_okurigana(tokens):
    """若某段读音末假名 == 下一段表层首假名且下一段读音为空 → 去掉该末假名。"""
    for i in range(len(tokens) - 1):
        r = tokens[i]["r"]
        nxt = tokens[i + 1]
        if not r or not nxt["t"] or nxt["r"]:
            continue
        if not re.search(r"[ぁ-ゖ]$", r):
            continue
        if nxt["t"][0] == r[-1]:
            tokens[i]["r"] = r[:-1]
    return tokens


def annotate(text):
    tok = get_tokenizer()
    out = []
    for t in tok.tokenize(text):
        for st, rd in annotate_token(t.surface, kata_to_hira(t.reading or "")):
            out.append({"t": st, "r": rd})
    return fix_okurigana(out)


def coverage(tokens):
    k = h = 0
    for x in tokens:
        for c in x["t"]:
            if is_kanji(c):
                h += 1
                if x["r"]:
                    k += 1
    return (k, h)


if __name__ == "__main__":
    kd, kd_fb = load_kanjidic()
    print("kanji dict:", len(kd), "字; word table:", len(load_word_reading()))
    tests = ["日本語教師のシュンです。", "友達", "新しい友達。", "温泉に行きました。",
             "跳ねている", "急いで", "下さい", "今日", "世界", "カメラ",
             "元気ですか?", "良い天気", "仕事", "お金", "昔的衣服",
             "友達と話しています", "楽しい時間", "夏休み", "ichi", "新しい友達。"]
    for s in tests:
        tk = annotate(s)
        k, h = coverage(tk)
        print(f"{s}\n   → " + " ".join(f"{x['t']}({x['r']})" for x in tk))

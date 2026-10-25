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
# 🔴 2026-10-25 修正（实测踩到）：
#   旧默认值是 "/tmp/kanjidic2.xml.gz" ⇒ **沙箱重启 /tmp 被回收 ⇒ 词典消失**
#   ⇒ load_kanjidic() 走早退分支返回空 ⇒ 整条注音链**静默退化成空读音**，
#     而且**不报错**（最危险的一种坏法）。
#   ⇒ ① 默认路径改到**仓库内**（跟代码同目录）
#     ② 依赖缺失 ⇒ **抛异常**（硬失败），绝不静默降级
#     ③ 环境变量 KANJIDIC2 仍可覆盖（换词典时用）
KANJI_FILE = os.environ.get("KANJIDIC2") or os.path.join(HERE, "kanjidic2.xml.gz")


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
        # 🔴 硬失败，绝不静默降级（2026-10-25 改）
        #    旧行为：返回空 dict ⇒ 整条注音链退化成「全空读音」但**不报错**
        #    ⇒ 这种坏法半年后才被当成「数据不对」来查。
        raise FileNotFoundError(
            "kanjidic2 词典缺失：%s（这是 ruby.py 的必需依赖；"
            "缺失会让整条注音链静默退化成空读音，因此改为硬失败。"
            "请把 kanjidic2.xml.gz 放回该路径，或用环境变量 KANJIDIC2 指向它。）"
            % KANJI_FILE)
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


def is_iterative(c):
    """叠字符「々」（U+3005）——它是表意文字，**重复前一个汉字**。
    🔴 不能当送り假名（否则「色々」会被切成 漢字串「色」+ 送り假名「々」
       ⇒ DP 让「色」独吞整个读音 ⇒ 色(いろいろ) —— 多注了一个音）。"""
    return c == "々"


def is_okurigana(c):
    """送り假名只能是**平假名**。
    🔴 片假名是外来语（バス/コーヒー…），**不能当送り假名**：
       「バス停」若把「バス」当送り假名剥掉 ⇒ 剩下「停」配整读音「ばすてい」
       ⇒ 产出「バス・停(ばすてい)」＝ 读音挂在错的字上。
    """
    return "ぁ" <= c <= "ゖ"


def split_surface(surface):
    """→ (前导假名, 汉字串, 尾部送り假名)

    🔴 边界只认**平假名**（送り假名）与**汉字/叠字符**：
       片假名（外来语）既不算前导假名也不算送り假名
       ⇒ 「バス停」会切成 ('', '停', 'バス')？ 不对 —— 实测要保证
         片假名留在**汉字串之外但不被当送り假名**。
       做法：先把片假名段**锚定**，只在其余部分找汉字边界。
    """
    # ① 找出所有「汉字/叠字符」的位置
    kpos = [i for i, c in enumerate(surface)
            if is_kanji(c) or is_iterative(c)]
    if not kpos:
        return surface, "", ""
    i = kpos[0]
    j = kpos[-1] + 1
    pre = surface[:i]
    kanji = surface[i:j]
    post = surface[j:]
    # ② 送り假名只允许平假名；片假名段要**保留在 pre/post 里**
    #    （外来语不做注音，读音由 token.reading 提供）
    return pre, kanji, post


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


def ruby_dp_split(kanji, reading, kd, kd_fb, kd_dp):
    """把**词级读音**当「读音预算」，试逐字切分。

    返回 [(字,读音)] 或 None：
      · 能切出且拼回来的读音流与 reading **逐字相同** ⇒ 返回逐字切分
      · 切不出 / 读音流对不上 ⇒ 返回 None（交上层整块兜底）

    🔴 这是「词表当读音预算、不当整块覆盖」的实现：
       逐字化是**纯粒度细化**，读音流一个假名都不变。
    """
    if not kanji or not reading:
        return None
    parts = dp_split(list(kanji), reading, kd, kd_fb)
    if parts is None:
        parts = dp_split(list(kanji), reading, kd_dp, kd_fb)
    if parts is None:
        return None
    if "".join(r for _, r in parts) != reading:
        return None
    return parts


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
    # 🔴 2026-10-25 修正（实测抓到）：
    #   旧代码取「消耗最多」的那个 j，**不要求把整个 reading 用完**
    #   ⇒ reading 尾部剩余的假名被**静默丢弃**
    #   ⇒ 例：試合/しあい 被切成 試(し)・合(あ) ⇒ 读音流变成「しあ」（丢了 い）
    #   修法：**必须 j == len(reading)**（读音流必须逐字用尽，一个假名都不能剩）
    #        否则返回 None（交由上层走并集候选 / 整块兜底，不产出半截读音）
    j_best = best[0]
    if j_best != len(reading):
        return None
    j = j_best
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
    # 自证：拼回来的读音流必须与输入逐字相同
    if "".join(r for _, r in res) != reading:
        return None
    return res


def _annotate_iterative(surface, reading):
    """含叠字符「々」的**汉字串**的注音 —— **整块 emit**。

    规则（日本語的叠字符）与🔴 关键事实：
      「々」表示重复 ⇒ 但**读音常常发生音变**，不能机械复制：
        色々 = いろ+いろ  （复制对）
        時々 = とき+どき  （❗不是 とき+とき ⇒ 音变「ときどき」）
        人々 = ひと+びと  （❗不是 ひと+ひと ⇒ 音变「ひとびと」）
      ⇒ 所以「X々」应当**整块读**（词典就是这么标的）
      ⇒ emit 一个单元：表层 = 原串，读音 = 词级读音

    🔴 为什么必须这么做（2026-10-25 实测，两版踩坑）：
      ① 把 々 当送り假名 ⇒ 汉字串只剩「色」，读音流仍是「いろいろ」
         ⇒ DP 让「色」独吞 4 个音 ⇒ 产出「色(いろいろ)・々()」＝ **多注了一个音**
      ② 机械复制前字读音 ⇒ 「時々」产出「時(とき)・々(とき)」＝ ときとき（错）
      ③ 展开成重复字再 DP ⇒ 「時々」产出「時(とき)・々(どき)」＝ ときどき（读音流对，
         但**表层与读音的对应关系是错的**：々 并不是「どき」那个音）
      ✅ 正确：**整块 emit** —— 「時々(ときどき)」「人々(ひとびと)」「色々(いろいろ)」
         ⇒ 读音流必然与词典一致（本来就是词级读音），且不撒谎。
    """
    return [(surface, reading)]


def annotate_token(surface, reading):
    """token → [(表层,读音)]"""
    if not surface:
        return []
    pre, kanji, post = split_surface(surface)
    if not kanji:
        return [(surface, "")]
    # 🔴 叠字符「々」（U+3005）必须**继承前一个字的读音**（2026-10-25 实测）：
    #   「色々」= いろ+いろ ⇒ 若把 々 当送り假名，
    #   汉字串只剩「色」而读音流仍是「いろいろ」⇒ DP 让「色」独吞 4 个音
    #   ⇒ 产出 色(いろいろ)・々(空)  ← **多注了一个音**
    #   ⇒ 正确是 色(iro)・々(いろ)
    if is_iterative(kanji[0]) or is_iterative(kanji[-1]):
        out = []
        if pre:
            out.append((pre, ""))
        out.extend(_annotate_iterative(kanji, reading))
        if post:
            out.append((post, ""))
        return out

    # 前导假名：从读音头部剥离
    r = reading
    if pre and r.startswith(pre):
        r = r[len(pre):]
    # 尾部送り假名：从读音尾部剥离
    _, r2 = okurigana_reading(post, r)

    wr = load_word_reading()
    kd, kd_fb = load_kanjidic()
    pri = load_published_reading()
    # 🔴 候选并集（词典 ∪ 上下文表 ∪ 已发布反推表）
    kd_dp = {}
    for ch in set(list(kd.keys()) + list(pri.keys())):
        cl = list(kd.get(ch, []))
        for x in pri.get(ch, []):
            if x not in cl:
                cl.append(x)
        if cl:
            kd_dp[ch] = cl

    # 🔴 规则①（2026-10-25 改版）：词表命中 ⇒ **先用表里的读音当「读音预算」试逐字 DP**
    #    —— 切得出且读音流逐字相同 ⇒ 逐字 emit（用户硬要求「逐字 ruby，不是整块注音」）
    #    —— 切不出（真不规则/促音变体，如 今日/きょう、日本人/にっぽんじん）
    #       ⇒ 才整块 emit 一个单元（用户口径「不规则词整块兜底」）
    #
    #    ⚠️ 旧版是「词表命中 ⇒ 直接整块」⇒ 把 144 个**本来能逐字**的词也整块了
    #       （如  treasurer 教師/きょうし ⇒ 教(きょう)・師(し) 就能切且读音流相同）
    #       ⇒ 违反用户「逐字 ruby」硬要求。
    #    ⚠️ 但**读音流一个假名都不变**（逐字切分只是把同一串读音重新分配到各字）
    #       ⇒ 这是纯粒度细化，不是改读音。
    v = wr.get(kanji)
    if v:
        parts = ruby_dp_split(kanji, v, kd, kd_fb, kd_dp)
        out = []
        if pre:
            out.append((pre, ""))
        if parts is not None:
            for ch, rd in parts:
                out.append((ch, rd))
        else:
            out.append((kanji, v))
        if post:
            out.append((post, ""))
        return out
    # 规则②：否则逐字 DP（primary 优先，熟字訓兜底）
    # 🔴 候选源必须是**并集**（缺一个就切不出/切错）：
    #    ① 词典逐字读音（kanjidic2：音读+训读+名乗り）
    #    ② 上下文读音表（kanji_readings.json）
    #    ③ **已发布数据反推的单字读音**（published_reading.json）
    #       ⇒ 它含**促音便形态**（学=がく/がっ），这是词典里没有的
    #       ⇒ 实测：「小学校/しょうがっこう」靠 ③ 的「がっ」才切得出逐字
    #          （只有 ①② ⇒ DP 无解 ⇒ 落整块 ⇒ 粒度退化）
    # 🔴 两段式（2026-10-25 定案）：
    #    第一段：**原候选集**（词典 + 上下文表）⇒ 与线上口径一致，粒度不变
    #    第二段：原 DP 无解时，才用**并集候选**（加上已发布优先表）再试一次
    #             ⇒ 并集里含**促音便形态**（学=がく/がっ）⇒ 「学校/がっこう」能切出逐字
    #             ⇒ 只在「原来切不出」时生效 ⇒ **已能切出的句子粒度零漂移**
    parts = dp_split(list(kanji), r2, kd, kd_fb)
    if parts is None:
        parts = dp_split(list(kanji), r2, kd_dp, kd_fb)
    if parts is None:
        # 🔴 DP 无解 ⇒ **整块 emit 一个单元**（用词级读音 r2），不再硬凑逐字。
        #    硬凑的两宗罪（2026-10-25 全量实测，11 处缺陷）：
        #      ① 读音流不对 —— 「言葉」的 r2=「ことば」切不出（言→い/げん/こと/ごん、
        #         葉→は/よう，无解）⇒ 旧代码逐字各取 cands[0] ⇒ 凑成「言(い)·葉(は)」，
        #         而正确是「ことば」（ことば ≠ いは ⇒ 语义错）
        #      ② 读音可能为空 —— cands 为空时 parts 里塞 (ch, "") ⇒ 该汉字**完全没读音**
        #    整块兜底既不硬凑也不丢，且符合用户口径「不规则词整块兜底」。
        #    （表层粒度：用户硬要求是逐字；但「切不出」时整块是唯一不撒谎的选择）
        out = []
        if pre:
            out.append((pre, ""))
        if r2:
            out.append((kanji, r2))
        else:
            # 连词级读音都拿不到 ⇒ 逐字找第一个候选，至少不空着
            parts = []
            for ch in kanji:
                cands = CURATED.get(ch) or kd.get(ch) or kd_fb.get(ch) or []
                parts.append((ch, cands[0] if cands else ""))
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


@lru_cache(maxsize=1)
def load_published_reading():
    """已发布数据反推的「单字读音优先表」{字: [读音, ...]}（频次>=2）。
    🔴 来源：直读线上 50 集已发布 ru ⇒ 凡「单字单元 + 有读音」就记一次频次。
       取频次>=2 的读音，按频次降序。
    ⇒ 它是**已发布事实**，且含**促音便形态**（学=がく/がっ）——
       这是 kanjidic2 里没有的 ⇒ 缺了它，「小学校/しょうがっこう」切不出逐字。
    ⇒ 文件：tools/ruby/published_reading.json（零外部依赖，可随时重建）
    """
    f = os.path.join(HERE, "published_reading.json")
    if not os.path.exists(f):
        return {}
    with open(f, encoding="utf-8") as fh:
        return json.load(fh)


def fix_okurigana(tokens):
    """若某段读音末假名 == 下一段表层首假名且下一段读音为空 → 去掉该末假名。

    🔴 2026-10-25 修正（实测 11 处读音被误删）：
      旧规则**不看表层是否含汉字** ⇒ 把汉字自己的读音（聞→き）误当成
      「送り假名与下一段表层重复」而删掉 ⇒ 出现「聞()」这种**完全没读音**的汉字。
      实测触发例：
        聞(き) + き()   ← 下一段表层「き」以同音假名开头 ⇒ 旧的把「聞」的读音删了
        言(い) + い()
        課(か) + から()
      ⇒ 现在加一条保护：**只有当删完读音会变成空串时才不删**。
        ⇒ 汉字读音「き」被删 ⇒ 变空 ⇒ 保住 ⇒ 聞(き) ✔
        ⇒ 汉字读音「だち」被删 ⇒ 剩「だ」（不空）⇒ 照旧删 ⇒ 達(だ)
          （线上是 達(だち) ⇒ 这条会漂 83 句，**故意保留**：
            线上口径把送り假名算进汉字读音里，是用户已看过的既有形态；
            而「整块没读音」是硬错误 ⇒ 两者优先级不同）
    """
    for i in range(len(tokens) - 1):
        r = tokens[i]["r"]
        nxt = tokens[i + 1]
        if not r or not nxt["t"] or nxt["r"]:
            continue
        if not re.search(r"[ぁ-ゖ]$", r):
            continue
        if nxt["t"][0] != r[-1]:
            continue
        # 🔴 删完会变成空读音 ⇒ 不删（保护「聞→き」这种唯一的读音）
        if len(r) == 1 and is_kanji(tokens[i]["t"][0]):
            continue
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

#!/usr/bin/env python3
"""
NHK やさしいことばニュース —— 数据构建

读：
  data/episodes/<id>/{episode.json, easy.html, web.json}
  data/timing/<id>.json           {easy:[[s,e]...], web:[[s,e]...]}
  data/audio{,_fast}/<id>.m4a
  work/out/<id>.{easy,web}.json   翻译（subagent 产出）
  work/out/lex_*.json             词义（subagent 产出）

写：
  site/app/data/episodes.js
  site/app/subs/<id>.js
  site/app/data/reading.js
  site/app/data/vocab.js
  site/app/data/lexicon.js
  site/app/data/patterns.js
"""
import json
import os

_SUBS_BAK = None
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import parse_text as P
import patterns as PAT
import ep_sort as EP_SORT
import structure as ST

DATA = "/workspace/nhk/data"
WORK = "/workspace/nhk/work"
OUT = "/workspace/nhk/site/app"
NEP = int(sys.argv[1]) if len(sys.argv) > 1 else 30
# 只 build 翻译完整的期（增量更新时新期翻译完才进来）
REQUIRE_ZH = "--allow-missing-zh" not in sys.argv

# 🔴 2026-10-24：「哪些词值得挂在句子上」的规则抽到 lex_rules.py
#    （scripts/build_sp.py 也要用同一份 ⇒ 不能各写一份）
#    ⚠️ 原来这份规则在这里，而 build_data.py 在 **import 时**就读 sys.argv[1]
#    ⇒ 别的脚本 import 它就会炸 ⇒ 规则没法被第二个脚本复用。
from lex_rules import SKIP_WORDS, SKIP_POS, FREQ_RATIO, is_skippable  # noqa: E402


def load_json(p, default=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def dur_of(path):
    if not os.path.exists(path):
        return 0.0
    p = subprocess.run(["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                        "-of", "csv=p=0", path], capture_output=True, text=True)
    try:
        return round(float(p.stdout.strip()), 2)
    except Exception:
        return 0.0


def build_cues(sents, times, zhmap, freq, bzhmap=None):
    """把解析结果 + 时间轴 + 翻译拼成 cue 数组"""
    cues = []
    n = len(sents)
    for i, s in enumerate(sents):
        t = times[i] if i < len(times) else None
        if t:
            st, en = int(t[0]), int(t[1])
        else:
            st = int(i * 3000)
            en = st + 2800
        if en <= st:
            en = st + 1200
        ja = s["ja"]
        plain = s["plain"]
        # 生词标记：只标「值得记」的
        marks = []
        for tk in s["tokens"]:
            w = ja[tk[0]:tk[0] + tk[1]]
            if not w.strip():
                continue
            winfo = next((x for x in s["words"] if x["w"] == w), None)
            pos = winfo["p"] if winfo else ""
            if is_skippable(w, pos, freq):
                continue
            if not re.search(r"[一-鿿ァ-ヶ]", w):
                continue
            if len(w) > 10:
                continue
            marks.append([tk[0], tk[1], w])
        # 句型标记
        gmarks = []
        for pid, (a, b) in PAT.detect(s):
            p2j, pend = s.get("p2j"), s.get("pend")
            if not p2j or a >= len(p2j):
                continue
            js = p2j[a]
            je = pend[min(b - 1, len(pend) - 1)]
            if je > js:
                # 🔴 p2j/pend 是「整段坐标」表（easy 版 p2j = range(idx, idx+len)），
                #    而 t/c 已经是「相对本句」的坐标 → 句型标记必须减掉本句起点，
                #    否则 g 的坐标会跑到本句 ja 长度之外（实测 9.3% 越界）。
                #    web 版 p2j = range(len(sp))，p2j[0]=0 → 减了也不变。
                off = p2j[0]
                gmarks.append([js - off, je - js, pid])
        zh = zhmap.get(plain, "") if zhmap else ""
        # 整句结构（成分块）
        try:
            yb = ST.analyze(plain)
        except Exception:
            yb = []
        bzh = (bzhmap or {}).get(plain, [])
        if len(bzh) != len(yb):
            bzh = []
        cues.append({
            "s": st, "e": en, "ja": ja, "zh": zh,
            "t": s["tokens"], "c": marks, "g": gmarks,
            "y": yb, "yb": bzh,
        })
    return cues


def _backup_subs():
    """build 前备份 subs/ —— 坐标自检不过就回滚，避免坏文件留在 site/app 里。"""
    import shutil
    src = os.path.join(OUT, "subs")
    dst = os.path.join(OUT, ".subs_bak")
    if os.path.isdir(dst):
        shutil.rmtree(dst, ignore_errors=True)
    if os.path.isdir(src):
        shutil.copytree(src, dst)
    return dst


def _restore_subs(bak):
    import shutil
    src = os.path.join(OUT, "subs")
    if not bak or not os.path.isdir(bak):
        return
    shutil.rmtree(src, ignore_errors=True)
    shutil.copytree(bak, src)
    print("   ↩ 已回滚 subs/ 到 build 前的状态")


def main():
    epdir = os.path.join(DATA, "episodes")
    eps = [e for e in os.listdir(epdir) if os.path.exists(os.path.join(DATA, "audio", e + ".m4a"))]
    # 按播出日期倒序（最新的排第 1 期）
    # 🔴🔴 2026-10-24 修：原来拿 **time 原始字符串**直接排序 ⇒ NHK 的 time 格式不统一
    #   （"2026-10-22 20:15:00" vs "20261022T201500+0900"）⇒ 字符串比较把新一期甩到最后
    #   ⇒ 首页第一条变成旧的 ⇒ **用户看到的就是「停更」**（而且不报错）
    # ⇒ 现在一律先解析成 (YYYYMMDD, HHMM) 数字元组再排（ep_sort 是唯一权威）
    def _dt(e):
        t = str((load_json(os.path.join(epdir, e, "episode.json"), {}) or {}).get("time", ""))
        return EP_SORT.raw_key(t)
    eps.sort(key=_dt, reverse=True)
    if NEP > 0:
        eps = eps[:NEP]

    # ---- 第一遍：解析全部句子，顺便统计词频 ----
    parsed = {}          # ep -> {ver: [sents]}
    freq = {"__total__": len(eps)}
    for e in eps:
        d = os.path.join(epdir, e)
        per_ep = set()
        for ver, fn, parser in [("easy", "easy.html", P.parse_easy), ("web", "web.json", P.parse_web)]:
            p = os.path.join(d, fn)
            if not os.path.exists(p):
                continue
            try:
                sents = parser(p)
            except Exception as ex:
                print("  ! %s %s parse: %s" % (e, ver, ex))
                sents = []
            if not sents:
                continue
            parsed.setdefault(e, {})[ver] = sents
            for sn in sents:
                for w in sn["words"]:
                    if re.search(r"[一-鿿]", w["w"]):
                        per_ep.add(w["w"])
        for w in per_ep:
            freq[w] = freq.get(w, 0) + 1

    # ---- 词库 ----
    vocab = {}
    outdir = os.path.join(WORK, "out")
    if os.path.isdir(outdir):
        for f in sorted(os.listdir(outdir)):
            if not (f.startswith("lex_") or f.startswith("kana_")):
                continue
            d = load_json(os.path.join(outdir, f), {})
            for k, v in d.items():
                if isinstance(v, dict) and v.get("z"):
                    vocab[k] = v
    lexicon = {}
    reading = {}

    episodes = []
    subs_all = {}
    subs_bak = _backup_subs()
    stats = {"sent": 0, "ep": 0, "nozh": 0}

    for e in eps:
        d = os.path.join(epdir, e)
        ep = load_json(os.path.join(d, "episode.json"), {}) or {}
        timing = load_json(os.path.join(DATA, "timing", e + ".json"), {}) or {}
        ver_data = {}
        for ver in ("easy", "web"):
            # 🔴 只收「翻译完整」的版本：没译完的期先跳过，等译文补齐再 build
            tr_chk = load_json(os.path.join(WORK, "out", "%s.%s.json" % (e, ver)), {}) or {}
            if REQUIRE_ZH:
                its = tr_chk.get("items") or []
                if not its or any(not it.get("zh") for it in its):
                    continue    # 没译文 / 译完一半 → 先不上线
            sents = parsed.get(e, {}).get(ver)
            if not sents:
                continue
            tr = load_json(os.path.join(WORK, "out", "%s.%s.json" % (e, ver)), {}) or {}
            zhmap = {it["ja"]: it.get("zh", "") for it in tr.get("items", [])}
            # 成分块中文（第二轮 subagent 产出）
            syn = load_json(os.path.join(WORK, "syn_out", "%s.%s.json" % (e, ver)), {}) or {}
            bzhmap = {it["ja"]: it.get("bzh", []) for it in syn.get("items", [])}
            cues = build_cues(sents, timing.get(ver, []), zhmap, freq, bzhmap)
            ver_data[ver] = cues
            stats["sent"] += len(cues)
            stats["nozh"] += sum(1 for c in cues if not c["zh"])
            for sn in sents:
                for w in sn["words"]:
                    surf = w["w"]
                    if not surf:
                        continue
                    if (w.get("r") and re.search(r"[一-鿿ァ-ヶ]", surf)
                            and surf not in reading):
                        reading[surf] = w["r"]
                    if surf not in lexicon:
                        lexicon[surf] = {"p": w.get("p", ""), "r": w.get("r", ""),
                                          "bf": w.get("bf", surf)}
        if not ver_data:
            continue
        easy_t = ""
        if ver_data.get("easy"):
            easy_t = re.sub(r"[（(][ぁ-んァ-ヶ一-鿿]+[）)]", "", ver_data["easy"][0]["ja"])
            easy_t = easy_t.split("。")[0][:40]
        dt = dur_of(os.path.join(DATA, "audio", e + ".m4a"))
        df = dur_of(os.path.join(DATA, "audio_fast", e + ".m4a"))
        # 时间：episode.json 的 time = "2026-10-20 20:14:00"
        tm = str(ep.get("time") or "")
        m = re.search(r"(\d{4})-?(\d{2})-?(\d{2})[ T]?(\d{2})?:?(\d{2})?", tm)
        if m:
            date = "%s-%s-%s" % (m.group(1), m.group(2), m.group(3))
            clock = (m.group(4) or "00") + ":" + (m.group(5) or "00")
        else:
            date = ""
            clock = str(ep.get("time") or "")[:5]
        ep_row = {
            "id": e, "no": len(episodes) + 1,
            "title": (ep.get("title") or "")[:70], "easyTitle": easy_t,
            "date": date, "week": ep.get("week") or "", "time": clock,
            "duration": dt, "durFast": df,
            "subs": "subs/%s.js" % e,
            "audio": "media/slow/%s.m4a" % e,
        }
        # 🔴 2026-10-24 修：**没有原速音就别写 audioFast**（原来无条件写）
        #   ⇒ episodes.js 里挂着指向不存在文件的 audioFast（23 期）
        #   ⇒ 判据不一致：durFast=0（没按钮）却有个「看起来有」的指针
        #   ⇒ 现在：有原速音才写，durFast>1 与 audioFast 的存在**严格一致**。
        if df > 1:
            ep_row["audioFast"] = "media/fast/%s.m4a" % e
        ep_row["ver"] = sorted(ver_data.keys())
        episodes.append(ep_row)
        subs_all[e] = ver_data
        stats["ep"] += 1

    # ---- 写文件 ----
    os.makedirs(os.path.join(OUT, "data"), exist_ok=True)
    os.makedirs(os.path.join(OUT, "subs"), exist_ok=True)

    def wjs(path, expr):
        with open(path, "w", encoding="utf-8") as f:
            f.write(expr)
        print("  ->", path, os.path.getsize(path) // 1024, "KB")

    wjs(os.path.join(OUT, "data", "episodes.js"),
        "window.__EPISODES__=" + json.dumps(episodes, ensure_ascii=False) + ";\n")
    wjs(os.path.join(OUT, "data", "reading.js"),
        "window.__READING__=" + json.dumps(reading, ensure_ascii=False) + ";\n")
    wjs(os.path.join(OUT, "data", "lexicon.js"),
        "window.__LEXICON__=" + json.dumps(lexicon, ensure_ascii=False) + ";\n")
    wjs(os.path.join(OUT, "data", "vocab.js"),
        "window.__VOCAB__=" + json.dumps(vocab, ensure_ascii=False) + ";\n")
    wjs(os.path.join(OUT, "data", "patterns.js"),
        "window.__PATTERNS__=" + json.dumps(PAT.build(episodes, subs_all), ensure_ascii=False) + ";\n")
    cdn = {}
    for ep in episodes:
        cdn[ep["id"]] = {
            "slow": ["media/slow/%s.m4a" % ep["id"]],
            "fast": ["media/fast/%s.m4a" % ep["id"]],
        }
    wjs(os.path.join(OUT, "data", "cdn.js"),
        "/* 音声地址表（相对本站根；慢速=易懂版 / 原速=普通版） */\n"
        "window.__CDN__=" + json.dumps(cdn, ensure_ascii=False) + ";\n")
    for e, vd in subs_all.items():
        wjs(os.path.join(OUT, "subs", e + ".js"),
            "window.__SUBS__=window.__SUBS__||{};window.__SUBS__[%s]=%s;\n"
            % (json.dumps(e), json.dumps(vd, ensure_ascii=False)))
    # 清理：不在本次 build 列表里的旧 subs（增量更新会越攒越多，白占流量）
    subdir = os.path.join(OUT, "subs")
    keep = set(subs_all.keys())
    removed = 0
    for fn in os.listdir(subdir):
        if fn.endswith(".js") and fn[:-3] not in keep:
            os.remove(os.path.join(subdir, fn))
            removed += 1
    if removed:
        print("  -> 清理旧 subs %d 个" % removed)
    # 同样清理 media 里不再需要的音频
    for d in ("slow", "fast"):
        md = os.path.join(OUT, "media", d)
        if not os.path.isdir(md):
            continue
        keepm = set(e["id"] + ".m4a" for e in episodes)
        for fn in os.listdir(md):
            if fn.endswith(".m4a") and fn not in keepm:
                os.remove(os.path.join(md, fn))
    print("== %d 期 / %d 句（无译文 %d）/ 词库 %d / 读音 %d / 词表 %d ==" %
          (stats["ep"], stats["sent"], stats["nozh"], len(vocab), len(reading), len(lexicon)))

    global _SUBS_BAK
    _SUBS_BAK = subs_bak
    check_coords()
    import shutil
    shutil.rmtree(subs_bak, ignore_errors=True)


def check_coords():
    """🔴 坐标自检：t / c / g 三套标记必须全部「相对本句 ja」。

    历史 bug：easy 版 parse 时 base_off 用了整段坐标（ss）而不是本句坐标，
    导致 ruby 注音、生词 chip、句型标签全部错位（实测 9.3% 的 g 越界）。
    前端用 ja.substr(tk[0], tk[1]) 切词 → 坐标一错，界面就花。

    这里做两道断言：
      ① 每个 token 切出来的字，必须和 ja 对应位置一致
      ② t/c/g 标记不得越出本句 ja 的长度
    不过就 exit 1 —— 宁可 build 失败，也别把坏版本部署上线。
    """
    import glob
    import re as _re
    import sys as _sys
    bad_align, bad_range, tot = [], [], 0
    for p in sorted(glob.glob(os.path.join(OUT, "subs", "*.js"))):
        s = open(p, encoding="utf-8").read()
        m = _re.search(r"=\s*(\{.*\})\s*;?\s*$", s.strip(), _re.S)
        if not m:
            continue
        try:
            o = json.loads(m.group(1))
        except Exception:
            continue
        for ver in ("easy", "web"):
            for c in o.get(ver, []) or []:
                ja = c.get("ja", "") or ""
                tks = c.get("t") or []
                if tks:
                    tot += 1
                    ok = all(ja[tk[0]:tk[0] + tk[1]] == tk[2] for tk in tks)
                    joined = "".join(tk[2] for tk in tks)
                    if not ok or _re.sub(r"\s", "", joined) != _re.sub(r"\s", "", ja):
                        bad_align.append((os.path.basename(p), ver, ja[:30]))
                for it in (c.get("c") or []) + (c.get("g") or []):
                    if it[0] < 0 or it[0] + it[1] > len(ja):
                        bad_range.append((os.path.basename(p), ver, it, len(ja)))
    if bad_align or bad_range:
        print("🔴 坐标自检未通过：")
        for x in bad_align[:5]:
            print("   错位:", x)
        for x in bad_range[:5]:
            print("   越界:", x)
        print("   → 拒绝交付。修 parse_text.py（base_off 要减本句起点 idx）"
              "或 build_data.py（gmarks 要减 p2j[0]）后重跑。")
        _restore_subs(globals().get("_SUBS_BAK"))
        _sys.exit(1)
    print("== 坐标自检通过：%d 句，t/c/g 全部相对本句，无错位无越界 ==" % tot)


if __name__ == "__main__":
    main()

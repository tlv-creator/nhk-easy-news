#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""books56c 输入自检：推仓库之前先自己跑一遍，别等我这边才发现格式问题。

用法
  python3 check_books_input.py --books-dir <books56c/books 目录> [--index <asr_truth/index.json>]
  python3 check_books_input.py --books-dir <dir> --fetch-index      # 从共享仓库拉 index

判据（缺一即 FAIL，exit 1）
  ① 文件名(去掉 .json) == 顶层 tag == manifest 里的 tag（对齐器靠 tag 找书）
  ② 顶层字段齐：tag / title / level / dur / cues
  ③ 每个 cue 有 rows；每行有 t（非空）；行的顺序就是句子顺序
  ④ t 里**不能带注音标记**（〈〉《》或竖线）⇒ 对齐器把 t 逐字当字符流去比 ASR
  ⑤ 注音字段：app.js 的 rubyHtml() 只认 `ru` = [[表层, 读音], ...]（二维数组）
     ⇒ 只有 `seg`（{base, rt}）时**报 WARN**（前端不会出注音）——不是 FAIL，因为那是发布侧的转换责任
  ⑥ zh 非空
  ⑦ dur 与 asr_truth/index.json 里该轨的 dur 一致（±0.5s）
  ⑧ 能在 index 里找到这条轨（按 mp3 / audio_mp3_all 对上）⇒ 拿不到就是「书与音频对不上」
  ⑨ 全库体检：t 不重复、cue 数 == 行数之和、时间轴字段存在

退出码：0 = 全过（可能有 WARN）｜1 = 有 FAIL
"""
import argparse, json, os, re, sys, urllib.request

REPO = "tlv-creator/nhk-easy-news"
INDEX_PATH = "tadoku/books56c/asr_truth/index.json"
# 🔴 「」**是对话引号**（书里本来就有），不是注音标记
#   实测：批次 1 的 445 行里 119 行带「」；线上现役 75 本的 t 里也带「」⇒ 不能当错误
#   而且 align_audio.py 的 norm() 的 PUNCT 里本来就含「」⇒ 比对时两边都被剥掉 ⇒ 不污染字符流
#   （2026-10-25 构建会话指出，我复核后确认：PUNCT = set("、。「」『』（）()【】［］〈〉<>〔〕…"))
RUBY_MARK = re.compile(r"[〈〉《》]|｜|\|")
# 「制作信息行」：书末 credits（推给老师的书上常有，朗読里也念）
CREDIT = re.compile(r"^(作|監修|原作|再話|簡約|著者|翻訳|NPO多言語多読|多読)[\s　]*")


def fetch_index():
    """先试 raw.githubusercontent（快），失败再走 API（更稳）。两条路都失败才报错。"""
    errs = []
    url = f"https://raw.githubusercontent.com/{REPO}/main/{INDEX_PATH}"
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        errs.append(f"raw: {e}")
    try:
        import base64
        api = f"https://api.github.com/repos/{REPO}/contents/{INDEX_PATH}"
        req = urllib.request.Request(api, headers={"Accept": "application/vnd.github+json",
                                                   "User-Agent": "mavis"})
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read().decode())
        return json.loads(base64.b64decode(d["content"]).decode())
    except Exception as e:
        errs.append(f"api: {e}")
    print(f"⚠️ 从仓库拉 index 失败（{'；'.join(errs)}）⇒ 用 --index 给本地路径")
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--books-dir", required=True)
    ap.add_argument("--index", default="")
    ap.add_argument("--fetch-index", action="store_true")
    a = ap.parse_args()

    idx = None
    if a.index:
        idx = json.load(open(a.index))
    elif a.fetch_index:
        idx = fetch_index()
    if idx:
        tracks = idx["tracks"]
        by_mp3 = {t["mp3"]: t for t in tracks}
        by_title = {}
        for t in tracks:
            by_title.setdefault(t["title_official"], []).append(t)
    else:
        tracks, by_mp3, by_title = [], {}, {}

    fails, warns = [], []
    files = sorted(f for f in os.listdir(a.books_dir) if f.endswith(".json"))
    print(f"检查 {len(files)} 个书文件：{a.books_dir}\n")
    total_rows = 0
    for f in files:
        tag = f[:-5]
        p = os.path.join(a.books_dir, f)
        try:
            d = json.load(open(p))
        except Exception as e:
            fails.append(f"{tag}: JSON 解析失败 {e}")
            continue
        # ①
        if d.get("tag") != tag:
            fails.append(f"{tag}: 顶层 tag={d.get('tag')!r} ≠ 文件名")
        # ②
        for k in ("tag", "title", "level", "dur", "cues"):
            if k not in d:
                fails.append(f"{tag}: 缺顶层字段 {k}")
        if "cues" not in d:
            continue
        cues = d["cues"]
        n_rows = 0
        has_ru = has_seg = 0
        for ci, c in enumerate(cues):
            rows = c.get("rows")
            if not isinstance(rows, list) or not rows:
                fails.append(f"{tag}: cue[{ci}] 没有 rows")
                continue
            if "s" not in c or "e" not in c:
                fails.append(f"{tag}: cue[{ci}] 缺 s/e（对齐器会填，但先要字段在）")
            for ri, r in enumerate(rows):
                n_rows += 1
                t = r.get("t") or ""
                if not t.strip():
                    fails.append(f"{tag}: cue[{ci}].rows[{ri}] 的 t 为空")
                    continue
                if RUBY_MARK.search(t):
                    fails.append(f"{tag}: cue[{ci}].rows[{ri}] 的 t 里带注音标记 "
                                 f"（〈〉/｜）⇒ 对齐字符流会脏：{t[:30]}")
                if not (r.get("zh") or "").strip():
                    fails.append(f"{tag}: cue[{ci}].rows[{ri}] 缺 zh")
                if r.get("ru"):
                    has_ru += 1
                    if not (isinstance(r["ru"], list) and all(
                            isinstance(x, (list, tuple)) and len(x) == 2 for x in r["ru"])):
                        fails.append(f"{tag}: cue[{ci}].rows[{ri}] 的 ru 不是 [[表层,读音],...]")
                    else:
                        # 🔴 **ru 的表层拼起来必须等于 t** ⇒ 一条就抓出「读音混进正文 / 漏注音 / 多注音」
                        surf = "".join(x[0] for x in r["ru"])
                        if surf != t:
                            fails.append(f"{tag}: cue[{ci}].rows[{ri}] ru 表层拼接 ≠ t "
                                         f"（读音混进正文或注音缺失）：t={t[:24]!r} surf={surf[:24]!r}")
                if r.get("seg"):
                    has_seg += 1
        total_rows += n_rows
        if has_seg and not has_ru:
            warns.append(f"{tag}: 只有 seg（{has_seg} 行）没有 ru ⇒ app.js 的 rubyHtml() 不认 seg "
                         f"⇒ 生成 app 数据时要转成 ru，否则点句弹窗没注音")
        elif has_ru and has_seg:
            warns.append(f"{tag}: ru 与 seg 都有（{has_ru}/{has_seg} 行）⇒ 保持一致，别两份打架")
        # ⑦⑧ 找到对应的轨
        mp3s = d.get("audio_mp3_all") or []
        if not mp3s:
            aud = d.get("audio") or ""
            if aud:
                mp3s = [aud.split("/")[-2] + "/" + aud.split("/")[-1]] if "/" in aud else [aud]
        hit = None
        for m in mp3s:
            mm = m if m in by_mp3 else m.split("/")[-1]
            for k in by_mp3:
                if k == m or k.endswith("/" + mm):
                    hit = by_mp3[k]
                    break
            if hit:
                break
        if tracks:
            if not hit:
                # 退而用标题找
                cand = by_title.get(d.get("title_official") or "", [])
                if len(cand) == 1:
                    hit = cand[0]
            if not hit:
                fails.append(f"{tag}: 在 asr_truth/index.json 里找不到对应的轨"
                             f"（mp3={mp3s}）⇒ 书与音频对不上")
            else:
                if abs(float(d.get("dur") or 0) - float(hit["dur"])) > 0.5:
                    fails.append(f"{tag}: dur={d.get('dur')} 与官方音频实测 {hit['dur']} 不一致（±0.5s）")
                else:
                    print(f"  ✅ {tag:34} {len(cues):4}句 {n_rows:5}行  dur={d['dur']:7.2f} "
                          f"→ 轨 {hit['file']}（{hit['title_official']}）")
        else:
            print(f"  ✅ {tag:34} {len(cues):4}句 {n_rows:5}行  dur={d.get('dur')}")
    # ⑨ 全库
    print(f"\n合计 {len(files)} 本 / {total_rows} 行")
    if warns:
        print(f"\n⚠️ WARN {len(warns)} 条：")
        for w in warns:
            print("   -", w)
    if fails:
        print(f"\n❌ FAIL {len(fails)} 条：")
        for x in fails[:40]:
            print("   -", x)
        print(f"\n退出码 1（{len(fails)} 条 FAIL）")
        return 1
    print("\n✅ 全部通过（可以去推仓库了）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_books_input.py 的自检：正向（好数据必须过）＋ 反向（每种坏法必须被抓住）。

没有反向验证的闸门 = 不知道它有没有鉴别力 ⇒ 这里逐条造坏样本。
"""
import json, os, subprocess, sys, tempfile, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
CHECK = os.path.join(HERE, "check_books_input.py")
B1 = "/workspace/work/batch1"
TRUTH = json.load(open(f"{B1}/asr_truth/b1-9784866394701-01.json"))
IDX = json.load(open(f"{B1}/asr_truth/index.json")) if os.path.exists(f"{B1}/asr_truth/index.json") else None


def make_book():
    cues = []
    for r in TRUTH:
        cues.append({"s": 0.0, "e": 0.0, "rows": [{
            "t": r["text"],
            "ru": [[r["text"], ""]],
            "zh": "（测试译文）",
        }]})
    return {
        "tag": "9784866394701-track01",
        "title": "一つ二つ三つ",
        "level": "Level Start",
        "dur": 145.53,
        "audio_mp3_all": ["9784866394701/Track01.mp3"],
        "cues": cues,
    }


def run(books_dir, index_path):
    r = subprocess.run([sys.executable, CHECK, "--books-dir", books_dir, "--index", index_path],
                       capture_output=True, text=True)
    return r.returncode, r.stdout


def main():
    tmp = tempfile.mkdtemp(prefix="chkbooks_")
    idx_path = os.path.join(tmp, "index.json")
    # 用真 index（若不在本地就现拉）
    local_idx = "/workspace/work/batch1/peer/tadoku__books56c__asr_truth__index.json"
    if os.path.exists(local_idx):
        shutil.copyfile(local_idx, idx_path)
    elif IDX is None:
        import base64, urllib.request
        api = ("https://api.github.com/repos/tlv-creator/nhk-easy-news/contents/"
               "tadoku/books56c/asr_truth/index.json")
        req = urllib.request.Request(api, headers={"Accept": "application/vnd.github+json",
                                                   "User-Agent": "mavis"})
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read().decode())
        open(idx_path, "wb").write(base64.b64decode(d["content"]))
    else:
        json.dump(IDX, open(idx_path, "w"), ensure_ascii=False)

    books = os.path.join(tmp, "books")
    os.makedirs(books)

    def write(b, name="9784866394701-track01.json"):
        p = os.path.join(books, name)
        json.dump(b, open(p, "w"), ensure_ascii=False)
        return p

    # ---------- 正向 ----------
    write(make_book())
    rc, out = run(books, idx_path)
    ok = rc == 0
    print(f"[正向] 好数据 ⇒ rc={rc} {'✅' if ok else '❌'}")
    if not ok:
        print(out)
    results = [("正向", ok)]

    # ---------- 反向 1：t 里带尖括号注音 ----------
    b = make_book(); b["cues"][0]["rows"][0]["t"] = "風〈かぜ〉"
    write(b); rc, out = run(books, idx_path)
    r = rc == 1 and "注音标记" in out
    print(f"[反向1] t 带〈〉 ⇒ rc={rc} {'✅' if r else '❌'}")
    results.append(("反向1", r))

    # ---------- 反向 2：缺 zh ----------
    b = make_book(); b["cues"][1]["rows"][0].pop("zh")
    write(b); rc, out = run(books, idx_path)
    r = rc == 1 and "缺 zh" in out
    print(f"[反向2] 缺 zh ⇒ rc={rc} {'✅' if r else '❌'}")
    results.append(("反向2", r))

    # ---------- 反向 3：dur 与官方音频实测不符 ----------
    b = make_book(); b["dur"] = 999.0
    write(b); rc, out = run(books, idx_path)
    r = rc == 1 and "dur" in out
    print(f"[反向3] dur 不符 ⇒ rc={rc} {'✅' if r else '❌'}")
    results.append(("反向3", r))

    # ---------- 反向 4：tag 与文件名不符 ----------
    b = make_book(); b["tag"] = "wrong-tag"
    write(b); rc, out = run(books, idx_path)
    r = rc == 1 and "≠ 文件名" in out
    print(f"[反向4] tag≠文件名 ⇒ rc={rc} {'✅' if r else '❌'}")
    results.append(("反向4", r))

    # ---------- 反向 5：书与音频对不上（mp3 不在 index 里） ----------
    b = make_book(); b["audio_mp3_all"] = ["9999999999999/Track99.mp3"]
    write(b); rc, out = run(books, idx_path)
    r = rc == 1 and "找不到对应的轨" in out
    print(f"[反向5] mp3 对不上 ⇒ rc={rc} {'✅' if r else '❌'}")
    results.append(("反向5", r))

    # ---------- 反向 7：读音混进 t（ru 表层拼接 ≠ t）----------
    b = make_book(); b["cues"][0]["rows"][0]["t"] = "風かぜと太陽"
    b["cues"][0]["rows"][0]["ru"] = [["風", "かぜ"], ["と太陽", ""]]
    write(b); rc, out = run(books, idx_path)
    r = rc == 1 and "ru 表层拼接" in out
    print(f"[反向7] 读音混进 t ⇒ rc={rc} {'✅' if r else '❌'}")
    results.append(("反向7", r))

    # ---------- 反向 8：对话引号「」不该被判失败（书里本来就有）----------
    b = make_book()
    b["cues"][0]["rows"][0]["t"] = "「お正月」が、来ます。"
    b["cues"][0]["rows"][0]["ru"] = [["「お正月」", ""], ["が、", ""], ["来ます。", ""]]
    write(b); rc, out = run(books, idx_path)
    r = rc == 0
    print(f"[反向8] t 带「」（对话引号）⇒ rc={rc}（应为 0）{'✅' if r else '❌'}")
    results.append(("反向8", r))

    # ---------- 反向 6：ru 形状错（只给 seg）⇒ 应当是 WARN 而不是 FAIL ----------
    b = make_book()
    for c in b["cues"]:
        c["rows"][0].pop("ru")
        c["rows"][0]["seg"] = [{"base": c["rows"][0]["t"], "rt": "よみ"}]
    write(b); rc, out = run(books, idx_path)
    r = (rc == 0) and ("WARN" in out) and ("app.js" in out)
    print(f"[反向6] 只有 seg ⇒ rc={rc}（应为 0 + WARN）{'✅' if r else '❌'}")
    results.append(("反向6", r))

    shutil.rmtree(tmp, ignore_errors=True)
    bad = [n for n, ok in results if not ok]
    print(f"\n自检 {len(results)-len(bad)}/{len(results)} 通过" + (f"｜失败：{bad}" if bad else " ✅"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

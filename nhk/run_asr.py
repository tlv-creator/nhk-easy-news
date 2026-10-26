#!/usr/bin/env python3
"""
批量 ASR + 对齐：给每期生成时间轴。

产出：
  timing/<id>.json      { easy: [[s,e],...], web: [[s,e],...] }
  只处理已经有音频 + 文本的期；缺哪个版本就跳过哪个版本。

用法： python3 run_asr.py [期数] [数据目录]
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import align
import parse_text

DATA = sys.argv[2] if len(sys.argv) > 2 else "/workspace/nhk/data"
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 30
MODEL = os.environ.get("ASR_MODEL", "small")
# --only-web：只补 web 版时间轴（easy 版已有就别重算，省一半时间）
ONLY_WEB = "--only-web" in sys.argv
# 只处理清单里的期（ASR_ONLY 指向一个文件，一行一个期号）
# 🔴 2026-10-22 修：原来只认环境变量 ASR_ONLY，命令行传 --only-list <file> 会被**静默忽略**
#    （表现：跑出来「ASR 完成 4 期」但只处理了 1 期，且没有任何报错 —— 极难查）
#    ⇒ 现在两种都支持，且给了不存在的文件会直接报错退出，不许静默跑全量。
ONLY_LIST = os.environ.get("ASR_ONLY", "")
if "--only-list" in sys.argv:
    _i = sys.argv.index("--only-list")
    if _i + 1 < len(sys.argv):
        ONLY_LIST = sys.argv[_i + 1]
    else:
        sys.exit("--only-list 后面要跟文件路径")
if ONLY_LIST and not os.path.exists(ONLY_LIST):
    sys.exit("清单文件不存在: %s（不許静默跑全量）" % ONLY_LIST)

_model = None


def model():
    global _model
    if _model is None:
        _model = align.get_model(MODEL)
    return _model


def to_wav(src, dst):
    if os.path.exists(dst) and os.path.getsize(dst) > 1000:
        return dst
    p = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src,
                        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", dst],
                       capture_output=True, text=True, timeout=300)
    if p.returncode != 0 or not os.path.exists(dst):
        return None
    return dst


def main():
    tdir = os.path.join(DATA, "timing")
    os.makedirs(tdir, exist_ok=True)
    m = model()
    print("model ready (%s)" % MODEL, flush=True)

    eps = sorted(os.listdir(os.path.join(DATA, "episodes")), reverse=True)
    if ONLY_LIST:
        lst = [x.strip() for x in open(ONLY_LIST, encoding="utf-8") if x.strip()]
        eps = [e for e in eps if e in lst]
        print("按清单限定 %d 期" % len(eps), flush=True)
    n = 0
    for ep in eps:
        if n >= LIMIT:
            break
        d = os.path.join(DATA, "episodes", ep)
        outp = os.path.join(tdir, ep + ".json")
        if os.path.exists(outp):
            if not ONLY_WEB:
                n += 1
                continue
            # 只补 web 版：fast 音不在就别费事
            fast_p0 = os.path.join(DATA, "audio_fast", ep + ".m4a")
            if not os.path.exists(fast_p0):
                continue
            try:
                prev = json.load(open(outp, encoding="utf-8"))
            except Exception:
                prev = {}
            if prev.get("web"):
                continue          # 已有 web 时间轴
        else:
            prev = {}
        easy_p = os.path.join(d, "easy.html")
        web_p = os.path.join(d, "web.json")
        slow_p = os.path.join(DATA, "audio", ep + ".m4a")
        fast_p = os.path.join(DATA, "audio_fast", ep + ".m4a")
        has_audio = os.path.exists(slow_p) or os.path.exists(fast_p)
        if not os.path.exists(easy_p) or not has_audio:
            continue
        res = {}
        t0 = time.time()
        if ONLY_WEB:
            # easy 版时间轴沿用旧的，只补 web
            res["easy"] = prev.get("easy", [])
            res["easy_dur"] = prev.get("easy_dur", 0)
            easy = []
        else:
            try:
                easy = parse_text.parse_easy(easy_p)
            except Exception as e:
                print("  %s easy parse fail %s" % (ep, e), flush=True)
                easy = []
        if easy and os.path.exists(slow_p):
            wav = to_wav(slow_p, "/tmp/_asr_%s.wav" % ep)
            if wav:
                try:
                    words, dur = align.transcribe(wav, m)
                    res["easy"] = align.align(words, [s["plain"] for s in easy])
                    res["easy_dur"] = dur
                except Exception as e:
                    print("  %s easy asr fail %s" % (ep, e), flush=True)
        if os.path.exists(web_p) and os.path.exists(fast_p):
            try:
                web = parse_text.parse_web(web_p)
            except Exception:
                web = []
            if web:
                wav = to_wav(fast_p, "/tmp/_asrf_%s.wav" % ep)
                if wav:
                    try:
                        words, dur = align.transcribe(wav, m)
                        res["web"] = align.align(words, [s["plain"] for s in web])
                        res["web_dur"] = dur
                    except Exception as e:
                        print("  %s web asr fail %s" % (ep, e), flush=True)
        with open(outp, "w", encoding="utf-8") as f:
            json.dump(res, f)
        n += 1
        print("  [%3d] %s  easy=%s web=%s  %.0fs" % (
            n, ep, len(res.get("easy", [])), len(res.get("web", [])),
            time.time() - t0), flush=True)
        for tmp in ("/tmp/_asr_%s.wav" % ep, "/tmp/_asrf_%s.wav" % ep):
            if os.path.exists(tmp):
                os.remove(tmp)
    print("== ASR 完成 %d 期 ==" % n, flush=True)


if __name__ == "__main__":
    main()

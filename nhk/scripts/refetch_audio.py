#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""refetch_audio.py —— 重抓某一期的音声，并且**用体检判据验收**（不合格就重来）

🔴 为什么需要这个（2026-10-23 抓到）：
  慢速音是从 **HLS m3u8 抓 TS 分片拼**出来的 ⇒ 偶发会抓到**坏帧**。
  实测 20261016de60356：整段 46 秒里有 **3 处爆发性爆音**，
  最狠的一处峰值 **+74.7 dBFS**、该秒 9.34% 的样本超出满刻度
  ⇒ 用户听到的是「啪啪啪」三声炸响。

  🔴 之前的下载流程**只判「文件大小 > 20000」就算成功** ⇒ 坏文件直接上线了。
  ⇒ 现在：**下完必须过体检**（真峰值 / 平顶占比），不过就重抓。

用法：
  python3 scripts/refetch_audio.py 20261016de60356            # 重抓 + 体检 + 落盘
  python3 scripts/refetch_audio.py 20261016de60356 --check-only  # 只体检现有文件
  python3 scripts/refetch_audio.py --all                      # 全量体检（体检有问题的自动重抓）
"""
import argparse
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import auth                                    # noqa: E402

# 🔴 fetch_batch.py 在**导入时**就解析 sys.argv（LIMIT = int(sys.argv[1])）
#    ⇒ 导入前先把 argv 藏起来，不然传期号会炸。
_ARGV = list(sys.argv)
sys.argv = [sys.argv[0]]
try:
    from fetch_batch import media_token            # noqa: E402
finally:
    sys.argv = _ARGV

from audio_health import probe, FLAT_RATIO_LIMIT, PEAK_LIMIT_DB   # noqa: E402
import audio_recipe  # noqa: E402  （配方唯一权威）

DATA = os.path.join(ROOT, "data")
SITE = os.path.join(ROOT, "site/app")
UA = auth.UA


# ── 真峰值：解码成原始样本算（比 astats 更硬，astats 也会被 clamp 影响）──
def measure(path):
    """一次解码量出：真峰值(dBFS) / 超满刻度占比(4.0 硬失败口径) / 轻微超限占比(1.2 警告口径) / 时长

    🔴🔴 **口径三处都必须改对**（2026-10-23 我自己踩了三次）：
      ① **不能用 `-ac 1`**（单声道下混是**另一个信号** ⇒ 比设备实际播的高 3 dB）
      ② **不能用 `-ar 48000`**（重采样器**自己会造出**超满刻度样本 ⇒ 同一文件 0.1009% → 0.1656%）
      ③ **不能用 `|x| > 1.0` 当硬失败口径**（合法满幅有损内容就有 **0.942%** ⇒ 阈值 0.05% 的 19 倍 ⇒ **恒红**）
         ⇒ 硬失败用 **4.0 口径**（正常内容恒 0.000%），**1.2 口径只做警告**
    ⇒ astats 的 `Peak level` 本来就是原生口径（以它为准）
    """
    try:
        import numpy as np
    except Exception:
        np = None
    p = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-f", "f32le", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=300)
    d = p.stdout
    n = len(d) // 4
    if n == 0:
        return None, 0.0, 0.0, 0.0
    if np is not None:
        a = np.frombuffer(d[:n * 4], dtype=np.float32)
        if len(a) % 2 == 0:                      # 原生立体声 ⇒ 逐声道取最大
            ch = a.reshape(-1, 2)
            m = np.abs(ch).max(axis=1)
            over4 = 100.0 * float((np.abs(ch) > 4.0).sum()) / np.abs(ch).size
            over12 = 100.0 * float((np.abs(ch) > 1.2).sum()) / np.abs(ch).size
        else:
            m = np.abs(a)
            over4 = 100.0 * float((m > 4.0).sum()) / len(m)
            over12 = 100.0 * float((m > 1.2).sum()) / len(m)
        pk = float(m.max())
    else:
        v = struct.unpack("<%df" % n, d[:n * 4])
        pk = max(abs(x) for x in v)
        over4 = 100.0 * sum(1 for x in v if abs(x) > 4.0) / n
        over12 = 100.0 * sum(1 for x in v if abs(x) > 1.2) / n
    import math
    db = (20.0 * math.log10(pk)) if pk > 0 else -999.0
    return db, over4, over12, n / 48000.0


def m3u8_expected_dur(m3u8):
    """从 HLS 播放列表算**期望时长**（#EXTINF 求和）—— 用作「输出被截断」的闸门依据"""
    import urllib.request
    try:
        req = urllib.request.Request(m3u8, headers={"Referer": "https://news.web.nhk/",
                                                    "User-Agent": "Mozilla/5.0"})
        txt = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
    except Exception:
        return None
    tot = 0.0
    for line in txt.splitlines():
        if line.startswith("#EXTINF:"):
            try:
                tot += float(line.split(":")[1].split(",")[0])
            except Exception:
                pass
    return tot or None


def health(path, expect_dur=None):
    """返回 (ok, 报告dict)

    🔴 **判据分三类，别混**（2026-10-23 与 Shun 对拍后的定版）：
       硬失败：削平（平顶 > 5%）｜垃圾样本（超满刻度 **4.0 口径** > 0.05%）｜**时长对不上**
       警告  ：过冲（峰值 > 0 dBFS）｜轻微超限（**1.2 口径** > 0.05%）
    ⇒ 混成一个判据 = 假警报（把 0.002% 的过冲报成爆音）或假绿灯（漏掉真坏帧）
    """
    p = probe(path)
    tp, over4, over12, dur = measure(path)
    rep = {"peak_db": p.get("peak_db"), "flat_ratio": p.get("flat_ratio"),
           "true_peak_db": tp, "over_pct": over4, "over_pct_mild": over12,
           "lufs": p.get("lufs"), "dur": round(dur, 2)}
    bad = []
    warn = []
    if p.get("flat_ratio") is not None and p["flat_ratio"] > FLAT_RATIO_LIMIT:
        bad.append("削平：平顶占比 %.2f%% > %.1f%%" % (p["flat_ratio"], FLAT_RATIO_LIMIT))
    if over4 > 0.05:
        bad.append("垃圾样本：超满刻度(4.0口径) %.4f%% > 0.05%%" % over4)
    if tp is not None and tp > PEAK_LIMIT_DB:
        warn.append("过冲：真峰值 %.2f dBFS > %.2f（默认放行）" % (tp, PEAK_LIMIT_DB))
    if over12 > 0.05:
        warn.append("轻微超限(1.2口径) %.4f%% > 0.05%%（默认放行）" % over12)
    if expect_dur and dur and abs(expect_dur - dur) > 0.5:
        bad.append("时长对不上：期望 %.2fs 实际 %.2fs（差 %.2fs）⇒ 疑似截断"
                   % (expect_dur, dur, abs(expect_dur - dur)))
    rep["bad"] = bad
    rep["warn"] = warn
    return (not bad), rep


def download(m3u8, out_path):
    cmd = ["ffmpeg", "-nostdin", "-y", "-loglevel", "error",
           "-headers", "Referer: https://news.web.nhk/\r\nUser-Agent: Mozilla/5.0\r\n",
           "-i", m3u8, "-c", "copy", "-bsf:a", "aac_adtstoasc", "-f", "mp4", out_path]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return "timeout"
    if p.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 20000:
        return "ok"
    if os.path.exists(out_path):
        os.remove(out_path)
    return "fail:" + (p.stderr or "")[:120]


def voice_base(ep_id):
    ej = os.path.join(DATA, "episodes", ep_id, "episode.json")
    ep = json.load(open(ej, encoding="utf-8"))
    return ep["voice_uri"][:-4], ep


def refetch(ep_id, tries=3):
    base, ep = voice_base(ep_id)
    t = auth.get_token()
    mtok = media_token(t["token"])
    m3u8 = ("https://media.vd.st.nhk/news/easy_audio/%s/index.m3u8?hdnts=%s"
            % (base, mtok))
    dest = os.path.join(DATA, "audio", ep_id + ".m4a")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    exp_dur = m3u8_expected_dur(m3u8)
    for i in range(1, tries + 1):
        tmp = dest + ".dl.m4a"
        r = download(m3u8, tmp)
        if r != "ok":
            print("   第%d次下载失败：%s" % (i, r))
            continue
        ok, rep = health(tmp, expect_dur=exp_dur)
        print("   第%d次下载：%s" % (i, json.dumps(rep, ensure_ascii=False)))
        if ok:
            shutil.copyfile(tmp, dest)
            os.remove(tmp)
            print("   ✅ 通过体检，已替换 %s" % dest)
            return True
        os.remove(tmp)
        print("   ❌ 体检不过（%s）→ 重抓" % "；".join(rep["bad"]))
    print("   ❌ %d 次都不过 ⇒ 保留原文件（并在 manifest 里标记）" % tries)
    return False


def transcode(ep_id):
    """重转码成站点用的干净 AAC（跟 retranscode.py 同一配方）"""
    src = os.path.join(DATA, "audio", ep_id + ".m4a")
    dst = os.path.join(SITE, "media/slow", ep_id + ".m4a")
    if not os.path.exists(src) or not os.path.exists(dst):
        print("   （跳过转码：源或目标不存在）")
        return
    tmp = dst + ".new.m4a"
    r = subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", src,
                        "-c:a", "aac", "-b:a", audio_recipe.AAC_BITRATE, "-ar", "48000", "-ac", "2",
                        "-movflags", "+faststart", tmp],
                       capture_output=True, text=True, timeout=300)
    if r.returncode != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) < 20000:
        print("   ❌ 转码失败：%s" % r.stderr[:120])
        if os.path.exists(tmp):
            os.remove(tmp)
        return
    src_dur = measure(src)[3]
    ok, rep = health(tmp, expect_dur=src_dur)
    print("   转码后体检：%s" % json.dumps(rep, ensure_ascii=False))
    if not ok:
        print("   ❌ 转码后仍不过 → 不替换（保持原样，避免把坏东西推上去）")
        os.remove(tmp)
        return
    os.replace(tmp, dst)
    print("   ✅ 已更新 %s（%d KB）" % (dst, os.path.getsize(dst) // 1024))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("episodes", nargs="*")
    ap.add_argument("--check-only", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--no-transcode", action="store_true")
    a = ap.parse_args()

    ids = list(a.episodes)
    if a.all or not ids:
        ej = os.path.join(SITE, "data/episodes.js")
        s = open(ej, encoding="utf-8").read()
        E = json.loads(s[s.index("["):s.rindex("]") + 1])
        ids = [e["id"] for e in E]

    bad = []
    for ep_id in ids:
        p = os.path.join(DATA, "audio", ep_id + ".m4a")
        if not os.path.exists(p):
            print("== %s：没有源文件，跳过" % ep_id)
            continue
        ok, rep = health(p)
        print("== %s：%s" % (ep_id, json.dumps(rep, ensure_ascii=False)))
        if not ok:
            bad.append(ep_id)
            if a.check_only:
                continue
            print("   → 重抓")
            if refetch(ep_id) and not a.no_transcode:
                transcode(ep_id)
    print("\n== 不合格的期：%d %s ==" % (len(bad), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

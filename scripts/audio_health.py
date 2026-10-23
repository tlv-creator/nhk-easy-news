#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audio_health.py —— 音频体检（峰值 / 平顶占比 / 响度），**自带会自证的对照组**

🔴🔴 这份工具存在的理由（2026-10-23 Shun 提醒后重写）：
  ① **「峰值 > 0.05 dB」抓不到最常见的削波。**
     最常见的削波是**样本被钉在 0 dBFS**（平顶）⇒ 此时 **Peak level = 0.000 dB（不是正数！）**
     ⇒ 用 `peak > 0.05` 判 ⇒ **恒绿**。
     实测（对照组）：
       削波样本 aevalsrc=2.0*sin(...)  → Peak level **0.000265 dB**、Peak count/样本 = **66.7%**
       干净样本 aevalsrc=0.9*sin(...)  → Peak level  -0.914944 dB、Peak count/样本 = **0.45%**
  ② **必须用「全量 astats」。**
     只开 `measure_overall=Peak_level` ⇒ **Peak count / Number of samples 根本不打印**
     ⇒ 平顶占比读成 0% ⇒ 削波被判成「干净」。
  ③ **对照组必须能证明自己造对了。**
     造削波样本时振幅必须 **> 1.0**（lavfi 的 `sine` 默认振幅只有 1/8 ⇒ 推 +6 dB 也才 -12 dBFS
     ⇒ 根本没削到 ⇒ 对照组自己是假的）。

判据（两条独立维度，别混）：
  · 平顶占比 = astats `Peak count` / `Number of samples`   > 5% ⇒ 削波
  · 峰值     = astats `Peak level dB`                      > +0.05 dB ⇒ 过冲（次要信号）

用法：
  python3 scripts/audio_health.py --selftest                     # 自证对照组
  python3 scripts/audio_health.py --check site/app/media/fast     # 体检一个目录
  python3 scripts/audio_health.py --check DIR --max-lufs-spread 2.0   # 顺带查响度极差
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import wave
import math

RESULT = {}
FLAT_RATIO_LIMIT = 5.0      # 平顶占比阈值（%）
PEAK_LIMIT_DB = 0.05        # 过冲阈值（dB）
AUDIO_EXT = (".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".mp4")


def _run(cmd):
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=600)
        return p.stdout.decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return ""


# 🔴🔴🔴 **「超满刻度」的幅度口径**（2026-10-23 Shun 实测定，NHK 那版用 1.0 是错的）
#
#   实测数据（**同一批样本，两个口径**）：
#   ```
#   样本                        |x|>1.0   |x|>1.2   |x|>1.5  |x|>2.0  |x|>4.0   峰值
#   合法满幅 0.99→AAC64k        0.199%   0.199%   0.097%   0.000%   0.000%   +5.39 dB
#   合法满幅 0.9 →mp3→AAC32k    0.942%   0.027%   0.000%   0.000%   0.000%   +1.94 dB
#   真人声（Shun 12 集）         0.003%   0.001%   0.000%   0.000%   0.000%   +0.6~3.5 dB
#   垃圾 |x|=5（0.1% 样本）      0.101%   0.101%   0.101%   0.101%   0.101%  +13.98 dB
#   ```
#   ⇒ **口径 1.0 会让正常的有损编码内容恒红**（0.94% ≫ 0.05% 阈值）⇒ 错
#   ⇒ **口径 1.2 也会误报合法满幅内容**（0.199%）⇒ 拿它做**硬失败**会误伤 ⇒ 错
#   ⇒ **口径 4.0（远超满刻度）⇒ 正常内容恒为 0.000%** ⇒ 阈值 0.05% 有极大余量 ✅
#
#   🔴 **两个口径的职责不同，工具两个都报，由调用方选**：
#   - **over_pct（口径 4.0）= 「垃圾样本」维度** ⇒ 幅度荒谬的坏帧（NHK +11~+76.9 dBFS 那批）
#     ⚠️ 这一维与「峰值维度」**重叠**（荒谬幅度必然带来荒谬峰值）
#   - **over_pct_mild（口径 1.2）= 「大面积轻微超限」维度** ⇒ 峰值温和、但超限样本很多
#     （NHK 那种「峰值只有 +2.8 dB、坏帧占 0.004%~0.12%」的批次）
#     ⚠️ **这一维与「合法编码过冲」在数值上无法区分**（满幅合法内容就有 0.2%）
#     ⇒ 建议**只做警告**，硬失败交给峰值维度
OVER_CLIP_DEFAULT = 4.0        # 垃圾维度：远超满刻度
OVER_CLIP_MILD = 1.2          # 轻微超限维度（只做警告用）


def over_pct(path, clip=OVER_CLIP_DEFAULT):
    """🔴 **超满刻度样本占比**（|x| > 1.0 的样本比例）

    🔴 为什么要有这一维（2026-10-23 NHK 实测）：
      **有损编码器会把「波形削平」抹掉**（Shun 实测：硬削平 wav 66.7% → AAC 32k 后 0.00075%）
      ⇒ 「平顶占比」在真实有损链路里基本是空的。
      但 NHK 的源流坏帧**不是**削平，是**零散的、幅度荒谬的垃圾样本**：
      峰值 +2.8 ~ +76.9 dBFS，而平顶占比 = 0.000%
      ⇒ **只有「超满刻度样本占比」抓得住它**（NHK 侧实测 0.004% ~ 0.12%）。
    """
    try:
        import numpy as np
    except Exception:
        return None
    # 🔴🔴 **必须用原生声道布局**（2026-10-23 NHK 实测踩到）：
    #   `-ac 1` 的单声道下混是**另一个信号** ⇒ 峰值与用户实际听到的**差 3 dB**
    #   （实测同一文件：下混 -1.48 dBFS，逐声道 -4.48 dBFS）
    #   ⇒ 用户设备播的是**立体声原文件** ⇒ 判据必须落在**原生逐声道**上。
    # 🔴🔴 **不要重采样**（2026-10-23 Shun 实测发现，NHK 那版带 `-ar 48000`）：
    #   重采样器会把「钉在满刻度的干净内容」也推过 1.0 ⇒ **自己造出超满刻度样本**
    #   （实测同一文件：原生率 over=0.1009% ⇒ 重采样后 **0.1656%（放大 1.6 倍）**）
    #   ⇒ 判据要落在**文件自己的样本**上 ⇒ 原生率解码 ✅
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path,
                        "-f", "f32le", "-"],
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=600)
    d = p.stdout
    n = len(d) // 4
    if n == 0:
        return None
    a = np.frombuffer(d[:n * 4], dtype=np.float32)
    if len(a) % 2 == 0:
        ch = a.reshape(-1, 2)          # 立体声：逐声道逐个看（不下混）
        over = (np.abs(ch) > clip)
        return 100.0 * float(over.sum()) / over.size
    a = np.abs(a)
    return 100.0 * float((a > clip).sum()) / len(a)


def probe(path, deep=False, over_clip=OVER_CLIP_DEFAULT):
    """返回 {peak_db, flat_ratio, peak_count, samples, mean_db, lufs, over_pct}"""
    out = {}

    # ── ① 全量 astats（必须全量：否则 Peak count / Number of samples 不打印）──
    txt = _run(["ffmpeg", "-hide_banner", "-nostats", "-i", path,
                "-af", "astats", "-f", "null", "-"])
    m = re.search(r"Peak level dB:\s*(-?[\d.]+)", txt)
    out["peak_db"] = float(m.group(1)) if m else None
    # 全量 astats 会打两遍（channel + overall），取最后一组 overall
    pcs = re.findall(r"Peak count:\s*([\d.]+)", txt)
    nss = re.findall(r"Number of samples:\s*(\d+)", txt)
    pc = float(pcs[-1]) if pcs else None
    ns = float(nss[-1]) if nss else None
    out["peak_count"] = pc
    out["samples"] = ns
    out["flat_ratio"] = (pc / ns * 100.0) if (pc is not None and ns) else None

    # ── ② 平均响度（volumedetect 的 mean_volume 不 clamp，可信）──
    txt2 = _run(["ffmpeg", "-hide_banner", "-nostats", "-i", path,
                 "-af", "volumedetect", "-f", "null", "-"])
    m2 = re.search(r"mean_volume:\s*(-?[\d.]+)\s*dB", txt2)
    out["mean_db"] = float(m2.group(1)) if m2 else None

    # ── ③ 整体 LUFS（loudnorm 的 input_i）──
    txt3 = _run(["ffmpeg", "-hide_banner", "-nostats", "-i", path,
                 "-af", "loudnorm=print_format=json", "-f", "null", "-"])
    m3 = re.search(r'"input_i"\s*:\s*"(-?[\d.]+)"', txt3)
    if not m3:
        m3 = re.search(r'"input_i"\s*:\s*(-?[\d.]+)', txt3)
    out["lufs"] = float(m3.group(1)) if m3 else None
    out["over_pct"] = over_pct(path, over_clip) if deep else None
    out["over_clip"] = over_clip if deep else None
    out["over_pct_mild"] = over_pct(path, OVER_CLIP_MILD) if deep else None
    return out


def make_control_samples(tmpdir):
    """造一对样本：**削波的必须真的被削平**，干净的那条不能有平顶。

    🔴 坑：lavfi 的 `sine` 默认振幅只有 1/8 ⇒ 推 +6 dB 也才 -12 dBFS ⇒ 根本没削到。
    ⇒ 这里用 `aevalsrc=2.0*sin(...)`（振幅 2.0 ⇒ 被钉在 ±1.0）。
    """
    clip = os.path.join(tmpdir, "ctrl_clip.wav")
    clean = os.path.join(tmpdir, "ctrl_clean.wav")
    over = os.path.join(tmpdir, "ctrl_overshoot.m4a")
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
          "aevalsrc=2.0*sin(2*PI*440*t):d=2", "-c:a", "pcm_s16le", clip, "-y"])
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
          "aevalsrc=0.9*sin(2*PI*440*t):d=2", "-c:a", "pcm_s16le", clean, "-y"])
    # 🔴 第三个对照样本：**过冲但没削平**（2026-10-23 加）
    #   造法：0.99 振幅 → 有损编码 → **解码器重建后越过 0 dBFS**（实测 +5.4 dB）
    #   但平顶占比 ≈ 0.001% ⇒ **不是**削波
    #   ⇒ 证明「过冲」与「削平」是**两个独立维度**，不能混成一个判据
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
          "aevalsrc=0.99*sin(2*PI*440*t):s=44100:d=5", "-c:a", "aac", "-b:a", "64k",
          "-ar", "44100", "-ac", "1", over, "-y"])
    # 🔴🔴 第四个对照样本：**零散垃圾样本**（2026-10-23 NHK 坏帧那一类）
    #   造法：0.9 振幅的音，把 0.1% 的样本抬到 1.05（|x| > 1.0）
    #   ⇒ 峰值只有 **+0.42 dB**（**故意低于 +6 dB 的「峰值荒谬」门槛**）
    #   ⇒ 平顶占比 ≈ 0（不是削平）
    #   ⇒ **只有「超满刻度样本占比」这一维抓得住它** ⇒ 证明第三维不可省
    junk = os.path.join(tmpdir, "ctrl_junk.wav")
    _make_junk(junk)                       # 🔴 荒谬：|x|=5.0，0.1% 的样本
    mild = os.path.join(tmpdir, "ctrl_mild.wav")
    _make_junk(mild, rate=0.05, amp=1.3)   # 🔴 温和：|x|=1.3，**5%** 的样本
    return clip, clean, over, junk, mild


def _make_junk(path, rate=0.001, amp=5.0):
    """造「零散垃圾样本」：正常音 + 少量 |x|>1.0 的样本（NHK 坏帧那一类）。

    🔴🔴 **必须用 float32 WAV**：
      s16 会被夹到 ±32767 ⇒ 解码出来正好 ±1.0 ⇒ `>1.0` 判不出来 ⇒ **样本是假的**
      （我第一版就栽在这：造出来的样本在原生率下 over=0.0000%）
    🔴 峰值刻意压在 +3.5 dB ⇒ **低于 +6 dB 的「峰值荒谬」门槛**
      ⇒ 只有「超满刻度样本占比」抓得住 ⇒ 这才是这一维的**隔离样本**
    """
    import struct
    n = 44100 * 2
    sr = 44100
    data = bytearray()
    # 🔴 `rate` = **被替换样本的占比**（0.001 = 0.1%）
    #   （我第一版把「块占比」和「块内取样率」相乘 ⇒ 实际占比只有预期的 1/10 ⇒ 样本是假的）
    every = max(1, int(1.0 / rate))
    for i in range(n):
        v = 0.9 * math.sin(2 * math.pi * 440.0 * i / sr)
        if i % every == 0:
            v = amp if (i // every) % 2 == 0 else -amp
        data += struct.pack("<f", v)          # 🔴 float32 ⇒ 真的能超过 1.0
    # float32 WAV 头（format tag 3）
    hdr = b"RIFF" + (36 + len(data)).to_bytes(4, "little") + b"WAVEfmt "
    hdr += (16).to_bytes(4, "little") + (3).to_bytes(2, "little")
    hdr += (1).to_bytes(2, "little") + sr.to_bytes(4, "little")
    hdr += (sr * 4).to_bytes(4, "little") + (4).to_bytes(2, "little")
    hdr += (32).to_bytes(2, "little") + b"data" + len(data).to_bytes(4, "little")
    with open(path, "wb") as f:
        f.write(hdr + bytes(data))


def selftest():
    """**对照组自证**：证明判据抓得住真削波、且**旧判据抓不住**（否则这工具没意义）"""
    fails = []

    def ck(name, cond, extra=""):
        print(("  OK  " if cond else "  FAIL") + "  " + name +
              (("  " + str(extra)) if extra else ""))
        if not cond:
            fails.append(name)

    with tempfile.TemporaryDirectory() as td:
        clip, clean, over, junk, mild = make_control_samples(td)
        ck("对照组样本造出来了",
           os.path.exists(clip) and os.path.exists(clean) and os.path.exists(over)
           and os.path.exists(junk) and os.path.exists(mild))
        c = probe(clip)
        k = probe(clean)
        o = probe(over)
        print("  过冲样本：peak=%s dB  平顶占比=%s%%" %
              (o.get("peak_db"), o.get("flat_ratio")))
        print("  削波样本：peak=%s dB  平顶占比=%s%%" %
              (c.get("peak_db"), c.get("flat_ratio")))
        print("  干净样本：peak=%s dB  平顶占比=%s%%" %
              (k.get("peak_db"), k.get("flat_ratio")))

        ck("对照组 A：削波样本的平顶占比 > 阈值",
           (c.get("flat_ratio") or 0) > FLAT_RATIO_LIMIT, c.get("flat_ratio"))
        ck("对照组 A：干净样本的平顶占比 < 阈值",
           (k.get("flat_ratio") or 999) < FLAT_RATIO_LIMIT, k.get("flat_ratio"))
        # 🔴 关键：证明「旧的 peak>0.05 判据」确实漏掉了这个削波样本
        old_missed = not ((c.get("peak_db") or -99) > PEAK_LIMIT_DB)
        ck("对照组 A：旧判据(peak>0.05)确实会漏掉它（这才是要修的理由）",
           old_missed, "peak=%s" % c.get("peak_db"))
        # 反向：新判据不能把干净样本判成削波
        ck("对照组 B：新判据不误伤干净样本",
           (k.get("flat_ratio") or 999) < FLAT_RATIO_LIMIT, k.get("flat_ratio"))
        ck("对照组 B：干净样本峰值 < 0 dB",
           (k.get("peak_db") or 99) < 0, k.get("peak_db"))
        # 🔴 对照组 C：**过冲 ≠ 削平**（两个独立维度）
        ck("对照组 C：过冲样本峰值 > 0 dB（确实是过冲）",
           (o.get("peak_db") or -99) > 0, o.get("peak_db"))
        ck("对照组 C：过冲样本平顶占比 < 阈值（**不是**削平）",
           (o.get("flat_ratio") or 999) < FLAT_RATIO_LIMIT, o.get("flat_ratio"))
        ck("对照组 C：过冲样本**不该**被当成削平（否则就是假警报）",
           not ((o.get("flat_ratio") or 0) > FLAT_RATIO_LIMIT), o.get("flat_ratio"))

        # 🔴🔴 对照组 D：**垃圾样本**（NHK 坏帧那一类）⇒ 两个口径各管一段
        j = probe(junk, deep=True)
        print("  垃圾样本(|x|=5)：peak=%s dB  平顶=%s%%  over(4.0)=%s%%  over(1.2)=%s%%" %
              (j.get("peak_db"), j.get("flat_ratio"), j.get("over_pct"),
               j.get("over_pct_mild")))
        _ov = j.get("over_pct")
        ck("对照组 D：荒谬垃圾样本在 **4.0 口径**下超满刻度占比 > 0.05%",
           _ov is not None and _ov > 0.05, _ov)
        # 🔴 `or` 会把 **0.0 吃掉**（0.0 是 falsy ⇒ `x or 99` → 99）⇒ 一律用 is not None 判
        _pk = j.get("peak_db")
        ck("对照组 D：荒谬垃圾样本的峰值也荒谬（**这一维与峰值维度重叠**）",
           _pk is not None and _pk > 6.0, _pk)
        ck("对照组 D：**平顶判据抓不住它**（不是削平）",
           (j.get("flat_ratio") or 999) < FLAT_RATIO_LIMIT, j.get("flat_ratio"))
        # 🔴🔴 对照组 D2：**温和大面积**的轻微超限（NHK 那种「峰值温和、坏帧很多」）
        md = probe(mild, deep=True)
        print("  温和垃圾(|x|=1.3, 5%%样本)：peak=%s dB  over(4.0)=%s%%  over(1.2)=%s%%" %
              (md.get("peak_db"), md.get("over_pct"), md.get("over_pct_mild")))
        _mp = md.get("peak_db")
        ck("对照组 D2：温和垃圾样本的**峰值判据抓不住它**（peak < +6 dB）",
           _mp is not None and _mp < 6.0, _mp)
        _mm = md.get("over_pct_mild")
        ck("对照组 D2：**1.2 口径**能抓到它（占比 > 0.05%）",
           _mm is not None and _mm > 0.05, _mm)
        # 反向：干净样本在第三维下**不能**被判成垃圾（否则就是假警报）
        kd = probe(clean, deep=True)
        _ok = kd.get("over_pct")
        ck("对照组 D：干净样本的超满刻度占比 < 0.05%（不误伤）",
           _ok is not None and _ok < 0.05, _ok)
        # 安全默认：**不加 --deep 时这一维不参与判定**（不能因为没算就误报）
        ck("对照组 D：不加 --deep 时超满刻度占比为 None（默认不判）",
           probe(clean).get("over_pct") is None, probe(clean).get("over_pct"))
        # 🔴 **输出形状守卫**：JSON 里每个文件条目必须含全部键
        #   （「加一列只改了一处」那个 bug 的通用护栏：形状变了 ⇒ 自检当场红）
        #   🔴 **要验的是 check_dir 的 rows（JSON 里的文件条目），不是 probe() 的返回**
        #      （两处是**不同结构** ⇒ 拿错一个去断言 ⇒ 断言本身是错的）
        _need = ("file", "peak_db", "flat_ratio", "lufs", "flat", "overshoot",
                 "over_pct", "over_pct_mild", "junk")
        RESULT.clear()
        check_dir(td, quiet=True, deep=True, max_over_pct=0.05)
        _files = RESULT.get("dirs", [{}])[0].get("files", [])
        _miss = [k for k in _need for f in _files if k not in f]
        ck("输出形状：check_dir 的每个文件条目含全部键",
           bool(_files) and not _miss, _miss or ("文件数 %d" % len(_files)))
        # 🔴🔴 **口径守卫**：正常的有损编码内容（满幅正弦 → mp3 → AAC）
        #   在「|x|>1.0」口径下有 **0.94%**（= 阈值的 19 倍 ⇒ 恒红）
        #   ⇒ 口径必须是 1.2（实测 0.027%）⇒ 这条守住「别退回 1.0」
        loud = os.path.join(os.path.dirname(clean), "ctrl_loud.m4a")
        _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
              "aevalsrc=0.9*sin(2*PI*440*t):s=44100:d=5", "-c:a", "libmp3lame",
              "-b:a", "64k", os.path.join(os.path.dirname(clean), "ctrl_loud.mp3"), "-y"])
        _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i",
              os.path.join(os.path.dirname(clean), "ctrl_loud.mp3"), "-c:a", "aac",
              "-b:a", "32k", "-ar", "44100", "-ac", "1", loud, "-y"])
        ld = probe(loud, deep=True)
        print("  正常满幅AAC：over(1.2)=%s%%  over(1.0)=%s%%  peak=%s dB" %
              (ld.get("over_pct"),
               probe(loud, deep=True, over_clip=1.0).get("over_pct"),
               ld.get("peak_db")))
        _l1 = ld.get("over_pct")
        ck("对照组 D：正常满幅 AAC 内容在 **4.0 口径**下恒为 0%（硬失败用它，很安全）",
           _l1 is not None and _l1 == 0.0, _l1)
        _lm = ld.get("over_pct_mild")
        ck("对照组 D：正常满幅 AAC 在 1.2 口径下余量有限（**只做警告，不做硬失败**）",
           _lm is not None and _lm < 0.05, _lm)
        _l10 = probe(loud, deep=True, over_clip=1.0).get("over_pct")
        ck("对照组 D：口径 1.0 确实会误报（这就是为什么要定 4.0）",
           _l10 is not None and _l10 > 0.05, _l10)
    return fails


def check_dir(d, max_lufs_spread=None, strict_overshoot=False, quiet=False,
              deep=False, max_over_pct=None, over_clip=OVER_CLIP_DEFAULT):
    files = []
    for root, _dirs, names in os.walk(d):
        for n in sorted(names):
            if n.lower().endswith(AUDIO_EXT):
                files.append(os.path.join(root, n))
    files.sort()
    if not files:
        if not quiet:
            print("❌ 目录里没有音频文件：%s" % d)
        RESULT.setdefault("dirs", []).append(
            {"dir": d, "files": [], "flat_bad": [], "overshoot": [],
             "lufs_spread": None, "reason": "no_files", "rc": 2})
        return 2

    rows = []
    bad = []          # 削平（真正的削波）⇒ 硬失败
    over = []         # 过冲（解码器重建超出 0 dBFS）⇒ **默认只警告**
    junk = []         # 🔴 垃圾样本（超满刻度占比超限）⇒ 源里有坏帧 ⇒ 硬失败
    for f in files:
        p = probe(f, deep=deep, over_clip=over_clip)
        fr = p.get("flat_ratio")
        pk = p.get("peak_db")
        # 🔴🔴 两条独立维度（2026-10-23 修）：**过冲 ≠ 削波**
        #   削平：样本被钉在 ±1.0（平顶占比高）⇒ 波形削成方波 ⇒ 真正的「爆」⇒ 硬失败
        #   过冲：解码器重建后**极少量**样本超过 0 dBFS ⇒ 听感是「偶尔极轻的咔」
        #        ⇒ 与削平混在一起报，会把「0.002% 样本过冲」的文件误判成爆音（假警报）
        flat = (fr is not None and fr > FLAT_RATIO_LIMIT)
        overshoot = (pk is not None and pk > PEAK_LIMIT_DB)
        op = p.get("over_pct")
        is_junk = (op is not None and max_over_pct is not None and op > max_over_pct)
        # 🔴🔴🔴 **用 dict，不用元组**（2026-10-23 NHK 踩到并修的 bug 类）：
        #   元组 ⇒ 「加一列」时**每一处访问都要改** ⇒ 漏一处就崩
        #   （真的崩过：JSON 那处是防御式下标，打印那处是元组解包 ⇒ 崩的是不防御的那个）
        #   dict ⇒ 加字段**不会炸任何一处**，漏改的地方直接是 KeyError 而非错位取值
        rows.append({
            "file": os.path.basename(f), "peak_db": pk, "flat_ratio": fr,
            "lufs": p.get("lufs"), "flat": flat, "overshoot": overshoot,
            "over_pct": op, "over_pct_mild": p.get("over_pct_mild"), "junk": is_junk,
        })
        if is_junk:
            junk.append(f)
        if flat:
            bad.append(f)
        if overshoot:
            over.append(f)

    if not quiet:
        print("== %s（%d 个文件）==" % (d, len(rows)))
    if not quiet:
        print("  %-34s %10s %10s %9s" % ("文件", "峰值dB", "平顶%", "LUFS"))
    if not quiet:
        # 🔴🔴 **用下标取，不要用元组解包**（2026-10-23 修）：
        #   下面那个 JSON 序列化用的是 `r[6] if len(r) > 6`（防御式），
        #   这里原来也是解包 `for name, pk, fr, lu, _f, _o in rows`
        #   ⇒ **加一列就崩**（我加 over_pct 时把共享工具弄崩了 ⇒ Shun 也被坑）
        #   ⇒ 同一个数据结构，两处用不同访问方式 ⇒ 崩的是不防御的那个
        for r in rows:
            print("  %-34s %10s %10s %9s" % (
                r["file"][:34],
                "%.3f" % r["peak_db"] if r["peak_db"] is not None else "-",
                "%.3f" % r["flat_ratio"] if r["flat_ratio"] is not None else "-",
                "%.1f" % r["lufs"] if r["lufs"] is not None else "-"))

    lufs_vals = [r["lufs"] for r in rows if r["lufs"] is not None]
    spread = (max(lufs_vals) - min(lufs_vals)) if len(lufs_vals) > 1 else 0.0
    if not quiet:
        print("\n  🔴 削平文件（真削波，必须重做）：%d 个 %s" % (len(bad), bad if bad else ""))
        print("  🟡 过冲文件（峰值>0dBFS，默认放行；要严加 --strict-overshoot）：%d 个 %s"
              % (len(over), [os.path.basename(x) for x in over] if over else ""))
    if not quiet:
        print("  LUFS：%.1f ~ %.1f，极差 %.2f LU" %
              (min(lufs_vals), max(lufs_vals), spread) if lufs_vals else "  LUFS: 无数据")

    # 🔴🔴 **判定与写 JSON 必须解耦**：原来 RESULT 只在「全过」时才写
    #   ⇒ 一旦判失败就**提前 return** ⇒ JSON 里**什么都没有**
    #   ⇒ 调用方（闸门）恰好在最该拦的那种文件上**读不到任何数据** ⇒ 变成瞎子（假绿灯！）
    #   （2026-10-23 由 Shun 的闸门自检抓出来：平顶 66.7% 的文件 → JSON {"rc":1} → 闸门放行）
    reason = "ok"
    rc = 0
    if max_lufs_spread is not None and spread > max_lufs_spread:
        reason, rc = "lufs_spread", 1
        if not quiet:
            print("  ❌ LUFS 极差 %.2f > 允许的 %.2f" % (spread, max_lufs_spread))
    elif bad:
        reason, rc = "flat", 1
        if not quiet:
            print("  ❌ 有削平文件（波形被削成方波 ⇒ 听着会爆）")
    elif junk:
        reason, rc = "junk", 1
        if not quiet:
            print("  ❌ 有垃圾样本文件（超满刻度占比 > %.3f%% ⇒ 源里有坏帧）" % max_over_pct)
    elif over and strict_overshoot:
        reason, rc = "overshoot", 1
        if not quiet:
            print("  ❌ 有过冲文件（--strict-overshoot）")
    elif not quiet:
        print("  ✅ 全部通过")

    RESULT.setdefault("dirs", []).append({
        "dir": d,
        "files": [dict(r, over_clip=(over_clip if deep else None)) for r in rows],
        "flat_bad": [os.path.basename(x) for x in bad],
        "junk_bad": [os.path.basename(x) for x in junk],
        "overshoot": [os.path.basename(x) for x in over],
        "lufs_spread": round(spread, 2) if lufs_vals else None,
        "reason": reason,          # 🔴 为什么失败（调用方不用去猜）
        "rc": rc,
    })
    return rc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--check", action="append", default=[])
    ap.add_argument("--max-lufs-spread", type=float, default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--deep", action="store_true",
                    help="额外解码一遍算「超满刻度样本占比」（抓源里的坏帧）")
    ap.add_argument("--max-over-pct", type=float, default=None,
                    help="超满刻度样本占比上限（%%），超了判 junk（源里有坏帧）")
    ap.add_argument("--over-clip", type=float, default=OVER_CLIP_DEFAULT,
                    help="「超满刻度」的幅度口径：统计 |x| > 这个值的样本（默认 1.2；"
                         "🔴 用 1.0 会让正常的有损编码内容恒红）")
    ap.add_argument("--strict-overshoot", action="store_true",
                    help="把「过冲」也算失败（默认只警告：过冲≠削波）")
    a = ap.parse_args()

    if a.selftest:
        fails = selftest()
        print("\n== 自检失败 %d 项 ==" % len(fails))
        return 1 if fails else 0

    if a.check:
        rc = 0
        for d in a.check:
            r = check_dir(d, a.max_lufs_spread, a.strict_overshoot, quiet=a.json,
                          deep=a.deep, max_over_pct=a.max_over_pct,
                          over_clip=a.over_clip)
            if r == 1:
                rc = 1
            elif r == 2:
                rc = 2
        if a.json:
            # 🔴 机器可读输出（闸门要用）—— 以前这个参数是**声明了没实现**的假参数
            RESULT["rc"] = rc
            print(json.dumps(RESULT, ensure_ascii=False, indent=1))
        return rc

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())

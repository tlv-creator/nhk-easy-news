#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""尾段 ASR 修复：短尾切片（<8s）会被平台 ASR「编造」⇒ 必须重切 40s 尾窗 + 重跑。

【实测证据（2026-10-25，batch1 20 轨 / 99 段）】
  b1-9784866394701-05 尾切片 0.86s ⇒ ASR 返回 3 句，时间戳到 10s（**超出切片 11 倍**）
  b1-9784866398310-02 尾切片 0.37s ⇒ ASR 返回 6 句，时间戳到 10s
  同一段 0.86s 切片**再跑一次** ⇒ 返回完全不同的内容（「日暮れ方の、寒々とした薄暗がりを…」
    另一本书，43 秒）⇒ 确认是**幻觉**，不是真实音频
【机器判据】ASR 行的 start > 该切片实际时长 + 1s ⇒ 该段结果必是编造 ⇒ 重做
【修法】尾切片过短 ⇒ 改切「以轨尾结尾的 40 秒窗」，重跑 ASR；
        旧尾段与新窗有重叠区 ⇒ 用重叠区内容互检（新旧一致 ⇒ 新窗可信）
【产出】asr_truth/<tag>.json 仍是仓库同款格式：[{start, text}, ...]
"""
import json, os, re, subprocess, sys, time

B1 = "/workspace/work/batch1"
LINE_RE = re.compile(r'【\s*((?:\d+:)?\d+(?:\.\d+)?)\s*秒?\s*】')
PROMPT = ("这是一本日语有声读物。**严格逐字转写**这段音频的日文内容："
          "①保留原有标点；②不要添加原文没有的词，不要翻译、不要概括、不要补全；"
          "③听不清用 □；④按听到顺序逐句输出，每句前标【秒】=该句在这段音频里的起始秒数"
          "（整数，本段开头为 0）。")
MIN_CLIP = 8.0     # 尾切片短于此值 ⇒ 不可信
WIN = 40.0         # 尾窗长度


def sh(cmd, timeout=900):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout).stdout


def dur_of(path):
    return float(sh(f'ffprobe -v error -show_entries format=duration -of csv=p=0 "{path}"').strip() or 0)


def parse_desc(desc, base):
    rows = []
    for line in desc.splitlines():
        m = LINE_RE.search(line)
        if not m:
            continue
        ts = m.group(1)
        if ":" in ts:
            mm, ss = ts.split(":")
            off = float(mm) * 60 + float(ss)
        else:
            off = float(ts)
        txt = LINE_RE.sub("", line).strip()
        if txt:
            rows.append({"start": round(base + off, 2), "text": txt})
    rows.sort(key=lambda r: r["start"])
    # 去重（同一起点同文本）
    out, seen = [], set()
    for r in rows:
        k = (r["start"], r["text"])
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def asr_one(path, tries=3):
    """上传 + 单条 ASR（不走批次，避免批次内错位）。返回 description。"""
    up = json.loads(sh(f'mcode-tools upload_temp_url "{path}"'))["temp_url"]
    ap = f"{B1}/args/fix_{os.getpid()}_{int(time.time()*1000)}.json"
    os.makedirs(os.path.dirname(ap), exist_ok=True)
    json.dump({"audio_info": [{"url": up, "mime_type": "audio/mpeg", "prompt": PROMPT}]},
              open(ap, "w"), ensure_ascii=False)
    for i in range(tries):
        r = subprocess.run(["mcode-tools", "connector", "call",
                            "connector__matrix__audios_understand", "--args-file", ap],
                           capture_output=True, text=True, timeout=1800)
        try:
            d = json.loads(r.stdout)
        except Exception:
            d = None
        if d and d.get("results"):
            res = d["results"][0]
            if res.get("success") and (res.get("description") or "").strip():
                return res["description"]
        time.sleep(4 * (i + 1))
    return None


def rebuild_from_raw(tag, dur, ov_base=None):
    """用 asr_raw/ 下的所有段重算真值。
    · start 越界 ⇒ 该段被判定为编造 ⇒ 不采纳
    · ov_base 不为空 ⇒ 尾窗覆盖 [ov_base, dur]：旧段只保留 start < ov_base 的行；
      尾窗自己丢掉「窗口开头那行」（它是跨边界的半句，旧段已有全句）
    """
    rows, dropped = [], []
    for f in sorted(os.listdir(f"{B1}/asr_raw/{tag}")):
        if not f.endswith(".json"):
            continue
        d = json.load(open(f"{B1}/asr_raw/{tag}/{f}"))
        desc = d.get("description") or ""
        stem = f[:-5]                      # 去掉 .json ⇒ 切片文件名（含 .mp3）
        m = re.match(r"^(?:tail)?(\d+)\.mp3$", stem)
        if not m:
            print(f"  ?? 跳过无法解析的段名 {tag}/{f}")
            continue
        base = int(m.group(1)) / 1000.0
        clip = f"{B1}/clips/{tag}/{stem}"
        cd = dur_of(clip) if os.path.exists(clip) else None
        ts = [float(x) for x in LINE_RE.findall(desc)]
        if ts and cd is not None and max(ts) > cd + 1.0:
            dropped.append((f, cd, max(ts)))
            continue
        seg = parse_desc(desc, base)
        if ov_base is not None:
            if base >= ov_base - 0.01:
                seg = [r for r in seg if r["start"] >= ov_base + 1.0]   # 丢开头半句
            else:
                seg = [r for r in seg if r["start"] < ov_base]           # 被尾窗取代
        rows += seg
    rows.sort(key=lambda r: r["start"])
    out, seen = [], set()
    for r in rows:
        k = (r["start"], r["text"])
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    over = [r for r in out if r["start"] > dur + 0.01]
    return out, dropped, over


def main():
    units = json.load(open(f"{B1}/units.json"))
    print(f"{'轨':28} {'dur':>7}  {'尾切片':>7}  动作")
    fixed = []
    for u in units:
        tag, dur = u["tag"], u["dur"]
        clips = sorted(f for f in os.listdir(f"{B1}/clips/{tag}") if f.endswith(".mp3"))
        last = clips[-1]
        mm = re.match(r"^(?:tail)?(\d+)\.mp3$", last)
        last_start = int(mm.group(1)) / 1000.0
        cd = dur_of(f"{B1}/clips/{tag}/{last}")
        if cd >= MIN_CLIP:
            continue
        # 旧尾段（含重叠对照用）
        old_desc = (json.load(open(f"{B1}/asr_raw/{tag}/{last}.json")).get("description") or "")
        old_rows = parse_desc(old_desc, last_start)
        # 新尾窗：以轨尾结尾的 40 秒
        base = max(0.0, round(dur - WIN, 2))
        win = f"{B1}/clips/{tag}/tail{int(base*1000):09d}.mp3"
        sh(f'ffmpeg -nostdin -v error -y -ss {base} -t {dur-base} -i {u["tracks"][0]["path"]} '
           f'-b:a 48k -ac 1 "{win}"')
        wd = dur_of(win)
        desc = asr_one(win)
        if not desc:
            print(f"  ❌ {tag} 尾窗 ASR 失败 ⇒ 不改（保留旧值，另行处理）")
            continue
        json.dump({"success": True, "description": desc, "error": "",
                   "source": f"local://tail-fix/{tag}"},
                  open(f"{B1}/asr_raw/{tag}/tail{int(base*1000):09d}.mp3.json", "w"),
                  ensure_ascii=False)
        new_rows = parse_desc(desc, base)
        # 重叠区互检：新窗起点 base 与旧尾段起点 last_start 之间的内容
        ov = [r for r in new_rows if r["start"] < last_start + 1.0]
        ov_old = [r for r in old_rows if r["start"] < last_start + 1.0]
        agree = sum(1 for r in ov if any(o["text"] == r["text"] for o in ov_old))
        print(f"  🔧 {tag:24} dur={dur:6.1f} 尾切片={cd:5.2f}s → 尾窗[{base:.1f},{dur:.1f}]={wd:.1f}s "
              f"新行{len(new_rows):3} 重叠区新{len(ov):2}/旧{len(ov_old):2} 逐字一致{agree}")
        fixed.append(tag)
    # ---- 全量重建 ----
    print("\n=== 重建 20 本真值 ===")
    tot = 0
    for u in units:
        tag, dur = u["tag"], u["dur"]
        ov_base = None
        cdir = f"{B1}/clips/{tag}"
        for f in sorted(os.listdir(cdir)):
            mm = re.match(r"^tail(\d+)\.mp3$", f)
            if mm:
                ov_base = int(mm.group(1)) / 1000.0
        rows, dropped, over = rebuild_from_raw(tag, dur, ov_base)
        json.dump(rows, open(f"{B1}/asr_truth/{tag}.json", "w"), ensure_ascii=False)
        tot += len(rows)
        flag = ""
        if dropped:
            flag += f" 丢弃编造段 {len(dropped)}"
        if over:
            flag += f" ⚠️越界 {len(over)}"
        print(f"  {tag:26} 行{len(rows):4} 末{rows[-1]['start'] if rows else 0:7.1f}/{dur:6.1f}s{flag}")
    print(f"\n共 {tot} 行（修前 675 行）")


if __name__ == "__main__":
    main()

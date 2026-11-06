#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audio_dedupe.py —— 「新一期是不是把上一期的音频又发了一遍」的闸门

🔴🔴 **为什么必须有这个闸门（2026-11-06 实测）**：
    Shun 播客 2026-11-06 发布 Ep279「Souvenirs」，RSS 指向的音频
    **与 Ep278 的音频逐字节相同**（md5 / S3 ETag / 时长 / 播报内容 四重对上）。
    ⇒ 「音频健康闸门」全绿（峰值、时长、垃圾样本都正常）—— **因为它确实是一份健康的音频**，
      只是**内容是上一期的那一份**。
    ⇒ 「健康」和「是不是新内容」是**两个独立维度**：健康闸门查的是「这份音频能不能听」，
      本闸门查的是「这份音频是不是新的」。

    ⇒ **只查健康 ⇒ 会把上一期的音频当成新一期发出去**（用户看到同内容、不同标题）。

判据：
    · **逐字节相同**（md5 一致）⇒ ⛔ 硬失败（不许发）
    · 拿不到证据（音频站取不到 / 覆盖率不足）⇒ ⛔ 不放行（拿不到证据 ≠ 没问题，同音频闸门口径）
    · 播客**确实想重发旧集**（rerun）⇒ 需要人工确认 ⇒ `--allow-duplicate` 放行（带记录）

用法：
    python3 tools/audio_dedupe.py --product work/audio/ep14.m4a \
        --index web/data/eps/index.json --json
退出码：0=不是重复 / 1=是重复（硬失败）/ 2=没法判定（不放行）
"""
import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request

BOOT_URL = "https://tlv-creator.github.io/nhk-easy-news/shun.json"
# 本地 shun.json 的候选位置（沙箱每周重置 ⇒ 路径会变 ⇒ 多找几处）
_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL_BOOTS = [
    "/workspace/shun.json",
    os.path.join(os.path.dirname(_PROJ), "shun.json"),
    "/workspace/nhk-easy-news/shun.json",
]
UA = "shun-updater/dedupe/1.0"


def say(msg):
    print(msg, file=sys.stderr)


def md5_of_file(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def md5_of_url(url, timeout=90):
    h = hashlib.md5()
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def resolve_base(explicit):
    """音频站基址：显式参数 > 线上 shun.json > 本地 shun.json"""
    if explicit:
        return explicit
    try:
        req = urllib.request.Request(BOOT_URL, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read().decode("utf-8"))
        if d.get("audio"):
            return d["audio"]
    except Exception as e:  # noqa: BLE001
        say("  ⚠️ 读线上 shun.json 失败：%s" % e)
    try:
        for p in LOCAL_BOOTS:
            if not os.path.exists(p):
                continue
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            if d.get("audio"):
                return d["audio"]
    except Exception as e:  # noqa: BLE001
        say("  ⚠️ 读本地 shun.json 失败：%s" % e)
    return None


def load_index(path):
    with open(path, encoding="utf-8") as f:
        idx = json.load(f)
    out = []
    for e in idx.get("episodes", []):
        a = e.get("audio")
        if not a:
            continue
        out.append((e.get("tag") or a, a))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", required=True, help="新一期成品（m4a）")
    ap.add_argument("--index", default=None, help="已发布集索引（index.json）")
    ap.add_argument("--base", default=None, help="音频站基址（默认从 shun.json 取）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--allow-duplicate", action="store_true",
                    help="人工确认过「播客确实重发旧集」时放行")
    ap.add_argument("--min-coverage", type=float, default=0.8,
                    help="至少要成功比对多少比例的已发布集（默认 0.8）")
    args = ap.parse_args()

    idx_path = args.index or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "web", "data", "eps", "index.json")
    if not os.path.exists(args.product):
        print(json.dumps({"ok": False, "reason": "成品不存在：%s" % args.product},
                         ensure_ascii=False))
        return 2
    if not os.path.exists(idx_path):
        print(json.dumps({"ok": False, "reason": "索引不存在：%s" % idx_path},
                         ensure_ascii=False))
        return 2

    product_md5 = md5_of_file(args.product)
    base = resolve_base(args.base)
    if not base:
        print(json.dumps({"ok": False, "reason": "拿不到音频站基址 ⇒ 不放行"},
                         ensure_ascii=False))
        return 2
    if not base.endswith("/"):
        base += "/"

    published = load_index(idx_path)
    matches, checked, failed = [], 0, []
    for tag, name in published:
        url = base + name
        try:
            m = md5_of_url(url)
            checked += 1
            if m == product_md5:
                matches.append({"tag": tag, "url": url, "md5": m})
        except Exception as e:  # noqa: BLE001
            failed.append({"tag": tag, "url": url, "error": str(e)[:120]})

    coverage = (checked / float(len(published))) if published else 0.0
    result = {
        "ok": False,
        "product_md5": product_md5,
        "base": base,
        "published_total": len(published),
        "checked": checked,
        "failed": failed,
        "coverage": round(coverage, 4),
        "matches": matches,
    }
    if matches:
        result["reason"] = ("新一期的音频与已发布的 %s 逐字节相同（md5 %s）"
                            "⇒ 播客源把旧音频挂到了新集上 ⇒ 不发"
                            % ("、".join(m["tag"] for m in matches), product_md5))
        if args.allow_duplicate:
            result["ok"] = True
            result["reason"] += "（已用 --allow-duplicate 人工确认放行）"
            result["allowed"] = True
    elif coverage < args.min_coverage:
        result["reason"] = ("只能比对 %d/%d 个已发布集（覆盖率 %.0f%% < %.0f%%）⇒ 拿不到证据 ⇒ 不放行"
                            % (checked, len(published), coverage * 100, args.min_coverage * 100))
    else:
        result["ok"] = True
        result["reason"] = ("与 %d 个已发布集全部不同（md5 %s）" % (checked, product_md5))

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    else:
        say("  去重体检：%s" % result["reason"])
    return 0 if result["ok"] else (1 if matches else 2)


if __name__ == "__main__":
    sys.exit(main())

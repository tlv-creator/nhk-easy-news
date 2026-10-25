#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NHK 日更发版决策器（2026-10-25）—— **决定「今天要不要发 mcode 站」**

🔴🔴 为什么要有这个（真事故，不是假想）：
    `website_deploy` **每次都新建站点节点** ⇒ 发一版 = 占 1 个配额位（上限 50）
    ⇒ NHK 日更（周一~周五各 1 期）= 每周 +5 个位 ⇒ **10 周必然撞 `ENTITLEMENT_LIMIT_EXCEEDED`**
    ⇒ 而 2026-10-25 当天就撞过一次（从 v1.24 连发 v1.29 共 6 次，从不删旧的）

🔴🔴 核心洞察（本脚本存在的理由）：
    **NHK 每天变的是「内容」（字幕/词库/语法/前端），不是「站」**
    ⇒ 内容放 **GitHub Pages**（永久地址、同一个仓库同一个站点 ⇒ **不占 mcode 配额位**）
    ⇒ 只有**音频**必须留 mcode（实测：github.io 拉 790KB 要 23~48 秒，mcode 只要 0.28 秒，
       差 60 倍 ⇒ 音频搬 Pages 等于让用户没法听）
    ⇒ 而音频**攒着发**就行（单站 200MB 上限 ⇒ 一个音频站能装约 148 期 ⇒ 约 29 周才需发一次）

⇒ **本脚本的判据（只回答一个问题：今天要不要发 mcode 站？）**
    ① 内容指纹变了？（字幕/词库/前端）⇒ 变了 ⇒ **必须同步 Pages**（零配额）
    ② 有没有**新增音频**（media/ 下的新文件）？⇒ 有 ⇒ 音频站得更新 ⇒ **才需要发 mcode 站**
    ③ 距上次发音频站多久/攒了多少？⇒ 达到阈值（或用户要求）⇒ 发；否则 ⇒ 不发

⇒ **判据必须落在「事实量」上**：
    内容用 **md5 指纹**（data/*.js + subs/*.js + app.js + style.css），
    音频用 **media/ 下文件的 md5 集合**（不是「有没有新文件」这种表示量）。
    ⇒ 内容没变 ⇒ 同步 Pages 也不该升版号（用户不必白下几百 KB）。

用法：
    python3 scripts/daily_publish.py --plan              # 只看决策，不动手
    python3 scripts/daily_publish.py --sync-pages         # 只同步 Pages（零部署位）
    python3 scripts/daily_publish.py --need-audio-deploy  # 问「今天要不要发 mcode 站」（exit 0=要 / 3=不要）
    python3 scripts/daily_publish.py --mark-audio-deployed --domain <域名>

状态文件：/workspace/nhk/work/daily_state.json
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time

ROOT = "/workspace/nhk"
SITE = os.path.join(ROOT, "site", "app")
STATE = os.path.join(ROOT, "work", "daily_state.json")
TOKEN = "/workspace/.secrets/gh_token"

# 内容指纹要覆盖：用户能看到的一切文本内容 + 前端逻辑
FINGERPRINT_FILES = ["app.js", "style.css", "index.html", "config.js"]
FINGERPRINT_DIRS = ["data", "subs"]

# 音频站的攒批阈值：
#   · 单站 200MB 硬上限 ⇒ 装满约 148 期（均摊 1381KB/期）
#   · 攒到 10 期就发一次 ⇒ 每站只装 10 期（14MB）⇒ 远低于上限 ⇒ 安全余量大
#   · 但也不至于攒太久（用户想尽快听到新一期）⇒ MAX_DAYS = 7
AUDIO_BATCH_EPISODES = 10
AUDIO_BATCH_MAX_DAYS = 7


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def content_fingerprint():
    """用户能看到的内容指纹（md5 集合 → 汇总 md5）"""
    items = []
    for rel in FINGERPRINT_FILES:
        p = os.path.join(SITE, rel)
        if os.path.isfile(p):
            items.append([rel, md5(p)])
    for d in FINGERPRINT_DIRS:
        root = os.path.join(SITE, d)
        if not os.path.isdir(root):
            continue
        for fn in sorted(os.listdir(root)):
            if fn.endswith(".js"):
                items.append([f"{d}/{fn}", md5(os.path.join(root, fn))])
    if not items:
        die("内容指纹为空 ⇒ 源目录不对（%s）" % SITE)
    return hashlib.md5(json.dumps(items, ensure_ascii=False).encode("utf-8")).hexdigest(), items


def audio_fingerprint():
    """音频指纹：media/ 下所有音频文件的 md5 集合 → **dict**（{路径: md5}）

    🔴 为什么强制 dict 而不是 list：
        状态文件 work/daily_state.json 里的 audio_sha 必须和这里同型，
        否则「读基线」和「写基线」两处会一个按 dict 一个按 list 用
        ⇒ 一旦有一处写错，读取处就会崩在 `.items()` 这种看不懂的地方
        ⇒ **同一个数据结构，两处必须同一种访问方式**
    """
    out = {}
    for sub in ("slow", "fast"):
        root = os.path.join(SITE, "media", sub)
        if not os.path.isdir(root):
            continue
        for fn in sorted(os.listdir(root)):
            if fn.endswith((".m4a", ".mp3", ".aac")):
                out[f"{sub}/{fn}"] = md5(os.path.join(root, fn))
    return out


def as_dict(v):
    """把可能是 list / dict 的历史状态统一成 dict（坏状态不许让脚本崩在看不懂的地方）"""
    if isinstance(v, dict):
        return dict(v)
    if isinstance(v, list):
        return {str(k): str(x) for k, x in v}
    return {}


def load_state():
    if os.path.isfile(STATE):
        try:
            return json.load(open(STATE, encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_state(st):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE)


def die(msg):
    print("❌ " + msg, file=sys.stderr)
    sys.exit(1)


def log(msg):
    print(msg, flush=True)


def plan():
    st = load_state()
    fp, items = content_fingerprint()
    aud = audio_fingerprint()
    old_fp = st.get("content_sha")
    old_aud = as_dict(st.get("audio_sha"))
    cur_aud = dict(aud)

    new_audio = [k for k in cur_aud if k not in old_aud]
    changed_audio = [k for k in cur_aud if k in old_aud and old_aud[k] != cur_aud[k]]
    removed_audio = [k for k in old_aud if k not in cur_aud]

    content_changed = (old_fp != fp) if old_fp else True
    last_deploy = st.get("last_audio_deploy_at") or 0
    last_domain = st.get("last_audio_domain") or ""
    days_since = (time.time() - last_deploy) / 86400.0 if last_deploy else 999

    need_audio_deploy = bool(new_audio or changed_audio) and (
        len(new_audio) >= AUDIO_BATCH_EPISODES or days_since >= AUDIO_BATCH_MAX_DAYS
    )

    log("════ NHK 日更发版决策 ════")
    log("   内容指纹 %s（%d 个文件）" % (fp[:12], len(items)))
    if old_fp is None:
        log("   ⇒ 内容：**没有基线**（第一次跑）⇒ 按「变了」处理")
    elif content_changed:
        log("   ⇒ 内容：**变了** ⇒ 要同步 Pages（零配额，地址永不变）")
    else:
        log("   ⇒ 内容：没变 ⇒ 无需同步 Pages（用户也不必白下几百 KB）")
    log("   音频 %d 个文件（md5 集合）" % len(cur_aud))
    if new_audio:
        log("   ⇒ 新增音频 %d 个：%s" % (len(new_audio), ", ".join(new_audio[:6])))
    if changed_audio:
        log("   ⇒ 🔴 音频内容被改 %d 个：%s" % (len(changed_audio), ", ".join(changed_audio[:6])))
    if removed_audio:
        log("   ⇒ 音频被删 %d 个：%s" % (len(removed_audio), ", ".join(removed_audio[:6])))
    if last_deploy:
        log("   上次发音频站：%.1f 天前（%s）" % (days_since, last_domain))
    else:
        log("   上次发音频站：从来没有（首次必须发一次）")
    log("   攒批阈值：新增 ≥ %d 期 或 距上次 ≥ %d 天" % (AUDIO_BATCH_EPISODES, AUDIO_BATCH_MAX_DAYS))
    if need_audio_deploy:
        log("   ✅ 决策：**要发 mcode 站**（音频有新东西，且到阈值了）")
    else:
        if new_audio or changed_audio:
            log("   ⏸️ 决策：**先不发 mcode 站**（音频攒着，够阈值再发）⇒ **今天 0 配额占用**")
        else:
            log("   ⏸️ 决策：**不发 mcode 站**（音频没变）⇒ **今天 0 配额占用**")
    log("   💡 无论上面怎样，**Pages 同步永远是零配额**（同一个仓库、同一个站点）")
    return {
        "content_sha": fp,
        "content_changed": content_changed,
        "audio_changed": bool(new_audio or changed_audio),
        "new_audio": new_audio,
        "need_audio_deploy": need_audio_deploy,
        "last_audio_domain": last_domain,
        "days_since_audio_deploy": days_since,
    }


def sync_pages():
    """同步 Pages（零配额）。先过护栏，再推。"""
    st = load_state()
    fp, _ = content_fingerprint()
    if st.get("content_sha") and st["content_sha"] == fp and st.get("pages_synced_sha") == fp:
        log("ℹ️ 内容与上次同步完全一致 ⇒ 跳过（不升版号、不触发 build）")
        return 0
    src = os.path.join(ROOT, "deploy_site")
    if not os.path.isdir(src):
        src = SITE
        log("⚠️ 没有 deploy_site ⇒ 用 site/app 作为源")
    # ① 同步前护栏：源目录不能比线上旧（否则会把已发布的能力降级）
    r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "check_pages_sync.py"),
                        "--before", "--src", src], capture_output=True, text=True)
    if r.returncode != 0:
        log(r.stdout[-2000:])
        log(r.stderr[-800:])
        die("Pages 同步前护栏不过 ⇒ 不许推（会把线上能力降级）")
    # ② 推
    r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "sync_pages.py"),
                        "--src", src], capture_output=True, text=True)
    log(r.stdout[-2500:])
    if r.returncode != 0:
        log(r.stderr[-1200:])
        die("sync_pages.py 失败")
    # ③ 同步后护栏
    r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "check_pages_sync.py"),
                        "--after", "--src", src], capture_output=True, text=True)
    log(r.stdout[-1500:])
    if r.returncode != 0:
        die("Pages 同步后护栏不过")
    st["content_sha"] = fp
    st["pages_synced_sha"] = fp
    save_state(st)
    log("✅ Pages 已同步（**零 mcode 配额占用**）")
    return 0


def mark_audio_deployed(domain):
    st = load_state()
    fp, _ = content_fingerprint()
    aud = audio_fingerprint()
    st["content_sha"] = fp
    st["audio_sha"] = dict(aud)
    st["last_audio_deploy_at"] = time.time()
    st["last_audio_domain"] = domain
    st["pages_synced_sha"] = None      # 音频站换了 ⇒ Pages 的 MEDIA_BASE 要跟着更新
    save_state(st)
    log("✅ 已记录：音频站 = %s（%d 个音频文件已入基线）" % (domain, len(aud)))
    return 0


def need_audio_deploy():
    """exit 0 = 要发 mcode 站；exit 3 = 不要发（让 cron/脚本能直接判）"""
    p = plan()
    log("   ⇒ 退出码 %d（0=要发 / 3=不要发）" % (0 if p["need_audio_deploy"] else 3))
    sys.exit(0 if p["need_audio_deploy"] else 3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--sync-pages", action="store_true")
    ap.add_argument("--need-audio-deploy", action="store_true")
    ap.add_argument("--mark-audio-deployed", action="store_true")
    ap.add_argument("--domain", default="")
    a = ap.parse_args()
    if a.plan or not (a.sync_pages or a.need_audio_deploy or a.mark_audio_deployed):
        plan()
        return 0
    if a.sync_pages:
        return sync_pages()
    if a.need_audio_deploy:
        # 🔴 这一支必须**自己退出**：否则 main() 会继续往下走、最终 return 0
        #    ⇒ 「不要发」被当成「要发」⇒ 日更就会白占一个部署位（这正是要防的坑）
        return need_audio_deploy()
    if a.mark_audio_deployed:
        return mark_audio_deployed(a.domain)
    return 0


if __name__ == "__main__":
    sys.exit(main())

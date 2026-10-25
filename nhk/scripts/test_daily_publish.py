#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
daily_publish.py 的自测 + 反向验证（2026-10-25）

🔴 为什么要有这个：
    改「什么时候发 mcode 站」这种**决定用户能不能当天看到新一期**的阈值，
    光跑通不算数 —— 必须证明「改坏了它会红」。

🔴 纪律（照记忆里的老教训）：
  ① **判据落行为上**：只看 plan() 返回的 need_audio_deploy（真决策），不看日志文本
  ② **反向验证必须指向「期望红的那条断言」** ⇒ 不是笼统地「有红」
  ③ **离线可跑**：pending_backlog 被替换成假的 ⇒ 不依赖网络、不依赖线上站
  ④ 场景互相独立 ⇒ 一个挂了不影响别的

跑法：
    python3 scripts/test_daily_publish.py
    退出码 0 = 全过；非 0 = 有场景没过（打印哪条、期望什么、实际什么）
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time

DP = "/workspace/nhk/scripts/daily_publish.py"


def load_module():
    spec = importlib.util.spec_from_file_location("dp_mod", DP)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def build_site(root):
    """搭一个最小但真实的站目录（内容指纹 + 音频指纹都要有东西）"""
    app = os.path.join(root, "site", "app")
    for rel, body in [
        ("app.js", "// v1\n"), ("style.css", "body{}\n"), ("index.html", "<html></html>\n"),
        ("config.js", "window.CFG={};\n"), ("data/episodes.js", "var E=[];\n"),
        ("subs/a.js", "var S={};\n"),
    ]:
        p = os.path.join(app, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w", encoding="utf-8").write(body)
    slow = os.path.join(app, "media", "slow")
    os.makedirs(slow, exist_ok=True)
    open(os.path.join(slow, "ep1.m4a"), "wb").write(b"AUDIO1")
    return app


def add_new_audio(app, name="ep2.m4a"):
    """真的加一个新音频文件（否则「新增音频」这个前提是假的 ⇒ 场景成空语句）"""
    open(os.path.join(app, "media", "slow", name), "wb").write(b"AUDIO2-NEW")


def scenario(m, tmp, name, *, audio_state, last_deploy, backlog, expect, mutate=None,
             extra_audio=False):
    """跑一个场景 ⇒ 返回 (是否通过, 说明)"""
    root = os.path.join(tmp, name)
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)
    build_site(root)
    m.SITE = os.path.join(root, "site", "app")
    if extra_audio:
        add_new_audio(m.SITE)
    m.STATE = os.path.join(root, "work", "daily_state.json")

    # 状态：基线音频集合 + 上次发站时间
    st = {}
    if audio_state is not None:
        st["audio_sha"] = audio_state
    if last_deploy is not None:
        st["last_audio_deploy_at"] = last_deploy
    st["content_sha"] = "whatever"
    os.makedirs(os.path.dirname(m.STATE), exist_ok=True)
    json.dump(st, open(m.STATE, "w", encoding="utf-8"))

    m.pending_backlog = lambda: backlog   # 离线：不联网

    # 备份常量，反向验证时改
    save = (m.AUDIO_BATCH_EPISODES, m.AUDIO_BATCH_MAX_DAYS, m.MAX_PENDING_DELETES)
    if mutate:
        mutate(m)
    try:
        p = m.plan()
        got = p["need_audio_deploy"]
    finally:
        m.AUDIO_BATCH_EPISODES, m.AUDIO_BATCH_MAX_DAYS, m.MAX_PENDING_DELETES = save

    ok = (got == expect)
    return ok, "%-46s 期望要发=%-5s 实际=%-5s %s" % (name, expect, got, "✅" if ok else "❌")


def main():
    m = load_module()
    tmp = tempfile.mkdtemp(prefix="dptest_")
    now = time.time()
    day = 86400.0
    # 基线：只有 ep1
    base_audio = {"slow/ep1.m4a": m.md5.__name__ and None}  # 占位，下面用真实 md5
    # 真实 md5（用模块自己的函数，保证口径一致）
    import hashlib
    def md5_bytes(b):
        return hashlib.md5(b).hexdigest()
    base_audio = {"slow/ep1.m4a": md5_bytes(b"AUDIO1")}

    results = []

    # ── 正向场景 ───────────────────────────────────────────────
    # 1 首次跑（无音频基线）⇒ 有音频 ⇒ 要发
    results.append(scenario(m, tmp, "S1 首次跑_无基线", audio_state=None,
                            last_deploy=None, backlog=0, expect=True))
    # 2 音频没变 ⇒ 不发
    results.append(scenario(m, tmp, "S2 音频没变", audio_state=base_audio,
                            last_deploy=now, backlog=0, expect=False))
    # 3 ★本次改动的核心：新增 1 期（阈值 1）⇒ 要发（改之前阈值 10 ⇒ 不发）
    results.append(scenario(m, tmp, "S3 新增1期音频_阈值1", audio_state=base_audio,
                            last_deploy=now, backlog=0, extra_audio=True, expect=True))
    # 4 音频内容变了但当天刚发过（days_since=0 < MAX_DAYS=1）⇒ 不发
    results.append(scenario(m, tmp, "S4 音频变但当天刚发过",
                            audio_state={"slow/ep1.m4a": "DIFFERENT-md5"},
                            last_deploy=now, backlog=0, expect=False))
    # 5 距上次发站 1.2 天 ⇒ 要发
    results.append(scenario(m, tmp, "S5 距上次1.2天", audio_state=base_audio,
                            last_deploy=now - 1.2 * day, backlog=0, extra_audio=True, expect=True))
    # 6 ★配额保险：积压 3 个（= MAX）⇒ 停发（哪怕有新媒体）
    results.append(scenario(m, tmp, "S6 积压3个_停发", audio_state=base_audio,
                            last_deploy=now, backlog=3, extra_audio=True, expect=False))
    # 7 积压 2 个（< MAX）⇒ 照发
    results.append(scenario(m, tmp, "S7 积压2个_照发", audio_state=base_audio,
                            last_deploy=now, backlog=2, extra_audio=True, expect=True))

    # 8 音频**内容变了**（不是新增文件）+ 距上次 1.2 天 ⇒ 发（走 days_since 那条路径）
    results.append(scenario(m, tmp, "S8 音频内容变_1.2天前发过",
                            audio_state={"slow/ep1.m4a": "DIFFERENT-md5"},
                            last_deploy=now - 1.2 * day, backlog=0, expect=True))

    # ── 反向验证（每条必须让**指定的那条**场景翻红）────────────
    # R1：把配额闸门放宽到 99 ⇒ S6 必须从「不发」变成「发」（= 红）
    ok, msg = scenario(m, tmp, "R1 闸门放宽到99", audio_state=base_audio,
                       last_deploy=now, backlog=3, extra_audio=True, expect=False,
                       mutate=lambda mm: setattr(mm, "MAX_PENDING_DELETES", 99))
    results.append((not ok, msg + "   ← 反向验证：期望本条**翻红**（闸门被放宽 ⇒ S6 不再拦）"))
    # R2：把发布阈值调回 10 期 ⇒ S3 必须翻红（不再当天发）
    ok, msg = scenario(m, tmp, "R2 阈值调回10期", audio_state=base_audio,
                       last_deploy=now, backlog=0, extra_audio=True, expect=True,
                       mutate=lambda mm: setattr(mm, "AUDIO_BATCH_EPISODES", 10))
    results.append((not ok, msg + "   ← 反向验证：期望本条**翻红**（阈值调回 10 ⇒ 不再当天发）"))
    # R3：把 MAX_DAYS 调到 365 ⇒ S5 必须翻红（不再按天发）
    ok, msg = scenario(m, tmp, "R3 MAX_DAYS调365",
                       audio_state={"slow/ep1.m4a": "DIFFERENT-md5"},
                       last_deploy=now - 1.2 * day, backlog=0, expect=True,
                       mutate=lambda mm: setattr(mm, "AUDIO_BATCH_MAX_DAYS", 365))
    results.append((not ok, msg + "   ← 反向验证：期望本条**翻红**（MAX_DAYS=365 ⇒ 不再按天发）"))

    # ── 汇总 ───────────────────────────────────────────────────
    print("=" * 78)
    print("daily_publish 自测")
    print("=" * 78)
    passed = 0
    for ok, msg in results:
        print("  " + msg.replace("❌", "🔁 已按预期翻红"))
        if ok:
            passed += 1
    print("-" * 78)
    print("通过 %d / %d" % (passed, len(results)))
    shutil.rmtree(tmp, ignore_errors=True)
    if passed != len(results):
        print("❌ 有场景没过")
        return 1
    print("✅ 全过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

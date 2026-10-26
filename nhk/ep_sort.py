#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🔴🔴 播出时间的**唯一解析/排序权威**（2026-10-24 新增）

为什么要有：
    `build_data.py` 原来拿 `episode.json` 的 **time 原始字符串**直接排序：
        eps.sort(key=lambda e: str(ep.get("time","")), reverse=True)
    ⇒ 而 NHK 的 time **格式不统一**：
        "2026-10-22 20:15:00"   ← 带横杠
        "20261022T201500+0900"  ← 紧凑 ISO（另一条链路抓的）
        "2026/10/22 20:15"      ← 斜杠
    ⇒ **字符串比较**的结果：'2026-10-21' > '20261022'（'-' < '1'）
    ⇒ **新一期被甩到最后** ⇒ 首页第一条是旧的 ⇒ 用户看到的就是「停更」
    ⇒ 而且**不报错**（文件里没有重复 id，只是顺序反了）⇒ 极难发现。

⇒ 现在：时间一律先解析成 **(YYYYMMDD, HHMM) 数字元组**再排序
   ⇒ 任何格式都能排对，**格式变了也不怕**。

用法：
    from ep_sort import raw_key, field_key
    eps.sort(key=raw_key, reverse=True)      # 排序 episode.json 的 time
    eps.sort(key=field_key, reverse=True)     # 排序 episodes.js 的 date+time
"""
import re

# ① 日期：4 位年 + 2 位月 + 2 位日（中间的分隔符可有可无）
_DATE_RE = re.compile(r"(\d{4})[-/年.]?(\d{2})[-/月.]?(\d{2})")
# ② 时间：2 位时 + 2 位分（跟在日期后面找，避免误吃日期里的数字）
_TIME_RE = re.compile(r"(\d{2})[:時.]?(\d{2})")


def raw_key(raw_time):
    """从**原始 time 字符串**解析出排序键 (YYYYMMDD, HHMM)；解析不出来 → (0, 0)

    🔴 一定要**数字元组**，不能返回原字符串 —— 原字符串比较会被格式坑死。
    """
    t = str(raw_time or "")
    m = _DATE_RE.search(t)
    if not m:
        return (0, 0)
    try:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    except ValueError:
        return (0, 0)
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return (0, 0)
    date_key = y * 10000 + mo * 100 + d
    hh = mm = 0
    m2 = _TIME_RE.search(t[m.end():])
    if m2:
        try:
            hh, mm = int(m2.group(1)), int(m2.group(2))
        except ValueError:
            hh = mm = 0
    if hh > 23:
        hh = 0
    if mm > 59:
        mm = 0
    return (date_key, hh * 100 + mm)


def field_key(ep):
    """从 **episodes.js 的一条记录**（已有 date / time 字段）取排序键"""
    d = str((ep or {}).get("date") or "")
    m = _DATE_RE.search(d)
    if not m:
        return (0, 0)
    y, mo, dd = int(m.group(1)), int(m.group(2)), int(m.group(3))
    date_key = y * 10000 + mo * 100 + dd
    t = str((ep or {}).get("time") or "")
    m2 = _TIME_RE.search(t)
    hh = mm = 0
    if m2:
        hh, mm = int(m2.group(1)), int(m2.group(2))
    return (date_key, hh * 100 + mm)

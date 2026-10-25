#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🔴 薄转发（**不是第二份实现**）：真正的闸门在 scripts/check_cache_ver.py。

为什么留这个文件：别的会话（主会话 / cron）按老路径调用
`python3 scripts_check_cache_ver.py <app.js> <sw.js> <期望APP_VER>`。
⇒ 路径不能断，但**逻辑只许有一份**（手册铁律：同一个配方写在 N 个地方 = 改了 1/N）。

用法：python3 scripts_check_cache_ver.py <app.js> <sw.js> [期望APP_VER]
      python3 scripts_check_cache_ver.py --dir <站点目录> [期望APP_VER]
"""
import os
import runpy
import sys

TARGET = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "scripts", "check_cache_ver.py")
if not os.path.exists(TARGET):
    sys.exit("❌ 找不到唯一实现：%s" % TARGET)
sys.argv[0] = TARGET
runpy.run_path(TARGET, run_name="__main__")

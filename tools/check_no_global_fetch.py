#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查「云函数代码里没有全局 fetch / 全局 URL」—— 协作方可复现的机器判据

🔴 为什么必须有这个：
   CloudBase 的 Node 沙箱（scf_bootstrap 起的那个 node）是 Node 12/16
   ⇒ **没有全局 fetch、也没有全局 URL**
   ⇒ 代码里只要有一处真用 fetch() / new URL() ⇒ 函数照样能起来（部署"成功"）
      但**一调就 500** ⇒ 老师那边表现为「端点全挂」
   ⇒ 现役 ai-proxy 的 index.js 里就写着警告：「绝对不要用 new URL()」

🔴🔴 检查必须**按代码**检查，不能按文本检查：
   文件里的**注释**常会写「不能用 fetch()」「别用 new URL()」
   ⇒ 按文本扫 ⇒ **假红**（我今天就被自己的注释误伤过）
   ⇒ 所以：先做**词法级**剥离（注释与字符串内容），再扫
   ⇒ 剥离后仍保留字符串的**引号**（内容清空）—— 免得把字符串里的 id 名字也剥掉

用法：
    python3 tools/check_no_global_fetch.py <文件.js | 包.zip>
    退出码：0 = 通过（代码里没有全局 fetch / new URL）；1 = 不通过
"""
import os
import re
import sys
import zipfile


def strip_code(src):
    """剥掉注释与字符串内容（字符串留空引号）⇒ 只剩真代码"""
    out = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        two = src[i:i + 2]
        if two == '//':
            j = src.find('\n', i)
            j = n if j < 0 else j
            out.append(' ' * (j - i))
            i = j
            continue
        if two == '/*':
            j = src.find('*/', i + 2)
            j = n if j < 0 else j + 2
            out.append(''.join(ch if ch == '\n' else ' ' for ch in src[i:j]))
            i = j
            continue
        if c in '"\'':
            q = c
            j = i + 1
            while j < n:
                if src[j] == '\\':
                    j += 2
                    continue
                if src[j] == q:
                    j += 1
                    break
                j += 1
            out.append(q + q)
            i = j
            continue
        out.append(c)
        i += 1
    return ''.join(out)


def check_text(name, text, src_text):
    code = strip_code(text)
    bad = []
    for m in re.finditer(r"(^|[^.\w])fetch\s*\(", code):
        bad.append(('fetch(', code[max(0, m.start() - 40):m.start() + 60].replace('\n', ' ')))
    for m in re.finditer(r"new\s+URL\s*\(", code):
        bad.append(('new URL(', code[max(0, m.start() - 40):m.start() + 60].replace('\n', ' ')))
    # 注释里的出现（供人核对：不是违规，但要让人看见）
    n_fetch_txt = len(re.findall(r"fetch\s*\(", src_text))
    n_url_txt = len(re.findall(r"new\s+URL\s*\(", src_text))
    print("· %s：文本里 fetch( %d 处 / new URL( %d 处；**代码**里 %d 处违规"
          % (name, n_fetch_txt, n_url_txt, len(bad)))
    for kind, ctx in bad:
        print("   🔴 %s → …%s…" % (kind, ctx))
    return len(bad) == 0


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    target = sys.argv[1]
    ok = True
    if zipfile.is_zipfile(target):
        with zipfile.ZipFile(target) as z:
            for name in z.namelist():
                if not name.endswith('.js'):
                    continue
                text = z.read(name).decode('utf-8', 'replace')
                ok = check_text("%s!%s" % (os.path.basename(target), name), text, text) and ok
    else:
        text = open(target, encoding='utf-8', errors='replace').read()
        ok = check_text(os.path.basename(target), text, text)
    print("✅ 通过：代码里没有全局 fetch / new URL" if ok else "❌ 不通过：代码里有全局 fetch / new URL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

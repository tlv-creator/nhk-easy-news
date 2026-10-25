#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
墨水屏描边闸门 · 自检（2026-10-22 v4）
========================================================================
这个脚本测的是**闸门本身**，不是 css。

为什么必须有它
--------------------------------------------------------------------------
🔴 「已知违规 → 必须抓到」这一侧**不够**：
   一个恒返回「没有违规」的空实现也能通过那个测试。
   ⇒ 必须再有「**已知合法 → 必须判干净**」这一侧。
（这是 APK API 闸门 v4 的教训： pony 指出我的反向用例「拆掉全部守卫」，
  而我原来的反向用例只拆了第一个 → 样本假设错 → 自检看着像坏了。）

🔴 更狠的一条：**闸门被改松了，自检必须红**。
   「放宽外部告警」这种改动很容易悄悄变成「所有错误都不报了」。
   ⇒ D 组：把判色函数改成恒 false / 恒 true，看自检会不会红。

四组测试
--------------------------------------------------------------------------
A 组 · 正向：已知违规必须抓到（条数 / 行号 / 原因类型 / 豁免不误报）
B 组 · 反向：已知合法必须判干净（exit 0 / 无 FAIL / 结构线名额内不报）
C 组 · 跨实现对拍：JS 与 Python 两份实现**输出逐字一致**
D 组 · 闸门自身有效性：改松了必须红
E 组 · 真实项目 css 不误报（拿真的 ai-client.css 跑）

用法
--------------------------------------------------------------------------
    python3 test_eink_gate.py            # 全跑
    python3 test_eink_gate.py -v         # 打印每项明细
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))   # eink-gate/
ROOT = HERE
# 🔴 可被环境变量覆盖 —— F 组要用「改坏的闸门」跑一遍本自检，
#    证明「自检本身有效」，而不是只证明「闸门有效」。
JS = os.environ.get('EINK_JS') or os.path.join(ROOT, 'check_eink_css.js')
PY = os.environ.get('EINK_PY') or os.path.join(ROOT, 'check_eink_css.py')
SAMPLE = os.path.join(HERE, 'test', 'sample.css')
CLEAN = os.path.join(HERE, 'test', 'clean.css')

VERBOSE = '-v' in sys.argv

_results = []
_skipped = []


def ok(name, cond, detail=''):
    _results.append((bool(cond), name, detail))
    if VERBOSE or not cond:
        mark = 'PASS' if cond else 'FAIL'
        print('[%s] %s%s' % (mark, name, ('  → ' + detail) if detail else ''))
    return bool(cond)


def _skip(name, detail=''):
    """显式跳过 —— 不算通过、也不算失败，但必须**看得见**。"""
    _skipped.append((name, detail))
    print('[SKIP] %s%s' % (name, ('  → ' + detail) if detail else ''))


def run_js(path, extra=()):
    return subprocess.run(['node', JS] + list(extra) + [path],
                          capture_output=True, text=True)


def run_py(path, extra=()):
    return subprocess.run([sys.executable, PY] + list(extra) + [path],
                          capture_output=True, text=True)


def both(path, extra=()):
    return run_js(path, extra), run_py(path, extra)


def run_js_multi(paths, extra=()):
    return subprocess.run(['node', JS] + list(extra) + list(paths),
                          capture_output=True, text=True)


def run_py_multi(paths, extra=()):
    return subprocess.run([sys.executable, PY] + list(extra) + list(paths),
                          capture_output=True, text=True)


# ─────────────────────────────────────────────────────────────────
# A 组 · 正向（已知违规必须抓到）
# ─────────────────────────────────────────────────────────────────
def group_a():
    j, p = both(SAMPLE)

    ok('A1 违规样本 → JS exit 1', j.returncode == 1, 'got %s' % j.returncode)
    ok('A1 违规样本 → PY exit 1', p.returncode == 1, 'got %s' % p.returncode)

    # 期望的行号（从 test/sample.css 的注释编号推出来）
    expect_lines = [2, 4, 6, 8, 11, 13, 15, 21, 30, 34, 36]
    for tag, r in (('JS', j), ('PY', p)):
        got = [int(m) for m in re.findall(r'第 (\d+) 行：', r.stdout)]
        ok('A2 %s 违规行号完全一致（%d 条）' % (tag, len(expect_lines)),
           got == expect_lines, 'got %s' % got)

        reasons = r.stdout
        ok('A3 %s 报出「纯黑盒子边框」' % tag,
           '纯黑盒子边框' in reasons)
        ok('A3 %s 报出「解析不出来」' % tag,
           '解析不出来' in reasons)
        ok('A3 %s 报出「currentColor」' % tag,
           'currentColor' in reasons)

        # eink-ok 豁免行（第 28 行）不该被报
        ok('A4 %s eink-ok 豁免行没被报出来' % tag,
           '第 28 行' not in reasons)

        # 合法行不该被报
        for ln in (18, 25, 32, 38):
            ok('A5 %s 合法行 %d 没被误报' % (tag, ln),
               ('第 %d 行' % ln) not in reasons)

    # 两份实现输出必须逐字一致
    ok('C1 违规样本：JS / PY 输出逐字一致', j.stdout == p.stdout,
       'JS %d 行 / PY %d 行' % (len(j.stdout.splitlines()),
                                len(p.stdout.splitlines())))


# ─────────────────────────────────────────────────────────────────
# B 组 · 反向（已知合法必须判干净）
# ─────────────────────────────────────────────────────────────────
def group_b():
    j, p = both(CLEAN)

    ok('B1 合法样本 → JS exit 0', j.returncode == 0,
       'stdout=%s' % j.stdout.strip()[:200])
    ok('B1 合法样本 → PY exit 0', p.returncode == 0,
       'stdout=%s' % p.stdout.strip()[:200])

    for tag, r in (('JS', j), ('PY', p)):
        ok('B2 %s 合法样本输出里没有 FAIL' % tag, 'FAIL' not in r.stdout)
        ok('B2 %s 合法样本输出里有「全部通过」' % tag,
           '全部通过' in r.stdout)

    ok('C2 合法样本：JS / PY 输出逐字一致', j.stdout == p.stdout)

    # 结构线名额：合法样本里 1 条 border-bottom 实黑（名额 2）→ 不报
    ok('B3 结构线名额内的 border-bottom 不算违规',
       'border-bottom' not in j.stdout and 'border-bottom' not in p.stdout)


# ─────────────────────────────────────────────────────────────────
# C 组 · 跨实现对拍（JSON 模式）
# ─────────────────────────────────────────────────────────────────
def group_c():
    for label, path in (('违规', SAMPLE), ('合法', CLEAN)):
        j = run_js(path, ['--json'])
        p = run_py(path, ['--json'])
        try:
            jd = json.loads(j.stdout)
            pd = json.loads(p.stdout)
        except Exception as e:
            ok('C3 %s样本 JSON 可解析' % label, False, str(e))
            continue
        ok('C3 %s样本 JSON 逐字一致' % label, j.stdout == p.stdout)
        ok('C3 %s样本 JSON ok 字段一致' % label, jd.get('ok') == pd.get('ok'))
        ok('C3 %s样本 JSON 违规条数一致' % label,
           len(jd['files'][0].get('problems', [])) ==
           len(pd['files'][0].get('problems', [])))


# ─────────────────────────────────────────────────────────────────
# D 组 · 闸门自身有效性（改松了必须红）
# ─────────────────────────────────────────────────────────────────
def _break(src, is_js, mode):
    """把闸门改坏（4 种形态），用来验证「自检本身有效」。"""
    if mode == 'never' or mode == 'always':
        if is_js:
            old = 'function isPureBlack(color) {'
            ret = ' return false;' if mode == 'never' else ' return true;'
        else:
            old = 'def is_pure_black(color):'
            ret = '\n    return False' if mode == 'never' else '\n    return True'
        assert old in src, '找不到判色函数：%s' % old
        return src.replace(old, old + ret, 1)

    if mode == 'unresolvable_passthrough':
        # 「变量解析不出来」从 FAIL 改成放行 —— 最阴险的静默失真
        if is_js:
            old = """  return {
    colors: [],
    err: `${pc.name} 在这些 css 里解析不出来 —— 可能是纯黑但没法确认；` +
         `要么补上定义，要么在该行加「eink-ok: 理由」显式豁免`
  };"""
            new = "  return { colors: ['#ffffff'], err: null };"
        else:
            old = ("""    return [], ('%s 在这些 css 里解析不出来 —— 可能是纯黑但没法确认；'
                '要么补上定义，要么在该行加「eink-ok: 理由」显式豁免' % name)""")
            new = "    return ['#ffffff'], None"
        assert old in src, '找不到「解析不出」分支'
        return src.replace(old, new, 1)

    if mode == 'no_important_strip':
        # 不剥 !important → '#000 !important' 判不出纯黑 → 漏检
        if is_js:
            old = 'const IMPORTANT_RE = /!\\s*important\\b/gi;'
            new = 'const IMPORTANT_RE = /(?!x)x/gi;'
        else:
            old = "IMPORTANT_RE = re.compile(r'!\\s*important\\b', re.IGNORECASE)"
            new = "IMPORTANT_RE = re.compile(r'(?!x)x', re.IGNORECASE)"
        assert old in src, '找不到 IMPORTANT_RE'
        return src.replace(old, new, 1)

    raise ValueError('未知改坏方式：%s' % mode)


def group_d():
    tmp = tempfile.mkdtemp(prefix='eink-broken-')
    try:
        src_js = open(JS, encoding='utf-8').read()
        src_py = open(PY, encoding='utf-8').read()

        # D1 · 恒 false → 纯黑判不出来 → 正向样本里必须**抓不到**「纯黑盒子边框」
        #      （证明闸门真的在判黑，而不是恒绿）
        bjs = os.path.join(tmp, 'b.js')
        bpy = os.path.join(tmp, 'b.py')
        open(bjs, 'w', encoding='utf-8').write(_break(src_js, True, 'never'))
        open(bpy, 'w', encoding='utf-8').write(_break(src_py, False, 'never'))
        rj = subprocess.run(['node', bjs, SAMPLE], capture_output=True, text=True)
        rp = subprocess.run([sys.executable, bpy, SAMPLE],
                            capture_output=True, text=True)
        ok('D1 JS 改成「不判黑」→ 抓不到纯黑（证明闸门在判）',
           '纯黑盒子边框' not in rj.stdout, rj.stdout[:120])
        ok('D1 PY 改成「不判黑」→ 抓不到纯黑（证明闸门在判）',
           '纯黑盒子边框' not in rp.stdout, rp.stdout[:120])
        # 但它**仍要**报「解析不出来」—— 那些跟判黑无关
        ok('D1 改成「不判黑」→ 仍报得出「解析不出来」（没把闸门整个关掉）',
           '解析不出来' in rj.stdout and '解析不出来' in rp.stdout)

        # D2 · 恒 true → 合法样本被误判 → 反向组必须红
        #      （证明反向组不是摆设）
        cjs = os.path.join(tmp, 'c.js')
        cpy = os.path.join(tmp, 'c.py')
        open(cjs, 'w', encoding='utf-8').write(_break(src_js, True, 'always'))
        open(cpy, 'w', encoding='utf-8').write(_break(src_py, False, 'always'))
        qj = subprocess.run(['node', cjs, CLEAN], capture_output=True, text=True)
        qp = subprocess.run([sys.executable, cpy, CLEAN],
                            capture_output=True, text=True)
        ok('D2 JS 改成「全判黑」→ 合法样本被判 FAIL（反向组有效）',
           qj.returncode == 1 and 'FAIL' in qj.stdout)
        ok('D2 PY 改成「全判黑」→ 合法样本被判 FAIL（反向组有效）',
           qp.returncode == 1 and 'FAIL' in qp.stdout)

        # D3 · 文件不存在 → 必须 FAIL，不能「找不到就放行」
        nj = run_js(os.path.join(tmp, 'nope.css'))
        np_ = run_py(os.path.join(tmp, 'nope.css'))
        ok('D3 JS 文件不存在 → exit 1（不能当成没东西可查）',
           nj.returncode == 1 and '文件不存在' in nj.stdout)
        ok('D3 PY 文件不存在 → exit 1（不能当成没东西可查）',
           np_.returncode == 1 and '文件不存在' in np_.stdout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────
# E 组 · 真实项目 css 不误报
# ─────────────────────────────────────────────────────────────────
def _real_css():
    """找一份**真的**项目 css：优先本机的 ai-client.css，没有就用内置样本。
    返回 (路径, 说明) 或 None。
    🔴 内置样本 test/real-sample.css 就是 ai-client.css 的副本（真正在用过的样式）
    → 线上独立分发版也能跑 E1/E2 → **不再有 SKIP**
    """
    real = os.path.join(os.path.dirname(ROOT), 'ai-client', 'ai-client.css')
    if os.path.exists(real):
        return real, '本机 ai-client.css'
    bundled = os.path.join(HERE, 'test', 'real-sample.css')
    if os.path.exists(bundled):
        return bundled, '内置 real-sample.css'
    return None, ''


def group_e():
    real, where = _real_css()
    if real is None:
        # 🔴 这里跟「闸门的『文件不存在 → FAIL』」是**两回事**：
        #    闸门那条铁律针对的是「闸门扫不到东西就放行」= 假绿灯；
        #    而这里是**测试样本不在**（线上独立分发 / 别人的仓库里没这个文件）
        #    → 这一项**没得跑**，不是「测试失败」。
        #    ⇒ 显式 SKIP，单独计数，**不算通过**。
        _skip('E1 真实 css 既不在本机也没有内置样本 —— 本项跳过，不算通过')
        return
    j, p = both(real)
    ok('E1 真实 ai-client.css → JS exit 0', j.returncode == 0,
       j.stdout.strip()[:300])
    ok('E1 真实 ai-client.css → PY exit 0', p.returncode == 0,
       p.stdout.strip()[:300])
    ok('E1 真实 ai-client.css 两份输出逐字一致', j.stdout == p.stdout)


# ─────────────────────────────────────────────────────────────────
# F 组 · 自检的有效性（把闸门改坏 → 本自检必须红）
# ─────────────────────────────────────────────────────────────────
def group_f():
    if os.environ.get('EINK_SELFTEST_F') == '1':
        return    # 🔴 子进程里跳过 F 组，否则无限递归
    tmp = tempfile.mkdtemp(prefix='eink-f-')
    try:
        src_js = open(JS, encoding='utf-8').read()
        src_py = open(PY, encoding='utf-8').read()
        cases = [
            ('never', '判黑失明（恒 false）'),
            ('always', '过度敏感（恒 true）'),
            ('unresolvable_passthrough', '变量解析不出就放行'),
            ('no_important_strip', '不剥 !important（漏检变体）'),
        ]
        for kind, desc in cases:
            bjs = os.path.join(tmp, kind + '.js')
            bpy = os.path.join(tmp, kind + '.py')
            try:
                open(bjs, 'w', encoding='utf-8').write(_break(src_js, True, kind))
                open(bpy, 'w', encoding='utf-8').write(_break(src_py, False, kind))
            except AssertionError as e:
                ok('F 「%s」改坏点存在' % desc, False, str(e))
                continue
            env = dict(os.environ, EINK_JS=bjs, EINK_PY=bpy,
                       EINK_SELFTEST_F='1')
            r = subprocess.run([sys.executable, os.path.abspath(__file__)],
                               capture_output=True, text=True, env=env)
            # 🔴🔴 **「子进程 returncode != 0」不等于「自检抓到了改坏」**：
            #    子进程可能只是**崩了**（路径错 / 异常）→ returncode 也是 1
            #    → 那样 F 组会「假绿」，什么都没证明。
            #    ⇒ 必须要求它**正常跑完**（输出里有收尾汇总行）**且确实有失败项**。
            out = r.stdout + r.stderr
            m = re.search(r'墨水屏闸门自检：(\d+) 通过 / (\d+) 失败', out)
            if not m:
                ok('F 闸门改坏（%s）→ 子进程必须正常跑完' % desc, False,
                   '子进程没跑到收尾（崩了？）returncode=%s，尾部=%s'
                   % (r.returncode, out.strip()[-160:]))
                continue
            ok('F 闸门改坏（%s）→ 自检正常跑完且**抓到了**' % desc,
               int(m.group(2)) > 0,
               '通过=%s 失败=%s —— 改坏没被抓到，这条自检形同虚设'
               % (m.group(1), m.group(2)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def group_e2():
    """🔴 真实链路验证：拿**真的** css 改坏一份 → 必须被拦住。

    🔴 这是 NHK 提的一条很硬的观点，我照做：
    > 「闸门能不能拦住必须在**真实链路**上验，不能在自检里自证。」
    理由：自检是「我造的样本 ↔ 我写的判据」→ 两边都是我写的，容易一起错。
    拿**真实项目的 css** 改坏一字节再跑 → 才验证了「闸门在真实输入上真的拦得住」。
    """
    # 优先用**本机的真项目 css**；没有就退回**内置的真实样本**
    # （test/real-sample.css = ai-client.css 的副本，是真正在用过的样式）
    # 🔴 之前这里找不到文件就 SKIP → 线上分发版永远有 2 项 SKIP
    #    → 线上看不到「真实链路到底能不能拦住」，等于没验
    real, where = _real_css()
    if real is None:
        _skip('E2 真实 css 既不在本机也没有内置样本 —— 本项跳过，不算通过')
        return
    src = open(real, encoding='utf-8').read()

    # ① 原样必须判干净
    j, p = both(real)
    ok('E2 真实 css 原样 → JS 判干净', j.returncode == 0)
    ok('E2 真实 css 原样 → PY 判干净', p.returncode == 0)

    # ② 改坏一字节 → 必须被拦住（**两份实现都要拦**）
    broken = src + '\n.eink-broken { border: 2px solid #000; }\n'
    tmp = tempfile.mkdtemp(prefix='eink-e2-')
    try:
        bf = os.path.join(tmp, 'broken.css')
        with open(bf, 'w', encoding='utf-8') as f:
            f.write(broken)
        bj, bp = both(bf)
        ok('E2 改坏的真实 css → JS 拦住',
           bj.returncode == 1 and '纯黑盒子边框' in bj.stdout)
        ok('E2 改坏的真实 css → PY 拦住',
           bp.returncode == 1 and '纯黑盒子边框' in bp.stdout)
        ok('E2 两份实现在真实链路上的判定一致', bj.stdout == bp.stdout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def group_rel():
    """🔴🔴 v4.4 新增：**相对路径**调用必须和绝对路径**完全一致**

    🔴🔴 这是本轮抓到的最险的一个 bug（我自己 v4.3 引入的）：
       主流程用 path.resolve(f) 查结果、聚合时用相对路径存 → **key 对不上**
       → 单文件（或任何**没传 html** 的多文件）扫描**全部返回 0 条**
       → 输出「全部通过 ✅」= **假绿灯**，比报错危险得多。

    为什么自检没抓到：**自检全都用绝对路径调用**，
    而真实用户是 `node check_eink_css.js web/*.css`（**相对路径**）
    → 两者行为不同 → 自检永远抓不到。

    ⇒ 这一组专门用**相对路径**跑，并要求：
       ① 相对路径**必须抓到**已知违规（不能是 0 条）
       ② 相对路径与绝对路径的输出**归一化路径后逐字一致**
       ③ 多文件（无 html）也一样
    """
    def run_rel(cmd, paths):
        return subprocess.run(cmd + list(paths), capture_output=True,
                              text=True, cwd=HERE)

    j_rel = run_rel(['node', 'check_eink_css.js'], ['test/leak.css'])
    p_rel = run_rel([sys.executable, 'check_eink_css.py'], ['test/leak.css'])
    j_abs, p_abs = run_js(LEAK), run_py(LEAK)

    for tag, r in (('JS', j_rel), ('PY', p_rel)):
        got = [int(m) for m in re.findall(r'第 (\d+) 行：', r.stdout)]
        ok('H1 %s 相对路径调用**必须抓到**已知违规（不能是假绿灯）' % tag,
           len(got) > 0 and r.returncode == 1,
           'got=%s exit=%s' % (got, r.returncode))

    def norm(txt, abs_path):
        return txt.replace(abs_path, os.path.relpath(abs_path, HERE))

    ok('H2 JS 相对路径 vs 绝对路径 → 归一化后逐字一致',
       norm(j_rel.stdout, LEAK) == norm(j_abs.stdout, LEAK),
       'rel=%r abs=%r' % (norm(j_rel.stdout, LEAK)[:120],
                          norm(j_abs.stdout, LEAK)[:120]))
    ok('H2 PY 相对路径 vs 绝对路径 → 归一化后逐字一致',
       norm(p_rel.stdout, LEAK) == norm(p_abs.stdout, LEAK),
       'rel=%r abs=%r' % (norm(p_rel.stdout, LEAK)[:120],
                          norm(p_abs.stdout, LEAK)[:120]))

    # ③ 多文件、**没传 html**（退回跨文件合并）—— 也必须抓到
    multi = ['test/leak.css', 'test/sample.css']
    jm = run_rel(['node', 'check_eink_css.js'], multi)
    pm = run_rel([sys.executable, 'check_eink_css.py'], multi)
    for tag, r in (('JS', jm), ('PY', pm)):
        got = [int(m) for m in re.findall(r'第 (\d+) 行：', r.stdout)]
        ok('H3 %s 多文件且无 html → 仍必须抓到已知违规' % tag,
           len(got) > 0 and r.returncode == 1,
           'got=%s exit=%s' % (got, r.returncode))


def group_pages():
    """🔴🔴 v4.3 新增：变量表必须**按页面分组**（同名变量在不同页面里无关）

    背景（实测踩到的）：
      hub 的结构 = 总目录 index.html（内联 `--line:#111`）+ 6 个项目子页
      → 全局合并变量表 ⇒ 总目录的 --line:#111 污染所有项目的 var(--line)
      → 一次扫描 93 条，其中 **80 条是假的**（维护者会白改一堆）

    正向：页面 A（--line:#111）必须被抓
    反向：页面 B（--line:#2e4a32，1px 细线）**必须判干净**
          —— 如果实现是全局合并，--line 同时含 #111 → B 被误报
    """
    a_html = os.path.join(HERE, 'test', 'page-a', 'index.html')
    a_css = os.path.join(HERE, 'test', 'page-a', 'style.css')
    b_html = os.path.join(HERE, 'test', 'page-b', 'index.html')
    b_css = os.path.join(HERE, 'test', 'page-b', 'style.css')
    j, p = run_js_multi([a_html, a_css, b_html, b_css]), run_py_multi([a_html, a_css, b_html, b_css])

    for tag, r in (('JS', j), ('PY', p)):
        out = r.stdout
        ok('G8 %s 页面 A（--line:#111）被抓到' % tag,
           'page-a/style.css' in out and '第 3 行' in out, out.strip()[:300])
        ok('G8 %s 页面 A 判的是「纯黑级」' % tag, '纯黑级' in out)
        ok('G9 %s 页面 B 不被误报（跨页面同名变量不得污染）' % tag,
           'page-b/style.css' in out and 'FAIL' not in
           out.split('page-b/style.css')[1].split('[eink-css]')[0],
           out.strip()[:300])
    ok('G10 两份实现在多页面分组上逐字一致', j.stdout == p.stdout)


LEAK = os.path.join(HERE, 'test', 'leak.css')
LEAK_HTML = os.path.join(HERE, 'test', 'leak.html')


def group_g():
    """🔴 v4.2 新增：三个**曾经会漏**的洞，每个都要正反两向。

    ① 近黑（#111/#222/#1a1a1a/#333）—— 墨水屏是二值的，近黑在屏上就是纯黑
    ② 小数 px（1.5px）—— 正则 (\\d+)px 匹配不到
    ③ HTML 内联 <style> —— 只扫 .css = 完全扫不到
    ④ 半透明（rgba(0,0,0,.35)）**不得**误判 —— 我加近黑判据时漏了 alpha，
       E1/E2 真实 css 组当场抓到的回归（这条是反向组的最后一道）
    """
    # ── ① 近黑 + ② 小数 px + 变量链 ──
    j, p = both(LEAK)
    ok('G1 漏检样本 → JS exit 1', j.returncode == 1)
    ok('G1 漏检样本 → PY exit 1', p.returncode == 1)
    for tag, r in (('JS', j), ('PY', p)):
        got = [int(m) for m in re.findall(r'第 (\d+) 行：', r.stdout)]
        expect = [25, 27, 29, 31, 33, 35]
        ok('G1 %s 漏检样本行号完全一致（%d 条）' % (tag, len(expect)),
           got == expect, 'got %s' % got)
        ok('G1 %s 报出「纯黑级」' % tag, '纯黑级' in r.stdout)
        ok('G1 %s 报出「纯黑」' % tag, '纯黑盒子边框' in r.stdout)
        ok('G1 %s 报出「深色粗」' % tag, '深色粗' in r.stdout)
        # ③ 反向：合法色 / 半透明 / 细线 / 单边 / 豁免行 都不该出现
        for ln, why in ((39, '1px 深色线（细线不是粗杠杠）'),
                        (41, '单边深色粗线（只是分隔线）'),
                        (43, '合法色 #556677'),
                        (45, '半透明 rgba(0,0,0,.35)'),
                        (47, 'border-bottom 结构线'),
                        (49, 'eink-ok 豁免行')):
            ok('G4 %s %s 未被误报' % (tag, why),
               ('第 %d 行' % ln) not in r.stdout)
    ok('G6 漏检样本：JS / PY 输出逐字一致', j.stdout == p.stdout)

    # ── ③ HTML 内联 <style> ──
    hj, hp = both(LEAK_HTML)
    ok('G3 HTML 内联 → JS exit 1', hj.returncode == 1)
    ok('G3 HTML 内联 → PY exit 1', hp.returncode == 1)
    for tag, r in (('JS', hj), ('PY', hp)):
        got = [int(m) for m in re.findall(r'第 (\d+) 行：', r.stdout)]
        ok('G3 %s HTML 内联行号正确（[12, 17]）' % tag,
           got == [12, 17], 'got %s' % got)
    ok('G6 HTML 内联：JS / PY 输出逐字一致', hj.stdout == hp.stdout)

    # ── ⑦ 近黑判据本身可被验证：把它关掉，近黑必须抓不到 ──
    # ⑦a 纯黑级判据（--max-dark）可被验证
    j0 = run_js(LEAK, ['--max-dark', '0'])
    p0 = run_py(LEAK, ['--max-dark', '0'])
    ok('G7a 关掉纯黑级判据 → 字面量纯黑仍抓到',
       '纯黑盒子边框' in j0.stdout and '纯黑盒子边框' in p0.stdout)
    ok('G7a 关掉纯黑级判据 → 「纯黑级」不再报（证明这条判据真的在起作用）',
       '纯黑级' not in j0.stdout and '纯黑级' not in p0.stdout)
    ok('G7a 关掉纯黑级判据 → 违规条数 6 → 3（#111 的 2px 那条降级成「深色粗」仍报）',
       len(re.findall(r'第 \d+ 行：', j0.stdout)) == 3,
       'got %d' % len(re.findall(r'第 \d+ 行：', j0.stdout)))

    # ⑦b 深色级判据（--max-mid）可被验证
    j1 = run_js(LEAK, ['--max-mid', '0'])
    p1 = run_py(LEAK, ['--max-mid', '0'])
    ok('G7b 关掉深色级判据 → 「深色粗」不再报（证明这条判据真的在起作用）',
       '深色粗' not in j1.stdout and '深色粗' not in p1.stdout)
    ok('G7b 关掉深色级判据 → 「纯黑级」仍报（两条判据互相独立）',
       '纯黑级' in j1.stdout and '纯黑级' in p1.stdout)
    ok('G7b 关掉深色级判据 → 违规条数 6 → 5',
       len(re.findall(r'第 \d+ 行：', j1.stdout)) == 5,
       'got %d' % len(re.findall(r'第 \d+ 行：', j1.stdout)))


def main():
    group_a()
    group_b()
    group_c()
    group_d()
    group_e()
    group_e2()
    group_g()
    group_rel()
    group_pages()
    group_f()

    npass = sum(1 for r in _results if r[0])
    nfail = len(_results) - npass
    print('─' * 58)
    print('墨水屏闸门自检：%d 通过 / %d 失败 / %d 跳过（共 %d）'
          % (npass, nfail, len(_skipped), len(_results) + len(_skipped)))
    if _skipped:
        print('跳过项（**不算通过**）：')
        for name, detail in _skipped:
            print('  ○ %s  %s' % (name, detail))
    if nfail:
        print('失败项：')
        for good, name, detail in _results:
            if not good:
                print('  ✗ %s  %s' % (name, detail))
    print('─' * 58)
    sys.exit(1 if nfail else 0)


if __name__ == '__main__':
    main()

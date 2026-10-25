#!/usr/bin/env python3
"""
frame.s 校验层（生成侧的后处理闸门）—— 2026-10-25 peer 复核抓出 3 类缺陷后的修法。

三处缺陷的共同根因：**frame[].s 不是原句的逐字连续子串**
  ① s 里混进了语义括号字符（`［］〈〉（）【】`）—— 括号只能由 b 字段承载
  ② 同义替换（「各々」→「色々な」）
  ③ 块越界（多吃了「と」）

判据（全部落在「事实量」上）：
  ① s 里不许有任何语义括号字符        ⇒ 剥掉后必须仍等于剥之前的**裸片段**
  ② s 必须是 ja 的**连续子串**        ⇒ 抓同义替换 / 越界
  ③ 剥括号后的 s 仍须是 ja 的子串     ⇒ 剥完若不成立 ⇒ 整块丢弃（不做猜测性修补）

**丢弃策略**：宁可少一个块，也不让一个不逐字的块进数据（用户原话「要分开不要一起」＝
每一块都必须真）。丢弃的块记进 fail.log，不静默。
"""
import re

# 语义括号字符：这些**只能**出现在 b 字段里，绝不能出现在 s 里
SEM_BRACKETS = "［］〈〉（）【】〔〕｛｝"
# 半角方括号在句子里是合法的（如「〜こと」不涉及；但 [ ] 也不是日文标点）⇒ 一并清掉
HALF_BRACKETS = "[]"


def strip_sem_brackets(s):
    """
    s 里混进了语义括号 ⇒ 取出**第一个括号对内的内容**作为该块的 s。

    🔴 为什么不是「剥掉所有括号、保留全部文字」（2026-10-25 实测踩到）：
       模型的 s = "［日本語を教えるの］が大好きですから"
       「剥掉括号」⇒ "日本語を教えるのが大好きですから"
       ⇒ 这一块**横跨了从句边界**（从句 + 接续尾巴）⇒ 把后面的主句成分
         （私は / 今の仕事が / 大好きです）**全盖住了** ⇒ 那些块被当"被覆盖"丢弃
       ⇒ 主句谓语整个消失 💥
       「取括号内」⇒ "日本語を教えるの" ⇒ 边界正确（p=[0,8)）⇒ 后续块各归其位 ✅
    ⇒ 语义上这也对：模型用括号标的就是**这个成分的范围**。
    """
    if not s:
        return s
    m = re.search(r'[\[［]([^\]］]*)[\]］]', s)
    if m:
        return m.group(1).strip()
    return "".join(c for c in s if c not in SEM_BRACKETS and c not in HALF_BRACKETS)


def check_frame(ja, frame):
    """
    校验并清洗一帧的 frame。
    返回 (clean_frame, problems, repaired)
      problems: [{'i': 块序号, 's': 原 s, 'why': 'bracket-mixed'|'not-substring'|'empty'|'bad-bracket-type'}]
      repaired: [{'i': 块序号, 'from': 原 s, 'to': 修好的 s}]  ← 剥括号后仍合法的（**不丢块**）
    """
    problems = []
    repaired = []
    clean = []
    for i, f in enumerate(frame or []):
        if not isinstance(f, dict):
            continue
        s = f.get('s') or ''
        why = None
        if not s:
            why = 'empty'
        else:
            had_bracket = any(c in s for c in SEM_BRACKETS + HALF_BRACKETS)
            s2 = strip_sem_brackets(s)
            if had_bracket:
                # 剥掉括号后必须仍在原句里（剥括号不改变「裸片段」）
                if s2 and s2 in ja:
                    repaired.append({'i': i, 'from': s, 'to': s2})
                    s = s2
                else:
                    why = 'bracket-mixed'
            elif s not in ja:
                why = 'not-substring'
        if why:
            problems.append({'i': i, 's': (f.get('s') or '')[:60], 'why': why})
            continue
        b = (f.get('b') or '').replace(' ', '').replace('　', '')
        bmap = {'[]': '[]', '［］': '［］', '〈〉': '〈〉', '（）': '（）',
                '[': '[]', '［': '［］', '〈': '〈〉', '（': '（）'}
        b = bmap.get(b, b)
        if b not in ('[]', '［］', '〈〉', '（）'):
            problems.append({'i': i, 's': s[:60], 'why': 'bad-bracket-type'})
            continue
        clean.append({'s': s, 'b': b, 'role': (f.get('role') or '').strip()})
    return clean, problems, repaired


def check_sentence(ja, frame):
    """返回 (clean_frame, problems, repaired)"""
    return check_frame(ja, frame)


if __name__ == '__main__':
    import json, sys, glob, os
    # 🔴 反向验证：用**已落盘**的数据（零 API 调用）看这层能不能抓到 peer 报的那 3 处
    total = 0
    caught = []
    fixed = []
    for f in sorted(glob.glob('/workspace/shun-web/prod/*.json')):
        d = json.load(open(f))
        for si, s in enumerate(d.get('sentences', [])):
            fr = s.get('frame') or []
            if not fr:
                continue
            total += 1
            clean, probs, reps = check_frame(s.get('ja', ''), fr)
            for p in probs:
                caught.append((os.path.basename(f), si, p))
            for r in reps:
                fixed.append((os.path.basename(f), si, r))
    print("=== 离线跑校验层（零 API 调用）===")
    print("  检查帧数: %d" % total)
    print("  丢弃块: %d 处" % len(caught))
    for f, si, p in caught:
        print("    [丢] %s 第%d句 块#%d  why=%-15s s=%r" % (f, si, p['i'], p['why'], p['s']))
    print("  修复块（剥括号后仍合法 ⇒ 不丢）: %d 处" % len(fixed))
    for f, si, r in fixed:
        print("    [修] %s 第%d句 块#%d  %r → %r" % (f, si, r['i'], r['from'], r['to']))
    if not caught:
        print("  ⚠️ 一处都没丢弃 ⇒ 这层判据可能恒绿（不可信）")
        sys.exit(1)
    # 反向验证：故意造 ① 不逐字 ② 括号混入但剥后不合法 ③ 合法块 ⇒ 必须 1 过 2 拒
    ja = "本当にそうだなと思いました。"
    cf, pr, rp = check_frame(ja, [
        {'s': '本当にそうだなとっ', 'b': '（）', 'role': 'x'},      # ① 不逐字（多一个字）
        {'s': '［日本語を］教えるの', 'b': '［］', 'role': 'y'},   # ② 括号混入且剥后不在原句
        {'s': '本当にそうだな', 'b': '（）', 'role': 'z'},         # ③ 合法
    ])
    ok = len(cf) == 1 and len(pr) == 2
    print("\n=== 反向验证（造坏样本）===")
    print("  期望: 1 个块通过、2 个被拒 | 实际: %d 通过、%d 被拒 ⇒ %s"
          % (len(cf), len(pr), '✅' if ok else '❌ 判据不可信'))
    print("  被拒原因: %s" % [p['why'] for p in pr])
    print("  通过的块: %s" % [c['s'] for c in cf])
    # 再验「剥括号后仍合法 ⇒ 修而不丢」
    cf2, pr2, rp2 = check_frame("日本語を教えるのが大好きですから",
                                [{'s': '［日本語を教えるの］が大好きですから', 'b': '［］', 'role': 'x'}])
    ok2 = len(cf2) == 1 and len(pr2) == 0 and len(rp2) == 1
    print("  [修不丢] 期望 1 修 0 拒 | 实际 %d 修 %d 拒 ⇒ %s"
          % (len(rp2), len(pr2), '✅' if ok2 else '❌'))
    print("  修成: %s" % (rp2[0]['to'] if rp2 else None))
    sys.exit(0 if (ok and ok2) else 1)

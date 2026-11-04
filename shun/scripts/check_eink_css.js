#!/usr/bin/env node
/* check_eink_css.js —— 墨水屏描边闸门（2026-10-22 v3）
 *
 * 为什么需要这个闸门：
 *   电子墨水屏（黑白）上，**纯黑描边会糊成一排粗杠杠**。半透明边框
 *   （rgba(0,0,0,.35)）在墨水屏上才是「一条细线」。
 *   真实事故：2026-10-22 v1.5 带着 6 处纯黑盒子边框上线（Shun 实测抓到，
 *   不是自测抓到的）→ 光修 client 目录不够，**每个项目各有一份 css 副本**。
 *
 * 判定口径（与 Python 版 check_eink_css.py 逐条一致）：
 *   ✅ 盒子边框：border / border-top / border-right / border-left
 *      → 颜色解出来是纯黑就违规
 *   ✅ border-bottom → 豁免（结构线），但**只数实黑的**、且计入名额（默认 ≤2）
 *   ✅ 变量形式：var(--fg) 要**解析变量链**（--fg → var(--sh-fg) → 值）
 *   ✅ **跨文件变量表**：传入的所有 css 的变量**合并**（子项目 css 用的变量
 *      常定义在 shell/shell.css 里 → 逐文件解析会误报一片）
 *   ✅ **同名变量多定义**（多主题）→ 存成 list，**任一**定义解析出硬色即算硬
 *   ✅ fallback 多值：border: 1px solid #000, var(--x) → 取**第一个具体色**
 *   ✅ rgb(0,0,0) / black / #000 / #000000 / rgba(0,0,0,1) 都算纯黑
 *   ✅ **!important 要先剥掉**（否则 '#000 !important' 判不出纯黑 → 漏检）
 *   ✅ currentColor → FAIL（解析不了）
 *   ✅ 变量解析不出来 → FAIL（不是 WARN —— 放行就是假绿灯）
 *   ✅ 豁免机制：所在行（或上一行）写「eink-ok: 理由」→ 跳过
 *
 * 用法：
 *   node scripts/check_eink_css.js <css 文件> [...]        # 检查，违规 exit 1
 *   node scripts/check_eink_css.js --fix <css 文件> [...]   # 自动改 + 回读复检
 *
 * 退出码：0 = 通过；1 = 发现不合规（打包脚本应 die() 拒绝发布）
 */
'use strict';
const fs = require('fs');
const path = require('path');

/* 盒子边框 = border / border-top / border-right / border-left
 * 🔴 border-bottom 不在这个正则里（结构线，单独统计）
 * 🔴 宽度必须能匹配**小数**：`border:1.5px solid var(--line)` 是 hub 总目录页真实在用的
 *    → 原来 `(\d+)px` 匹配不到（`5px` 前面是 `.` 不在 `[;{\s]` 里）→ **静默漏检**
 * 🔴 「收紧」时的坑：若只留 bottom 当豁免，top/right/left 的纯黑会
 *    既不豁免、也完全不检查 → 从「放水」变成「失明」。所以它们按盒子边框查。 */
const SOLID_BOX_RE = /(^|[;{\s])(border(?:-(?:top|right|left))?)\s*:\s*(\d+(?:\.\d+)?)px\s+solid\s+([^;]+?)\s*;/g;
/* 结构线 = border-bottom */
const STRUCTURE_RE = /(^|[;{\s])border-bottom\s*:\s*(\d+(?:\.\d+)?)px\s+solid\s+([^;]+?)\s*;/g;
/* 变量定义：--fg: #000; */
const VAR_DEF_RE = /(--[a-z0-9-]+)\s*:\s*([^;{}]+);/g;
/* 豁免标记 */
const EINK_OK_RE = /eink-ok\s*:/i;
/* !important 要剥掉 */
const IMPORTANT_RE = /!\s*important\b/gi;

const MAX_STRUCTURE = 2;   /* .ai-h 五段标题 + .ai-thead 表头 —— 结构线就该只有这两条 */

/* ------------------------------------------------------------------ */
/* 颜色判定                                                              */
/* ------------------------------------------------------------------ */
function stripImportant(s) {
  return String(s == null ? '' : s).replace(IMPORTANT_RE, '').trim();
}

/* 🔴🔴🔴 墨水屏是**二值**的 —— 「近黑」在屏上就是纯黑
 * 真实事故：hub 总目录页 `:root{ --line:#111 }` + `button{border:1.5px solid var(--line)}`
 *   → #111 亮度 7%，黑白墨水屏渲染出来是**纯黑** → 一排粗杠杠
 *   → 而老口径只认字面量 #000 → **静默放行**（假绿灯）
 * ⇒ 判据从「纯黑字面值」改成「**屏上会变黑的亮度**」。
 * 亮度口径 = (R+G+B)/3/255（好解释：平均亮度低于阈值在墨水屏上就是黑）
 * 阈值默认 0.25（约 #3f3f3f 以内都算）→ --max-dark 可配。
 */
let MAX_DARK = 0.08;   /* 纯黑级：亮度 ≤ 此值 → 墨水屏上就是纯黑（#141414 以内） */
let MAX_MID = 0.15;    /* 深色级：亮度 ≤ 此值 且 宽度 ≥ MIN_THICK → 粗深线也糊
 *  🔴🔴 v4.4 从 0.25 收到 0.15 —— 原来的 0.25 **卡在主题线色中间**：
 *    主题线色 #2e4a32(22%) / #3d3358(26%) 差 4% 就翻转判定，而它们视觉上几乎没差
 *  ⇒ 阈值必须定在「**明显暗于主题线色**」的地方（0.08~0.15），而不是「稍微暗」
 *  ⇒ 22%/26% 的主题线色一律放行；11% 这种「接近黑」的才算深色级 */
const MIN_THICK = 2;    /* 深色级要求的最小宽度（px）：1px 深灰细线不是「粗杠杠」 */

function parseRgb(color) {
  const c = stripImportant(color).toLowerCase().trim();
  if (c === 'black') return { r: 0, g: 0, b: 0, a: 1 };
  if (c === 'white') return { r: 255, g: 255, b: 255, a: 1 };
  let m = /^#([0-9a-f]{3})$/.exec(c);
  if (m) {
    const h = m[1];
    return {
      r: parseInt(h[0] + h[0], 16),
      g: parseInt(h[1] + h[1], 16),
      b: parseInt(h[2] + h[2], 16), a: 1
    };
  }
  m = /^#([0-9a-f]{6})$/.exec(c);
  if (m) {
    const h = m[1];
    return {
      r: parseInt(h.slice(0, 2), 16),
      g: parseInt(h.slice(2, 4), 16),
      b: parseInt(h.slice(4, 6), 16), a: 1
    };
  }
  m = /^rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*(?:,\s*([\d.]+)\s*)?\)/.exec(c);
  if (m) {
    return { r: +m[1], g: +m[2], b: +m[3], a: m[4] === undefined ? 1 : parseFloat(m[4]) };
  }
  return null;
}

function brightness(rgb) {
  return (rgb.r + rgb.g + rgb.b) / 3 / 255;
}

function isPureBlack(color) {
  const c = stripImportant(color).toLowerCase().trim();
  if (c === 'black') return true;
  if (c === '#000' || c === '#000000') return true;
  if (/^rgb\(\s*0\s*,\s*0\s*,\s*0\s*\)$/.test(c)) return true;
  if (/^rgba\(\s*0\s*,\s*0\s*,\s*0\s*,\s*1(\.0+)?\s*\)$/.test(c)) return true;
  return false;
}

/* 屏上会变黑？（二维判据：亮度 × 宽度）
 *
 * 🔴🔴 **只按亮度判会误报一堆**：实测 hub v1.11 的 8 个 css，
 *    165 条里 **164 条**是 `#2e4a32`（22% 亮度的深绿灰分隔线，1px 宽）
 *    → 那是 sakura 主题的分隔线色，1px 深灰线在墨水屏上是**细线**，不是「粗杠杠」。
 *    真正该抓的只有 1 条 `#0b0b0b`（4.3%）。
 * 🔴 而且「墨水屏是二值的」这个前提**不严谨** —— 实际是 16 级灰度，
 *    深灰会显示成深灰，不是纯黑。真正扎眼的是「**纯黑级 + 粗**」。
 *
 * ⇒ 判据改成二维：
 *    ① 纯黑级（亮度 ≤ MAX_DARK，默认 8%，约 #141414 以内）→ 任何宽度都违规
 *    ② 深色级（≤ MAX_MID，默认 25%）且 宽度 ≥ MIN_THICK（2px）→ 违规
 *    ③ 1px 的深色线 → 放行（是细线，不是粗杠杠）
 *
 * 🔴🔴 **半透明不是硬色** —— rgba(0,0,0,.35) 在墨水屏上是**淡线**，不是黑
 *    （我加近黑判据时漏了 alpha → 把所有半透明边框误判成违规，
 *     **E1/E2 真实 css 组当场抓到的回归**）。半透明本来就该放行 —— v3 的规则。
 */
function isEinkBlack(color, widthPx, isAllSides) {
  if (isPureBlack(color)) return true;
  const rgb = parseRgb(color);
  if (!rgb) return false;
  if (rgb.a < 1) return false;          // 半透明 → 放行
  const b = brightness(rgb);
  if (b <= MAX_DARK) return true;       // 纯黑级：任何宽度
  // 深色级：只对**四边框**且够粗才算（单边 2px 只是一条分隔线）
  if (isAllSides !== false && b <= MAX_MID && widthPx >= MIN_THICK) return true;
  return false;
}

/* 文案：区分「纯黑级」和「深色粗线」—— 用户要知道自己改的是哪种 */
function blackWhy(color, widthPx, isAllSides) {
  const c = stripImportant(color);
  if (isPureBlack(color)) {
    return `纯黑盒子边框（解出来是 ${c}）—— 墨水屏上会糊成粗杠杠`;
  }
  const rgb = parseRgb(color);
  const pct = Math.round(brightness(rgb) * 100);
  if (brightness(rgb) <= MAX_DARK) {
    return `纯黑级盒子边框（解出来是 ${c}，平均亮度 ${pct}% ≤ ${Math.round(MAX_DARK * 100)}%）`
      + `—— 墨水屏上等同纯黑，会糊成粗杠杠`;
  }
  return `深色粗盒子边框（解出来是 ${c}，平均亮度 ${pct}%，四边宽度 ${widthPx}px ≥ ${MIN_THICK}px）`
    + `—— 墨水屏上接近纯黑，粗框会糊成杠杠`;
}

/* ------------------------------------------------------------------ */
/* 变量表：跨文件合并 + 同名多定义存 list                                */
/* ------------------------------------------------------------------ */
function collectVars(src, into) {
  const vars = into || {};
  VAR_DEF_RE.lastIndex = 0;
  let m;
  while ((m = VAR_DEF_RE.exec(src)) !== null) {
    const key = m[1].toLowerCase();
    const val = stripImportant(m[2]);
    if (!vars[key]) vars[key] = [];
    /* 同名多定义（多主题）→ 都留着；解析时任一解析出硬色即算硬 */
    if (vars[key].indexOf(val) === -1) vars[key].push(val);
  }
  return vars;
}

/* 解析变量链 → 返回**所有**终端值（一个变量可能有多条定义 / 多主题）
 *   var(--line) → var(--sh-line) → ['#edd9ea', '#3d3358', ...] */
function resolveVarAll(name, vars, depth) {
  depth = depth || 0;
  if (depth > 5) return [];
  const key = String(name).toLowerCase();
  if (!Object.prototype.hasOwnProperty.call(vars, key)) return [];
  const out = [];
  for (const raw of vars[key]) {
    const m = /^var\(\s*(--[a-z0-9-]+)\s*\)$/i.exec(raw);
    if (m) {
      for (const v of resolveVarAll(m[1], vars, depth + 1)) out.push(v);
    } else {
      out.push(raw);
    }
  }
  return out;
}

/* 按**顶层逗号**切
 * 🔴 **不能按逗号硬切** —— 颜色自身就可能带逗号（rgb(0,0,0)），
 *    硬切会把 rgb(0,0,0) 切成 'rgb(0' → 纯黑判定失效（实测漏检）。
 *    同理 var(--x, #eee) 里的逗号也在括号内，不能切。 */
function splitTopLevel(value) {
  const s = String(value);
  const parts = [];
  let depth = 0, cur = '';
  for (let i = 0; i < s.length; i++) {
    const ch = s[i];
    if (ch === '(') depth++;
    else if (ch === ')') depth--;
    if (ch === ',' && depth === 0) { parts.push(cur); cur = ''; continue; }
    cur += ch;
  }
  parts.push(cur);
  return parts.map(x => stripImportant(x).trim()).filter(Boolean);
}

/* fallback 多值：border: 1px solid #000, var(--x) → 取第一个「具体色」（跳过 var） */
function splitFallback(value) {
  const list = splitTopLevel(value);
  for (const p of list) {
    if (!/^var\(/i.test(p)) return p;
  }
  return list.length ? list[0] : '';
}

/* 解析 var() 调用 → { name, fallback }
 *   var(--x)            → { name:'--x', fallback:null }
 *   var(--x, #eee)      → { name:'--x', fallback:'#eee' }
 *   var(--x, var(--y))  → { name:'--x', fallback:'var(--y)' } */
function parseVarCall(s) {
  const t = stripImportant(s);
  if (!/^var\(/i.test(t)) return null;
  let depth = 0, end = -1;
  for (let i = 3; i < t.length; i++) {
    if (t[i] === '(') depth++;
    else if (t[i] === ')') { depth--; if (depth === 0) { end = i; break; } }
  }
  if (end === -1) return null;
  const parts = splitTopLevel(t.slice(4, end));
  if (!parts.length) return null;
  return { name: parts[0].trim(), fallback: parts.length > 1 ? parts.slice(1).join(', ') : null };
}

/* 某一行（或上一行）有没有 eink-ok 豁免标记 */
function hasEinkOk(src, index) {
  const lineOf = src.slice(0, index).split('\n').length;   /* 1-based */
  const lines = src.split('\n');
  const cur = lines[lineOf - 1] || '';
  const prev = lines[lineOf - 2] || '';
  return EINK_OK_RE.test(cur) || EINK_OK_RE.test(prev);
}

/* ------------------------------------------------------------------ */
/* 判定：把一个颜色表达式解析成「候选色列表」+ 错误信息
 * 🔴 **var 带 fallback 时要用 fallback**：
 *    `border: 1px solid var(--nope, #eeeeee)` —— --nope 没定义时，
 *    浏览器渲染的就是 #eeeeee → 合法。不该报「解析不出来」。
 *    （这是对拍样本自己抓出来的 bug：两份实现一开始都误报了这条） */
function resolveColorValue(rawValue, vars, depth) {
  depth = depth || 0;
  if (depth > 5) return { colors: [], err: '变量链太深（>5 层），解析不了' };
  const v = stripImportant(rawValue);

  const pc = parseVarCall(v);
  if (!pc) {
    if (/^currentcolor$/i.test(v)) {
      return { colors: [], err: 'currentColor 跟着 color 走，解析不了 —— 请改成具体色，或加「eink-ok: 理由」' };
    }
    return { colors: [v], err: null };
  }

  const resolved = resolveVarAll(pc.name, vars, 0);
  if (resolved.length) {
    const out = [];
    for (const r of resolved) {
      const sub = resolveColorValue(r, vars, depth + 1);
      if (sub.err) return sub;
      for (const c of sub.colors) out.push(c);
    }
    return { colors: out, err: null };
  }

  /* var 本身没定义 → 用 fallback */
  if (pc.fallback) {
    const fparts = splitTopLevel(pc.fallback);
    const concrete = fparts.filter(p => !/^var\(/i.test(p))[0];
    return resolveColorValue(concrete || fparts[0], vars, depth + 1);
  }

  return {
    colors: [],
    err: `${pc.name} 在这些 css 里解析不出来 —— 可能是纯黑但没法确认；` +
         `要么补上定义，要么在该行加「eink-ok: 理由」显式豁免`
  };
}

function judgeColors(rawValue, vars) {
  const first = splitFallback(rawValue);
  return resolveColorValue(first, vars, 0);
}

/* ------------------------------------------------------------------ */
/* 🔴 HTML 内联 <style> 也要扫                                          */
/*   真实事故：hub 总目录页把主题变量（:root{--line:#111}）**内联在      */
/*   <style> 块**里 → 只扫 .css 文件 = **完全扫不到** → 假绿灯            */
/*   行号保持：<style> 之前的内容用等行数的空行占位                        */
/*   → 块内第 k 行的行号 = 原文件里那一行的行号                            */
/* ------------------------------------------------------------------ */
function extractStyleBlocks(src) {
  const re = /<style\b[^>]*>([\s\S]*?)<\/style>/gi;
  let m, out = '', last = 0, count = 0;
  while ((m = re.exec(src)) !== null) {
    const before = src.slice(last, m.index);
    out += '\n'.repeat(before.split('\n').length - 1);
    out += m[1];
    last = m.index + m[0].length;
    count++;
  }
  const tail = src.slice(last);
  out += '\n'.repeat(tail.split('\n').length - 1);
  return { css: out, count };
}

/* 🔴🔴 剥掉 CSS 注释（**保留行号**：注释里的换行留着，其余换成空格）
 *
 * 为什么必须剥：
 *   ① **注释里的变量定义会污染跨文件合并的变量表**
 *      —— 实测：hub 的 sakura/style.css 注释里写了「它的 `--line:#000000`
 *      还会污染跨文件合并的变量表」→ 闸门把这条**注释里的** `--line:#000000`
 *      当成真定义 → dora/pony/tadoku 里合法的 var(--line) 全被带成误报
 *      （一次扫描 81 条，其中绝大多数是假的）
 *   ② 注释里写的 `border: 2px solid #000` 不该算违规
 *   ③ 注释里写的 `eink-ok:` 不该真的豁免代码行
 *   ⇒ 判据统一走「剥掉注释后的文本」，行号不变（换行保留）。
 */
function stripComments(src) {
  return src.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '));
}

/* ══════════════════════════════════════════════════════════════════
 * 🔴🔴 变量表必须**按页面分组** —— 同名变量在不同页面里是**无关**的
 *
 * CSS 语义：变量在**同一文档**内解析。
 * hub 的结构：总目录 index.html（自己内联 --line:#111）+ 6 个项目子页
 *   → 总目录的 `--line:#111` **不该**污染 sakura 的 `var(--line)`
 *   → 全局合并变量表 ⇒ 一次扫描 93 条，其中 **80 条是假的**
 *      （真问题其实只有十来条，不改就是让维护者白改一堆）
 *
 * 分组规则（两个实现必须**完全一致**）：
 *   ① 每个 html = 一个页面组 = {该 html} ∪ {它引用的 css（按引用顺序）}
 *   ② 没被任何 html 引用的 css → 自己单独一组
 *   ③ 传入列表里**没有 html** → 退回旧行为（全局合并），单文件扫描口径不变
 */
const CSS_REF_RE = /(?:href|src)\s*=\s*["']([^"']+\.css[^"']*)["']/gi;
const CSS_IMPORT_RE = /@import\s+(?:url\()?["']([^"']+\.css)["']/gi;

function parseCssRefs(text, htmlPath) {
  const out = [];
  const dir = path.dirname(path.resolve(htmlPath));
  const push = (raw) => {
    const rel = raw.split('?')[0].split('#')[0].trim();
    if (!rel) return;
    if (/^(https?:)?\/\//i.test(rel)) return;
    const abs = path.resolve(dir, rel);
    if (out.indexOf(abs) < 0) out.push(abs);
  };
  let m;
  CSS_REF_RE.lastIndex = 0;
  while ((m = CSS_REF_RE.exec(text)) !== null) push(m[1]);
  CSS_IMPORT_RE.lastIndex = 0;
  while ((m = CSS_IMPORT_RE.exec(text)) !== null) push(m[1]);
  return out;
}

function buildGroups(files) {
  const abs = files.map(f => path.resolve(f));
  const set = new Set(abs);
  const htmls = files.filter(f => /\.html?$/i.test(f));
  const cssOnly = files.filter(f => !/\.html?$/i.test(f));
  const groups = [];
  if (!htmls.length) return null;   /* 没有 html → 退回全局合并（旧行为） */
  for (const h of htmls) {
    const g = [path.resolve(h)];
    /* 🔴 必须读**原始 html** —— readForScan 对 html 只返回 <style> 块，
     *    `<link href="...css">` 全丢了 → 一个引用都找不到 → 分组全废 */
    let txt = '';
    try { txt = fs.readFileSync(h, 'utf-8'); } catch (e) { /* 读不了 → 该文件单独报 FAIL */ }
    for (const ref of parseCssRefs(txt, h)) {
      if (set.has(ref) && g.indexOf(ref) < 0) g.push(ref);
    }
    groups.push(g);
  }
  for (const c of cssOnly) {
    const a = path.resolve(c);
    let inGroup = false;
    for (const g of groups) { if (g.indexOf(a) >= 0) { inGroup = true; break; } }
    if (!inGroup) groups.push([a]);
  }
  return groups;
}

/* 某文件在所有页面组里的变量表并集（一个文件可能被多个页面引用） */
function varsForFile(file, groups) {
  if (!groups) return null;   /* 旧行为：全局表 */
  const out = {};
  let hit = false;
  for (const g of groups) {
    if (g.indexOf(path.resolve(file)) < 0) continue;
    hit = true;
    for (const f of g) {
      if (!fs.existsSync(f)) continue;
      try { collectVars(readForScan(f).text, out); } catch (e) { /* 读不了 → 单独报 FAIL */ }
    }
  }
  return hit ? out : null;
}

/* 读一个文件用于扫描：html → 只取 <style> 块（行号保持）
 * 🔴🔴 **判断和提取必须用同一个逻辑** ——
 *    原来这里用 `/<style\b/i.test(raw)` 判断、那边用「有闭合标签」提取
 *    → 一个**只在注释里提到 `<style` 的 .css 文件**会被误判成 HTML
 *    → 整份 css 被抽成 49 字节空壳 → **一条违规都扫不到**（假绿灯）
 *    （我自己写测试样本时踩到的：注释里写了「HTML 内联 <style>」）
 *    ⇒ 改成：先 extract，再看抽到几个块。
 */
function readForScan(path) {
  const raw = fs.readFileSync(path, 'utf-8');
  const ex = extractStyleBlocks(raw);
  /* 🔴 统一在**读取时**剥注释 → 下游（扫描 / 修 / 变量表）拿到的都是干净文本
   *    （放在读取层 → --fix 路径与扫描路径口径必然一致） */
  if (!ex.count) {
    return { text: stripComments(raw), textRaw: raw, blocks: 0, isHtml: false };
  }
  /* 🔴 textRaw = **带注释**的原文（stripComments 是等长替换 → 偏移/行号不变）
   *    → 豁免标记 hasEinkOk 用 textRaw 判定：
   *      「在上一行注释里写 eink-ok: 理由」是**正常用法**（ai-client.css 就这么写）
   *      剥注释只该影响「变量定义收集」和「边框正则匹配」，不该让豁免失效 */
  return { text: stripComments(ex.css), textRaw: ex.css, blocks: ex.count, isHtml: true };
}

/* ------------------------------------------------------------------ */
/* 检查一个文件（vars 是**跨文件合并**后的变量表）                        */
/* ------------------------------------------------------------------ */
function checkFile(file, sharedVars, maxStructure) {
  const maxStruct = (typeof maxStructure === 'number') ? maxStructure : MAX_STRUCTURE;
  const scan = readForScan(file);
  const src = scan.text;        /* 注释已剥（保留行号/偏移） */
  const srcRaw = scan.textRaw;  /* 带注释原文 → 判 eink-ok 豁免用 */
  const problems = [];
  const warnings = [];
  const vars = sharedVars || collectVars(src);

  SOLID_BOX_RE.lastIndex = 0;
  let m;
  while ((m = SOLID_BOX_RE.exec(src)) !== null) {
    if (hasEinkOk(srcRaw, m.index)) continue;
    const j = judgeColors(m[4], vars);
    const line = src.slice(0, m.index).split('\n').length;
    if (j.err) {
      problems.push({ line, text: m[0].trim(), why: j.err });
      continue;
    }
    for (const c of j.colors) {
      const wpx = parseFloat(m[3]);
      /* 🔴 「粗」的判定**只对四边框**生效**：
       *    四边 2px = 一个粗框（墨水屏上真的糟）
       *    单边 2px = 一条分隔线/竖条（深灰细条，可接受）
       *    但单边的**纯黑级**仍然算（纯黑竖条视觉很重） */
      const isAllSides = (m[2] === 'border');
      if (isEinkBlack(c, wpx, isAllSides)) {
        problems.push({
          line, text: m[0].trim(),
          why: blackWhy(c, wpx, isAllSides)
        });
        break;
      }
    }
  }

  /* 结构线：只数**实黑**的（半透明的 border-bottom 不该占名额） */
  let structCount = 0;
  STRUCTURE_RE.lastIndex = 0;
  while ((m = STRUCTURE_RE.exec(src)) !== null) {
    if (hasEinkOk(srcRaw, m.index)) continue;
    /* ⚠️ STRUCTURE_RE 的捕获组是 (前缀, 宽度, 颜色) —— **没有**属性名组
     *    → 颜色是 m[3]，不是 m[4]
     *    🔴 我给 SOLID_BOX_RE 加捕获组时用 Python 做了**全局替换**，
     *       把这里也一起改成了 m[4] → undefined → 颜色判空 → 结构线永远数不到
     *    ⇒ 跨实现对拍当场抓到（单份实现自己不会发现） */
    const j = judgeColors(m[3], vars);
    if (j.err) continue;
    for (const c of j.colors) {
      /* ⚠️ STRUCTURE_RE 的捕获组是 (前缀, 宽度, 颜色) —— **没有**加属性名组
       *    所以这里是 m[2]=宽度、m[3]=颜色（别跟 SOLID_BOX_RE 搞混）
       *    🔴 我加捕获组时把这里错改成 m[3] → 宽度取成了颜色 → 跨实现对拍当场抓到 */
      if (isEinkBlack(c, parseFloat(m[2]))) { structCount++; break; }
    }
  }
  if (structCount > maxStruct) {
    problems.push({
      line: 0,
      text: `border-bottom 实黑出现 ${structCount} 条`,
      why: `结构线最多 ${maxStruct} 条（.ai-h 五段标题 + .ai-thead 表头）`
    });
  }

  return { file, problems, warnings, structCount, styleBlocks: scan.blocks };
}

/* ------------------------------------------------------------------ */
/* --fix：把纯黑盒子边框自动改成 rgba(0, 0, 0, .35)                      */
/*   ① **只改真正解析为纯黑的那一条** —— 合法变量（--ink: #1a1a1a）不动 */
/*   ② **保留原属性名** —— border-right 不能被改成 border（样式会变） */
/*   ③ **刻意不改变量定义** —— 同一变量常也用于 color/background，        */
/*      改了会让文字变半透明、几乎看不见                                  */
/*   ④ **保留 !important** —— 改颜色不改变优先级                          */
/* ------------------------------------------------------------------ */
function fixFile(file, sharedVars, maxStructure) {
  const src = fs.readFileSync(file, 'utf-8');
  const vars = sharedVars || collectVars(src);
  let n = 0;

  const out = src.replace(SOLID_BOX_RE, function (m0, pre, px, value, offset) {
    /* 🔴 用回调的 **offset** 参数，不是 m0.index（replace 回调里 match 是字符串，没有 .index；
     *    m0.index 是 undefined → slice(0, undefined) 返回整串 → 算成「最后一行」
     *    → 撞上那行的 eink-ok → 所有行都被跳过 → --fix 改 0 处） */
    if (hasEinkOk(src, offset)) return m0;
    const j = judgeColors(value, vars);
    if (j.err) return m0;
    let hit = false;
    for (const c of j.colors) { if (isEinkBlack(c, parseFloat(px), true)) { hit = true; break; } }
    if (!hit) return m0;
    n++;
    /* 保留原属性名：border / border-top / border-right / border-left */
    const prop = m0.replace(/^[^b]*/, '').split(':')[0].trim();
    /* 保留 !important（如果有） */
    const imp = IMPORTANT_RE.test(value) ? ' !important' : '';
    return pre + prop + ': ' + px + 'px solid rgba(0, 0, 0, .35)' + imp + ';';
  });

  if (!n) return { file, fixed: 0, ok: true, remain: checkFile(file, sharedVars, maxStructure).problems.length };

  const before = checkFile(file, sharedVars, maxStructure).problems.length;
  fs.writeFileSync(file, out, 'utf-8');
  const after = checkFile(file, sharedVars, maxStructure).problems.length;

  /* 🔴 回滚条件是「**修复让情况变差**」，不是「还有问题」。
   *   剩下那几条（var 解析不出 / currentColor）**本来就修不了**
   *   → 「只要还剩问题就整份回滚」会把**已经修好的也一起撤销** → --fix 永远 0 处。 */
  if (after > before) {
    fs.writeFileSync(file, src, 'utf-8');
    return { file, fixed: 0, ok: false, remain: before };
  }
  return { file, fixed: n, ok: true, remain: after };
}

/* ------------------------------------------------------------------ */
/* 主流程                                                                */
/* ------------------------------------------------------------------ */
const FIX = process.argv.includes('--fix');
const JSON_OUT = process.argv.includes('--json');

/* --max-structure N：border-bottom 实黑的豁免名额（默认 2）
 * 🔴 Python 版支持这个参数、JS 版原来硬编码 → 两份能力不对等，这里补齐 */
function argNum(name, dflt) {
  const i = process.argv.indexOf(name);
  if (i < 0) return dflt;
  const v = parseInt(process.argv[i + 1], 10);
  return Number.isFinite(v) ? v : dflt;
}
const MAX_STRUCT = argNum('--max-structure', MAX_STRUCTURE);

/* --max-dark 0.08：纯黑级亮度阈值（#141414 以内 → 墨水屏上等同纯黑）
 * --max-mid 0.25：深色级亮度阈值（且宽度 ≥2px 才算「粗杠杠」）
 * 0 = 只认字面量纯黑（v3 老口径）。 */
const MAX_DARK_ARG = process.argv.indexOf('--max-dark');
if (MAX_DARK_ARG >= 0) {
  const v = parseFloat(process.argv[MAX_DARK_ARG + 1]);
  if (Number.isFinite(v)) MAX_DARK = Math.max(0, Math.min(1, v));
}
const MAX_MID_ARG = process.argv.indexOf('--max-mid');
if (MAX_MID_ARG >= 0) {
  const v = parseFloat(process.argv[MAX_MID_ARG + 1]);
  if (Number.isFinite(v)) MAX_MID = Math.max(0, Math.min(1, v));
}

const args = process.argv.slice(2).filter(a => !a.startsWith('-'));
const files = args.length ? args : [];

if (!files.length) {
  console.log('[eink-css] 用法：node check_eink_css.js [--fix] [--json] [--max-structure N] <css 路径> [...]');
  console.log('[eink-css] 提示：把 css 和 html 一起传（变量表按页面分组）；');
  console.log('[eink-css]       只传 css → 退回「跨文件合并」→ 多页面站会互相污染');
  process.exit(0);
}

/* 🔴🔴 变量表按**页面**分组（同名变量在不同页面里无关）—— 见 buildGroups 注释
 *   没有 html 传入 → 退回旧的「全局合并」行为（单文件扫描口径不变） */
const GROUPS = buildGroups(files);
if (!GROUPS && files.length > 1) {
  emit('提示：没传 html → 变量表按「跨文件合并」算 → 多页面站里同名变量会互相污染。');
  emit('      建议把 html 一起传（每个 html = 一个页面组）。');
}
function groupVars(group) {
  const out = {};
  for (const f of group) {
    if (!fs.existsSync(f)) continue;
    try { collectVars(readForScan(f).text, out); } catch (e) { /* 读不了 → 单独报 FAIL */ }
  }
  return out;
}
/* 一个文件可能被多个页面引用（例：shell/shell.css）→ 各页面各判一次，结果合并 */
function scanAll() {
  const per = new Map();
  const put = (f, r) => {
    /* 🔴🔴 key **必须**用绝对路径！
     *   否则：主循环用 path.resolve(f) 查、这里用相对路径存 → 对不上
     *   → 单文件（或任何没传 html 的多文件）扫描**全部返回 0 条 = 假绿灯**
     *   → 而且只在**相对路径**调用时触发 → 自检全用绝对路径 → 永远抓不到
     *   （v4.3 引入 PER_FILE 聚合时埋的，v4.4 才发现） */
    const key = path.resolve(f);
    let cur = per.get(key);
    if (!cur) { cur = { problems: [], warnings: [], structCount: 0, styleBlocks: 0 }; per.set(key, cur); }
    const seen = new Set(cur.problems.map(p => `${p.line}|${p.text}|${p.why}`));
    for (const p of (r.problems || [])) {
      const k = `${p.line}|${p.text}|${p.why}`;
      if (!seen.has(k)) { seen.add(k); cur.problems.push(p); }
    }
    const seenW = new Set(cur.warnings.map(w => `${w.line}|${w.text}|${w.why}`));
    for (const w of (r.warnings || [])) {
      const k = `${w.line}|${w.text}|${w.why}`;
      if (!seenW.has(k)) { seenW.add(k); cur.warnings.push(w); }
    }
    cur.structCount = Math.max(cur.structCount, r.structCount || 0);
    cur.styleBlocks = cur.styleBlocks || r.styleBlocks || 0;
  };
  if (GROUPS) {
    for (const g of GROUPS) {
      const gv = groupVars(g);
      for (const f of g) {
        try { put(f, checkFile(f, gv, MAX_STRUCT)); } catch (e) { put(f, { error: String(e) }); }
      }
    }
  } else {
    const gv = groupVars(files.map(f => path.resolve(f)));
    for (const f of files) {
      try { put(f, checkFile(f, gv, MAX_STRUCT)); } catch (e) { put(f, { error: String(e) }); }
    }
  }
  return per;
}

/* ------------------------------------------------------------------ */
/* 统一输出层（v4）—— 与 Python 版逐字一致                              */
/*   🔴 「统一」不是肉眼看着像，是**同一批样本跑两份实现、输出逐字比对** */
/*      （test_eink_gate.py 的对拍组就在干这件事）                        */
/* ------------------------------------------------------------------ */
const jsonReport = { ok: true, max_structure: MAX_STRUCT, files: [] };
function emit(text) { if (!JSON_OUT) console.log('[eink-css] ' + text); }

const FIX_HELP = [
  '═════════════════════════════════════════════',
  '墨水屏描边不合规 —— 拒绝发布。',
  '修法（任选其一）：',
  '  ① 盒子边框改成 rgba(0, 0, 0, .35)',
  '     border: 2px solid #000;  →  border: 2px solid rgba(0, 0, 0, .35);',
  '  ② 直接换用已修好的 client（v1.6+）：',
  '     https://dcjk8zanl3e4r.space.mcode.cn/ai-client.js',
  '  ③ border-bottom 的实黑**不用改** —— 那是结构线（.ai-h / .ai-thead）',
  '  ④ 确实该留的 → 在该行加「eink-ok: 理由」显式豁免',
  '     （例：当前句位置指示竖条 border-left: 6px solid #000 —— 设计要求）',
  '═════════════════════════════════════════════'
];

if (FIX) {
  let total = 0, failed = 0;
  for (const f of files) {
    if (!fs.existsSync(f)) {
      /* 🔴 路径写错不能当「没东西可检」→ 那是假绿灯 */
      failed++;
      emit(`FAIL ${f}`);
      emit('  → 文件不存在（路径写错了？）。拒绝发布 —— 不能因为找不到文件就放行。');
      jsonReport.files.push({ file: f, ok: false, error: 'missing_file' });
      continue;
    }
    let src;
    try {
      src = fs.readFileSync(f, 'utf-8');
    } catch (e) {
      failed++;
      emit(`FAIL ${f}`);
      emit(`  → 读不了：${String(e)}。拒绝发布 —— 不能因为读不了就放行。`);
      jsonReport.files.push({ file: f, ok: false, error: 'unreadable' });
      continue;
    }
    if (/\.html?$/i.test(f)) {
      /* 🔴 --fix 不改 HTML —— 内联 <style> 改写风险大（会动到整份文档结构） */
      failed++;
      emit(`FAIL ${f}`);
      emit('  → --fix 不改 HTML（内联 <style> 改写风险大）—— 请手工改这处。');
      jsonReport.files.push({ file: f, ok: false, error: 'html_no_autofix' });
      continue;
    }
    let r;
    try {
      r = fixFile(f, (GROUPS ? varsForFile(f, GROUPS) : null) || groupVars(files.map(x => path.resolve(x))), MAX_STRUCT);
    } catch (e) {
      failed++;
      emit(`FAIL ${f}`);
      emit(`  → 读不了：${String(e)}。拒绝发布 —— 不能因为读不了就放行。`);
      jsonReport.files.push({ file: f, ok: false, error: 'unreadable' });
      continue;
    }
    if (!r.ok) {
      failed++;
      emit(`FAIL ${f}`);
      emit('  → 修复让情况变差，已回滚。');
      jsonReport.files.push({ file: f, ok: false, error: 'rollback' });
      continue;
    }
    total += r.fixed;
    emit(`${r.fixed ? 'FIX ' : '—   '} ${f}（改了 ${r.fixed} 处）`);
    if (r.remain) {
      failed++;
      emit(`⚠️  ${f} 还剩 ${r.remain} 处修不了的（var 解析不出 / currentColor / 需人工）—— 发布仍应被拦`);
    }
    jsonReport.files.push({ file: f, ok: r.remain === 0, fixed: r.fixed, remain: r.remain });
  }
  emit(`共改 ${total} 处。`);
  jsonReport.ok = (failed === 0);
  if (JSON_OUT) console.log(JSON.stringify(jsonReport, null, 2));
  process.exit(failed ? 1 : 0);
}

let bad = 0;
const PER_FILE = scanAll();
for (const f of files) {
  if (!fs.existsSync(f)) {
    bad++;
    emit(`FAIL ${f}`);
    emit('  → 文件不存在（路径写错了？）。拒绝发布 —— 不能因为找不到文件就放行。');
    jsonReport.files.push({ file: f, ok: false, error: 'missing_file' });
    continue;
  }
  const r = PER_FILE.get(path.resolve(f)) || { problems: [], warnings: [], structCount: 0, styleBlocks: 0 };
  if (r.error) {
    bad++;
    emit(`FAIL ${f}`);
    emit(`  → 读不了：${r.error}。拒绝发布 —— 不能因为读不了就放行。`);
    jsonReport.files.push({ file: f, ok: false, error: 'unreadable' });
    continue;
  }
  for (const w of (r.warnings || [])) {
    emit(`WARN ${f}`);
    emit(`  第 ${w.line} 行：${w.text}`);
    emit(`    → ${w.why}`);
  }
  const note = r.styleBlocks ? `，扫了 ${r.styleBlocks} 个 <style> 块` : '';
  if (!r.problems.length) {
    emit(`OK   ${f}（实黑结构线 ${r.structCount} 条，豁免名额 ${MAX_STRUCT}${note}）`);
    jsonReport.files.push({ file: f, ok: true, struct_count: r.structCount, problems: [] });
  } else {
    bad++;
    emit(`FAIL ${f}`);
    for (const p of r.problems) {
      if (p.line) emit(`  第 ${p.line} 行：${p.text}`);
      else emit(`  ${p.text}`);
      emit(`    → ${p.why}`);
    }
    jsonReport.files.push({
      file: f, ok: false, struct_count: r.structCount,
      problems: r.problems.map(p => ({ line: p.line, text: p.text, why: p.why }))
    });
  }
}

if (bad) {
  emit('');
  for (const h of FIX_HELP) emit(h);
} else {
  emit('全部通过 ✅');
}
jsonReport.ok = (bad === 0);
if (JSON_OUT) console.log(JSON.stringify(jsonReport, null, 2));
process.exit(bad ? 1 : 0);

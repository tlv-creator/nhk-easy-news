#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Shun 播客 13 集 / 1154 句 · 语法框架分析引擎 v6
=================================================
输入: tokens.json (kuromoji + IPADIC; 逐 token 拼接 == 原句, 已断言)
输出: analysis.json

数据结构
--------
nodes : 树（前序扁平数组）每节点 5 个整数:
        [start, len, typeCode, childCount, firstChildIndex]
        typeCode:
          0 连接/独立成分  [ ]
          1 从句          ［ ］
          2 句体成分      〈 〉
          3 谓语          （ ）
          4 主题          【 】
          5 主语          【 】
          6 宾语          【 】
          7 状语          【 】
          8 连体修饰      [ ]
          9 形式名词      【 】
         10 引用内容      【 】
         11 独立成分      [ ]
         12 名词短语      〈 〉
c     : clauses [start, len, typeCode, connective]
p     : pred   [start, len, funcCode, label]   谓语细分链
g     : 句型 id
k     : 句末类型
w     : 逐词 [start, len, posCode]

硬约束:
  * 所有 start/len 都指向**原句字符** ⇒ 片段天然逐字来自原句
  * 类型/功能/词性全部用**固定词表**(整数码) ⇒ 不自创术语
  * 原句无非 BMP 字符 ⇒ 字符偏移 == UTF-16 偏移 ⇒ 浏览器 slice 可直接用
  * 树里**每个字符只属于一个叶子** ⇒ 括号不会交叉、不会重复
"""
import json, os, sys, collections, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conj import chain as conj_chain

HERE = os.environ.get('SHUN_XVAL_DIR') or os.path.dirname(os.path.abspath(__file__))
TOK  = os.path.join(HERE, 'tokens.json')
OUT  = os.path.join(HERE, 'analysis.json')

# ============================================================ 类型词表
T_NAMES = ['连接', '从句', '句体成分', '谓语', '主题', '主语', '宾语', '状语',
           '连体修饰', '形式名词', '引用内容', '独立成分', '名词短语']
T = {n: i for i, n in enumerate(T_NAMES)}

BRACKET = {
    T['连接']:   ('[', ']'),
    T['从句']:   ('［', '］'),
    T['句体成分']: ('〈', '〉'),
    T['谓语']:   ('（', '）'),
    T['主题']:   ('【', '】'),
    T['主语']:   ('【', '】'),
    T['宾语']:   ('【', '】'),
    T['状语']:   ('【', '】'),
    T['连体修饰']: ('[', ']'),
    T['形式名词']: ('【', '】'),
    T['引用内容']: ('【', '】'),
    T['独立成分']: ('[', ']'),
    T['名词短语']: ('〈', '〉'),
}

PFN = ['动词', '形容词', '形容动词', '判断', '否定', '过去', '礼貌体', '推量',
       '意志', '愿望', '可能', '被动', '使役', '条件', '理由', '转折', '并列',
       '敬语', '命令', '义务', '经历', '传闻', '语气', '补助', '时制', '接续',
       '形式名词', '名词谓语']
F = {n: i for i, n in enumerate(PFN)}

POSC = ['名词', '动词', '形容词', '形容动词', '副词', '数词', '连体词', '接续词',
        '感叹词', '助词', '助动词', '符号', '前缀', '其他']
P = {n: i for i, n in enumerate(POSC)}


def pos_code(t):
    p, d1 = t['p'], t['d1']
    if p == '名詞':
        if d1 == '形容動詞語幹': return P['形容动词']
        if d1 == '副詞可能':     return P['副词']
        if d1 == '数':           return P['数词']
        return P['名词']
    if p == '動詞':     return P['动词']
    if p == '形容詞':   return P['形容词']
    if p == '副詞':     return P['副词']
    if p == '連体詞':   return P['连体词']
    if p == '接続詞':   return P['接续词']
    if p == '感動詞':   return P['感叹词']
    if p == '助動詞':   return P['助动词']
    if p == '助詞':     return P['助词']
    if p == '記号':     return P['符号']
    if p == '接頭辞':   return P['前缀']
    return P['其他']


# ============================================================ 句型目录
def M(s=None, p=None, d1=None, d2=None, b=None, ct=None, cf=None):
    return (s, p, d1, d2, b, ct, cf)

GP = [
 ('to_omoshiru', '〜と思います', '動詞辞書形＋ます', '（说话人）认为、觉得；句末也用于向对方确认', [
     M('と','助詞','格助詞','引用'), M(None,'動詞','自立',None,'思う'), M('ます','助動詞')]),
 ('to_iu', '〜と言う', '〜と＋言う', '（引用）据说～', [
     M('と','助詞','格助詞','引用'), M(None,'動詞','自立',None,'言う')]),
 ('koto_dekiru', '〜ことができます', '動詞辞書形＋こと＋が＋できる', '能够做到～（能力）', [
     M('こと','名詞'), M('が','助詞'), M(None,'動詞','自立',None,'できる'), M('ます','助動詞')]),
 ('koto_dekirai', '〜ことができません', '〜ことが＋できません', '无法做到～', [
     M('こと','名詞'), M('が','助詞'), M(None,'動詞','自立',None,'できる'), M(None,'助動詞')]),
 ('nakute', '〜なくて', 'ない形＋て', '（原因）因为没～；不然就～', [
     M(None,'助動詞',None,None,None,'特殊・ナイ','連用テ接続'), M('て','助詞','接続助詞')]),
 ('nakereba', '〜なければ', 'ない形＋ば', '如果不～（条件）', [
     M(None,'助動詞',None,None,None,'特殊・ナイ','仮定形'), M('ば','助詞','接続助詞')]),
 ('tai_desu', '〜たいです', '動詞連用形＋たい＋です', '想要～（愿望・礼貌）', [
     M(None,'助動詞',None,None,None,'特殊・タイ'), M('です','助動詞')]),
 ('tai', '〜たい', '動詞連用形＋たい', '想要～（愿望）', [
     M(None,'助動詞',None,None,None,'特殊・タイ')]),
 ('tara', '〜たら', '過去形＋たら', '如果～就／～之后（条件）', [
     M('たら','助動詞',None,None,None,'特殊・タ','仮定形')]),
 ('ba', '〜ば', 'ば形（假定形）', '如果～就（条件）', [
     M('ば','助詞','接続助詞')]),
 ('nara', '〜なら', '名詞・形容動詞語幹＋なら', '如果是～的话（条件）', [
     M('なら')]),
 ('to_conditional', '〜と', '動詞基本形＋と', '一～就～（条件・反复）', [
     M('と','助詞','接続助詞')]),
 ('no_de', '〜ので', '連用形＋ので', '因为～（原因）', [
     M('ので','助詞','接続助詞')]),
 ('kara_reason', '〜から', '連用形＋から', '因为～（原因）', [
     M('から','助詞','接続助詞')]),
 ('kedo', '〜けど', '連用形＋けど', '但是～（转折）', [
     M('けど','助詞','接続助詞')]),
 ('kedo_mo', '〜けれども', '連用形＋けれども', '但是～（转折）', [
     M('けれど','助詞','接続助詞')]),
 ('ga_kedo', '〜が', '連用形＋が', '但是～（转折）', [
     M('が','助詞','接続助詞')]),
 ('te_mo', '〜ても', 'て形＋も＋（后续成分）', '即使～（让步）', [
     M('て','助詞','接続助詞'), M('も','助詞','係助詞')]),
 ('te_mo_ii', '〜なくてもいいです', 'て形＋も＋いい', '不用～也可以（许可）', [
     M('て','助詞','接続助詞'), M('も','助詞','係助詞'), M('いい',None,None,None,'いい')]),
 ('nagara', '〜ながら', '連用形＋ながら', '一边～一边～（同时）', [
     M('ながら','助詞','接続助詞')]),
 ('youni', '〜ように', '連用形・名詞＋ように', '为了～／像～一样', [
     M('よう','名詞','非自立'), M('に','助詞','格助詞')]),
 ('tame_ni', '〜ために', '連用形＋ために', '为了～（目的）', [
     M('ため','名詞','非自立'), M('に','助詞','格助詞')]),
 ('ni_totte', '〜にとって', '名詞＋にとって', '对于～来说', [
     M('にとって')]),
 ('to_shite', '〜として', '名詞＋として', '作为～', [
     M('として')]),
 ('ni_yotte', '〜によって', '名詞＋によって', '通过～／依据～', [
     M('によって')]),
 ('ni_motte', '〜をもって', '名詞＋をもって', '以～（手段）', [
     M('をもって')]),
 ('ni_taisuru', '〜に対する', '名詞＋に対する', '关于～／对～而言', [
     M('に対する')]),
 ('kamo_shire', '〜かもしれない', '～かも＋しれない', '说不定～（推测・可能性）', [
     M('かも','助詞','副助詞'), M('しれ',None,None,None,'しれる'), M('ない','助動詞',None,None,None,'特殊・ナイ')]),
 ('rashii', '〜らしい', '名詞＋らしい', '听说～（传闻）', [
     M(None,None,None,None,'らしい')]),
 ('mashou', '〜ましょう', '動詞連用形＋ましょう', '让我们～吧（意志・提议）', [
     M('ましょ','助動詞',None,None,None,'特殊・マス'), M('う','助動詞')]),
 ('mashouka', '〜ましょうか', '～ましょうか', '要不要～（征求意见）', [
     M('ましょ','助動詞',None,None,None,'特殊・マス'), M('う','助動詞'), M('か')]),
 ('te_kudasai', '〜てください', '動詞て形＋ください', '请～（请求）', [
     M('て','助詞','接続助詞'), M('ください',None,None,None,'くださる')]),
 ('te_iru', '〜ている', '動詞て形＋いる', '正在～／处于～状态（持续）', [
     M('て','助詞','接続助詞'), M(None,'動詞','非自立',None,'いる')]),
 ('te_shimau', '〜てしまう', '動詞て形＋しまう', '～掉了（完了・遗憾）', [
     M('て','助詞','接続助詞'), M(None,'動詞','非自立',None,'しまう')]),
 ('te_miru', '〜てみる', '動詞て形＋みる', '试着～（尝试）', [
     M('て','助詞','接続助詞'), M(None,'動詞',None,None,'みる')]),
 ('ni_tsuite', '〜について', '名詞＋について', '关于～（话题）', [
     M('について')]),
 ('janai', '〜じゃない', 'だ＋ない（じゃ）', '不是～（否定）', [
     M('じゃ',None,None,None,None,None,'体言接続'), M('ない','助動詞',None,None,None,'特殊・ナイ')]),
 ('arigatou', '〜ございます', '動詞連用形＋ございます', '（敬语）～（礼貌）', [
     M(None,'助動詞',None,None,None,'五段・ラ行特殊'), M('ます','助動詞')]),
 ('dake', '〜だけ', '名詞＋だけ', '只～（限定）', [
     M('だけ')]),
 ('shika', '〜しか', '名詞＋しか（＋ない）', '只有～（限定）', [
     M('しか')]),
 ('kurai', '〜ぐらい', '数量＋ぐらい', '大约～（程度）', [
     M('ぐらい')]),
 ('yori', '〜より', '名詞＋より', '比～（比较）', [
     M('より','助詞','格助詞')]),
]
GP = [g for g in GP if g[3] is not None]

QUOTE_VERBS = {'言う', '思う', '問う', '答える', '云う'}

# 形式名词 (把动词变成名词性成分)
FORM_NOUNS = ('こと', 'もの', 'の', 'ため', 'ほう', 'とき', 'ところ', 'わけ')


def match_seq(toks, i, spec):
    for k, sp in enumerate(spec):
        s, p, d1, d2, b, ct, cf = sp
        j = i + k
        if j >= len(toks): return False
        t = toks[j]
        if s is not None and t['s'] != s: return False
        if p is not None and t['p'] != p: return False
        if d1 is not None and t['d1'] != d1: return False
        if d2 is not None and t['d2'] != d2: return False
        if b is not None and t['b'] != b: return False
        if ct is not None and t['ct'] != ct: return False
        if cf is not None and t['cf'] != cf: return False
    return True


# ============================================================ 助动词
AUX_WORDS = {
    'ません':   (F['否定'],   '否定（ません）'),
    'でした':   (F['过去'],   '过去・断定（でした）'),
    'でしょう': (F['推量'],   '推量（でしょう）'),
    'ましょう': (F['意志'],   '意志・提议（ましょう）'),
    'ないです': (F['否定'],   '否定（ないです）'),
    'ました':   (F['过去'],   '过去（ました）'),
    'だった':   (F['过去'],   '过去（だった）'),
    'ます':     (F['礼貌体'], '礼貌体（ます）'),
    'ない':     (F['否定'],   '否定（ない）'),
    'た':       (F['过去'],   '过去（た）'),
    'だ':       (F['判断'],   '判断（だ）'),
    'です':     (F['判断'],   '判断（です）'),
    'たい':     (F['愿望'],   '愿望（たい）'),
    'よう':     (F['意志'],   '意志・推量（よう）'),
    'だろう':   (F['推量'],   '推量（だろう）'),
    'な':       (F['形容动词'], '形容动词（な）'),
    'ば':       (F['条件'],   '条件（ば）'),
    'たら':     (F['条件'],   '条件（たら）'),
    'そう':     (F['传闻'],   '传闻・推量（そう）'),
    'らしい':   (F['传闻'],   '传闻（らしい）'),
    'ながら':   (F['补助'],   '同时（ながら）'),
    'ので':     (F['理由'],   '理由（ので）'),
    'から':     (F['理由'],   '理由（から）'),
    'けど':     (F['转折'],   '转折（けど）'),
    'が':       (F['转折'],   '转折（が）'),
    'のに':     (F['转折'],   '转折（のに）'),
    'と':       (F['接续'],   '接续（と）'),
    'し':       (F['并列'],   '并列（し）'),
    'う':       (F['推量'],   '推量・意志（う）'),
    'ん':       (F['否定'],   '否定（ん）'),
    'ぬ':       (F['否定'],   '否定（ぬ）'),
    'ず':       (F['否定'],   '否定（ず）'),
    'き':       (F['时制'],   '时制（き）'),
    'けり':     (F['时制'],   '时制（けり）'),
    'べし':     (F['义务'],   '义务（べし）'),
}

def aux_label(joined):
    return AUX_WORDS.get(joined, (F['补助'], '补助（%s）' % joined))


def verb_kind(t):
    if t['p'] == '形容詞':
        return '形容词'
    if t['p'] == '名詞' and t['d1'] == '形容動詞語幹':
        return '形容动词'
    if t['p'] == '名詞':
        return '名词谓语'
    if t['p'] == '副詞':
        return '副词谓语'
    if t['p'] == '形容詞':
        return '形容词'
    ct = t['ct']
    if ct.startswith('五段'):     return '五段动词'
    if ct.startswith('上一段'):   return '一段动词'
    if ct.startswith('サ変'):     return 'サ变动词'
    if ct.startswith('形容詞'):   return '形容词'
    if ct.startswith('不変化'):   return '不变化动词'
    if ct.startswith('下二'):     return '下二动词'
    if ct.startswith('特殊'):     return '特殊动词'
    if ct.startswith('文語'):     return '文语动词'
    return '动词'


CLAUSE_END_CONN = {'から', 'ので', 'けど', 'けれど', 'だが', 'が', 'ば', 'たら',
                   'なら', 'と', 'ながら', 'のに', 'し', 'で'}

ASCII_RE = re.compile(r'^[A-Za-z0-9%+\-.,!?\'"()/:&]+$')

# IPADIC 把 ? ! 标成「名詞/サ変接続」⇒ 必须自己认出来, 否则会混进名词短语
PUNCT_SURFACES = {'?', '!', '？', '！', '。', '、', ',', '．', '.'}


def is_punct(t):
    return t['s'] in PUNCT_SURFACES


def end_kind(toks):
    n = len(toks)
    for i in range(n - 1, -1, -1):
        t = toks[i]
        if t['p'] == '助詞' and t['d1'] in ('終助詞', '副助詞／並立助詞／終助詞'):
            if t['s'] == 'か':
                return '疑问句'
            if t['s'] in ('よ', 'ね'):
                return '感叹句'
        if t['p'] == '助動詞':
            if t['ct'] == '特殊・タ':   return '过去句'
            if t['ct'] == '特殊・ナイ': return '否定句'
            if t['ct'] == '特殊・マス' and t['cf'] == '未然ウ接続': return '劝诱句'
            if t['ct'] == '不変化型' and t['s'] == 'う': return '推量句'
        if t['p'] == '動詞' and t['d1'] == '非自立' and t['cf'] == '命令ｉ':
            return '命令句'
    if n and toks[-1]['s'] in ('?', '？'):
        return '疑问句'
    return '陈述句'


class Node:
    __slots__ = ('type', 'start', 'end', 'children')
    def __init__(self, tp, start, end):
        self.type = tp
        self.start = start
        self.end = end
        self.children = []


def analyze_sentence(sent):
    toks = sent['toks']
    text = sent['t']
    n = len(toks)

    offs = []
    o = 0
    for t in toks:
        offs.append(o)
        o += len(t['s'])
    assert o == len(text)
    offs.append(len(text))     # 哨兵

    if n == 0:
        return {'mc': [], 'nodes': [0, len(text), T['句体成分'], 0, 0],
                'c': [0, len(text), T['句体成分'], ''], 'p': [], 'g': [],
                'k': '陈述句', 'w': []}

    def prev_is_number(toks, i):
        """前一个 token 是数字 ⇒ 当前 ascii 是单位(3m/5kg) 不是独立成分"""
        if i <= 0:
            return False
        p = toks[i-1]
        return p['p'] == '名詞' and p['d1'] == '数'

    def is_ascii(t):
        """英文词表条目 (wonderful / personally …) —— 纯数字不算"""
        if is_punct(t):
            return False
        if not ASCII_RE.match(t['s']):
            return False
        if not any(c.isalpha() for c in t['s']):
            return False
        return not any('\u3040' <= c <= '\u30ff' or '\u4e00' <= c <= '\u9fff' for c in t['s'])

    # ---------------- 谓语核心 ----------------
    # 从后往前找**实词**; 但「非自立动词」(しまう/いる) 是补助动词,
    # 若它前面有「て」连用形 ⇒ 真正的主动词在更前面 (終わる + て + しまう)
    head = -1
    for i in range(n - 1, -1, -1):
        t = toks[i]
        if t['p'] in ('助詞', '助動詞', '記号') or is_punct(t):
            continue
        if t['p'] == '名詞' and is_ascii(t):
            continue
        if t['p'] == '動詞' and t['d1'] == '非自立':
            # 往前穿过「て」链找**主动词** (链可多跳: 持っ|て|いっ|て|い)
            j = i
            found = -1
            while j >= 2:
                if toks[j-1]['p'] == '助詞' and toks[j-1]['d1'] == '接続助詞' \
                   and toks[j-1]['s'] == 'て' and toks[j-2]['p'] == '動詞':
                    if toks[j-2]['d1'] == '自立':
                        found = j - 2
                        break
                    j = j - 2
                    continue
                break
            if found >= 0:
                head = found       # 主动词 (終わる / 持つ)
                break
            head = i
            break
        if t['p'] == '動詞' and t['d1'] == '自立':
            head = i; break
        if t['p'] == '形容詞' and t['d1'] == '自立':
            # 形容词若**直接修饰后面的名词** (いい天気) ⇒ 它是连体修饰, 不是谓语
            nxt = toks[i+1] if i + 1 < n else None
            if nxt is not None and nxt['p'] == '名詞' and nxt['d1'] != '形容動詞語幹':
                # 后面还有 助动词(です/だ) ⇒ 真正的谓语是「名词+です」
                if i + 2 < n and toks[i+2]['p'] == '助動詞' \
                   and toks[i+2]['ct'] in ('特殊・ダ', '特殊・デス'):
                    head = i + 1      # 名词做主语
                    break
                continue           # 连体修饰, 继续往前找
            head = i; break
        if t['p'] == '名詞' and t['d1'] == '形容動詞語幹':
            head = i; break
        if t['p'] == '名詞' and i + 1 < n and toks[i+1]['p'] == '助動詞' \
           and toks[i+1]['ct'] in ('特殊・ダ', '特殊・デス'):
            head = i; break
        if t['p'] == '名詞':
            # 裸名词 (277。/1990s/初心者。) 不是谓语:
            #   只有「名詞+です/だ」或「形容动词语干」才算名词谓语
            nxt = toks[i+1] if i+1 < n else None
            has_copula = nxt is not None and nxt['p'] == '助動詞' \
                         and nxt['ct'] in ('特殊・ダ', '特殊・デス')
            if not has_copula and t['d1'] != '形容動詞語幹':
                continue
            head = i; break
    # head 仍为 -1 ⇒ 本句没有谓语 (纯名词短语/数字/英文)
    if head < 0:
        head = -1

    pred_start = head
    nom = -1
    if head < 0:
        pred_start = 0
    if head >= 3 and toks[head-1]['p'] == '助詞' and toks[head-1]['s'] in ('が', 'は') \
       and toks[head-2]['p'] == '名詞' and toks[head-2]['d1'] == '非自立' \
       and toks[head-2]['s'] in ('こと', 'もの', 'の') \
       and toks[head-3]['p'] == '動詞':
        pred_start = head - 3
        nom = head - 2

    aux_end = head + 1 if head >= 0 else 0
    # 吃掉 て+补助动词 (終わっ|て|しまい) 这种结构
    while aux_end + 1 < n and toks[aux_end]['p'] == '助詞' \
          and toks[aux_end]['d1'] == '接続助詞' and toks[aux_end]['s'] == 'て' \
          and toks[aux_end+1]['p'] == '動詞':
        aux_end += 2
    while aux_end < n and toks[aux_end]['p'] == '助動詞':
        aux_end += 1
    mood_end = aux_end
    while mood_end < n and toks[mood_end]['p'] == '助詞' \
          and toks[mood_end]['d1'] in ('終助詞', '副助詞／並立助詞／終助詞'):
        mood_end += 1

    # ---------------- 谓语细分链 ----------------
    # 形态链: 取**本句最后一个自主动词/形容词**的辞書形, 推 て形/意志形/ば形
    # (述語 head 可能是名詞+です/だ, 或补助动词; 形态链要看实义的动词)
    # 形态链取**谓语链上**的实义动词 (连体修饰里的形容词不算主谓语)
    main_verb = None
    main_i = -1
    for i in range(pred_start, aux_end if aux_end > pred_start else pred_start):
        t = toks[i]
        if t['p'] == '動詞' and t['d1'] == '自立' and t['b'] and t['b'] != '*':
            main_verb, main_i = t, i; break
        if t['p'] == '形容詞' and t['d1'] == '自立' and t['b'] and t['b'] != '*':
            main_verb, main_i = t, i; break
    # 谓语核心是非自立动词 (いる/しまう/いく) ⇒ 往前穿过「て」链找主动词
    # 链可以有多跳: 持っ|て|いっ|て|い ⇒ 往前一路穿到 持っ
    if main_verb is not None and main_verb['p'] == '動詞' and main_verb['d1'] == '非自立':
        j = main_i
        while j >= 2:
            if toks[j-1]['p'] == '助詞' and toks[j-1]['d1'] == '接続助詞' \
               and toks[j-1]['s'] == 'て' and toks[j-2]['p'] == '動詞':
                if toks[j-2]['d1'] == '自立':
                    main_verb = toks[j-2]
                    break
                j = j - 2          # 继续往前穿 (非自立的中间环节)
                continue
            break
    if main_verb is None and pred_start >= 0:
        main_verb = toks[pred_start]
    mchain = []
    if main_verb is not None and main_verb['p'] in ('動詞', '形容詞') \
       and main_verb['b'] and main_verb['b'] != '*':
        mchain = conj_chain(main_verb['b'], main_verb['ct']) or []
    pred = []
    if head < 0:
        pred = []
    # 名词谓语 (N + です/だ) 的功能码
    def core_code(t):
        if t['p'] == '名詞':
            return F['名词谓语']
        if t['p'] == '形容詞':
            return F['形容词']
        if t['p'] == '副詞':
            return F['名词谓语']
        return F['动词']

    if aux_end > head + 1:
        if pred_start < head:
            pred.append((offs[pred_start], offs[head] - offs[pred_start], F['动词'],
                         verb_kind(toks[pred_start])))
            if nom >= 0:
                pred.append((offs[nom], offs[nom+1] - offs[nom], F['形式名词'],
                             '形式名词（%s）' % toks[nom]['s']))
        # 名词谓语 ⇒ 主体是名词, 后续 です/だ 单独作「判断」
        if toks[head]['p'] == '名詞' and toks[head]['d1'] != '形容動詞語幹':
            pred.append((offs[head], offs[head+1] - offs[head], F['名词谓语'],
                         '名词谓语'))
            a = head + 1
            while a < aux_end:
                b = a
                while b < aux_end:
                    b += 1
                    if ''.join(x['s'] for x in toks[a:b]) in AUX_WORDS:
                        break
                    if b - a >= 2:
                        break
                code, label = aux_label(''.join(x['s'] for x in toks[a:b]))
                pred.append((offs[a], offs[b] - offs[a], code, label))
                a = b
        else:
            pred.append((offs[head], offs[aux_end] - offs[head], core_code(toks[head]),
                         verb_kind(toks[head])))
            a = head + 1
            while a < aux_end:
                b = a
                while b < aux_end:
                    b += 1
                    if ''.join(x['s'] for x in toks[a:b]) in AUX_WORDS:
                        break
                    if b - a >= 2:
                        break
                code, label = aux_label(''.join(x['s'] for x in toks[a:b]))
                pred.append((offs[a], offs[b] - offs[a], code, label))
                a = b
    else:
        pred.append((offs[pred_start], offs[aux_end] - offs[pred_start],
                     core_code(toks[pred_start]), verb_kind(toks[pred_start])))
    if mood_end > aux_end:
        pred.append((offs[aux_end], offs[mood_end] - offs[aux_end], F['语气'], '语气（终助词）'))
    # 去掉空片段 (谓语为空时)
    pred = [x for x in pred if x[1] > 0]

    # IPADIC 把「〜と」(条件/引用) 都标成 格助詞/引用 ⇒ 这里人工消歧
    def is_conditional_to(i):
        """toks[i] 是「と」且应读作条件连用形「〜と」(那么)"""
        if toks[i]['s'] != 'と' or toks[i]['p'] != '助詞':
            return False
        nxt = toks[i + 1] if i + 1 < n else None
        if nxt is None:
            return False
        # 后面是「言う/思う」等引用动词 ⇒ 是引用
        if nxt['p'] == '動詞' and nxt['b'] in QUOTE_VERBS:
            return False
        if nxt['p'] == '名詞' and nxt['s'] in ('こと', 'の', 'ため'):
            return False
        prev = toks[i - 1] if i > 0 else None
        if prev is None:
            return False
        # 前一个是动词连用形 ⇒ 「〜と」表条件/推移 (いると / 出ると / すると)
        if prev['p'] == '動詞' and prev['cf'] in ('連用形', '基本形', '連用タ接続', '連用テ接続'):
            return True
        return False

    # ---------------- 分段 (token 区间) ----------------
    # 切分原则:
    #   接続詞       → 独立成段 (连接)
    #   接続助詞(から/ので/けど…) → **结束**当前段, 该段 = 从句 (含这个助词)
    #   感動詞 / 英文词表条目 → 独立成段 (独立成分)
    #   句点/？/！   → 结束当前段, 归入本段
    #   读点         → 归入前一段
    segments = []          # (t0, t1)
    cur = 0
    i = 0
    while i < n:
        t = toks[i]
        if t['p'] == '接続詞':
            if i > cur: segments.append((cur, i))
            segments.append((i, i + 1))       # 接続詞 单独一段
            i += 1
            cur = i
            continue
        if t['p'] == '感動詞' or (t['p'] == '名詞' and is_ascii(t) and not prev_is_number(toks, i)):
            if i > cur: segments.append((cur, i))
            segments.append((i, i + 1))       # 独立成分 单独一段
            i += 1
            cur = i
            continue
        if (t['p'] == '助詞' and t['d1'] == '接続助詞' and t['s'] in CLAUSE_END_CONN) \
           or is_conditional_to(i):
            # 该连接助词**属于本段**: 本段 = 从句, 含这个助词
            i += 1
            segments.append((cur, i))
            cur = i
            continue
        if (t['p'] == '記号' and t['d1'] == '句点') or \
           (is_punct(t) and t['s'] in ('?', '!', '？', '！')):
            i += 1
            while i < n and toks[i]['p'] == '記号' and toks[i]['d1'] == '読点':
                i += 1
            segments.append((cur, i))
            cur = i
            continue
        if t['p'] == '記号' and t['d1'] == '読点':
            i += 1
            continue                              # 读点归前段
        i += 1
    if cur < n:
        segments.append((cur, n))

    clause_defs = []   # (t0, t1, typeCode, connective)
    for (s0, s1) in segments:
        first, last = toks[s0], toks[s1-1]
        ctype, conn = T['句体成分'], ''
        if first['p'] == '接続詞':
            ctype, conn = T['连接'], first['s']
        elif first['p'] == '感動詞' or (first['p'] == '名詞' and is_ascii(first) and not prev_is_number(toks, s0)):
            ctype, conn = T['独立成分'], first['s']
        elif (last['p'] == '助詞' and last['d1'] == '接続助詞' and last['s'] in CLAUSE_END_CONN) \
             or (last['s'] == 'と' and is_conditional_to(s1 - 1)):
            ctype, conn = T['从句'], last['s']
        clause_defs.append((s0, s1, ctype, conn))

    def seg_start(tok_i):
        """tok_i 所在分段的起始 token (用于把成分范围钳在分段内)"""
        for (s0, s1) in segments:
            if s0 <= tok_i < s1:
                return s0
        return 0

    def clause_index(tok_i):
        for k, (s0, s1, ct, cn) in enumerate(clause_defs):
            if s0 <= tok_i < s1:
                return k
        return len(clause_defs) - 1

    # ---------------- 句体成分: 收集 (token 区间, 类型) ----------------
    def np_span(j):
        a = j
        while a > 0:
            t = toks[a-1]
            if is_punct(t):
                break
            if (t['p'] == '名詞' or t['p'] == '接頭辞'
                or (t['p'] == '助詞' and t['d1'] == '連体化')
                or (t['p'] == '動詞' and t['d1'] == '非自立')):
                a -= 1
            else:
                break
        return a

    def split_modifier(a, j):
        """[a,j) 里的「の」左半 = 连体修饰; 返回 (modA, modB) 或 None"""
        for k in range(j - 1, a - 1, -1):
            if toks[k]['p'] == '助詞' and toks[k]['d1'] == '連体化' and toks[k]['s'] == 'の':
                m = a
                while m < k and toks[m]['p'] in ('名詞', '接頭辞'):
                    m += 1
                if m > a:
                    return (a, m)
                return None
        return None

    # 收集所有句体成分 (token 区间, 类型, 修饰子区间, 是否强制挂在从句层)
    parts = []     # (t0, t1, typeCode, modSpan or None, force_clause)
    used = set()
    for i, t in enumerate(toks):
        if t['p'] != '助詞':
            continue
        s, d1, d2 = t['s'], t['d1'], t['d2']
        if d1 == '係助詞' and s in ('は', 'も'):
            tp = T['主题']
        elif d1 == '格助詞' and s == 'が':
            tp = T['主语']
        elif d1 == '格助詞' and s == 'を':
            tp = T['宾语']
        elif d1 == '格助詞' and s in ('に', 'で', 'から', 'まで', 'へ'):
            tp = T['状语']
        elif d1 == '連体化' and s == 'の':
            tp = T['连体修饰']
        else:
            continue
        a = np_span(i)
        # 钳在当前分段内 (不得跨越分段/从句边界)
        a = max(a, seg_start(i))
        if a >= i:
            continue
        mod = split_modifier(a, i) if tp != T['连体修饰'] else None
        if mod:
            ma, mb = mod
            ma = max(ma, seg_start(i))
            if ma >= mb:
                mod = None
            else:
                mod = (ma, mb)
        # 避免与已收区间重叠
        if any(x in used for x in range(a, i + 1)):
            continue
        # 「〜こと/もの/の が/は」⇒ 形式名词 单独成块 (与谓语平级, 不是谓语的子节点)
        if tp in (T['主语'], T['主题']) and toks[a]['p'] == '名詞' \
           and toks[a]['d1'] == '非自立' and toks[a]['s'] in FORM_NOUNS:
            fa = a
            if a > 0 and toks[a-1]['p'] == '動詞' and toks[a-1]['d1'] == '自立' \
               and a - 1 not in used:
                fa = a - 1
            parts.append((fa, a + 1, T['形式名词'], None, True))
            used.update(range(fa, a + 1))
            # 形式名词后的 が/は 是它的主格标记 ⇒ 单独标为主语/主题
            parts.append((i, i + 1, tp, None, True))
            used.add(i)
            continue
        parts.append((a, i + 1, tp, mod, False))
        used.update(range(a, i + 1))

    # 「と(引用)」= 和…一起 ⇒ 状语
    for i, t in enumerate(toks):
        if t['p'] == '助詞' and t['d1'] == '格助詞' and t['d2'] == '引用':
            nxt = toks[i+1] if i + 1 < n else None
            if nxt and nxt['p'] == '動詞' and nxt['b'] in QUOTE_VERBS:
                continue
            if is_conditional_to(i):
                continue
            a = np_span(i)
            if a < i and not any(x in used for x in range(a, i + 1)):
                parts.append((a, i + 1, T['状语'], None, False))
                used.update(range(a, i + 1))

    # 引用内容: 「〜と(引用)言う」 里的被引部分
    for i, t in enumerate(toks):
        if t['p'] == '動詞' and t['b'] in QUOTE_VERBS:
            # 往前找引用助词「と」(格助詞/引用)
            j = i - 1
            if j >= 0 and toks[j]['p'] == '助詞' and toks[j]['s'] == 'と' \
               and toks[j]['d1'] == '格助詞' and toks[j]['d2'] == '引用':
                # 被引部分从**本从句起点**开始 (它通常以 だ/です 结尾, np_span 回溯不到)
                a = clause_index(j) and clause_defs[clause_index(j)][0] or 0
                # 若从句起点起头就是连接词, 往后挪
                while a < j and toks[a]['p'] == '接続詞':
                    a += 1
                if a < j and not any(x in used for x in range(a, j)):
                    parts.append((a, j, T['引用内容'], None, False))
                    used.update(range(a, j))
            break

    # 形式名词 (こと/もの/の) + が/は ⇒ 主语化
    for i, t in enumerate(toks):
        if t['p'] == '名詞' and t['d1'] == '非自立' and \
           t['s'] in ('こと', 'もの', 'の', 'ため', 'ほう', 'とき', 'ところ', 'わけ'):
            if i + 1 < n and toks[i+1]['p'] == '助詞' and toks[i+1]['s'] in ('が', 'は') \
               and i not in used and not (pred_start <= i < aux_end):
                parts.append((i, i + 1, T['形式名词'], None, True))
                used.add(i)

    # ---------------- 建树 ----------------
    # 谓语节点先建 (它可能包含 こと+が 这样的主语化成分)
    pred_node = None
    if pred:
        # 谓语块只包**真正的谓语部分**, 不含前置的形式名词+主语
        # (那些已在 parts 里单独成块)
        p0 = offs[head] if head > pred_start else offs[pred_start]
        p1 = pred[-1][0] + pred[-1][1]
        # 若 pred 链第一项就是 head (无形式名词), 用它
        if pred[0][0] < p0:
            p0 = offs[head]
        pred_node = Node(T['谓语'], p0, p1)
        pred_tok_lo = pred_start
        pred_tok_hi = aux_end
    else:
        pred_tok_lo = pred_tok_hi = -1

    roots = []
    clause_nodes = []
    for (t0, t1, ct, cn) in clause_defs:
        nd = Node(ct, offs[t0], offs[t1])
        clause_nodes.append(nd)

    def add_part(t0, t1, tp, mod, force_clause=False):
        """把一个句体成分挂到正确的父节点: 落在谓语区间内 ⇒ 挂谓语; 否则挂从句"""
        if pred_node is not None and not force_clause \
           and pred_tok_lo <= t0 < pred_tok_hi:
            parent = pred_node
        else:
            ci = clause_index(t0)
            if ci < 0:
                return
            parent = clause_nodes[ci]
        nd = Node(tp, offs[t0], offs[t1])
        if mod:
            ma, mb = mod
            nd.children.append(Node(T['连体修饰'], offs[ma], offs[mb]))
        parent.children.append(nd)

    for (t0, t1, tp, mod, fc) in parts:
        add_part(t0, t1, tp, mod, fc)

    # 谓语节点挂到所属从句
    if pred_node is not None:
        pi = pred_start
        ci = clause_index(pi)
        if ci >= 0:
            clause_nodes[ci].children.append(pred_node)

    roots.extend(clause_nodes)

    # ---------------- 扁平化 (前序) ----------------
    flat = []
    def emit(nd):
        idx = len(flat)
        flat.append([nd.start, nd.end - nd.start, nd.type, 0, 0])
        for ch in nd.children:
            emit(ch)
        flat[idx][3] = len(nd.children)
        flat[idx][4] = idx + 1
        return idx
    for r in roots:
        emit(r)

    # 修正 firstChild: emit 里是 idx+1, 但第一个子节点可能前面还有别的? 不会 —— 子节点紧跟父节点
    # 但 roots 是逐个 emit, 所以父节点后紧跟其子树 ✔
    for nd in flat:
        if nd[3] == 0:
            nd[4] = 0

    # ---------------- 句型 ----------------
    gps = []
    for (gid, name, cs_, mean, spec) in GP:
        for i in range(n):
            if match_seq(toks, i, spec):
                gps.append(gid); break

    # ---------------- 逐词词性 ----------------
    w = []
    for idx, t in enumerate(toks):
        w.extend([offs[idx], len(t['s']), pos_code(t)])

    # 逐词 token (带字符偏移), 供 frame.py 判定空隙的词性
    toks_out = []
    for idx, t in enumerate(toks):
        toks_out.append({'off': offs[idx], 'len': len(t['s']), 's': t['s'],
                         'p': t['p'], 'd1': t['d1']})

    return {
        'mc': mchain,
        '_toks': toks_out,
        'nodes': [x for nd in flat for x in nd],
        'c': [x for (t0, t1, ct, cn) in clause_defs for x in (offs[t0], offs[t1] - offs[t0], ct, cn)],
        'p': [x for p in pred for x in p],
        'g': gps,
        'k': end_kind(toks),
        'w': w,
    }


def main():
    data = json.load(open(TOK, encoding='utf-8'))
    out = []
    fails = 0
    for sent in data:
        try:
            out.append(analyze_sentence(sent))
        except Exception as e:
            fails += 1
            print('FAIL', sent['i'], repr(sent['t'][:50]), type(e).__name__, e, file=sys.stderr)
            out.append({'mc': [], '_toks': [], 'nodes': [0, len(sent['t']), T['句体成分'], 0, 0],
                        'c': [0, len(sent['t']), T['句体成分'], ''], 'p': [], 'g': [],
                        'k': '陈述句', 'w': []})
    json.dump(out, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))

    nogp = sum(1 for o in out if not o['g'])
    print('sentences:', len(out), ' fails:', fails, ' no grammar point:', nogp)
    cnt = collections.Counter()
    for o in out:
        for g in o['g']: cnt[g] += 1
    print('--- 句型命中 ---')
    for gid, name, cs_, mean, spec in GP:
        print(f'  {name:22s} {cnt.get(gid,0):5d}')
    print('size:', os.path.getsize(OUT) // 1024, 'KB')


if __name__ == '__main__':
    main()

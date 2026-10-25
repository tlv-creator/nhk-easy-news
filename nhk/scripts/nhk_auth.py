#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NHK 免账号授权链 —— **2026-10-25 实测跑通**（沙箱重置后可直接用）
🔴 关键：idpresponse 那跳 **必须带 cookie jar**（第一跳设的 __HOST-bff-code）
   少了它 → 第三跳 400 → 表现为「idp 参数集丢了 / 授权链坏了」（假象）
用法：python3 nhk_auth.py  ⇒ 打印 z_at 和媒体令牌
"""
import json, subprocess, re, urllib.parse, base64, sys, os
UA = os.environ.get('NHK_UA', 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36')
JAR = os.environ.get('NHK_JAR', '/tmp/nhkjar.txt')

def hop(url, extra=None, body='/dev/null'):
    cmd = ['curl','-sS','-m','40','-o',body,'-D','-','-c',JAR,'-b',JAR,'-H',f'User-Agent: {UA}']
    for k, v in (extra or {}).items():
        cmd += ['-H', f'{k}: {v}']
    cmd.append(url)
    r = subprocess.run(cmd, capture_output=True, text=True).stdout
    code = re.search(r'HTTP/\S+\s+(\d+)', r)
    loc  = re.search(r'(?im)^location:\s*(.+?)\r?$', r)
    return (int(code.group(1)) if code else 0), (loc.group(1).strip() if loc else ''), r

def get_zat():
    rd = urllib.parse.quote('https://news.web.nhk/news/easy/', safe='')
    u = ("https://news.web.nhk/tix/build_authorize"
         "?idp=a-alaz&profileType=abroad&entity=none"
         f"&area=130&pref=13&jisx0402=13101&redirect_uri={rd}")
    c1, l1, _ = hop(u)
    if not l1: raise SystemExit(f"❌ 第一跳断（http={c1}）")
    c2, l2, h2 = hop(l1)
    z = None
    for m in re.finditer(r'(?im)^set-cookie:\s*z_at=([^;]+)', h2): z = m.group(1)
    if not z:
        c3, l3, h3 = hop(l2, {'Referer': 'https://news.web.nhk/'})
        for m in re.finditer(r'(?im)^set-cookie:\s*z_at=([^;]+)', h3): z = m.group(1)
        if not z and c3 >= 300 and c3 < 400:
            c4, l4, h4 = hop(l3, {'Referer': 'https://news.web.nhk/'})
            for m in re.finditer(r'(?im)^set-cookie:\s*z_at=([^;]+)', h4): z = m.group(1)
    if not z: raise SystemExit('❌ 没拿到 z_at')
    return z

def media_token(z):
    cmd = ['curl','-sS','-m','40','-H',f'Authorization: Bearer {z}','-H',f'User-Agent: {UA}',
           'https://mediatoken.web.nhk/v1/token']
    r = subprocess.run(cmd, capture_output=True, text=True).stdout
    try: return json.loads(r).get('token','')
    except Exception: return ''

def news_list(z, out='/tmp/nl.json'):
    cmd = ['curl','-sS','-m','60','-H',f'Cookie: z_at={z}','-H',f'User-Agent: {UA}',
           '-o',out,'https://news.web.nhk/news/easy/news-list.json']
    subprocess.run(cmd, capture_output=True)
    return json.load(open(out))

if __name__ == '__main__':
    z = get_zat()
    p = z.split('.')[1]; p += '=' * (-len(p) % 4)
    pj = json.loads(base64.urlsafe_b64decode(p))
    print(f"✅ z_at  scope={pj.get('scope')}  profileType={pj.get('profileType')}")
    mt = media_token(z)
    print(f"✅ 媒体令牌 {'拿到' if mt else '空'}")
    # 🔴 顶层结构是 [ {日期: [条目...]} ]，不是 {items:[...]}
    d = news_list(z)
    g = d[0] if isinstance(d, list) else d
    latest = sorted(g.keys(), reverse=True)[0]
    arr = g[latest]
    print(f"✅ 索引日期 {len(g)} 天，最新 = {latest}，该日 {len(arr)} 条")
    for it in arr[:3]:
        print(f"   {it.get('news_prearranged_time','')[:16]}  {it.get('news_id')}  {str(it.get('title',''))[:40]}")
    print(f"   音声字段示例: {arr[0].get('news_easy_voice_uri','(无)')}")

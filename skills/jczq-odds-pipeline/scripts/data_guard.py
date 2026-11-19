#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞彩赔率时点快照 · 数值级检测（data_guard）

规则来源：交接卡 12.3 + 框架口径
  1. 源有值而输出空       → FAIL（阻断交付）
  2. 源机构未渲染         → WARN（标「未渲染」，不阻断）
  3. 基线缺失             → WARN（标「缺基线」，不阻断）
  4. 数值越界/数量不符    → FAIL
  5. 场次数与当日编号不符 → FAIL

用法：
  python3 jczq/data_guard.py <snapshot.json> \
      [--xml <odds.xml>] [--html-dir <dir>] [--fixtures <fixtures.json>] [--baseline <prev.json>]

退出码：0 通过（FAIL=0 且 WARN<=5）；1 阻断；2 用法错误
"""
import json, sys, os, re, io, argparse, xml.etree.ElementTree as ET

EU_KEYS = ['wl', 'lb', 'bet365']
AS_KEYS = ['am', 'hg', 'bet365']
SP_EXPECT = [('nspf', '胜平负', 3), ('spf', '让球', 3), ('jq', '总进球', 8), ('bqc', '半全场', 9)]
LINE_OK = {'平手', '平手/半球', '半球', '半球/一球', '一球', '一球/球半', '球半',
           '球半/两球', '两球', '两球/两球半', '两球半', '两球半/三球', '三球'}


LINE_UNIT = {'半', '一', '两', '二', '三', '四', '五', '六', '七', '八', '九', '十'}


def _seg_ok(seg):
    seg = seg.strip()
    if seg == '平手':
        return True
    if not seg.endswith('球') and not seg.endswith('球半'):
        return False
    if seg.endswith('球半'):
        num = seg[:-2]
        return num == '' or num in LINE_UNIT
    num = seg[:-1]
    return num in LINE_UNIT


def line_valid(s):
    s = (s or '').strip()
    if s.startswith('受'):
        s = s[1:]
    if s in LINE_OK:
        return True
    parts = s.split('/')
    return 1 <= len(parts) <= 2 and all(_seg_ok(x) for x in parts)


def load_json(p):
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def sp_count_from_html(path, kind):
    """从 500 玩法页提取 (场次, SP 数量) 映射"""
    if not os.path.exists(path):
        return None
    h = open(path, encoding='utf-8', errors='ignore').read()
    out = {}
    for r in re.split(r'<tr', h):
        m = re.search(r'data-processname="(\d{4})"', r)
        if not m:
            continue
        no = '周一' + m.group(1)[1:]
        tds = re.findall(r'<td[^>]*>(.*?)</td>', r, re.S)
        idx = 4 if kind in ('jq', 'bqc') else 5
        if len(tds) <= idx:
            continue
        out[no] = len(re.findall(r'data-sp="([0-9.]+)"', tds[idx]))
    return out


def page_est(path):
    """按排版粗算 A4 页数：表格行 22pt、正文行 26pt、可排高度 700pt"""
    t = io.open(path, encoding='utf-8').read()
    lines = t.split('\n')
    tbl = sum(1 for l in lines if l.startswith('|'))
    return (tbl * 22 + (len(lines) - tbl) * 26) / 700.0, len(t.replace(' ', ''))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('snapshot')
    ap.add_argument('--xml')
    ap.add_argument('--html-dir')
    ap.add_argument('--fixtures')
    ap.add_argument('--baseline')
    ap.add_argument('--max-warn', type=int, default=5)
    ap.add_argument('--md', action='append', default=[], help='落库前文档 md 路径，可重复；检查单文档页数≤20')
    ap.add_argument('--max-pages', type=int, default=20)
    a = ap.parse_args()

    try:
        snap = load_json(a.snapshot)
    except Exception as e:
        print(f'FAIL 快照无法解析：{e}')
        return 1

    matches = snap.get('matches') or []
    rows = {m.get('no'): m for m in matches}
    FAIL, WARN = [], []

    if not matches:
        print('FAIL 快照内 0 场，禁止交付')
        return 1

    # 场次数与 fixtures 对账
    if a.fixtures and os.path.exists(a.fixtures):
        fx = load_json(a.fixtures)
        pfx = (load_json(a.snapshot) or {}).get('prefix') or '周一'
        want = set()
        for f in fx:
            if f.get('processname'):
                want.add('%s%03d' % (pfx, int(str(f['processname'])[1:])))
            elif f.get('no'):
                want.add(f['no'])
        miss = want - set(rows)
        extra = set(rows) - want
        if miss:
            FAIL.append(f'缺场：{sorted(miss)}（快照 {len(rows)} 场 / 订单日 {len(want)} 场）')
        if extra:
            FAIL.append(f'多场（非当日编号）：{sorted(extra)}')

    # 与源 XML 对账：源有值而输出空 → FAIL
    if a.xml and os.path.exists(a.xml):
        root = ET.parse(a.xml).getroot()
        src = {m.get('id'): m for m in root.find('matches').findall('match')}
        for no, r in rows.items():
            mid = str(r.get('src_id') or r.get('infomatchid') or '')
            m = src.get(mid)
            if m is None:
                # 用主客名反查
                for mm in src.values():
                    pass
                WARN.append(f'{no} 源 XML 无对应 match（infomatchid={mid}）')
                continue
            eu = m.find('europe'); asia = m.find('asian')
            for k in EU_KEYS:
                sv = (eu.get(k) or '').strip() if eu is not None else ''
                if sv:
                    if not r.get('eu', {}).get(k):
                        FAIL.append(f'{no} 源有欧赔 {k}={sv}，输出为空')
                    elif len(r['eu'][k]) != 3:
                        FAIL.append(f'{no} 欧赔 {k} 三项数={len(r["eu"][k])}（源 {sv}）')
            for k in AS_KEYS:
                sv = (asia.get(k) or '').strip() if asia is not None else ''
                if sv:
                    got = r.get('asia', {}).get(k)
                    if not got:
                        FAIL.append(f'{no} 源有亚盘 {k}={sv}，输出为空')
                    elif not got.get('line'):
                        FAIL.append(f'{no} 亚盘 {k} 盘口为空（源 {sv}）')

    # 与玩法页对账：源有值而输出空 → FAIL；页面未渲染 → WARN
    if a.html_dir and os.path.isdir(a.html_dir):
        checks = [('play_354.html', 'nspf', 3), ('play_354.html', 'spf', 3),
                  ('play_270.html', 'jq', 8), ('play_272.html', 'bqc', 9)]
        for fn, kind, expect in checks:
            srcmap = sp_count_from_html(os.path.join(a.html_dir, fn), kind)
            if srcmap is None:
                WARN.append(f'玩法数据源缺失：{fn}（无法核对 {kind}）')
                continue
            for no, cnt in srcmap.items():
                if no not in rows:
                    continue
                got = len(rows[no].get('sp', {}).get(kind) or [])
                if cnt > 0 and got == 0:
                    FAIL.append(f'{no} 源有 {kind} SP（{cnt} 项），输出为空')
                elif cnt > 0 and got != expect:
                    FAIL.append(f'{no} {kind} 数量 {got}≠{expect}')
        # 比分玩法：页面「展开投注」→ 未渲染
        bf = sp_count_from_html(os.path.join(a.html_dir, 'play_271.html'), 'bf')
        if bf:
            notrendered = [no for no, c in bf.items()
                           if c == 0 and no in rows and not (rows[no].get('sp', {}) or {}).get('bf')]
            if notrendered:
                WARN.append(f'比分 SP 未渲染（页面「展开投注」）：{len(notrendered)} 场 {sorted(notrendered)}')

    # 内部合理性
    for no, r in rows.items():
        for k in EU_KEYS:
            v = r.get('eu', {}).get(k)
            if v and any(x < 1.01 for x in v):
                if all(x >= 1.00 for x in v):
                    WARN.append(f'{no} 欧赔 {k} 含封盘价 1.00（＜1.01 下限，按封盘处理）：{v}')
                else:
                    FAIL.append(f'{no} 欧赔 {k} 低于十进制下限：{v}')
        for k, v in (r.get('asia') or {}).items():
            if not line_valid(v.get('line')):
                FAIL.append(f'{no} 亚盘 {k} 盘口非法：「{v.get("line")}」')
            # 立博亚盘为另一套水位口径（如「受半球」0.48/1.45），与 am/hg/bet365 不可同区间判定；
            # tp_warnings 早已把 lb 排除在分歧判定外，此处保持一致，仅校验盘口合法性。
            if k == 'lb':
                continue
            if not (0.5 <= v.get('up', 0) <= 1.6) or not (0.5 <= v.get('down', 0) <= 1.6):
                FAIL.append(f'{no} 亚盘 {k} 水位越界：{v.get("up")}/{v.get("down")}')
        for key, label, expect in SP_EXPECT:
            got = len(r.get('sp', {}).get(key) or [])
            if got and got != expect:
                FAIL.append(f'{no} {label} SP 数量 {got}≠{expect}')
        bfv = r.get('sp', {}).get('bf') or []
        if bfv and len(bfv) != 31:
            FAIL.append(f'{no} 比分 SP 数量 {len(bfv)}≠31')

    # 基线
    if a.baseline and os.path.exists(a.baseline):
        base = load_json(a.baseline)
        bno = {m.get('no') for m in (base.get('matches') or [])}
        lack = set(rows) - bno
        if lack:
            WARN.append(f'基线缺 {len(lack)} 场：{sorted(lack)}')
    else:
        WARN.append('缺初场基线（无 baseline 文件），本时点异动类预警不判')

    # 落库文档页数硬门：单文档 >max_pages FAIL，>0.75*max_pages WARN
    for md in a.md:
        if not os.path.exists(md):
            FAIL.append(f'待检文档不存在：{md}')
            continue
        pg, ch = page_est(md)
        if pg > a.max_pages:
            FAIL.append(f'{os.path.basename(md)} 约 {pg:.1f} 页 > {a.max_pages} 页上限（{ch} 字符），须拆卷')
        elif pg > a.max_pages * 0.75:
            WARN.append(f'{os.path.basename(md)} 约 {pg:.1f} 页，接近 {a.max_pages} 页上限')
        print(f'文档：{os.path.basename(md)}｜约 {pg:.1f} 页｜{ch} 字符｜上限 {a.max_pages} 页')

    print(f'快照：{a.snapshot}｜场次 {len(rows)}｜时点 {snap.get("timepoint","-")}')
    print(f'FAIL：{len(FAIL)}')
    for x in FAIL:
        print('  - ' + x)
    print(f'WARN：{len(WARN)}')
    for x in WARN:
        print('  - ' + x)
    ok = not FAIL and len(WARN) <= a.max_warn
    print('RESULT：' + ('PASS' if ok else 'FAIL（阻断交付）'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())

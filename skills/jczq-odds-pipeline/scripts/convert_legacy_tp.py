#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""老窗口时点 json → 新窗口格式转换并补入

背景：整点任务迁移到新窗口后，11:30/13:00 两个时点仍留在老窗口目录，
导致 21:00 终报读新窗口时看不到早段基线，只能靠「跨窗口基线」补一个点。
本脚本把老窗口的缺档时点转成新格式写入新窗口，补齐当日时间线。

用法：python3 jczq/convert_legacy_tp.py <YYMMDD>
"""
import json, os, sys

OLD = '/home/user/.doubao/agent_mode/workspace/.sessions/38446277671972610/agents/m_0cwRti7GbEW/jczq'
NEW_ROOT = '/home/user/Doubao/jczq_shared'

NSPF_ORDER = {'3': 0, '1': 1, '0': 2}


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def sp3(pairs):
    """[["3","1.58"],["1","3.35"],["0","4.80"]] → 按胜/平/负顺序取值"""
    if not pairs:
        return [None, None, None]
    out = [None, None, None]
    for k, v in pairs:
        if k in NSPF_ORDER:
            out[NSPF_ORDER[k]] = v
    return out


def spseq(pairs, order=None):
    """[["0","9.50"],...] → 按 key 顺序或给定顺序取值"""
    if not pairs:
        return []
    if order:
        d = dict(pairs)
        return [d.get(k) for k in order]
    return [v for _, v in pairs]


def asia_obj(a):
    if not a:
        return None
    res = {}
    for k, v in a.items():
        if isinstance(v, list) and len(v) >= 3:
            res[k] = {'up': f(v[0]), 'line': v[1], 'down': f(v[2])}
        else:
            res[k] = v
    return res


def convert(src, dst, timepoint):
    d = json.load(open(src, encoding='utf-8'))
    ms = []
    for m in d.get('matches', []):
        date = str(m.get('date') or '')[:10]
        t = str(m.get('time') or '')
        kickoff = f'{date} {t}' if date and t else None
        ms.append({
            'no': m.get('matchnum'),
            'src_id': m.get('id'),
            'league': m.get('league'),
            'home': m.get('home'),
            'away': m.get('away'),
            'kickoff': kickoff,
            'buyend': m.get('buyend'),
            'rangqiu': m.get('rangqiu'),
            'in_sale': (m.get('status') == '在售'),
            'eu': {k: [f(x) for x in v] if isinstance(v, list) else v
                   for k, v in (m.get('europe') or {}).items()},
            'asia': asia_obj(m.get('asian')),
            'dxq': asia_obj(m.get('dxq')),
            'sp': {
                'nspf': sp3(m.get('sp_nspf')),
                'spf': sp3(m.get('sp_spf')),
                'jq': spseq(m.get('sp_jqs')),
                'bqc': spseq(m.get('sp_bqc')),
                'bf': m.get('sp_bf') or [],
            },
            'betfa': m.get('betfa'),
        })
    out = {
        'date': d.get('date'),
        'timepoint': timepoint,
        'sampled_at': d.get('updatetime'),
        'xml_updatetime': d.get('updatetime'),
        'source': 'legacy_convert',
        'matches': ms,
    }
    json.dump(out, open(dst, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    return len(ms)


def main():
    ymd = sys.argv[1] if len(sys.argv) > 1 else '20261116'
    short = ymd[2:]
    for tp, tpname in [('1130', '11:30'), ('1300', '13:00'), ('1500', '15:00')]:
        src = os.path.join(OLD, f'all_{short}_{tp}.json')
        if not os.path.exists(src):
            print(f'{tp}: 老窗口无文件，跳过')
            continue
        NEW = os.path.join(NEW_ROOT, 'work_%s' % ymd[2:8])
        os.makedirs(NEW, exist_ok=True)
        dst = os.path.join(NEW, f'all_{ymd}_{tp}.json')
        if os.path.exists(dst):
            dst = os.path.join(NEW, f'all_{ymd}_{tp}_legacy.json')
        n = convert(src, dst, tpname)
        print(f'{tp}: 转入 {n} 场 → {os.path.basename(dst)}')


if __name__ == '__main__':
    main()

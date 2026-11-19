#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""字段级赔率台账：把「时点快照」变成「每个值从哪次采样起就没变」。

为什么需要：我们按钟点采样，上游按自己的节奏变盘。某个值在某时点被看到，
只说明它在「上一次采样之后、这一次之前」的某个时刻变成了这样；真实变价时刻拿不到
（odds.xml 只有一个全局 updatetime，match/europe/asian 节点均无机构级时间戳）。

本脚本不去猜真实时刻，而是给每个字段定一个可审计的锚：
  first_seen      —— 首次观测到非空值的采样序号
  last_change     —— 最后一次观测到该值发生变化的那次采样序号
  unchanged_since —— 从哪次采样起该值再没变过（没变过时 = first_seen）

报告口径应写「主胜 1.48 自 S06 起未变，此前 S05 为 1.55」，
不得写成「15:00 主胜 1.48」——后者把 2 小时的漂移压成了瞬间跳变。

用法：
  python3 tp_ledger.py --dir /home/user/Doubao/jczq_shared/work_261119 \
      [--day 20261119] [--match 周四004] [--out ledger.json] [--max-rows 200]

产出：
  ledger_<day>.json   全字段台账（机器读）
  ledger_<day>.md     人读摘要：只列变过的字段 + 空采点清单
"""
import json, glob, os, re, argparse


def flat_fields(r):
    """与 collect_tp.py 的 flat_fields 保持同一套键名，保证两处可互相印证"""
    out = {}
    for bk, v in (r.get('eu') or {}).items():
        for i, x in enumerate(v):
            out['eu.%s.%d' % (bk, i)] = x
    for bk, v in (r.get('asia') or {}).items():
        out['as.%s.line' % bk] = v.get('line')
        out['as.%s.up' % bk] = v.get('up')
        out['as.%s.down' % bk] = v.get('down')
    for bk, v in (r.get('dxq') or {}).items():
        out['dx.%s.line' % bk] = v.get('line')
        out['dx.%s.over' % bk] = v.get('over')
        out['dx.%s.under' % bk] = v.get('under')
    for i, x in enumerate(((r.get('sp') or {}).get('nspf') or [])[:3]):
        out['sp.nspf.%d' % i] = x
    for i, x in enumerate((r.get('eu_cons') or [])[:3]):
        out['eucons.%d' % i] = x
    for i, x in enumerate((r.get('rq_cons') or [])[:3]):
        out['rqcons.%d' % i] = x
    out['in_sale'] = bool(r.get('in_sale'))
    return out


def load_snaps(dirp, day):
    snaps = []
    for f in glob.glob(os.path.join(dirp, 'all_*_*.json')):
        m = re.search(r'all_(\d{8})_(\d{4})\.json$', os.path.basename(f))
        if not m or (day and m.group(1) != day):
            continue
        try:
            d = json.load(open(f, encoding='utf-8'))
        except Exception:
            continue
        snaps.append({'file': os.path.basename(f), 'tp': d.get('timepoint') or m.group(2),
                      'seq': d.get('seq'), 'fetched_at': d.get('fetched_at'),
                      'xml_updatetime': d.get('xml_updatetime'),
                      'xml_hash': d.get('xml_hash'),
                      'fields_changed': d.get('fields_changed'), 'data': d})
    snaps.sort(key=lambda s: (s['seq'] if isinstance(s['seq'], int) else 10**6, s['tp']))
    for i, s in enumerate(snaps, 1):
        if not isinstance(s['seq'], int):
            s['seq'] = i
    return snaps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', required=True)
    ap.add_argument('--day', default=None, help='YYYYMMDD，缺省取目录里最多的那天')
    ap.add_argument('--match', default=None, help='只输出某一场，如 周四004')
    ap.add_argument('--out', default=None)
    ap.add_argument('--max-rows', type=int, default=200)
    a = ap.parse_args()

    day = a.day
    if not day:
        cand = [re.search(r'all_(\d{8})_', os.path.basename(f)).group(1)
                for f in glob.glob(os.path.join(a.dir, 'all_*_*.json'))
                if re.search(r'all_(\d{8})_', os.path.basename(f))]
        if not cand:
            print('目录内无 all_YYYYMMDD_HHmm.json：%s' % a.dir)
            return 1
        day = max(set(cand), key=cand.count)

    snaps = load_snaps(a.dir, day)
    if not snaps:
        print('%s 无 %s 的快照' % (a.dir, day))
        return 1

    # 逐场逐字段累计
    led = {}
    for s in snaps:
        for r in (s['data'].get('matches') or []):
            no = r.get('no')
            if a.match and no != a.match:
                continue
            f = flat_fields(r)
            for k, v in f.items():
                if v is None:
                    continue
                e = led.setdefault((no, k), {'first_seen': s['seq'], 'last_change': None,
                                             'value': v, 'n_changes': 0, 'n_obs': 0})
                e['n_obs'] += 1
                if v != e['value']:
                    e['n_changes'] += 1
                    e['last_change'] = s['seq']
                    e['value'] = v

    rows = []
    for (no, k), e in sorted(led.items()):
        rows.append({'no': no, 'key': k, 'value': e['value'],
                     'first_seen': e['first_seen'], 'last_change': e['last_change'],
                     'unchanged_since': e['last_change'] or e['first_seen'],
                     'n_changes': e['n_changes'], 'n_obs': e['n_obs']})

    seqmap = {s['seq']: {'tp': s['tp'], 'fetched_at': s['fetched_at']} for s in snaps}
    empty = [s['seq'] for s in snaps if s.get('fields_changed') == 0]
    first_seq = snaps[0]['seq']
    out = {'dir': a.dir, 'day': day,
           'snapshots': [{k: s[k] for k in ('seq', 'tp', 'fetched_at', 'xml_updatetime',
                                           'xml_hash', 'fields_changed')} for s in snaps],
           'empty_points': empty,
           'matches': rows}
    p = a.out or os.path.join(a.dir, 'ledger_%s.json' % day)
    json.dump(out, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

    # 人读摘要：只列「变过」或「中途才出现」的字段
    md = os.path.splitext(p)[0] + '.md'
    with open(md, 'w', encoding='utf-8') as fp:
        fp.write('# 字段级赔率台账 %s\n\n' % day)
        fp.write('- 快照 %d 个：%s\n' % (len(snaps), '、'.join(
            'S%02d=%s%s' % (s['seq'], s['tp'], '' if s['fields_changed'] is None
                           else '(Δ%s)' % s['fields_changed']) for s in snaps)))
        fp.write('- 空采点（与前一时点逐字段一致）%s\n\n' % (
            '、'.join('S%02d=%s' % (q, seqmap[q]['tp']) for q in empty) if empty else '无'))
        interesting = [r for r in rows if r['n_changes'] > 0 or r['first_seen'] != first_seq]
        fp.write('| 场次 | 字段 | 当前值 | 首现 | 最近变更 | 自Sxx未变 | 变更次数 |\n|---|---|---|---|---|---|---|\n')
        for r in interesting[:a.max_rows]:
            fp.write('| %s | %s | %s | S%02d | %s | S%02d | %d |\n' % (
                r['no'], r['key'], r['value'], r['first_seen'],
                ('S%02d' % r['last_change']) if r['last_change'] else '-',
                r['unchanged_since'], r['n_changes']))
        if len(interesting) > a.max_rows:
            fp.write('\n（仅列前 %d 行，共 %d 行，全量见 json）\n' % (a.max_rows, len(interesting)))

    nchg = sum(1 for r in rows if r['n_changes'] > 0)
    print('台账 %s｜快照 %d｜字段 %d｜变过的字段 %d｜空采点 %d' % (
        day, len(snaps), len(rows), nchg, len(empty)))
    for r in interesting[:12]:
        print('  %s %-16s %s｜首现 S%02d｜最近变更 %s｜自 S%02d 未变' % (
            r['no'], r['key'], r['value'], r['first_seen'],
            ('S%02d' % r['last_change']) if r['last_change'] else '-', r['unchanged_since']))
    print('台账 %s' % p)
    print('摘要 %s' % md)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

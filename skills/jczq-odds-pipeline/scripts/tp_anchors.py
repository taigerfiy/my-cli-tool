#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从时点快照派生每场「事件锚点」——不用固定钟点，用每场自己的起点。

派生三个锚点：
  first_sp     首个「SP 非空」的采集时点（竞彩官方初值被看见的最早时刻，是上界）
  first_sale   首个 in_sale=True 的采集时点（真正可投注起点）
  sp_drift     首个 SP → 最后时点 SP 的位移（主胜/平/客胜）

用法：
  python3 tp_anchors.py --dir /home/user/Doubao/jczq_shared/work_261119 [--out anchors.json]

注意（必须带进结论）：
  first_sp 是「首次观测到」，不是「官方发布时间」。要拿真发布时间，用 init_<YYMMDD>.json
  里的 init_asian_time（亚指初始盘口时间），或把 11:10 预触发升级成事件驱动补采。
  早于首个时点的发布，本文件看不见——这是上界，别当精确时刻。
"""
import json, glob, os, sys, argparse


def load(dirp):
    """按 timepoint 升序载入所有快照"""
    snaps = []
    for f in glob.glob(os.path.join(dirp, 'all_*_*.json')):
        try:
            d = json.load(open(f, encoding='utf-8'))
            snaps.append((d.get('timepoint') or '?', d))
        except Exception:
            continue
    snaps.sort(key=lambda x: x[0])
    return snaps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', required=True)
    ap.add_argument('--out', default=None)
    a = ap.parse_args()

    snaps = load(a.dir)
    if not snaps:
        print('无快照：%s' % a.dir)
        return 1

    nos = sorted({m['no'] for _, d in snaps for m in d.get('matches', [])})
    rows = []
    for no in nos:
        first_sp = first_sale = None
        sp0 = sp_last = []
        sale_sp = []
        for tp, d in snaps:
            m = next((x for x in d.get('matches', []) if x['no'] == no), None)
            if not m:
                continue
            sp = (m.get('sp') or {}).get('nspf') or []
            if sp and first_sp is None:
                first_sp, sp0 = tp, sp
            if sp:
                sp_last = sp
            if m.get('in_sale'):
                if first_sale is None:
                    first_sale, sale_sp = tp, sp
        drift = ''
        if sp0 and sp_last:
            try:
                drift = '/'.join('%+.2f' % (float(sp_last[i]) - float(sp0[i])) for i in range(3))
            except Exception:
                drift = ''
        rows.append({'no': no, 'first_sp_tp': first_sp, 'first_sale_tp': first_sale,
                     'sp_first': sp0, 'sp_at_sale': sale_sp, 'sp_last': sp_last, 'sp_drift': drift})

    out = {'dir': a.dir, 'timepoints': [tp for tp, _ in snaps], 'matches': rows}
    p = a.out or os.path.join(a.dir, 'anchors.json')
    json.dump(out, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

    print('时点 %s｜场次 %d' % ('/'.join(tp for tp, _ in snaps), len(rows)))
    print('%-8s %-9s %-11s %-18s %-18s %s' % ('场次', 'SP首现', '首次可投注', '首现SP', '最新SP', '位移'))
    for r in rows:
        print('%-8s %-9s %-11s %-18s %-18s %s' % (
            r['no'], r['first_sp_tp'] or '-', r['first_sale_tp'] or '-',
            '/'.join(r['sp_first']) or '-', '/'.join(r['sp_last']) or '-', r['sp_drift'] or '-'))
    print(r'%s' % p)
    return 0


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""时点预警计算（欧赔三家 + 亚盘三家），按交接卡 12.3 规则

用法：
  python3 jczq/tp_warnings.py --date 2026-11-18 --tp 1500 [--dir work_261118]
基线：当日最早的 all_<YYYYMMDD>_HHmm.json（默认 1130，缺失则取现存最早）
上一时点：当前时点之前最近的快照（用于「连续两时点同向」橙灯）
仅把 warnings 写回共享根快照；双写已退役（2026-11-18）。
"""
import json, os, glob, argparse

BASE = '/home/user/Doubao/jczq_shared'
OLD = '/home/user/.doubao/agent_mode/workspace/.sessions/38446277671972610/agents/m_0cwRti7GbEW/jczq'
LINE = {'平手': 0, '平手/半球': 0.25, '平半': 0.25, '半球': 0.5, '半球/一球': 0.75,
        '半一': 0.75, '一球': 1.0, '一球/球半': 1.25, '球半': 1.5, '球半/两球': 1.75,
        '两球': 2.0, '两球/两球半': 2.25, '两球半': 2.5, '两球半/三球': 2.75,
        '三球': 3.0, '三球/三球半': 3.25, '三球半': 3.5, '受平手': 0, '受平手/半球': -0.25,
        '受平半': -0.25, '受半球': -0.5, '受半球/一球': -0.75, '受半一': -0.75,
        '受一球': -1.0, '受一球/球半': -1.25, '受球半': -1.5, '受球半/两球': -1.75,
        '受两球': -2.0, '受两球/两球半': -2.25, '受两球半': -2.5}
OUT = ['主', '平', '客']


def ln(s):
    if not s:
        return None
    s = s.replace(' ', '')
    return LINE.get(s)


def load(p):
    return json.load(open(p, encoding='utf-8'))


def idx(d):
    return {m['no']: m for m in d.get('matches', [])}


def compute(cur, base, prev):
    w = []
    cb, pb = idx(cur), idx(base)
    pv = idx(prev) if prev else {}
    for no, m in sorted(cb.items()):
        b = pb.get(no)
        e, eb = m.get('eu') or {}, (b or {}).get('eu') or {}
        # ---- 欧赔 ----
        spread = m.get('eu_spread') or []
        for i, o in enumerate(OUT):
            if spread and spread[i] >= 0.25:
                w.append({'no': no, 'type': '欧赔分歧·红', 'level': 'red',
                          'detail': f'{o} 三家差 {spread[i]:.2f}≥0.25'})
        if eb:
            for i, o in enumerate(OUT):
                for bk in ('wl', 'lb', 'bet365'):
                    v0, v1 = eb.get(bk), e.get(bk)
                    if not v0 or not v1:
                        continue
                    d = v1[i] - v0[i]
                    if abs(d) >= 0.10 or abs(d) / max(v0[i], 1e-9) >= 0.05:
                        w.append({'no': no, 'type': '欧赔异动·黄', 'level': 'yellow',
                                  'detail': f'{o} {bk} {v0[i]:.2f}→{v1[i]:.2f}（{d:+.2f}）'})
        # 橙灯：与上一时点相对基线同向且两次都超阈值
        pm = pv.get(no)
        if pm and eb and pm.get('eu'):
            for i, o in enumerate(OUT):
                for bk in ('wl', 'lb', 'bet365'):
                    v0, v1, v2 = eb.get(bk), (pm.get('eu') or {}).get(bk), e.get(bk)
                    if not (v0 and v1 and v2):
                        continue
                    d1, d2 = v1[i] - v0[i], v2[i] - v0[i]
                    if d1 * d2 > 0 and min(abs(d1), abs(d2)) >= 0.05:
                        w.append({'no': no, 'type': '欧赔连续同向·橙', 'level': 'orange',
                                  'detail': f'{o} {bk} 连续两时点同向 {v0[i]:.2f}→{v1[i]:.2f}→{v2[i]:.2f}'})
        # ---- 亚盘（只判交接卡口径的三家 am/hg/bet365；lb 立博亚盘口径不同，不参与分歧判定） ----
        a, ab = m.get('asia') or {}, (b or {}).get('asia') or {}
        A3 = ('am', 'hg', 'bet365')
        lines = {k: ln(a[k].get('line')) for k in A3 if k in a}
        valid = [v for v in lines.values() if v is not None]
        if len(set(valid)) >= 2:
            w.append({'no': no, 'type': '亚盘让球分歧·红', 'level': 'red',
                      'detail': '至少两家让球数不同：' + ' / '.join(f'{k}={a[k].get("line")}' for k in lines)})
        ups = [a[k].get('up') for k in A3 if k in a and a[k].get('up') is not None]
        if ups and max(ups) - min(ups) >= 0.15:
            w.append({'no': no, 'type': '亚盘水位分歧·红', 'level': 'red',
                      'detail': f'上盘水位差 {max(ups) - min(ups):.2f}≥0.15'})
        if ab:
            for bk in A3:
                v0, v1 = ab.get(bk), a.get(bk)
                if not v0 or not v1:
                    continue
                l0, l1 = ln(v0.get('line')), ln(v1.get('line'))
                if l0 is not None and l1 is not None and abs(l1 - l0) >= 0.5:
                    w.append({'no': no, 'type': '亚盘升降盘·黄', 'level': 'yellow',
                              'detail': f'{bk} 让球 {v0.get("line")}→{v1.get("line")}'})
                for wl in ('up', 'down'):
                    if v0.get(wl) is not None and v1.get(wl) is not None:
                        d = v1[wl] - v0[wl]
                        if abs(d) >= 0.10:
                            w.append({'no': no, 'type': '亚盘水位异动·黄', 'level': 'yellow',
                                      'detail': f'{bk} {wl}水 {v0[wl]:.2f}→{v1[wl]:.2f}（{d:+.2f}）'})
    return w


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', required=True)
    ap.add_argument('--tp', required=True)
    ap.add_argument('--dir', default=None)
    a = ap.parse_args()
    yy = a.date[2:4] + a.date[5:7] + a.date[8:10]
    W = a.dir or f'{BASE}/work_{yy}'
    cur_p = f'{W}/all_{a.date.replace("-","")}_{a.tp}.json'
    snaps = sorted(glob.glob(f'{W}/all_{a.date.replace("-","")}_*.json'))
    cur = load(cur_p)
    base_p = f'{W}/all_{a.date.replace("-","")}_1130.json'
    base_p = base_p if os.path.exists(base_p) else snaps[0]
    prev_p = [s for s in snaps if s < cur_p]
    prev_p = prev_p[-1] if prev_p else None
    w = compute(cur, load(base_p), load(prev_p) if prev_p else None)
    cur['warnings'] = w
    cur['warning_count'] = len(w)
    cur['baseline'] = {'from': os.path.basename(base_p), 'xml_updatetime': load(base_p).get('xml_updatetime')}
    if prev_p:
        cur['prev'] = os.path.basename(prev_p)
    json.dump(cur, open(cur_p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    # 双写文件同步成同一内容
    dual = 'SKIP （双写已退役，仅共享根）'
    cats = {}
    for x in w:
        cats[x['type']] = cats.get(x['type'], 0) + 1
    print(f'时点 {a.tp}｜预警 {len(w)} 条｜基线 {os.path.basename(base_p)}｜上一时点 {os.path.basename(prev_p) if prev_p else "无"}')
    for k, v in sorted(cats.items()):
        print(f'  {k}: {v}')
    print(f'双写 {dual}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

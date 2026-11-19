#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞彩三家赔率时点采集（通用版，任意日期/前缀/时点）

用法：
  python3 jczq/collect_tp.py --date 2026-11-17 --prefix 周二 --tp 1130 [--betfa] [--dir work_261117]

产出：
  work_<YYMMDD>/all_<YYYYMMDD>_<HHmm>.json（八位，本窗口）
  老窗口 jczq/all_<YYMMDD>_<HHmm>.json（六位，双写，完整数据版读这里）

P0 新增字段（261119）：
  eu_cons     —— 欧赔共识（odds.xml <europe avg>，经与 okooo「99家平均」逐场比对，同口径）
  rq_cons     —— 让球欧赔共识（odds.xml <rq avg>）
  eu_avg      —— 仍为已采三家均值（wl/lb/bet365），跟共识分开放，不要混用
  fetched_at  —— 真实采集时刻（不是名义时点；tp=1300 可能 13:09 才抓到）
  xml_age_min —— 快照滞后：fetched_at - xml_updatetime（分钟）。>15 说明抓到的是陈旧缓存，
                 此时「频繁采样」是伪高频，不能当新信息用。
  close_gap_h —— 竞彩截止到开赛的小时差（>0 表示截止早于开赛；海外盘在截止后仍在走）

采样序号与差分（261119 新增）：
  seq            —— 当日第 N 次采样（补采点也入序，序号不跳空）
  prev_tp / prev_fetched_at —— 前一次快照的名义时点 / 真实采集时刻
  fields_changed / changed_vs_prev / changes —— 与前一次快照逐字段差分的字段数/布尔/明细
  为 0 时表示本时点无新信息，报告里不得把本时点的值当成"本时点才变成的值"。
"""
import io, re, os, sys, json, glob, argparse, subprocess, datetime, hashlib, xml.etree.ElementTree as ET

BASE_DIR = '/home/user/Doubao/jczq_shared'
WEEKDAY = {'周一': '1', '周二': '2', '周三': '3', '周四': '4', '周五': '5', '周六': '6', '周日': '7'}
OLD_DIR = '/home/user/.doubao/agent_mode/workspace/.sessions/38446277671972610/agents/m_0cwRti7GbEW/jczq'
EU = ['wl', 'lb', 'bet365']
AS = ['am', 'hg', 'bet365']
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36'
BF_KEYS = ['0:0', '0:1', '0:2', '0:3', '0:4', '0:5', '1:0', '1:1', '1:2', '1:3', '1:4', '1:5',
           '2:0', '2:1', '2:2', '2:3', '2:4', '2:5', '3:0', '3:1', '3:2', '3:3', '4:0', '4:1', '4:2',
           '5:0', '5:1', '5:2', '胜其他', '平其他', '负其他']


def rd(fn, enc='gb18030'):
    return io.open(fn, 'rb').read().decode(enc, 'ignore')


def curl(url, out, referer=None):
    cmd = ['curl', '-sL', '--max-time', '30', '-A', UA]
    if referer:
        cmd += ['-H', 'Referer: %s' % referer]
    cmd += [url, '-o', out]
    subprocess.run(cmd, check=False)
    return os.path.exists(out) and os.path.getsize(out) > 0


def fetch_all(W, date, betfa=False, force=False):
    """force=True 时忽略本地缓存强制重抓（事件补采用，避免拿到上一时点的旧页）"""
    os.makedirs(W, exist_ok=True)
    jobs = {
        '354': 'https://trade.500.com/jczq/?playid=354&g=2&vtype=nspf&date=%s' % date,
        '269': 'https://trade.500.com/jczq/?playid=269&vtype=spf&date=%s' % date,
        '270': 'https://trade.500.com/jczq/?playid=270&date=%s' % date,
        '272': 'https://trade.500.com/jczq/?playid=272&date=%s' % date,
    }
    for k, u in jobs.items():
        f = '%s/play_%s.html' % (W, k)
        if force or not (os.path.exists(f) and os.path.getsize(f) > 20000):
            curl(u, f)
    o = W + '/odds.xml'
    if force or not (os.path.exists(o) and os.path.getsize(o) > 3000):
        curl('https://www.500.com/static/public/jczq/xml/odds/odds.xml', o)
    curl('https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry?poolCode=crs&channel=c',
         W + '/crs.json', referer='https://www.sporttery.cn/')
    if betfa:
        curl('https://www.okooo.com/jingcai/shuju/betfa/', W + '/betfa.html')


def probe_once(a, WROOT):
    """事件探测：只抓玩法页判「SP 是否首次非空 / 是否首次可投注」。

    返回 (事件列表, 状态文件路径)。有事件时调用方再走一次完整补采。
    状态落 probe_state_<YYYYMMDD>.json，重跑不会重复触发同一事件。
    设计意图：竞彩投放时刻不固定（提前 24h～当天开售都有），固定钟点必漏真初赔；
    用探测把「首次看见」压到分钟级，只在事件发生时才付一次完整采集的成本。
    """
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    W = '%s/raw_probe_%s' % (WROOT, a.tp)
    os.makedirs(W, exist_ok=True)
    curl('https://trade.500.com/jczq/?playid=354&g=2&vtype=nspf&date=%s' % a.date,
         W + '/play_354.html')
    fx = parse_fixtures(W, a.prefix)
    sp = parse_sp(W, fx)
    stf = '%s/probe_state_%s.json' % (WROOT, a.date.replace('-', ''))
    state = {}
    if os.path.exists(stf):
        try:
            state = json.load(open(stf, encoding='utf-8'))
        except Exception:
            state = {}
    state.setdefault('date', a.date)
    recs = state.setdefault('matches', {})
    ev = []
    for f in fx:
        no = f['no']
        r = recs.setdefault(no, {})
        s = ((sp.get(no) or {}).get('nspf') or [])[:3]
        if s and not r.get('first_sp_at'):
            r['first_sp_at'] = now
            r['first_sp'] = s
            ev.append({'event': 'sp_first', 'no': no, 'sp': s, 'at': now})
        if f['in_sale'] and not r.get('first_sale_at'):
            r['first_sale_at'] = now
            ev.append({'event': 'sale_first', 'no': no, 'at': now})
        r['in_sale'] = f['in_sale']
        r['sp_now'] = s
        r['last_probe'] = now
    state['last_probe_at'] = now
    json.dump(state, open(stf, 'w'), ensure_ascii=False, indent=1)
    print('探测 %s｜%s 场次 %d｜命中事件 %d' % (now, a.prefix, len(fx), len(ev)))
    for e in ev:
        print('  %-11s %s %s' % (e['event'], e['no'], '/'.join(e.get('sp') or [])))
    return ev, stf


def parse_fixtures(W, prefix):
    h = rd(W + '/play_354.html')
    fx = []
    for b in h.split('data-matchnum="')[1:]:
        no = b.split('"')[0]
        if not no.startswith(prefix):
            continue
        lg = re.search(r'title="([^"]+)">([^<]+)</a>', b)
        home = re.search(r'class="team-l" title="([^"]+)"', b)
        away = re.search(r'class="team-r" title="([^"]+)"', b)
        endt = re.search(r'class="td td-endtime"[^>]*>([^<]+)<', b)
        buy = re.search(r'data-buyendtime="([^"]+)"', b)
        rang = re.findall(r'class="(itm-rangA2[^"]*)"[^>]*>\s*([-+]?\d+)', b)
        act = re.search(r'data-isactive="(\d+)"', b)
        end = re.search(r'data-isend="(\d+)"', b)
        fx.append({
            'no': no, 'league': lg.group(2) if lg else '?',
            'home': home.group(1) if home else '?',
            'away': away.group(1) if away else '?',
            'kick': endt.group(1).strip() if endt else '',
            'buyend': buy.group(1) if buy else '',
            'rangqiu': rang[0][1] if rang else '',
            'in_sale': (act.group(1) == '1' if act else False) and (end.group(1) == '0' if end else False),
        })
    return fx


def parse_sp(W, fx):
    pages = {'354': ['nspf', 'spf'], '269': ['spf'], '270': ['jqs'], '272': ['bqc']}
    name = {'jqs': 'jq'}
    sp = {}
    for pid, types in pages.items():
        f = '%s/play_%s.html' % (W, pid)
        if not os.path.exists(f):
            continue
        page = rd(f)
        for blk in page.split('data-matchnum="')[1:]:
            no = blk.split('"')[0]
            if not any(no == x['no'] for x in fx):
                continue
            head = blk.split('<td class="td td-data"')[0]
            for t in types:
                vals = re.findall(r'data-type="%s" data-value="[^"]+" data-sp="([^"]+)"' % t, head)
                if vals:
                    key = name.get(t, t)
                    cur = sp.setdefault(no, {}).setdefault(key, [])
                    if not cur:
                        cur.extend(vals[:3] if key in ('nspf', 'spf') else vals)
    return sp


def parse_odds(W):
    raw = io.open(W + '/odds.xml', 'rb').read()
    mm = re.search(rb'<matches>.*</matches>', raw, re.S)
    xh = hashlib.md5(mm.group(0) if mm else raw).hexdigest()[:12]
    root = ET.parse(W + '/odds.xml').getroot()
    upd = (root.find('head/updatetime').text or '').strip()
    byid = {}
    matches = root.find('matches')
    for m in (matches.findall('match') if matches is not None else []):
        pn = m.get('processname') or ''
        # odds.xml 的 processname 形如 3001：首位=星期（周一1…周日7），后三位=场次号
        # 同一 xml 常含当日+次日预售两套，只按后三位会串场，必须用完整 processname
        if pn.isdigit() and len(pn) >= 4:
            byid[pn] = m
    return upd, byid, xh


def _dt(s):
    """解析 'YYYY-MM-DD HH:MM[:SS]'，失败返回 None"""
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            return datetime.datetime.strptime((s or '').strip(), fmt)
        except Exception:
            pass
    return None


def parse_kick(s):
    """kickoff 形如 '2026-11-19 11-20 06:30'（销售日 + 月-日 时:分），解析为 datetime"""
    m2 = re.match(r'(\d{4})-(\d{2})-(\d{2}) (\d{2})-(\d{2}) (\d{2}):(\d{2})$', (s or '').strip())
    if not m2:
        return None
    try:
        return datetime.datetime(int(m2.group(1)), int(m2.group(4)), int(m2.group(5)),
                                 int(m2.group(6)), int(m2.group(7)))
    except Exception:
        return None


def close_gap_h(kick_off, buyend):
    """竞彩截止 → 开赛 的小时差；数据不全或截止晚于开赛（脏数据）返回 None"""
    k, b = parse_kick(kick_off), _dt(buyend)
    if not (k and b) or k <= b:
        return None
    return round((k - b).total_seconds() / 3600.0, 2)


def buyend_suspect(kick_off, buyend):
    """截售时间晚于开赛 = 页面脏数据，标记出来，不参与口径计算"""
    k, b = parse_kick(kick_off), _dt(buyend)
    return bool(k and b and k <= b)


def parse_crs(W):
    try:
        raw = json.load(open(W + '/crs.json', encoding='utf-8'))
    except Exception:
        return {}, {}
    out, nm = {}, {}
    for m in (raw.get('value', {}) or {}).get('matchInfoList', []) or []:
        for s in m.get('subMatchList', []) or []:
            k = s.get('matchNumStr') or s.get('matchNum')
            out[k] = s.get('crs') or {}
            nm[k] = (s.get('homeTeamAbbName') or '?', s.get('awayTeamAbbName') or '?')
    return out, nm


def build_bf(c):
    def get(k):
        if k == '胜其他':
            return c.get('s1sa') or ''
        if k == '平其他':
            return c.get('s1sd') or ''
        if k == '负其他':
            return c.get('s1sh') or ''
        h, a = k.split(':')
        return c.get('s0%ss0%s' % (h, a)) or ''
    return [[k, get(k)] for k in BF_KEYS]


def parse_betfa(W, fx):
    """按比赛块解析：每块含 <b>周XNNN</b> 与一个 id="table"，表内 3 行主胜/平局/客胜。
    列序：总成交额→冷热指数→市场指数→必发赔率→必发比例→99家平均→竞彩比例→模拟盈亏（空单元格已滤除）。"""
    f = W + '/betfa.html'
    if not os.path.exists(f):
        return {}
    bh = rd(f)
    out = {}
    for blk in re.split(r'<div class="clearfix container_wrapper betfa">', bh)[1:]:
        nos = re.findall(r'<b>(周[一二三四五六日]\d{3})</b>', blk)
        if not nos:
            continue
        no = nos[0]
        tabs = re.findall(r'id="table".*?</table>', blk, re.S)
        if not tabs:
            continue
        rows = []
        for row in re.findall(r'<tr[^>]*>(.*?)</tr>', tabs[0], re.S):
            cells = [re.sub(r'<[^>]+>', '', c).strip()
                     for c in re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)]
            cells = [c for c in cells if c != '']
            if len(cells) >= 9 and cells[0] in ('主胜', '平局', '客胜', '主负'):
                side = {'主胜': 'home', '平局': 'draw', '客胜': 'away', '主负': 'away'}[cells[0]]
                rows.append({'amount': cells[1], 'hot': cells[2], 'market': cells[3],
                             'bf_odds': cells[4], 'bf_pct': cells[5], 'avg99': cells[6],
                             'jc_pct': cells[7], 'pl': cells[8], 'side': side})
        if rows:
            out[no] = rows[:3]
    return out


def flat_fields(r):
    """把单场拍平成 字段→值，用于时点差分。键名与 tp_ledger / tp_minute_watch 对齐，
    保证「时点差分」和「字段级台账」能互相印证。"""
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', required=True, help='销售日期 YYYY-MM-DD')
    ap.add_argument('--prefix', required=True, help='周几，如 周二')
    ap.add_argument('--tp', required=True, help='时点 HHmm')
    ap.add_argument('--betfa', action='store_true')
    ap.add_argument('--dir', default=None)
    ap.add_argument('--old-write', action='store_true', help='兼容遗留：同时双写老窗口目录')
    ap.add_argument('--probe', action='store_true',
                    help='事件探测模式：只查 SP 首现与首次可投注，命中事件才补采一条完整快照')
    a = ap.parse_args()
    yy = a.date[2:4] + a.date[5:7] + a.date[8:10]
    WROOT = a.dir or '%s/work_%s' % (BASE_DIR, yy)
    W = '%s/raw_%s' % (WROOT, a.tp)
    events = []
    if a.probe:
        events, _ = probe_once(a, WROOT)
        if not events:
            print('无事件，不补采（仅更新探测状态）')
            return 0
        print('命中 %d 条事件，执行事件补采（强制重抓）' % len(events))
    fetch_all(W, a.date, a.betfa, force=a.probe)

    fx = parse_fixtures(W, a.prefix)
    sp = parse_sp(W, fx)
    upd, byid, xh = parse_odds(W)
    crs, crs_nm = parse_crs(W)
    bfm = parse_betfa(W, fx) if a.betfa else {}

    # 页面已下架但 odds/比分仍有数据的场次（如当日001）从 crs 补入
    have = {x['no'] for x in fx}
    for no in sorted(crs_nm):
        if no and no.startswith(a.prefix) and no not in have:
            h, aw = crs_nm[no]
            fx.append({'no': no, 'league': '?', 'home': h, 'away': aw, 'kick': '',
                       'buyend': '', 'rangqiu': '', 'in_sale': False})
    fx.sort(key=lambda x: x['no'])

    out = []
    for f in fx:
        wd = WEEKDAY.get(a.prefix)
        m = byid.get('%s%s' % (wd, re.sub(r'^周.', '', f['no'])))
        ko = ('%s %s' % (a.date, f['kick'])) if f['kick'] else ''
        row = {'no': f['no'], 'league': f['league'], 'home': f['home'], 'away': f['away'],
               'kickoff': ko,
               'buyend': f['buyend'], 'rangqiu': f['rangqiu'], 'in_sale': f['in_sale'],
               'close_gap_h': close_gap_h(ko, f['buyend']),
               'src_id': (m.get('id') if m is not None else '')}
        if buyend_suspect(ko, f['buyend']):
            row['buyend_suspect'] = True
        eu_ok = False
        if m is not None:
            eu, asia, dx = m.find('europe'), m.find('asian'), m.find('dxq')
            e = {}
            if eu is not None:
                for k in EU:
                    v = eu.get(k)
                    if v:
                        try:
                            e[k] = [float(x) for x in v.split(',')]
                        except Exception:
                            pass
            row['eu'] = e
            eu_ok = len(e) == 3
            if eu_ok:
                row['eu_avg'] = [round(sum(e[k][i] for k in e) / 3, 3) for i in range(3)]
                row['eu_spread'] = [round(max(e[k][i] for k in e) - min(e[k][i] for k in e), 3)
                                    for i in range(3)]
            # 共识层：xml 自带 <europe avg>、<rq avg>，与三家均值分开放，勿混用
            for el, key in ((eu, 'eu_cons'), (m.find('rq'), 'rq_cons')):
                if el is None:
                    continue
                v = el.get('avg')
                if not v:
                    continue
                try:
                    row[key] = [float(x) for x in v.split(',')][:3]
                except Exception:
                    pass
            av = {}
            if asia is not None:
                for k in AS + ['lb']:
                    v = asia.get(k)
                    if v:
                        p = v.split(',')
                        if len(p) >= 3:
                            try:
                                av[k] = {'up': float(p[0]), 'line': p[1], 'down': float(p[2])}
                            except Exception:
                                pass
            row['asia'] = av
            if dx is not None:
                dv = {}
                for k in ('am', 'bet365'):
                    v = dx.get(k)
                    if v:
                        p = v.split(',')
                        if len(p) >= 3:
                            try:
                                dv[k] = {'over': float(p[0]), 'line': p[1], 'under': float(p[2])}
                            except Exception:
                                pass
                row['dxq'] = dv
        s = sp.get(f['no'], {})
        row['sp'] = {'nspf': (s.get('nspf') or [])[:3], 'spf': (s.get('spf') or [])[:3],
                     'jq': s.get('jq', []), 'bqc': s.get('bqc', []), 'bf': build_bf(crs.get(f['no']) or {})}
        if bfm.get(f['no']):
            row['betfa'] = bfm[f['no']]
        out.append(row)

    fetched = datetime.datetime.now()
    upd_dt = _dt(upd)
    data = {'date': a.date, 'timepoint': a.tp,
            'sampled_at': '%s %s' % (a.date, '%s:%s' % (a.tp[:2], a.tp[2:])),
            'fetched_at': fetched.strftime('%Y-%m-%d %H:%M:%S'),
            'xml_age_min': (round((fetched - upd_dt).total_seconds() / 60.0, 1) if upd_dt else None),
            'xml_hash': xh,
            'xml_updatetime': upd, 'prefix': a.prefix, 'match_count': len(out),
            'source': 'odds.xml + trade.500.com玩法页 + sporttery crs' + (' + okooo必发' if a.betfa else ''),
            'matches': out}
    if events:
        # 事件补采快照：标出这条是哪类事件触发的，与常规时点快照区分
        data['events'] = events
        data['trigger'] = 'probe'

    # ---- 采样序号 + 相对前一时点差分（261119 新增）----
    # 约定：timepoint 是"名义时点"，seq 是"当日第 N 次采样"（补采点也入序，序号不跳空）；
    # 真正的时刻看 fetched_at，前一时点的时刻看 prev_fetched_at。
    # fields_changed = 与前一快照逐字段比对后的变更字段数；为 0 说明本时点无新信息，
    # 报告里不得把"本时点的值"当成"本时点才变成的值"。
    day8 = a.date.replace('-', '')
    others = []
    for x in glob.glob('%s/all_%s_*.json' % (WROOT, day8)):
        if x.endswith('_%s.json' % a.tp):
            continue
        try:
            d2 = json.load(open(x, encoding='utf-8'))
        except Exception:
            continue
        if d2.get('timepoint'):
            others.append(d2)
    others.sort(key=lambda d: d['timepoint'])
    data['seq'] = 1 + sum(1 for d2 in others if d2['timepoint'] < a.tp)
    prev = [d2 for d2 in others if d2['timepoint'] < a.tp]
    prev = prev[-1] if prev else None
    n_changed = None
    changed_list = []
    if prev:
        pidx = {m.get('no'): flat_fields(m) for m in (prev.get('matches') or [])}
        n_changed = 0
        for r in out:
            pf, f = pidx.get(r['no']), flat_fields(r)
            if not pf:
                n_changed += len(f)
                changed_list.append({'no': r['no'], 'key': '*', 'from': None, 'to': 'new'})
                continue
            for k, v in f.items():
                if pf.get(k) != v:
                    n_changed += 1
                    changed_list.append({'no': r['no'], 'key': k, 'from': pf.get(k), 'to': v})
    data['prev_tp'] = prev.get('timepoint') if prev else None
    data['prev_fetched_at'] = prev.get('fetched_at') if prev else None
    data['fields_changed'] = n_changed
    data['changed_vs_prev'] = (n_changed > 0) if n_changed is not None else None
    data['changes'] = changed_list

    p1 = '%s/all_%s_%s.json' % (WROOT, a.date.replace('-', ''), a.tp)
    os.makedirs(os.path.dirname(p1), exist_ok=True)
    json.dump(data, open(p1, 'w'), ensure_ascii=False, indent=1)
    if a.old_write:
        p2 = os.path.join(OLD_DIR, 'all_%s_%s.json' % (yy, a.tp))
        dual = 'OK'
        try:
            os.makedirs(OLD_DIR, exist_ok=True)
            json.dump(data, open(p2, 'w'), ensure_ascii=False, indent=1)
        except Exception as ex:
            dual = 'FAIL:%s' % ex
    else:
        p2 = '（双写已退役，仅共享根）'
        dual = 'SKIP'

    print('时点 %s｜序号 S%02d｜场次 %d｜xml updatetime %s' % (a.tp, data['seq'], len(out), upd))
    print('真实采集 %s｜滞后 %s 分｜前一时点 %s（%s）｜变更 %s 字段%s' % (
        data['fetched_at'],
        ('%.1f' % data['xml_age_min']) if data['xml_age_min'] is not None else '-',
        data['prev_tp'] or '无', data['prev_fetched_at'] or '无',
        data['fields_changed'] if data['fields_changed'] is not None else '-',
        '' if data['fields_changed'] else '（本时点无新信息）'))
    ncons = sum(1 for r in out if r.get('eu_cons'))
    nsp = sum(1 for r in out if r.get('sp', {}).get('nspf'))
    gaps = [r['close_gap_h'] for r in out if r.get('close_gap_h') is not None]
    print('共识 eu_cons %d/%d｜有SP %d/%d｜xml滞后 %s 分｜截止-开赛 %s h' % (
        ncons, len(out), nsp, len(out),
        ('%.1f' % data['xml_age_min']) if data['xml_age_min'] is not None else '-',
        ('%.2f~%.2f' % (min(gaps), max(gaps))) if gaps else '-'))
    print('本窗口 %s' % p1)
    print('双写 %s %s' % (dual, p2))
    for r in out:
        nbf = sum(1 for k, v in r['sp']['bf'] if v)
        print('  %s %s vs %s｜eu365 %s｜亚盘 %s｜SP nspf%d jq%d bqc%d｜bf项%d%s' % (
            r['no'], r['home'], r['away'],
            (r.get('eu') or {}).get('bet365'), ((r.get('asia') or {}).get('bet365') or {}).get('line'),
            len(r['sp']['nspf']), len(r['sp']['jq']), len(r['sp']['bqc']), nbf,
            '' if r['in_sale'] else '（已停售）'))
    return 0


if __name__ == '__main__':
    sys.exit(main())

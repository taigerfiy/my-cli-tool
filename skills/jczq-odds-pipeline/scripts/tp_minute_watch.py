#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""临场分钟级盯盘：只抓 odds.xml（约 9KB）+ 可选 SP 页，按时点差分记录「真实变化」。

为什么能分钟级：上游内容实测约 15 次/小时变更（平均 4 分钟一变），
1 分钟采样即可完整覆盖，不会漏变化，也不会产生大量重复点。

产出：
  minute_<YYYYMMDD>_<HHMM>.jsonl   一行一次采样；只在有变化时写 changed/delta
  minute_<YYYYMMDD>_<HHMM>.md      收尾汇总（每场变化次数、净位移、最大单跳）

用法：
  python3 tp_minute_watch.py --date 2026-11-19 --prefix 周四 --start-tp 2100 \
      --minutes 60 --interval 60 --sp-every 5 --dir /home/user/Doubao/jczq_shared/work_261119

  # 分片跑（每次触发只覆盖 5 分钟，避免单进程长跑）
  python3 tp_minute_watch.py ... --minutes 5 --interval 60

注意：本脚本默认不上线，属单场专项排查工具。临场采样以 19:00→21:00 每半小时一个点为准。
"""
import io, os, re, sys, json, time, argparse, subprocess, datetime
import xml.etree.ElementTree as ET

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36'
XML_URL = 'https://www.500.com/static/public/jczq/xml/odds/odds.xml'
EU = ['wl', 'lb', 'bet365']
AS = ['am', 'hg', 'bet365', 'lb']


def curl(url, out, referer=None):
    cmd = ['curl', '-sL', '--max-time', '20', '-A', UA]
    if referer:
        cmd += ['-H', 'Referer: %s' % referer]
    cmd += [url, '-o', out]
    subprocess.run(cmd, check=False)
    return os.path.exists(out) and os.path.getsize(out) > 2000


def parse_xml(path, prefix, wd):
    """返回 (updatetime, {no: 扁平字段字典})，只取当前前缀场次。"""
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return '', {}
    upd = ''
    h = root.find('head')
    if h is not None and h.find('updatetime') is not None:
        upd = (h.find('updatetime').text or '').strip()
    out = {}
    ms = root.find('matches')
    for m in (ms.findall('match') if ms is not None else []):
        pn = m.get('processname') or ''
        if not (pn.isdigit() and len(pn) >= 4 and pn[0] == wd):
            continue
        no = prefix + pn[1:].zfill(3)
        f = {}
        eu, asia, dx, rq = m.find('europe'), m.find('asian'), m.find('dxq'), m.find('rq')
        if eu is not None:
            for k in EU:
                v = eu.get(k)
                if v:
                    try:
                        vals = [float(x) for x in v.split(',')][:3]
                        for i, x in enumerate(vals):
                            f['eu.%s.%d' % (k, i)] = x
                    except Exception:
                        pass
            if eu.get('avg'):
                try:
                    for i, x in enumerate([float(y) for y in eu.get('avg').split(',')][:3]):
                        f['eucons.%d' % i] = x
                except Exception:
                    pass
        if rq is not None and rq.get('avg'):
            try:
                for i, x in enumerate([float(y) for y in rq.get('avg').split(',')][:3]):
                    f['rqcons.%d' % i] = x
            except Exception:
                pass
        if asia is not None:
            for k in AS:
                v = asia.get(k)
                if v:
                    p = v.split(',')
                    if len(p) >= 3:
                        try:
                            f['as.%s.up' % k] = float(p[0]); f['as.%s.down' % k] = float(p[2])
                        except Exception:
                            pass
                        f['as.%s.line' % k] = p[1]
        if dx is not None:
            for k in ('am', 'bet365'):
                v = dx.get(k)
                if v:
                    p = v.split(',')
                    if len(p) >= 3:
                        try:
                            f['dx.%s.over' % k] = float(p[0]); f['dx.%s.under' % k] = float(p[2])
                        except Exception:
                            pass
                        f['dx.%s.line' % k] = p[1]
        if f:
            out[no] = f
    return upd, out


def diff(prev, cur):
    """返回 [{no, key, from, to}]，键级差分。"""
    out = []
    for no, f in cur.items():
        pf = prev.get(no) or {}
        for k, v in f.items():
            if k not in pf:
                if prev:
                    out.append({'no': no, 'key': k, 'from': None, 'to': v})
                continue
            if pf[k] != v:
                out.append({'no': no, 'key': k, 'from': pf[k], 'to': v})
        for k in pf:
            if k not in f:
                out.append({'no': no, 'key': k, 'from': pf[k], 'to': None})
    return out


def parse_sp(path, prefix):
    """从玩法页取 胜平负 SP（3 值）"""
    if not path or not os.path.exists(path):
        return {}
    try:
        h = io.open(path, 'rb').read().decode('gb18030', 'ignore')
    except Exception:
        return {}
    res = {}
    for blk in h.split('data-matchnum="')[1:]:
        no = blk.split('"')[0]
        if not no.startswith(prefix):
            continue
        head = blk.split('<td class="td td-data"')[0]
        vals = re.findall(r'data-type="nspf" data-value="[^"]+" data-sp="([^"]+)"', head)
        if vals:
            res[no] = vals[:3]
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', required=True)
    ap.add_argument('--prefix', required=True)
    ap.add_argument('--start-tp', required=True, help='窗口起点 HHMM，只用于产出文件命名')
    ap.add_argument('--minutes', type=int, default=5, help='本进程持续分钟数')
    ap.add_argument('--interval', type=int, default=60, help='采样间隔秒数')
    ap.add_argument('--sp-every', type=int, default=5, help='每 N 次采样抓一次 SP 页（0=不抓）')
    ap.add_argument('--dir', default=None)
    a = ap.parse_args()

    WD = {'周一': '1', '周二': '2', '周三': '3', '周四': '4', '周五': '5', '周六': '6', '周日': '7'}[a.prefix]
    yy = a.date[2:4] + a.date[5:7] + a.date[8:10]
    WROOT = a.dir or '/home/user/Doubao/jczq_shared/work_%s' % yy
    os.makedirs(WROOT, exist_ok=True)
    tag = '%s_%s' % (a.date.replace('-', ''), a.start_tp)
    jf = '%s/minute_%s.jsonl' % (WROOT, tag)
    tmp = '/tmp/_w_%s.xml' % tag

    prev, prev_sp = {}, {}
    n_change = 0
    stats = {}
    end_at = datetime.datetime.now() + datetime.timedelta(minutes=a.minutes)
    i = 0
    while datetime.datetime.now() < end_at:
        ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        if not curl(XML_URL, tmp):
            print('%s 抓取失败，跳过' % ts)
            time.sleep(a.interval)
            continue
        upd, cur = parse_xml(tmp, a.prefix, WD)
        ch = diff(prev, cur) if prev else []
        sp_ch = []
        if a.sp_every and i % a.sp_every == 0:
            spf = '%s/play_354_sp.html' % WROOT
            if curl('https://trade.500.com/jczq/?playid=354&g=2&vtype=nspf&date=%s' % a.date, spf):
                cur_sp = parse_sp(spf, a.prefix)
                for no, v in cur_sp.items():
                    if no in prev_sp and prev_sp[no] != v:
                        sp_ch.append({'no': no, 'key': 'sp.nspf', 'from': prev_sp[no], 'to': v})
                prev_sp.update(cur_sp)
        for e in ch:
            key = (e['no'], e['key'].rsplit('.', 1)[0] if e['key'].count('.') >= 1 else e['key'])
            s = stats.setdefault(key, {'n': 0, 'first': e['from'], 'last': e['to'], 'max_jump': 0.0, 'at': ts})
            s['n'] += 1
            s['last'] = e['to']
            s['at'] = ts
            if isinstance(e['from'], (int, float)) and isinstance(e['to'], (int, float)):
                s['max_jump'] = max(s['max_jump'], abs(e['to'] - e['from']))
        if ch or sp_ch:
            rec = {'ts': ts, 'xml_upd': upd, 'changed': ch, 'sp_changed': sp_ch}
            with open(jf, 'a', encoding='utf-8') as fp:
                fp.write(json.dumps(rec, ensure_ascii=False) + '\n')
            n_change += 1
            print('%s 变化 %d 项%s' % (ts, len(ch) + len(sp_ch),
                                       '' if not ch else '（%s）' % ','.join(sorted({e['no'] for e in ch}))))
        prev = cur
        i += 1
        if datetime.datetime.now() < end_at:
            time.sleep(a.interval)

    ms = '%s/minute_%s.md' % (WROOT, tag)
    with open(ms, 'w', encoding='utf-8') as fp:
        fp.write('# 临场分钟级盯盘 %s %s 起 %d 分钟\n\n' % (a.date, a.start_tp, a.minutes))
        fp.write('- 采样 %d 次 / 间隔 %ds｜有变化的采样 %d 次｜覆盖场次 %d\n\n' % (i, a.interval, n_change, len(prev)))
        fp.write('| 场次 | 字段 | 变化次数 | 首值 | 末值 | 最大单跳 | 最后变化 |\n|---|---|---|---|---|---|---|\n')
        for (no, key), s in sorted(stats.items()):
            fp.write('| %s | %s | %d | %s | %s | %s | %s |\n' % (
                no, key, s['n'], s['first'], s['last'],
                ('%.2f' % s['max_jump']) if s['max_jump'] else '-', s['at'][11:]))
    print('采样 %d 次｜有变化 %d 次｜场次 %d' % (i, n_change, len(prev)))
    print('明细 %s' % jf)
    print('汇总 %s' % ms)
    return 0


if __name__ == '__main__':
    sys.exit(main())

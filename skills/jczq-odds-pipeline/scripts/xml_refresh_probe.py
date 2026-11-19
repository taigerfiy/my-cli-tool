#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探测 odds.xml 的真实刷新粒度（决定采样阶梯的步长下限）

背景（261119 实测）：
  <updatetime> 约每 60 秒推进一次，但内容可能完全没变 —— 它更像"文件重新生成时间"，
  不是"价格变更时间"。所以不能用 updatetime 判断滞后，必须比对内容哈希。
  另观察到个别节点返回旧版本，updatetime 会倒退 —— 采样时需识别并丢弃乱序点。

用法：
  python3 xml_refresh_probe.py --minutes 20 --interval 20 [--out probe.json]

输出：
  upd_gap       —— updatetime 推进间隔（≈文件重生成节奏）
  content_gap   —— 内容真实变更间隔（这才是信息到达频率，阶梯步长的下限依据）
  rewind        —— updatetime 倒退次数（节点缓存不一致）
  changed_matches —— 每次内容变更涉及哪些场次
"""
import argparse, json, re, subprocess, time, datetime, hashlib, statistics

URL = 'https://www.500.com/static/public/jczq/xml/odds/odds.xml'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36'


def fetch():
    r = subprocess.run(['curl', '-s', '--max-time', '20', '-A', UA, URL],
                       capture_output=True, text=True)
    s = r.stdout or ''
    if not s:
        return None, None, None
    m_upd = re.search(r'<updatetime>([^<]+)', s)
    upd = m_upd.group(1).strip() if m_upd else None
    # 只对"场次内容"取哈希，避开 head/updatetime 自身
    m = re.search(r'<matches>.*</matches>', s, re.S)
    body = m.group(0) if m else s
    return upd, hashlib.md5(body.encode()).hexdigest()[:12], body


def _gaps(times):
    out = []
    for x, y in zip(times, times[1:]):
        out.append(round((y - x).total_seconds(), 1))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--minutes', type=float, default=20)
    ap.add_argument('--interval', type=float, default=20)
    ap.add_argument('--out', default=None)
    a = ap.parse_args()

    n = max(3, int(a.minutes * 60 / a.interval))
    rows = []
    for i in range(n):
        t = datetime.datetime.now()
        upd, h, body = fetch()
        rows.append({'t': t.strftime('%Y-%m-%d %H:%M:%S'), 'upd': upd, 'hash': h})
        if i < n - 1:
            time.sleep(a.interval)

    ok = [r for r in rows if r['hash']]
    # updatetime 推进事件
    upd_ev = [r['t'] for i, r in enumerate(ok) if i == 0 or r['upd'] != ok[i - 1]['upd']]
    # 内容变更事件
    hs = [r['hash'] for r in ok]
    cont_ev_idx = [i for i, h in enumerate(hs) if i == 0 or h != hs[i - 1]]
    cont_t = [datetime.datetime.strptime(ok[i]['t'], '%Y-%m-%d %H:%M:%S') for i in cont_ev_idx]
    # updatetime 倒退
    rew = 0
    for i in range(1, len(ok)):
        try:
            p = datetime.datetime.strptime(ok[i - 1]['upd'], '%Y-%m-%d %H:%M:%S')
            c = datetime.datetime.strptime(ok[i]['upd'], '%Y-%m-%d %H:%M:%S')
            if c < p:
                rew += 1
        except Exception:
            pass

    def stats(g):
        return ({'min': min(g), 'median': statistics.median(g), 'max': max(g)} if g else None)

    res = {
        'probed_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'window_min': a.minutes, 'interval_s': a.interval, 'samples': len(ok),
        'upd_advance_events': len(upd_ev), 'upd_gap_s': stats(_gaps([datetime.datetime.strptime(x, '%Y-%m-%d %H:%M:%S') for x in upd_ev])),
        'content_versions': len(set(hs)), 'content_change_events': len(cont_ev_idx),
        'content_gap_s': stats(_gaps(cont_t)),
        'content_change_rate_per_h': (round(len(cont_ev_idx) / max(a.minutes, 1e-9) * 60, 2)),
        'upd_rewind_count': rew,
        'effective_resolution_s': (statistics.median(_gaps(cont_t)) if len(cont_t) > 1 else None),
    }
    print(json.dumps(res, ensure_ascii=False, indent=1))
    if res['effective_resolution_s']:
        print('结论：这批场次价格真实变更间隔中位 %s 秒，采样步长不应低于该值；'
              'updatetime 每 %s 秒推进一次但与内容无关，不能当滞后指标。' % (
                  res['effective_resolution_s'],
                  (res['upd_gap_s'] or {}).get('median', '-')))
    if rew:
        print('警告：探测期内出现 %d 次 updatetime 倒退（节点缓存不一致），时间线需按内容去重排序。' % rew)
    if a.out:
        json.dump({'summary': res, 'samples': rows}, open(a.out, 'w'), ensure_ascii=False, indent=1)
        print('明细 %s' % a.out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

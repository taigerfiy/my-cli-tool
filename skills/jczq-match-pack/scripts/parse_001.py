#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""001 赛前文档解析器（7表格 + 历史战绩 → JSON）

输入：001 文件夹里的两份 PDF（7表格、历史战绩），或已转好的 Markdown 文本。
输出：{"schema":"001doc-v1","date":...,"matches":[{no,lg,home,away,odds0,...}]}
供 jczq-match-pack 的 --doc 使用；Base1 不存赛前数据，本文件就是赛前文档层的入口。

用法：
  python3 parse_001.py --tables 261117_7tables.pdf --history 261117_history.pdf \
      --date 2026-11-17 --out 261117_data.json
"""
import argparse, json, re, sys
from datetime import datetime

try:
    import pymupdf
except ImportError:
    pymupdf = None


def all_text(path):
    if path.lower().endswith(('.md', '.txt')):
        return open(path, encoding='utf-8').read()
    if pymupdf is None:
        raise SystemExit('未安装 pymupdf：pip install pymupdf，或改用 .md 输入')
    d = pymupdf.open(path)
    return '\n'.join(p.get_text('text', sort=True) for p in d)


def split_tables(text):
    idx = [m.start() for m in re.finditer(r'表格\d：', text)]
    idx.append(len(text))
    out = {}
    for i in range(len(idx) - 1):
        block = text[idx[i]:idx[i + 1]]
        n = int(re.match(r'表格(\d)：', block).group(1))
        out[n] = block
    return out


def rows_of(block):
    pos = [(m.start(), m.group(1)) for m in re.finditer(r'周二(\d{3})', block)]
    out = {}
    for i, (p, n) in enumerate(pos):
        end = pos[i + 1][0] if i + 1 < len(pos) else len(block)
        line = re.sub(r'\s+', ' ', block[p:end]).strip()
        line = re.sub(r'^(周二\d{3})\s+\1\s+', r'\1 ', line)
        out['周二' + n] = line
    return out


DATE_RE = r'(\d{4}-\d{2}-\d{2})'
BASE_RE = r'周二\d{3} \d{4}-\d{2}-\d{2} (\S+) (\S+) vs (\S+) ' + DATE_RE + r' (\d{2}:\d{2}) (.*)$'
BASE_GENERIC = r'周二\d{3} \d{4}-\d{2}-\d{2} (\S+) (\S+) (\S+) ' + DATE_RE + r' (\d{2}:\d{2}) (.*)$'


def parse_t1(block):
    res = {}
    for no, line in rows_of(block).items():
        m = re.match(BASE_RE, line)
        if not m:
            print('T1 解析失败', no, line[:80])
            continue
        lg, home, away, date, tm, rest = m.groups()
        rest = re.sub(r'([+-]\d+)\s+球：', r'\1球：', rest)
        mh = re.search(r'([+-]\d+球)：?', rest)
        if mh:
            hcap = mh.group(1)
            rest2 = rest[:mh.start()].strip()
            hcap_txt = rest[mh.start():].strip()
        else:
            hcap, rest2, hcap_txt = None, rest.strip(), None
        sp = re.findall(r'\d\.\d+', rest2)
        odds0 = sp[:3] if len(sp) >= 3 else []
        sph = re.findall(r'\d\.\d+', hcap_txt or '')
        res[no] = {'lg': lg, 'home': home, 'away': away, 'date': date, 'time': tm,
                   'hcap': hcap, 'odds0': odds0, 'odds_h': sph[:3]}
    return res


def merge_tokens(toks):
    out = []
    for t in toks:
        if out and t == '次/球' and re.fullmatch(r'[0-9.]+', out[-1]):
            out[-1] += '次/球'
        elif out and (out[-1].endswith('次/') or out[-1].endswith('亿') or out[-1].endswith('万')) and t in ('球', '欧'):
            out[-1] += t
        elif t == '':
            continue
        else:
            out.append(t)
    return out


def parse_generic(block, n_fields, label):
    res = {}
    for no, line in rows_of(block).items():
        m = re.match(BASE_GENERIC, line)
        if not m:
            print(label, '解析失败', no, line[:80])
            continue
        lg, home, away, date, tm, rest = m.groups()
        vals = merge_tokens(rest.split(' '))
        if len(vals) != n_fields:
            print('%s 列数异常 %s: 期望%d 实得%d -> %s' % (label, no, n_fields, len(vals), vals))
        res[no] = {'lg': lg, 'home': home, 'away': away, 'date': date, 'time': tm, 'v': vals}
    return res


def parse_history(text, date):
    hs = {}
    secs = [(m.start(), m.group(1), m.group(2), m.group(3))
            for m in re.finditer(r'##\s*🏆\s*(周二\d{3})：(\S+?) vs (\S+)', text)]
    for i, (p, no, home, away) in enumerate(secs):
        end = secs[i + 1][0] if i + 1 < len(secs) else len(text)
        block = text[p:end]
        block = re.sub(r'\n(?!\|\s*\*\*|\|\s*:|\n#)', '', block)
        rec = {'home': home.strip(), 'away': away.strip()}

        def cell(label):
            m = re.search(r'\|\s*\*\*%s\*\*\s*\|([^\n]*)$' % re.escape(label), block, re.M)
            if not m:
                return ['', '']
            raw = m.group(1).rstrip()
            cells = [c.strip().replace('\\|', '｜') for c in re.split(r'(?<!\\)\|', raw)]
            if cells and cells[-1] == '':
                cells.pop()
            while len(cells) < 2:
                cells.append('')
            return cells

        def clean(s):
            s = (s or '').replace('**', '').strip()
            s = re.sub(r'\*\(.*?\)\*', '', s).strip()
            return s

        rec['venue_weather'] = clean(cell('比赛信息')[0])
        rec['rank'] = [clean(x) for x in cell('积分排名')]
        rec['h2h'] = [clean(x) for x in cell('历史交锋-近10场')]
        rec['h2h_raw_away'] = cell('历史交锋-近10场')[1]
        notes = re.findall(r'\*\((.*?)\)\*', rec['h2h_raw_away'])
        rec['h2h_note'] = notes[0] if notes else ''
        rec['form'] = [clean(x) for x in cell('近期战绩-近10场')]
        rec['fitness'] = [clean(x) for x in cell('赛程体能')]
        hs[no] = rec
    return hs


def parse_record(s):
    if not s or '暂无' in s:
        return None
    m = re.search(r'\[(\d+)胜(\d+)平(\d+)负\]', s)
    if not m:
        return None
    w, d, l = map(int, m.groups())
    r = {'w': w, 'd': d, 'l': l}
    for key, pat in (('rate', r'胜率(\d+)%'), ('winrate', r'赢率(\d+)%'), ('big', r'大球(\d+)%'),
                     ('gf', r'进(\d+)失'), ('ga', r'失(\d+)')):
        mm = re.search(pat, s)
        if mm:
            r[key] = int(mm.group(1))
    mm = re.search(r'实际记录(\d+)场', s)
    if mm:
        r['actual_n'] = int(mm.group(1))
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tables', required=True, help='7表格 PDF/MD')
    ap.add_argument('--history', required=True, help='历史战绩 PDF/MD')
    ap.add_argument('--date', required=True, help='销售日期 YYYY-MM-DD')
    ap.add_argument('--out', required=True)
    a = ap.parse_args()

    t7 = all_text(a.tables)
    tabs = split_tables(t7)
    missing = [i for i in range(1, 8) if i not in tabs]
    if missing:
        raise SystemExit('7表格缺表：%s' % missing)
    t1 = parse_t1(tabs[1])
    t2 = parse_generic(tabs[2], 4, 'T2')
    t3 = parse_generic(tabs[3], 8, 'T3')
    t4 = parse_generic(tabs[4], 6, 'T4')
    t5 = parse_generic(tabs[5], 8, 'T5')
    t6 = parse_generic(tabs[6], 10, 'T6')
    t7b = parse_generic(tabs[7], 4, 'T7')

    hs = parse_history(all_text(a.history), a.date)
    nos = sorted(set(t1) & set(hs))
    if not nos:
        raise SystemExit('7表格与历史战绩没有可对齐的编号')

    matches = []
    for no in nos:
        d = {'no': no}
        d.update(t1[no])
        for key, src in (('t2', t2), ('t3', t3), ('t4', t4), ('t5', t5), ('t6', t6), ('t7', t7b)):
            if no not in src:
                raise SystemExit('%s 缺 %s' % (no, key))
            d[key] = src[no]['v']
            if src[no]['home'] != t1[no]['home'] or src[no]['away'] != t1[no]['away']:
                raise SystemExit('%s %s 队名不一致' % (no, key))
        h = hs[no]
        if h['home'] != t1[no]['home'] or h['away'] != t1[no]['away']:
            raise SystemExit('%s 历史战绩队名不一致' % no)
        d['hist'] = h
        d['hist_form'] = [parse_record(x) for x in h['form']]
        d['hist_h2h'] = [parse_record(x) for x in h['h2h']]
        matches.append(d)

    payload = {
        'schema': '001doc-v1',
        'date': a.date,
        'tables_file': a.tables,
        'history_file': a.history,
        'parsed_at': datetime.now().astimezone().isoformat(timespec='seconds'),
        'matches': matches,
    }
    json.dump(payload, open(a.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('wrote %s｜%d 场｜%s' % (a.out, len(matches), '、'.join(m['no'] for m in matches)))
    return 0


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PDF -> Markdown 转换器（v3）
按"表"粒度决定处理方式：
 - 该表内出现 markdown 管道行 => pipe 模式：仅把 PDF 断行拼回整行
 - 否则 => plain 模式：按固定前缀 + 列数还原 GFM 表格
保真：数字多重集必须等于 PDF 原文。
"""
import re, sys, subprocess

TAB_RE = re.compile(r'^表格\s*\d')
ROW_RE = re.compile(r'^周[一二三四五六日]\d{3}')
HDR_RE = re.compile(r'^竞彩场次序号')

def pdf_text(pdf):
    return subprocess.run(['pdftotext', '-layout', pdf, '-'], capture_output=True).stdout.decode('utf-8')

def norm_ws(s):
    s = re.sub(r'次\s*/\s*球', '次/球', s)
    s = re.sub(r'([+-]?\d+)\s*球\s*[：:]', r'\1球：', s)
    s = re.sub(r'(万|亿)\s*欧', r'\1欧', s)
    s = re.sub(r'\s+', ' ', s).strip()
    s = re.sub(r'（\s+', '（', s); s = re.sub(r'\s+）', '）', s)
    return s

# ---------- 分段 ----------
def sections(raw):
    out = []; cur = None; buf = []
    for ln in raw.split('\n'):
        s = ln.strip()
        if TAB_RE.match(s):
            if cur is not None or buf:
                out.append((cur, buf))
            cur = s; buf = []
        else:
            buf.append(ln)
    out.append((cur, buf))
    return out

def is_pipe_section(buf):
    for ln in buf:
        s = ln.strip()
        if s.startswith('|') and s.count('|') >= 3:
            return True
    return False

# ---------- pipe 模式 ----------
NEW = re.compile(r'^(#|\||---+$|[-*]\s|\d+[.、]|表格\s*\d|26\d{6}|[（(]?[一二三四五六七八九十]+[）)]|\*\*)')
def pipe_convert(buf):
    out = []; no_append = True
    for ln in buf:
        s = ln.strip()
        if not s:
            if out and out[-1] != '': out.append('')
            no_append = True
            continue
        if NEW.match(s):
            out.append(s)
            no_append = bool(re.match(r'^(#|---)', s))
        else:
            if out and out[-1] and not no_append:
                pc = out[-1][-1]; nc = s[0]
                cjk = lambda c: '\u4e00' <= c <= '\u9fff' or c in '，。、；：（）【】“”‘’'
                sep = '' if (cjk(pc) and cjk(nc)) else ' '
                out[-1] = out[-1] + sep + s
            else:
                out.append(s)
                no_append = False
    txt = '\n'.join(out)
    txt = re.sub(r'\n{3,}', '\n\n', txt)
    return txt

# ---------- plain 模式 ----------
PREF_VS = re.compile(r'^(周[一二三四五六日]\d{3})\s+(\d{4}-\d{2}-\d{2})\s+(.*?)\s+(.+?)\s+vs\s+(.+?)\s+(\d{4}-\d{2}-\d{2})\s+(\d{1,2}:\d{2})\s*(.*)$')
PREF = re.compile(r'^(周[一二三四五六日]\d{3})\s+(\d{4}-\d{2}-\d{2})\s+(.*?)\s+(\d{4}-\d{2}-\d{2})\s+(\d{1,2}:\d{2})\s*(.*)$')
BALL = re.compile(r'(该玩法暂未开[售]?|[+-]?\d+球：)')

def parse_vs(v):
    m = PREF_VS.match(v)
    if not m: return None
    no, dt, ev, home, away, dt2, tm, rest = m.groups()
    rest = norm_ws(rest); h0, other = rest, ''
    mm = BALL.search(rest)
    if mm:
        if mm.group(1).startswith('该玩法暂未开'):
            after = rest[mm.end():]; m2 = BALL.search(after)
            if m2:
                cut = mm.end() + m2.start(); h0, other = rest[:cut], rest[cut:]
            else: h0, other = rest, ''
        else:
            h0, other = rest[:mm.start()], rest[mm.start():]
    return [no, dt, ev, home, away, f'{dt2} {tm}', h0.strip(), other.strip()]

UNIT = {'次/球', '%', '万欧', '亿欧'}
FEN = re.compile(r'^\d+[胜平负]')
FENFULL = re.compile(r'\d+胜\d+平\d+负')
NUM = re.compile(r'^\d+(?:\.\d+)?$')

def repair(tokens):
    """把被 PDF 换行拆散的单位片段合回同一个数值"""
    out = []
    for t in tokens:
        if out:
            prev = out[-1]
            if t in UNIT or t.startswith('次/球'):
                if NUM.match(prev) or prev.endswith(('次/', '%', '万', '亿')):
                    out[-1] = prev + t; continue
            if FENFULL.fullmatch(prev + t):
                out[-1] = prev + t; continue
            if NUM.match(t) and prev.endswith(('次/', '万', '亿')):
                out[-1] = prev + t; continue
        out.append(t)
    return out

def parse_gen(v):
    m = PREF.match(v)
    if not m: return None
    no, dt, mid, dt2, tm, rest = m.groups()
    parts = norm_ws(mid).split(' ')
    if len(parts) < 3: return None
    return [no, dt, ' '.join(parts[:-2]), parts[-2], parts[-1], f'{dt2} {tm}'], repair(norm_ws(rest).split(' '))

FIXED = ['竞彩场次序号', '订单日期', '赛事类型', '主队', '客队', '比赛日期与时间']

PROSE = re.compile(r'(。$|^至此|^注|^说明|^备注|^数据来源|^本表)')

def plain_convert(title, buf):
    hdr_segs = []; rows = []; cur = None; notes = []
    for ln in buf:
        s = ln.strip()
        if not s:
            if cur: rows.append(cur); cur = None
            continue
        if HDR_RE.match(s):
            if cur: rows.append(cur); cur = None
            hdr_segs = [s]; continue
        if ROW_RE.match(s):
            if cur: rows.append(cur)
            cur = [s]; continue
        if re.fullmatch(r'-{3,}', s) or s == '\x0c':
            if cur: rows.append(cur); cur = None
            continue
        if PROSE.match(s) and not ROW_RE.match(s):
            if cur: rows.append(cur); cur = None
            notes.append(s); continue
        if cur is not None: cur.append(s)
        elif hdr_segs: hdr_segs.append(s)
    if cur: rows.append(cur)

    is_vs = ('球：' in norm_ws(''.join(hdr_segs))) or any('vs' in ''.join(r) for r in rows[:3])
    recs = []
    for segs in rows:
        sp, jn = ' '.join(segs), ''.join(segs)
        best = None
        for v in (sp, jn):
            if is_vs:
                r = parse_vs(v)
                if r: best = (r, 8); break
            else:
                r = parse_gen(v)
                if r: best = (r[0] + r[1], 6 + len(r[1])); break
        recs.append(best if best else ([norm_ws(jn)], None))
    counts = [c for _, c in recs if c]
    if not counts:
        body = ['- ' + r[0][0] for r in recs]
        return [f'## {title}', ''] + body + [''], (title, None, len(recs), len(recs))
    ncols = max(set(counts), key=counts.count)
    out_rows = []
    for cells, c in recs:
        if c is None: cells = cells + [''] * (ncols - 1)
        elif len(cells) < ncols: cells = cells + [''] * (ncols - len(cells))
        elif len(cells) > ncols: cells = cells[:ncols - 1] + [' '.join(cells[ncols - 1:])]
        out_rows.append(cells)
    names = None
    for cand in (norm_ws(' '.join(hdr_segs)).split(' '), norm_ws(''.join(hdr_segs)).split(' ')):
        if cand and len(cand) == ncols: names = cand; break
    if names is None and '赔率汇总' in title and ncols == 8:
        names = FIXED + ['让球0（胜/平/负）', '其它让球（胜/平/负）']
    if names is None:
        names = FIXED + [f'字段{i}' for i in range(7, ncols + 1)]
    md = [f'## {title}', '', '| ' + ' | '.join(names) + ' |',
          '| ' + ' | '.join([':---'] * len(names)) + ' |']
    for r in out_rows:
        md.append('| ' + ' | '.join((x if x not in ('', None) else '-') for x in r) + ' |')
    md.append('')
    for nt in notes:
        md += [f'> {nt}', '']
    nbad = sum(1 for _, c in recs if c is None or c != ncols)
    return md, (title, ncols, nbad, len(recs))

def convert(raw):
    md = []; stats = []
    secs = sections(raw)
    if not any(t for t, _ in secs):
        body = pipe_convert(secs[0][1]).split('\n')
        body = [l for l in body if l.strip()]
        if body and not body[0].startswith('#'):
            body[0] = '# ' + body[0]
        return '\n'.join(body).strip() + '\n', [(None, 'doc', 0, len(body))]
    pre = secs[0]
    if pre[0]:
        md += [f'# {pre[0]}', '']
        pre = (None, pre[1])
    lead = [l.strip().replace('\x0c', '') for l in pre[1] if l.strip()]
    lead = [l for l in lead if l]
    if lead:
        md += ['# ' + lead[0].lstrip('# ').strip(), '']
        if len(lead) > 1:
            md += [' '.join(lead[1:]), '']
    for title, buf in secs[1:]:
        if is_pipe_section(buf):
            body = pipe_convert(buf)
            md += [f'## {title}', '', body, '']
            stats.append((title, 'pipe', 0, body.count('\n')))
        else:
            body, st = plain_convert(title, buf)
            md += body; stats.append(st)
    md = [re.sub(r'\s+---$', '', l) if not l.startswith('|') else l for l in md]
    return '\n'.join(md).strip() + '\n', stats

def main(pdf, out_md):
    raw = pdf_text(pdf)
    md, stats = convert(raw)
    open(out_md, 'w', encoding='utf-8').write(md)
    return stats

if __name__ == '__main__':
    for s in main(sys.argv[1], sys.argv[2]): print('  ', s)

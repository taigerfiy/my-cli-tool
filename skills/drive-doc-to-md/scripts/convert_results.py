#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""014 赛果清单 PDF -> Markdown（v1）

原始形态（pdftotext -layout）：
  261116 数据日期：周一 11-16
  整体概况：主队6胜1平1负 (共8场)
  编号 赛事 主队 客队 全场比分 半场比分 胜平负(SP) 让球(SP) 比分(SP) 总进球(赔率) 半全场(SP)
  001 欧国联 法罗群岛 摩尔多瓦 1:0 1:0 胜 1.63 让(-1) 平 2.95 5.25 1(4.25) 胜-胜 2.65

目标：11 列 GFM 表格，原样保留每个单元格（"无"/"胜其他"/"7+(4.50)" 等原值不许改写）。
"""
import re, sys, subprocess, os

ROW = re.compile(r'^(\d{3})\s+(.*)$')
SCORE = re.compile(r'(\d+:\d+)\s+(\d+:\d+)')
HDR = re.compile(r'^编号\s+赛事\s+主队')
GOALS = re.compile(r'^(?:\d+|7)\+?\(\d+(?:\.\d+)?\)$')
HTFT = re.compile(r'^(?:[胜平负]-[胜平负]|无)$')
RES = re.compile(r'^(胜|平|负|无)(?:\(冷\))?$')
NUM = re.compile(r'^\d+(?:\.\d+)?$')


def pdf_text(pdf):
    return subprocess.run(['pdftotext', '-layout', pdf, '-'],
                          capture_output=True).stdout.decode('utf-8')


def split_cells(rest):
    """把 '胜 1.63 让(-1) 平 2.95 5.25 1(4.25) 胜-胜 2.65' 切成 5 个语义单元格"""
    t = rest.split()
    i = 0
    cells = []

    # 1) 胜平负(SP)
    part = []
    if i < len(t) and RES.match(t[i]):
        part.append(t[i]); i += 1
        if i < len(t) and NUM.match(t[i]):
            part.append(t[i]); i += 1
    cells.append(' '.join(part) if part else '无')

    # 2) 让球(SP)
    part = []
    if i < len(t) and t[i].startswith('让('):
        part.append(t[i]); i += 1
        if i < len(t) and RES.match(t[i]):
            part.append(t[i]); i += 1
            if i < len(t) and NUM.match(t[i]):
                part.append(t[i]); i += 1
    cells.append(' '.join(part) if part else '无')

    # 3) 比分(SP)：无固定形态（可能是 '胜其他 19.00' 或 '5.25' 或 '无'）
    part = []
    while i < len(t):
        s = t[i]
        if GOALS.match(s):
            break
        if HTFT.match(s) and len(part) >= 1:
            break
        part.append(s); i += 1
        if len(part) >= 3:
            break
    cells.append(' '.join(part) if part else '无')

    # 4) 总进球(赔率)
    part = []
    if i < len(t) and (GOALS.match(t[i]) or t[i] == '无'):
        part.append(t[i]); i += 1
    cells.append(' '.join(part) if part else '无')

    # 5) 半全场(SP)
    part = []
    if i < len(t) and (HTFT.match(t[i]) or RES.match(t[i])):
        part.append(t[i]); i += 1
        if i < len(t) and NUM.match(t[i]):
            part.append(t[i]); i += 1
    cells.append(' '.join(part) if part else '无')

    tail = ' '.join(t[i:])
    return cells, tail


def convert(raw, stem=''):
    lines = [l.rstrip() for l in raw.split('\n')]
    title = ''; summary = []; header = []; rows = []; notes = []
    for ln in lines:
        s = ln.strip()
        if not s or s == '\x0c':
            continue
        if HDR.match(s):
            header = s.split()
            continue
        m = ROW.match(s)
        if m and header:
            num, rest = m.groups()
            sm = SCORE.search(rest)
            if sm:
                mid = rest[:sm.start()].split()
                if len(mid) >= 3:
                    ev, home, away = ' '.join(mid[:-2]), mid[-2], mid[-1]
                    cells, tail = split_cells(rest[sm.end():].strip())
                    rows.append([num, ev, home, away, sm.group(1), sm.group(2)] + cells)
                    if tail:
                        notes.append(f'（{num} 未归类残留：{tail}）')
                    continue
        if s.startswith('(') or s.startswith('（') or s.startswith('注'):
            notes.append(s.strip('()（）'))
            continue
        if not title:
            title = s
        elif not header and ('整体概况' in s or not summary):
            summary.append(s)
        else:
            notes.append(s)

    if not header:
        header = ['编号', '赛事', '主队', '客队', '全场比分', '半场比分',
                  '胜平负(SP)', '让球(SP)', '比分(SP)', '总进球(赔率)', '半全场(SP)']
    # 标题照抄原文（有的文件首行不带 YYMMDD 前缀，只要含日期即可；补写会引入多余数字，过不了闸门）
    if not title:
        title = stem

    md = [f'# {title}', '']
    for s in summary:
        md += [s, '']
    md += ['| ' + ' | '.join(header) + ' |',
           '| ' + ' | '.join([':---'] * len(header)) + ' |']
    for r in rows:
        r = r + ['-'] * (len(header) - len(r))
        md.append('| ' + ' | '.join((x if x.strip() else '-') for x in r) + ' |')
    md.append('')
    for n in notes:
        md += [f'> {n}', '']
    if not rows:
        md += ['> 未解析到赛果行，请人工核对原始 PDF。', '']
    return '\n'.join(md).strip() + '\n', len(rows)


def main(pdf, out_md):
    stem = os.path.splitext(os.path.basename(pdf))[0]
    raw = pdf_text(pdf)
    md, n = convert(raw, stem)
    open(out_md, 'w', encoding='utf-8').write(md)
    return n


if __name__ == '__main__':
    print('rows:', main(sys.argv[1], sys.argv[2]))

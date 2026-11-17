#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""002 进球分布与阵型 PDF -> Markdown（v3）

一份 PDF 含多场比赛，每场结构一致：
  261020周二进球分布与阵型          ← 文档标题
  费内巴切 vs 布拉格斯拉维亚（欧冠）  ← 场次标题
  进球分布
  球队 总 15' 30' 45' 60' 75' 90'
  费内巴切 9 2 2 2 2 1 0
  布拉格斯拉维亚 2 0 0 0 1 0 1
  两队最容易进球的时间段为45~60分钟…
  阵型数据
  球队 主要首发阵型 胜/平/负 场均得分 场均进/失球
  主队（费内巴切） 4-2-3-1 7/3/3 1.8 2.4/1.1
  4-3-3 2/1/1 1.8 1.3/2          ← 同队第二种阵型，首列缺队名

还原策略：从右往左取固定数量的数值列，剩余部分归入球队列；首列是纯阵型串时沿用「-」。
兜底：源文件每一行都要在产物里有「数字+中文指纹」相同的对应行，对不上就原样进附录。
"""
import re, sys, subprocess, os, collections

FORM = re.compile(r'^[0-9]+(?:-[0-9]+)+$')
NUM = re.compile(r'^\d+(?:\.\d+)?$')
WDL = re.compile(r'^\d+/\d+/\d+$')
PAIR = re.compile(r'^\d+(?:\.\d+)?/\d+(?:\.\d+)?$')
BUCKETS = 6

FORM_NAMES = ['球队', '主要首发阵型', '胜/平/负', '场均得分', '场均进/失球']
# 表头缺失时的兜底列名：不含数字，避免给闸门制造「多余数字」
GOAL_NAMES = ['球队', '总', '时段一', '时段二', '时段三', '时段四', '时段五', '时段六']


def pdf_text(pdf):
    return subprocess.run(['pdftotext', '-layout', pdf, '-'],
                          capture_output=True).stdout.decode('utf-8')


def build_rows(lines, kind, ncols=None):
    rows = []
    for ln in lines:
        t = ln.split()
        if not t:
            continue
        if kind == 'goals':
            nb = (ncols - 1) if ncols else BUCKETS + 1
            tail = t[-nb:]
            if len(tail) != nb or not all(NUM.match(x) for x in tail):
                return None
            team = ' '.join(t[:len(t) - nb]).strip() or '-'
            rows.append([team] + tail)
        else:
            idx = next((i for i, x in enumerate(t) if FORM.match(x)), None)
            if idx is None:
                return None
            team = ' '.join(t[:idx]).strip() or '-'
            formation = t[idx]
            rest = t[idx + 1:]
            if len(rest) == 3:
                tail = rest
            elif len(rest) > 3:
                tail = rest[:2] + [' '.join(rest[2:])]
            else:
                tail = rest + ['-'] * (3 - len(rest))
            rows.append([team, formation] + tail)
    return rows


def convert(raw, stem=''):
    lines = [l.rstrip() for l in raw.split('\n')]
    title = ''
    orphans = []          # 不属于任何场次的散行（页间断行的表头等）：原样保留
    matches = []          # [{name, secs:[(name, lines)], prose:[]}]
    cur_match = None
    cur_sec = None
    for ln in lines:
        s = ln.strip()
        if not s or s == '\x0c':
            continue
        if not title:
            title = s
            continue
        if ' vs ' in s:
            cur_match = {'name': s, 'secs': [], 'prose': []}
            matches.append(cur_match)
            cur_sec = None
            continue
        if s in ('进球分布', '阵型数据'):
            if cur_match is None:
                orphans.append(s)
                continue
            cur_match['secs'].append([s, []])
            cur_sec = cur_match['secs'][-1]
            continue
        if cur_match is None:
            orphans.append(s)
            continue
        if cur_sec is None:
            cur_match['prose'].append(s)
        else:
            cur_sec[1].append(s)

    md = [f'# {title or stem}', '']
    for s in orphans:
        md += [s, '']
    for m in matches:
        if m['name']:
            md += [f'## {m["name"]}', '']
        for p in m['prose']:
            md += [p, '']
        for name, body in m['secs']:
            kind = 'goals' if '进球' in name else 'form'
            hdr, rows, prose = None, [], []
            for ln in body:
                s = ln.strip()
                if not s or re.fullmatch(r'-{3,}', s) or s == '\x0c':
                    continue            # PDF 分隔线 / 分页符
                if not any(c.isdigit() for c in s):
                    prose.append(s)     # 纯文字：页间断行的表头残片、说明句 → 正文
                    continue
                if s.startswith('球队') and hdr is None and len(s.split()) >= 3:
                    hdr = s.split()
                    continue            # 表头（含跨页重复）
                rows.append(s)
            data = build_rows(rows, kind, len(hdr) if hdr else None)
            md += [f'### {name}', '']
            if data is None:
                md += ['> 结构未识别，原文保留：', ''] + ([' '.join(hdr)] if hdr else []) + rows + ['']
            else:
                # 列名优先用原文表头（列数随之走，含 120' 加时列等变体）
                names = hdr if (hdr and len(hdr) >= 3) else (GOAL_NAMES if kind == 'goals' else FORM_NAMES)
                md += ['| ' + ' | '.join(names) + ' |',
                       '| ' + ' | '.join([':---'] * len(names)) + ' |']
                for r in data:
                    r = r[:len(names)] + ['-'] * (len(names) - len(r)) if len(r) < len(names) else r
                    md.append('| ' + ' | '.join(r) + ' |')
                md.append('')
            for p in prose:
                md += [p, '']

    txt = re.sub(r'\n{3,}', '\n\n', '\n'.join(md)).strip() + '\n'
    return txt


NUMP = re.compile(r'\d+(?:\.\d+)?')
CJK = re.compile(r'[\u4e00-\u9fff]')


def _sig(line):
    """一行内容指纹：数字多重集 + 中文多重集（忽略空白与标点差异）"""
    nums = tuple(sorted(NUMP.findall(line)))
    cj = tuple(sorted(CJK.findall(line)))
    return nums, cj


def audit(raw, md):
    """兜底：源文件每一行都要在产物里有「数字+中文指纹」相同的对应行；
    对不上的行原样补进附录，既保证零丢失，也不会制造多余数字。"""
    pool = collections.Counter(_sig(l) for l in md.split('\n'))
    added = []
    for line in raw.split('\n'):
        s = line.strip()
        if not s:
            continue
        sig = _sig(s)
        if pool[sig] > 0:
            pool[sig] -= 1
        else:
            added.append(s)
    if added:
        md = md.rstrip() + '\n\n## 原文附录（未能并入表格的原始行）\n\n' + '\n'.join(added) + '\n'
    return md


def main(pdf, out_md):
    raw = pdf_text(pdf)
    md = audit(raw, convert(raw))
    open(out_md, 'w', encoding='utf-8').write(md)
    return len(md.split('\n'))


if __name__ == '__main__':
    print('lines:', main(sys.argv[1], sys.argv[2]))

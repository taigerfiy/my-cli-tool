#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""017 首发补充数据（纯文本叙述）PDF -> Markdown（v1）

原始形态：无表格、无管道，正文按 40 字左右硬换行，条目用两格缩进。
  欧罗巴 2026-2027 赛季联赛阶段第4轮 首发补充数据 (11-04 至 11-06)
  数据来源：直播吧/懂球帝/... 仅记录事实，不做胜负预测。
  第1场 雷恩 0-0 奥林匹亚科斯 (11-04 23:30)
  雷恩
    伤停/缺阵：...
    替补调整：60分钟 希曼斯基 下 龙吉耶 上；...（换行断开）
    发挥好：...

目标：H1 标题 + 来源段 + 每场 H2 + 每队 H3 + 分类条目列表；断行合并，数字与标点原样保留。
"""
import re, sys, subprocess, os

MATCH = re.compile(r'^第\s*\d+\s*场')
BULLET = re.compile(r'^(伤停|缺阵|伤停/缺阵|替补调整|替补|发挥好|发挥差|主教练|教练|阵型|备注|黄牌|红牌|进球|关键数据|数据)\s*[：:]')
TEAM = re.compile(r'^[^\s：:；;，。、|]{1,14}$')


def pdf_text(pdf):
    return subprocess.run(['pdftotext', '-layout', pdf, '-'],
                          capture_output=True).stdout.decode('utf-8')


def is_cjk(c):
    return '\u4e00' <= c <= '\u9fff' or c in '，。、；：（）【】“”‘’—·'


def join_text(a, b):
    if not a:
        return b
    if not b:
        return a
    sep = '' if (is_cjk(a[-1]) and is_cjk(b[0])) else ' '
    return a + sep + b


def convert(raw, stem=''):
    lines = [l.rstrip() for l in raw.split('\n')]
    blocks = []          # [kind, text]
    i = 0
    n = len(lines)

    def next_nonempty(k):
        while k < n and not lines[k].strip():
            k += 1
        return lines[k].strip() if k < n else ''

    while i < n:
        ln = lines[i]
        st = ln.strip()
        i += 1
        if not st or st == '\x0c':
            continue
        indent = len(ln) - len(ln.lstrip())

        if not blocks:
            blocks.append(['h1', st.lstrip('# ').strip()])
            continue

        if st.startswith('数据来源'):
            blocks.append(['para', st])
            continue

        if MATCH.match(st):
            blocks.append(['h2', st])
            continue

        m = BULLET.match(st)
        if m:
            label = m.group(1)
            body = st[m.end():].strip()
            blocks.append(['bullet', f'**{label}**：{body}' if body else f'**{label}**'])
            continue

        if indent >= 2:
            # 缩进但无标签：视为上一条的续行
            if blocks and blocks[-1][0] in ('bullet', 'para', 'h2', 'h3'):
                blocks[-1][1] = join_text(blocks[-1][1], st)
            else:
                blocks.append(['bullet', st])
            continue

        if TEAM.match(st) and (blocks[-1][0] == 'h2' or BULLET.match(next_nonempty(i))):
            blocks.append(['h3', st])
            continue

        # 其余：接续上一块
        if blocks and blocks[-1][0] in ('h1', 'para', 'bullet', 'h2', 'h3'):
            blocks[-1][1] = join_text(blocks[-1][1], st)
        else:
            blocks.append(['para', st])

    if not blocks:
        return f'# {stem}\n', blocks
    if not blocks[0][1]:
        blocks[0][1] = stem

    md = []
    for kind, text in blocks:
        if kind == 'h1':
            md += [f'# {text}', '']
        elif kind == 'h2':
            md += ['', f'## {text}', '']
        elif kind == 'h3':
            md += [f'### {text}', '']
        elif kind == 'bullet':
            md.append(f'- {text}')
        else:
            md += [text, '']
    txt = '\n'.join(md)
    txt = re.sub(r'\n{3,}', '\n\n', txt)
    return txt.strip() + '\n', blocks


def main(pdf, out_md):
    stem = os.path.splitext(os.path.basename(pdf))[0]
    raw = pdf_text(pdf)
    md, _ = convert(raw, stem)
    open(out_md, 'w', encoding='utf-8').write(md)
    return len(md.split('\n'))


if __name__ == '__main__':
    print('lines:', main(sys.argv[1], sys.argv[2]))

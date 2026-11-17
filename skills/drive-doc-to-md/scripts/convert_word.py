#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Word(.docx) 文件 -> Markdown（pandoc + HTML 表转 GFM 管道表 + 数字闸门，v1）

为什么要后处理：pandoc 的 gfm writer 遇到带列宽的表格会输出 HTML <table>，并带上
`<col style="width: 18%">` 这类属性，其中的数字会污染数字多重集（闸门判为"多余数字"）。
这里把 colgroup 整块丢掉、把 HTML 表格转成 GFM 管道表，既满足统一格式，也保证数字与原文件一一对应。
"""
import re, sys, subprocess, os, collections

NUM = re.compile(r'\d+(?:\.\d+)?')


def _pandoc(src, to):
    r = subprocess.run(['pandoc', '-f', 'docx', '-t', to, '--wrap=none', src],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[:300])
    return r.stdout


def _clean_cell(c):
    c = re.sub(r'<colgroup>.*?</colgroup>', '', c, flags=re.S)
    c = re.sub(r'<br\s*/?>', '<br>', c)
    c = re.sub(r'</?(?:p|span|div)[^>]*>', '', c)
    c = re.sub(r'<strong>(.*?)</strong>', r'**\1**', c, flags=re.S)
    c = re.sub(r'<em>(.*?)</em>', r'*\1*', c, flags=re.S)
    c = re.sub(r'<[^>]+>', '', c)
    c = c.replace('|', r'\|')
    c = re.sub(r'\s*\n\s*', ' ', c)
    c = re.sub(r'[ \t]{2,}', ' ', c)
    return c.strip()


def html_table_to_gfm(block):
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', block, flags=re.S)
    out = []
    for idx, r in enumerate(rows):
        cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', r, flags=re.S)
        cells = [_clean_cell(c) for c in cells]
        if not cells:
            continue
        out.append('| ' + ' | '.join(cells) + ' |')
        if idx == 0:
            out.append('| ' + ' | '.join([':---'] * len(cells)) + ' |')
    return '\n'.join(out)


def convert(src, out_md):
    md = _pandoc(src, 'gfm')
    md = re.sub(r'<colgroup>.*?</colgroup>', '', md, flags=re.S)
    md = re.sub(r'<table[^>]*>.*?</table>',
                lambda m: html_table_to_gfm(m.group(0)), md, flags=re.S)
    md = re.sub(r'\n{3,}', '\n\n', md).strip() + '\n'
    open(out_md, 'w', encoding='utf-8').write(md)
    return len(md.split('\n'))


def check(src, md_path):
    plain = _pandoc(src, 'plain')
    md = open(md_path, encoding='utf-8').read()
    a = collections.Counter(NUM.findall(plain))
    b = collections.Counter(NUM.findall(md))
    return {'ok': a == b,
            'num_missing': dict(list((a - b).items())[:20]),
            'num_extra': dict(list((b - a).items())[:20])}


def main(src, out_md):
    convert(src, out_md)
    return check(src, out_md)


if __name__ == '__main__':
    import json
    print(json.dumps(main(sys.argv[1], sys.argv[2]), ensure_ascii=False))

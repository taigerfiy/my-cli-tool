#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""006 足球对阵数据（在线文档）-> Markdown（表格化，v1）

原始导出（docs +fetch markdown）已经把内容写成「标题 + 空格式表格」：
  一、比赛信息
  项目 内容
  赛事 欧国联
  ...
  三、胜平负固定奖金
  发布时间 胜 平 负
  2026-10-04 16:15:40 1.52 3.55 5.30

这里把「表头行 + 后续数据行」还原成 GFM 表格：列数取表头 token 数，
每行按「最后一列起往前取 N-1 个值、剩下全部归首列」切分（首列常是公司名或
「2026-10-04 16:15:40」这种带空格的时间戳）。切不动就原样保留该行，绝不丢数。
"""
import re, sys, os, collections

NUM = re.compile(r'\d+(?:\.\d+)?')
CJK = re.compile(r'[\u4e00-\u9fff]')
HDR = re.compile(r'^([一二三四五六七八九十]+)、')
SUB = re.compile(r'^\d+\.\d+\s')


def convert(raw_md):
    lines = [l.rstrip() for l in raw_md.split('\n')]
    md = []
    i = 0
    n = len(lines)
    while i < n:
        s = lines[i].strip()
        if not s:
            md.append('')
            i += 1
            continue
        if s.startswith('#'):
            md += [s, '']
            i += 1
            continue
        if s.startswith('足球对阵数据：'):
            md += [f'## {s}', '']
            i += 1
            continue
        if HDR.match(s) or SUB.match(s):
            md += [f'### {s}', '']
            i += 1
            continue
        # 表头探测：≥2 token 且没有「纯数字」token（比分列名 1:0 / 胜其它 允许带数字）
        toks = s.split()
        if len(toks) >= 2 and not any(re.fullmatch(r'[-+]?\d+(?:\.\d+)?', t) for t in toks):
            names = toks
            cols = len(names)
            rows = []
            j = i + 1
            while j < n:
                t = lines[j].strip()
                if not t or t.startswith('#') or HDR.match(t) or SUB.match(t):
                    break
                tk = t.split()
                if len(tk) < cols - 1:
                    break
                tail = tk[len(tk) - (cols - 1):]
                head = ' '.join(tk[:len(tk) - (cols - 1)])
                if cols == 2:
                    # 两列键值表：值可能本身带空格（如「2026-09-30 02:45」）
                    m2 = re.match(r'^(\S+)\s+(\d{4}-\d{2}-\d{2})\s+(\d{1,2}:\d{2})?$', t)
                    if m2:
                        rows.append([m2.group(1), ' '.join(x for x in m2.groups()[1:] if x)])
                        j += 1
                        continue
                rows.append([head] + tail)
                j += 1
            if rows:
                md += ['| ' + ' | '.join(names) + ' |',
                       '| ' + ' | '.join([':---'] * cols) + ' |']
                for r in rows:
                    md.append('| ' + ' | '.join(r) + ' |')
                md.append('')
                i = j
                continue
        md += [s, '']
        i += 1
    txt = re.sub(r'\n{3,}', '\n\n', '\n'.join(md)).strip() + '\n'
    return txt


def _sig(line):
    return tuple(sorted(NUM.findall(line))), tuple(sorted(CJK.findall(line)))


def audit(raw, md):
    """每行源内容都要在产物里有「数字+中文指纹」相同的一行，对不上就补进附录"""
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


def main(raw_md, out_md, ref_name=''):
    md = audit(raw_md, convert(raw_md))
    open(out_md, 'w', encoding='utf-8').write(md)
    return {'ok': None, 'lines': len(md.split('\n'))}


if __name__ == '__main__':
    import json
    print(json.dumps(main(open(sys.argv[1], encoding='utf-8').read(), sys.argv[2]), ensure_ascii=False))

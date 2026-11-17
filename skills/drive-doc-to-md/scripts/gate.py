#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""转换保真闸门：
1) 数字多重集必须与 PDF 原文完全一致（无丢数、无串位）
2) PDF 中的 CJK 字符必须全部出现在 MD 中（允许少量换行合并导致的重复）
3) 不得出现替换符/乱码
4) 可选宽松口径（mode=chars / src=pymupdf / ignore_extra），仅供多栏排版报告，表格类目录一律用默认严格口径
"""
import re, sys, subprocess, collections

NUM = re.compile(r'\d+(?:\.\d+)?')
CJK = re.compile(r'[\u4e00-\u9fff]')
DIG = re.compile(r'[0-9.]')     # 宽松口径：数字字符 + 小数点，容忍多栏版面把数字拆开

FOOT = re.compile(r'file:///')

def txt(pdf, strip_artifacts=False):
    raw = subprocess.run(['pdftotext', '-layout', pdf, '-'], capture_output=True).stdout.decode('utf-8')
    if strip_artifacts:
        # 打印型 PDF 每页都带 file:/// 本地路径页脚，属版式痕迹，源与产物两侧一致剔除
        raw = '\n'.join(l for l in raw.split('\n') if not FOOT.search(l))
    return raw

def pmtxt(pdf):
    import pymupdf
    with pymupdf.open(pdf) as d:
        return '\n'.join(pg.get_text('text', sort=True) for pg in d)


def check(pdf, md_path, strip_artifacts=False, mode='strict', src='pdftotext', ignore_extra=False):
    if src == 'pymupdf':
        a = '\n'.join(l for l in pmtxt(pdf).split('\n') if not FOOT.search(l))
    else:
        a = txt(pdf, strip_artifacts)
    b = open(md_path, encoding='utf-8').read()
    if mode == 'chars':
        na, nb = collections.Counter(DIG.findall(a)), collections.Counter(DIG.findall(b))
    else:
        na, nb = collections.Counter(NUM.findall(a)), collections.Counter(NUM.findall(b))
    missing = na - nb
    extra = nb - na
    ca, cb = collections.Counter(CJK.findall(a)), collections.Counter(CJK.findall(b))
    cmiss = ca - cb
    bad = [ch for ch in ('\ufffd',) if ch in b]
    ok = not missing and not cmiss and not bad and (ignore_extra or not extra)
    info = {
        'ok': ok,
        'num_missing': dict(list(missing.items())[:20]),
        'num_extra': dict(list(extra.items())[:20]),
        'cjk_missing': ''.join(list(cmiss.elements())[:40]),
        'bad_chars': bad,
    }
    return info

if __name__ == '__main__':
    import json
    print(json.dumps(check(sys.argv[1], sys.argv[2]), ensure_ascii=False))

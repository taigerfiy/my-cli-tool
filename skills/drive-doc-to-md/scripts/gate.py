#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""转换保真闸门：
1) 数字多重集必须与 PDF 原文完全一致（无丢数、无串位）
2) PDF 中的 CJK 字符必须全部出现在 MD 中（允许少量换行合并导致的重复）
3) 不得出现替换符/乱码
"""
import re, sys, subprocess, collections

NUM = re.compile(r'\d+(?:\.\d+)?')
CJK = re.compile(r'[\u4e00-\u9fff]')

def txt(pdf):
    return subprocess.run(['pdftotext', '-layout', pdf, '-'], capture_output=True).stdout.decode('utf-8')

def check(pdf, md_path):
    a = txt(pdf)
    b = open(md_path, encoding='utf-8').read()
    na, nb = collections.Counter(NUM.findall(a)), collections.Counter(NUM.findall(b))
    missing = na - nb
    extra = nb - na
    ca, cb = collections.Counter(CJK.findall(a)), collections.Counter(CJK.findall(b))
    cmiss = ca - cb
    bad = [ch for ch in ('\ufffd',) if ch in b]
    ok = not missing and not extra and not cmiss and not bad
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

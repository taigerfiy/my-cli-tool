#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 Base1 名称对照表重建 assets/alias_map.json（别名 → 规范名）"""
import json, os, re, subprocess, sys

BASE_TOKEN = 'FsnQbGerEalbfBsmWFScFJtTnwe'
T_NAME = 'tblisjWesOVwl2We'
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'assets', 'alias_map.json')


def main():
    recs, off = [], 0
    while True:
        cp = subprocess.run(['lark-cli', 'base', '+record-list', '--base-token', BASE_TOKEN,
                             '--table-id', T_NAME, '--as', 'user', '--format', 'json',
                             '--limit', '200', '--offset', str(off)], capture_output=True, text=True)
        d = json.loads(cp.stdout)['data']
        f = d['fields']
        for row in d['data']:
            recs.append(dict(zip(f, row)))
        if not d.get('has_more'):
            break
        off += 200
    alias = {}
    for x in recs:
        canon = (x.get('规范名') or '').strip()
        if not canon:
            continue
        alias[canon] = canon
        for part in re.split(r'[\n\r、，,/；;]', x.get('别名') or ''):
            p = part.strip()
            if len(p) >= 2:
                alias.setdefault(p, canon)
    json.dump(alias, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
    print(f'写出 {OUT}：{len(alias)} 条映射（别名 {sum(1 for k, v in alias.items() if k != v)} 条）')


if __name__ == '__main__':
    main()

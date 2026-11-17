#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""飞书云盘文件夹批量转 Markdown 并归档原件（端到端编排）

流程：列目录 -> 下载 -> 转 MD -> 保真闸门 -> 上传 MD -> 回读校验 -> 原件移入回收站
特点：逐份闭环、断点续跑(state.json)、闸门不过禁止移动原件。

用法示例：
  python3 run.py --folder <folder_token_or_url> --bin <bin_folder_token> --dry-run
  python3 run.py --folder <folder_token> --bin <bin_folder_token>
  python3 run.py --folder <folder_token> --bin <bin_folder_token> --limit 5
"""
import argparse, collections, json, os, re, subprocess, sys, time

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS)
import convert_core as cc
import gate

NUM = re.compile(r'\d+(?:\.\d+)?')


def cli(args, retry=3):
    """调用 lark-cli，返回 data 段；失败重试后返回 None"""
    for i in range(retry):
        r = subprocess.run(['lark-cli'] + args, capture_output=True, text=True)
        if r.returncode == 0:
            try:
                d = json.loads(r.stdout)
                if d.get('ok'):
                    return d.get('data', {})
            except Exception:
                return None
        time.sleep(2 + 2 * i)
    return None


def list_files(folder):
    out, token = [], ''
    while True:
        params = {'folder_token': folder, 'page_size': 200}
        if token:
            params['page_token'] = token
        d = cli(['drive', 'files', 'list', '--params', json.dumps(params), '--format', 'json'])
        if not d:
            break
        out += d.get('files', [])
        if not d.get('has_more') or not d.get('next_page_token'):
            break
        token = d['next_page_token']
    return out


def norm_number_counter(text):
    return collections.Counter(NUM.findall(text))


def convert_one(item, work, out_dir):
    """返回 (md_path, ref_path_or_text, ok, info)"""
    tok, name, typ = item['token'], item['name'], item['type']
    stem = re.sub(r'\.(pdf|PDF|md)$', '', name)
    md_path = os.path.join(out_dir, stem + '.md')
    if typ in ('file', 'docx') and name.lower().endswith('.pdf'):
        pdf = os.path.join(work, tok + '.pdf')
        if not os.path.exists(pdf):
            d = cli(['drive', '+download', '--file-token', tok, '--output', pdf,
                     '--overwrite', '--format', 'json'])
            if not d or not os.path.exists(pdf):
                return None, None, False, {'error': 'download_fail'}
        cc.main(pdf, md_path)
        info = gate.check(pdf, md_path)
        return md_path, pdf, info['ok'], info
    if typ == 'docx':
        d = cli(['docs', '+fetch', '--doc', tok, '--doc-format', 'markdown', '--format', 'json'])
        if not d:
            return None, None, False, {'error': 'docx_fetch_fail'}
        raw = (d.get('document') or {}).get('content') or ''
        if not raw.strip():
            return None, None, False, {'error': 'docx_empty'}
        md, _ = cc.convert(raw)
        open(md_path, 'w', encoding='utf-8').write(md)
        a, b = norm_number_counter(raw), norm_number_counter(md)
        ok = (a == b)
        return md_path, raw, ok, {'ok': ok, 'num_diff': dict(list((a - b).items())[:10])}
    return None, None, False, {'error': 'unsupported_type'}


def upload(folder, md_path):
    d = cli(['markdown', '+create', '--folder-token', folder, '--file', md_path, '--format', 'json'])
    return (d or {}).get('file_token')


def verify(md_token, tmp, ref):
    if not cli(['markdown', '+fetch', '--file-token', md_token, '--output', tmp,
                '--overwrite', '--format', 'json']):
        return False
    if not os.path.exists(tmp):
        return False
    txt = open(tmp, encoding='utf-8').read()
    if ref.endswith('.pdf'):
        return gate.check(ref, tmp)['ok']
    return norm_number_counter(txt) == norm_number_counter(ref)


def move(tok, typ, bin_folder):
    args = ['drive', '+move', '--file-token', tok, '--folder-token', bin_folder,
            '--type', 'docx' if typ == 'docx' else 'file', '--format', 'json']
    return cli(args) is not None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--folder', required=True)
    ap.add_argument('--bin', required=True, help='回收站文件夹 token')
    ap.add_argument('--work', default='./work')
    ap.add_argument('--keep-originals', action='store_true', help='不移动原件')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    folder = args.folder.rstrip('/').split('/')[-1]
    allf = list_files(folder)
    files = [f for f in allf
             if (f['type'] == 'docx') or (f['type'] == 'file' and f['name'].lower().endswith('.pdf'))]
    existing_md = {re.sub(r'\.md$', '', f['name']) for f in allf
                   if f['type'] == 'file' and f['name'].lower().endswith('.md')}
    skipped = [f for f in allf if f not in files]
    if skipped:
        print('跳过非目标类型 %d 项（%s）' % (len(skipped), ', '.join(sorted({f['name'].rsplit('.',1)[-1] for f in skipped})[:4])))
    if args.limit:
        files = files[:args.limit]
    print('待处理 %d 项' % len(files))
    if args.dry_run:
        for f in files[:20]:
            print(' -', f['type'], f['name'])
        return

    os.makedirs(args.work, exist_ok=True)
    out_dir = os.path.join(args.work, 'md')
    os.makedirs(out_dir, exist_ok=True)
    state_path = os.path.join(args.work, 'state.json')
    st = json.load(open(state_path)) if os.path.exists(state_path) else {}
    ok = bad = 0
    for it in files:
        tok, typ, name = it['token'], it['type'], it['name']
        stem = re.sub(r'\.(pdf|PDF|md)$', '', name)
        if stem in existing_md and not st.get(tok, {}).get('md_token'):
            print('SKIP_EXISTING_MD', stem); continue
        rec = st.setdefault(tok, {'name': stem + '.md', 'type': typ})
        md_path, ref, conv_ok, info = convert_one(it, args.work, out_dir)
        if not conv_ok:
            print('CONVERT_FAIL', stem, info)
            rec['status'] = 'convert_fail'
            bad += 1
            continue
        rec['status'] = 'converted'
        if not rec.get('md_token'):
            mt = upload(folder, md_path)
            if not mt:
                print('UPLOAD_FAIL', stem)
                bad += 1
                continue
            rec['md_token'] = mt
        if not rec.get('verified'):
            tmp = os.path.join(args.work, 'rb_%s.md' % tok)
            if not verify(rec['md_token'], tmp, ref):
                print('VERIFY_FAIL', stem)
                bad += 1
                continue
            rec['verified'] = True
        if not args.keep_originals and not rec.get('moved'):
            if not move(tok, typ, args.bin):
                print('MOVE_FAIL', stem)
                bad += 1
                continue
            rec['moved'] = True
        json.dump(st, open(state_path, 'w'), ensure_ascii=False, indent=1)
        ok += 1
        print('DONE', stem)
    print('summary ok=%d bad=%d' % (ok, bad))


if __name__ == '__main__':
    main()

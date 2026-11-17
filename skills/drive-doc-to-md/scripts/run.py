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
import convert_results as cr
import convert_notes as cn
import convert_word as cw
import convert_goals as cgo
import gate

NUM = re.compile(r'\d+(?:\.\d+)?')

TITLE_TAG = re.compile(r'<title>.*?</title>', re.S)
DELIM_ROW = re.compile(r'^\|\s*(?:[-:]+\s*\|)+$')


def normalize_docx_md(raw):
    """飞书在线文档导出的 Markdown 规范化：去 <title> 标签、表头分隔行统一成 GFM（不动任何数字）"""
    md = TITLE_TAG.sub('', raw)
    out = []
    for line in md.split('\n'):
        st = line.strip()
        if DELIM_ROW.match(st):
            n = len([c for c in st.strip('|').split('|')])
            out.append('| ' + ' | '.join([':---'] * n) + ' |')
        else:
            out.append(line)
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(out)).strip() + '\n'



def cli(args, retry=3):
    """调用 lark-cli，返回 data 段；失败重试后返回 None"""
    for i in range(retry):
        try:
            r = subprocess.run(['lark-cli'] + args, capture_output=True, text=True)
        except FileNotFoundError:
            time.sleep(3)
            continue
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


def safe_stem(stem):
    """本地文件名安全化：云文档标题里可能带 '/'（如「卷3/3」），不能直接当路径"""
    return stem.replace('/', '／').replace('\\', '＼')


def convert_one(item, work, out_dir):
    """返回 (md_path, ref_path_or_text, ok, info)"""
    tok, name, typ = item['token'], item['name'], item['type']
    stem = re.sub(r'\.(pdf|PDF|md)$', '', name)
    md_path = os.path.join(out_dir, safe_stem(stem) + '.md')
    if typ == 'file' and name.lower().endswith(('.docx', '.doc')):
        src = os.path.join(work, tok + '.docx')
        if not os.path.exists(src):
            d = cli(['drive', '+download', '--file-token', tok, '--output', src,
                     '--overwrite', '--format', 'json'])
            if not d or not os.path.exists(src):
                return None, None, False, {'error': 'download_fail'}
        info = cw.main(src, md_path)
        return md_path, src, info['ok'], info
    if typ in ('file', 'docx') and name.lower().endswith('.pdf'):
        pdf = os.path.join(work, tok + '.pdf')
        if not os.path.exists(pdf):
            d = cli(['drive', '+download', '--file-token', tok, '--output', pdf,
                     '--overwrite', '--format', 'json'])
            if not d or not os.path.exists(pdf):
                return None, None, False, {'error': 'download_fail'}
        raw = cc.pdf_text(pdf)
        if len(raw.strip()) < 100:
            # 无文本层（App 截图/扫描件）：不做猜测性 OCR，交回用户决定
            return None, None, False, {'error': 'scanned_no_text', 'chars': len(raw.strip())}
        pipe_lines = sum(1 for l in raw.split('\n') if l.strip().startswith('|'))
        if '进球分布' in raw and '阵型数据' in raw:
            cgo.main(pdf, md_path)                 # 002 进球分布与阵型（多场/份）
        elif '全场比分' in raw and '半全场' in raw:
            cr.main(pdf, md_path)                  # 014 赛果流水（一行一场）
        elif pipe_lines >= 5:
            cc.main(pdf, md_path)                  # 017 首发阵容（已是管道表）/ 7表格
        elif '首发补充数据' in raw or '伤停/缺阵' in raw:
            cn.main(pdf, md_path)                  # 017 补充叙述（纯文本条目）
        else:
            cc.main(pdf, md_path)                  # 7表格 / 历史战绩 / 报告类（严格零差异优先）
        info = gate.check(pdf, md_path)
        return md_path, pdf, info['ok'], info
    if typ == 'docx':
        d = cli(['docs', '+fetch', '--doc', tok, '--doc-format', 'markdown', '--format', 'json'], retry=5)
        if not d:
            return None, None, False, {'error': 'docx_fetch_fail'}
        raw = (d.get('document') or {}).get('content') or ''
        if not raw.strip():
            return None, None, False, {'error': 'docx_empty'}
        # 在线文档导出本身就是 Markdown，只做轻量规范化（去 <title>、表头分隔行统一），不再二次加工
        ref = TITLE_TAG.sub('', raw)
        md = normalize_docx_md(raw)
        open(md_path, 'w', encoding='utf-8').write(md)
        a, b = norm_number_counter(ref), norm_number_counter(md)
        ok = (a == b)
        return md_path, ref, ok, {'ok': ok, 'num_diff': dict(list((a - b).items())[:10])}
    return None, None, False, {'error': 'unsupported_type'}


def upload(folder, md_path, name):
    """--name 显式传云端原名（标题里可能有 '/'，本地文件名已安全化）"""
    d = cli(['markdown', '+create', '--folder-token', folder, '--file', md_path,
             '--name', name, '--format', 'json'])
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
    if ref.endswith('.docx'):
        return cw.check(ref, tmp)['ok']
    return norm_number_counter(txt) == norm_number_counter(ref)


def move(tok, typ, bin_folder):
    """移动原件；服务端偶发 1061001，单独用长退避重试"""
    args = ['drive', '+move', '--file-token', tok, '--folder-token', bin_folder,
            '--type', 'docx' if typ == 'docx' else 'file', '--format', 'json']
    for i in range(6):
        try:
            r = subprocess.run(['lark-cli'] + args, capture_output=True, text=True)
        except FileNotFoundError:
            time.sleep(3)
            continue
        if r.returncode == 0:
            try:
                if json.loads(r.stdout).get('ok'):
                    return True
            except Exception:
                pass
        time.sleep(5 + 5 * i)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--folder', required=True)
    ap.add_argument('--bin', required=True, help='回收站文件夹 token')
    ap.add_argument('--work', default='./work')
    ap.add_argument('--keep-originals', action='store_true', help='不移动原件')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--delay', type=float, default=0.0, help='每份之间的间隔秒数（限流时用）')
    args = ap.parse_args()

    folder = args.folder.rstrip('/').split('/')[-1]
    allf = list_files(folder)
    files = [f for f in allf
             if (f['type'] == 'docx')
             or (f['type'] == 'file' and f['name'].lower().endswith(('.pdf', '.docx', '.doc')))]
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
            mt = upload(folder, md_path, name + '.md')
            if not mt:
                print('UPLOAD_FAIL', stem)
                bad += 1
                continue
            rec['md_token'] = mt
            json.dump(st, open(state_path, 'w'), ensure_ascii=False, indent=1)
        if not rec.get('verified'):
            tmp = os.path.join(args.work, 'rb_%s.md' % tok)
            if not verify(rec['md_token'], tmp, ref):
                print('VERIFY_FAIL', stem)
                bad += 1
                continue
            rec['verified'] = True
            json.dump(st, open(state_path, 'w'), ensure_ascii=False, indent=1)
        if not args.keep_originals and not rec.get('moved'):
            if not move(tok, typ, args.bin):
                print('MOVE_FAIL', stem)
                bad += 1
                continue
            rec['moved'] = True
        json.dump(st, open(state_path, 'w'), ensure_ascii=False, indent=1)
        ok += 1
        print('DONE', stem)
        if args.delay:
            time.sleep(args.delay)
    print('summary ok=%d bad=%d' % (ok, bad))


if __name__ == '__main__':
    main()

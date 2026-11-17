#!/usr/bin/env python3
"""技能索引：扫描技能根目录，抽取每个 SKILL.md 的名称/说明/脚本/依赖，落成机读索引。

用法：
  python3 skill_index.py                              # 扫默认两个根，打印概览
  python3 skill_index.py --out /tmp/skill_index.json  # 同时落盘
  python3 skill_index.py --root <dir> --md            # 指定根并输出 Markdown 表

索引字段：name / display / desc / path / scope / scripts / refs / deps
"""
import argparse
import json
import os
import re
import sys

DEFAULT_ROOTS = [
    os.path.expanduser("~/.doubao/agent_mode/workspace/.user_skills"),
    os.path.expanduser("~/.doubao/agent_mode/workspace/.skills"),
]

# 依赖探测：脚本里出现这些串就记一条（用于判断"装到本环境能不能跑"）
DEPS = {
    "lark-cli": "飞书 CLI（多维表格/云盘/消息）",
    "subprocess": "调用外部命令",
    "curl": "依赖 curl 抓网页",
    "requests": "依赖 requests（可能被网络策略拦）",
    "urllib": "urllib 直连（部分站点会 403）",
    "playwright": "浏览器自动化",
    "selenium": "浏览器自动化",
    "openai": "需 LLM API Key",
    "pandas": "需 pandas",
    "PIL": "需 Pillow",
    "fitz": "需 PyMuPDF",
    "docx": "需 python-docx",
}


def parse_front(text):
    """极简 YAML 头解析：只取顶层标量，够用即可，不引 PyYAML"""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    head = text[3:end]
    out = {}
    for line in head.splitlines():
        m = re.match(r'([A-Za-z_]+)\s*:\s*(.*)$', line)
        if not m:
            continue
        k, v = m.group(1), m.group(2).strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in '"\'':
            v = v[1:-1]
        out[k] = v
    return out


def scan_skill(path, scope):
    sp = os.path.join(path, "SKILL.md")
    if not os.path.exists(sp):
        return None
    text = open(sp, encoding="utf-8", errors="ignore").read()
    fm = parse_front(text)
    name = fm.get("name") or os.path.basename(path)
    display = fm.get("display_name") or ""
    if not display:
        # 很多平台技能把中文名只写在 agents/*.yaml 里，补一次回落
        for cand in ("agents/doubao.yaml", "agents/openai.yaml"):
            p2 = os.path.join(path, cand)
            if not os.path.exists(p2):
                continue
            m = re.search(r'display_name:\s*"?(.+?)"?\s*$',
                          open(p2, encoding="utf-8", errors="ignore").read(), re.M)
            if m:
                display = m.group(1).strip()
                break
    desc = fm.get("description") or ""
    scripts = []
    d = os.path.join(path, "scripts")
    if os.path.isdir(d):
        scripts = sorted(f for f in os.listdir(d) if not f.startswith((".", "__")))
    refs = []
    r = os.path.join(path, "references")
    if os.path.isdir(r):
        refs = sorted(f for f in os.listdir(r) if not f.startswith("."))
    deps = []
    for f in scripts:
        fp = os.path.join(path, "scripts", f)
        if not f.endswith((".py", ".sh")):
            continue
        try:
            body = open(fp, encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        for k in DEPS:
            if k in body and k not in deps:
                deps.append(k)
    return {
        "name": name, "display": display, "desc": desc,
        "path": path, "scope": scope,
        "scripts": scripts, "refs": refs, "deps": deps,
        "desc_len": len(desc),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", action="append", default=None, help="技能根目录，可多次给")
    ap.add_argument("--out", default=None, help="索引 JSON 落盘路径")
    ap.add_argument("--md", action="store_true", help="额外打印 Markdown 表")
    a = ap.parse_args()

    roots = a.root or DEFAULT_ROOTS
    rows, seen = [], set()
    for root in roots:
        if not os.path.isdir(root):
            print(f"[WARN] 根目录不存在：{root}", file=sys.stderr)
            continue
        scope = "user" if root.rstrip("/").endswith(".user_skills") else "platform"
        for dn in sorted(os.listdir(root)):
            p = os.path.join(root, dn)
            if not os.path.isdir(p) or dn.startswith("."):
                continue
            rec = scan_skill(p, scope)
            if not rec or rec["name"] in seen:
                continue
            seen.add(rec["name"])
            rows.append(rec)

    print(f"技能 {len(rows)} 个（user {sum(1 for r in rows if r['scope']=='user')} / "
          f"platform {sum(1 for r in rows if r['scope']=='platform')}）")
    nod = [r["name"] for r in rows if not r["desc"] or r["desc"].startswith("TODO")]
    if nod:
        print(f"[WARN] 说明为空或仍是 TODO：{', '.join(nod)}")
    if a.md:
        print("\n| 技能 | 显示名 | 脚本 | 依赖 | 说明(前40字) |")
        print("|---|---|---|---|---|")
        for r in rows:
            print(f"| `{r['name']}` | {r['display'] or '—'} | {len(r['scripts'])} | "
                  f"{'/'.join(r['deps']) or '—'} | {r['desc'][:40].replace('|', '/')} |")
    if a.out:
        json.dump(rows, open(a.out, "w"), ensure_ascii=False, indent=1)
        print(f"\n索引 -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

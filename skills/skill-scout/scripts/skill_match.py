#!/usr/bin/env python3
"""需求匹配：把你的意图和已装技能索引比对，判断"已覆盖 / 部分覆盖 / 没覆盖"。

用法：
  python3 skill_match.py "周三要出联赛总结报告"
  python3 skill_match.py "把云盘一个文件夹批量转 markdown" --top 5
  python3 skill_match.py --candidate ./某个外部skill/SKILL.md    # 评估外部候选值不值得装
  python3 skill_index.py --out /tmp/idx.json && python3 skill_match.py "..." --index /tmp/idx.json

打分口径：中文二元组 + 英文词重合，按需求侧词量归一（0–1）。阈值仅用于分流建议，不替代判断。
"""
import argparse
import json
import os
import re
import sys
import tempfile

CJK = re.compile(r"[\u4e00-\u9fff]+")
LAT = re.compile(r"[a-zA-Z][a-zA-Z0-9_\-]{1,}")
STOP = {"帮我", "一下", "一个", "这个", "那个", "可以", "需要", "怎么", "如何", "什么",
        "现在", "今天", "明天", "我们", "我的", "你们", "不要", "不是", "就是", "还有"}


def grams(text):
    """中文二元组 + 英文词（小写），去掉纯停用词"""
    out = set()
    for run in CJK.findall(text or ""):
        if len(run) == 1:
            continue
        for i in range(len(run) - 1):
            g = run[i:i + 2]
            if g not in STOP:
                out.add(g)
    for w in LAT.findall(text or ""):
        out.add(w.lower())
    return out


def score(need_g, text_g):
    if not need_g:
        return 0.0
    hit = need_g & text_g
    return len(hit) / len(need_g), hit


def verdict(s):
    if s >= 0.40:
        return "覆盖"
    if s >= 0.18:
        return "部分覆盖"
    return "无关"


def load_index(path):
    return json.load(open(path, encoding="utf-8"))


def build_index(roots=None):
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(tempfile.gettempdir(), "skill_index_match.json")
    cmd = [sys.executable, os.path.join(here, "skill_index.py"), "--out", out]
    if roots:
        for r in roots:
            cmd += ["--root", r]
    rc = os.system(" ".join(cmd) + " >/dev/null 2>&1")
    if rc != 0 or not os.path.exists(out):
        sys.exit("索引构建失败，请先单独运行 skill_index.py 看错误")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("need", nargs="?", help="一句话说明你要做的事")
    ap.add_argument("--index", default=None)
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--candidate", default=None, help="外部候选的 SKILL.md 路径，评估重叠与依赖")
    ap.add_argument("--root", action="append", default=None)
    a = ap.parse_args()

    idx_path = a.index or build_index(a.root)
    rows = load_index(idx_path)

    # ---- 外部候选评估模式 ----
    if a.candidate:
        text = open(a.candidate, encoding="utf-8", errors="ignore").read()
        m = re.search(r"---(.*?)---", text, re.S)
        head = m.group(1) if m else ""
        name = (re.search(r"name:\s*([^\n]+)", head) or [None, os.path.basename(os.path.dirname(a.candidate))])[1].strip()
        desc = (re.search(r"description:\s*([^\n]+)", head) or [None, ""])[1].strip()
        g = grams(desc)
        best = sorted(((score(g, grams(r["desc"] + " " + r["display"]))[0], r) for r in rows),
                      key=lambda x: -x[0])[:3]
        print(f"候选：{name}\n说明：{desc[:120]}\n")
        print("与本机已装技能的最大重叠：")
        for s, r in best:
            print(f"  {s:.2f}  {r['name']}（{r['display'] or '—'}）")
        dup = best[0][0] if best else 0
        print("\n结论：" + ("高重复，除非它有明确增量，否则不建议装" if dup >= 0.5 else
                          "有互补空间，可对照下表判断" if dup >= 0.25 else "基本不重叠，属新增能力"))
        dep = [k for k in ("lark-cli", "curl", "requests", "playwright", "selenium", "openai")
               if k in text]
        print("依赖线索：" + ("、".join(dep) if dep else "未识别"))
        return 0

    if not a.need:
        ap.error("需要一句需求描述，或用 --candidate 评估外部候选")

    ng = grams(a.need)
    ranked = []
    for r in rows:
        if r["name"] == "skill-scout":      # 不把侦察技能自己算进候选
            continue
        g = grams(r["desc"] + " " + r["display"] + " " + r["name"].replace("-", " "))
        s, hit = score(ng, g)
        if s > 0:
            ranked.append((s, r, hit))
    ranked.sort(key=lambda x: -x[0])

    print(f"需求：{a.need}\n")
    if not ranked:
        print("无任何技能命中关键词 → 建议自建，或先看 skill_index.py --md 全表。")
        return 0

    print(f"{'分':<5}{'判定':<8}{'技能'}")
    for s, r, hit in ranked[:a.top]:
        print(f"{s:<5.2f}{verdict(s):<8}{r['name']}（{r['display'] or r['desc'][:12]}）｜脚本 {len(r['scripts'])}｜依赖 {'/'.join(r['deps']) or '—'}")
        print(f"      命中词：{'、'.join(sorted(hit)[:8])}")

    top_s = ranked[0][0]
    print("\n结论：" + ("直接用它，无需新建" if top_s >= 0.40 else
                       "只有部分覆盖，缺的那块看要不要补脚本（别急着新建整个 skill）" if top_s >= 0.18 else
                       "没技能覆盖这个需求，建议新建或从外部找，先用 --candidate 评估候选"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

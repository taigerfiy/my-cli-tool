#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""017 首发阵容两份 md 渲染器。

输入一份结构化 JSON（schema 见 assets/sample_match.json），按云盘 017 既有模板
渲染「首发阵容数据（主表）」与「首发补充数据」两份 Markdown。

用法：
    python3 render_lineup_md.py --input match.json --out-dir ./out

只做确定性格式渲染；名单/号码/换人/伤停等事实由截图提取 + 在线核对后填入 JSON，
脚本不补造缺失字段：空值统一写「未提供」。
"""
import argparse
import json
import os
import sys

MISSING = "未提供"


def _cell(v):
    return v if (v is not None and str(v).strip() != "") else MISSING


def render_main(data):
    L = []
    L.append(f"# {data['season']}{data['league']}{data['round']}首发阵容数据\n")
    L.append(f"# {data['league']}联赛首发阵容与基础数据汇总 ({data['report_range']})\n")
    for m in data["matches"]:
        L.append(f"## {m['no']}. {m['home']} {m['score']} {m['away']} ({m['kick']})\n")
        for team_key in ("home_team", "away_team"):
            t = m[team_key]
            L.append(f"### {t['name']} ({t['formation']})\n")
            L.append(
                f"教练: {_cell(t.get('coach'))} | 首发身价: {_cell(t.get('value'))} | "
                f"平均身高: {_cell(t.get('height'))} | 平均年龄: {_cell(t.get('age'))}\n"
            )
            L.append("|位置|号码|球员|")
            L.append("|-|-|-|")
            for p in t["starters"]:
                L.append(f"|{_cell(p.get('pos'))}|{_cell(p.get('num'))}|{_cell(p.get('name'))}|")
            L.append("")
            if len(t["starters"]) != 11:
                print(f"⚠ {t['name']} 首发 {len(t['starters'])} 人（应为 11），请核对", file=sys.stderr)
    return "\n".join(L).rstrip() + "\n"


def _bullet(label, v):
    v = v if (v is not None and str(v).strip() != "") else "-"
    return f"- {label}：{v}"


def render_supplement(data):
    L = []
    L.append(f"# {data['season']} {data['league']}{data['round']} 首发补充数据（AI赛后补录）\n")
    L.append(
        f"本文件为「{data['season']}{data['league']}{data['round']}首发阵容数据」的补充字段"
        "（伤停/替补调整/发挥好/发挥差）。原始首发数据来自用户截图，经权威源交叉核对；"
        "此处只记录事实不分析。"
    )
    L.append(f"补录时间：{data.get('supplement_note', MISSING)}\n")
    for m in data["matches"]:
        kick_date = m["kick"].split(" ")[0]
        L.append(f"## 第{m['no']}场 {m['home']} {m['score']} {m['away']} ({kick_date})\n")
        for team_key in ("home_team", "away_team"):
            t = m[team_key]
            L.append(f"### {t['name']}\n")
            L.append(_bullet("伤停/缺阵", t.get("injuries")))
            L.append(_bullet("替补调整", t.get("subs")))
            L.append(_bullet("发挥好", t.get("good")))
            L.append(_bullet("发挥差", t.get("bad")))
            L.append("")
    notes = data.get("notes") or []
    if notes:
        L.append("---")
        L.append("## 口径与待核备注\n")
        for n in notes:
            L.append(f"- {n}")
    return "\n".join(L).rstrip() + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    data = json.load(open(args.input, encoding="utf-8"))
    os.makedirs(args.out_dir, exist_ok=True)
    base = f"{data['season']}{data['league']}{data['round']}"
    main_path = os.path.join(args.out_dir, f"{base}首发阵容数据.md")
    supp_path = os.path.join(args.out_dir, f"{base}首发补充数据.md")
    open(main_path, "w", encoding="utf-8").write(render_main(data))
    open(supp_path, "w", encoding="utf-8").write(render_supplement(data))
    print("written:", main_path)
    print("written:", supp_path)


if __name__ == "__main__":
    main()

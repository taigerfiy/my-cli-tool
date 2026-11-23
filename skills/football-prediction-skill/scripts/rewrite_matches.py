#!/usr/bin/env python3
"""把竞彩官方赛单里的中文队名改写成模型库里的规范英文名。

为什么要这一步：`football-predict` 的模型键是 football-data 的缩写名
（Man City / Ath Madrid / Paris SG / Nott'm Forest …），而 `sync` 出来的赛单是
中文短名（曼城、马竞、巴黎、布伦特）。名字对不上时 daily 会静默用“联赛均值”
去算一篇 statistical，概率看着有值但完全不可用。

用法：
  python3 scripts/rewrite_matches.py matches_YYYY-MM-DD.json --out matches_en.json
  # 只做体检，不落盘
  python3 scripts/rewrite_matches.py matches_2026-11-23.json --check

退出码：0 = 有模型的联赛全部解析成功；1 = 有模型覆盖的联赛存在未解析队名。
没有模型的联赛（瑞超/巴甲/美职/法乙…）原样保留，daily 会走 market_baseline。
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ALIAS_PATH = Path(__file__).resolve().parent.parent / "references" / "team_alias_cn.csv"
MODELS_DIR = Path.home() / ".local/share/football-prediction-skill/models"


def load_alias() -> dict[tuple[str, str], str]:
    rows = csv.DictReader(open(ALIAS_PATH, encoding="utf-8"))
    return {(r["cn"].strip(), r["competition"].strip()): r["canonical"].strip() for r in rows}


def covered_competitions() -> set[str]:
    comps = set()
    for p in MODELS_DIR.glob("*.json"):
        if p.name == "registry.json":
            continue
        try:
            comp = json.load(open(p)).get("metadata", {}).get("competition")
        except json.JSONDecodeError:
            continue
        if comp:
            comps.add(comp)
    return comps


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--out")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    data = json.load(open(a.path, encoding="utf-8"))
    alias = load_alias()
    covered = covered_competitions()
    resolved, unresolved, untouched = [], [], []
    for m in data.get("matches", []):
        league, home, away = m.get("league"), m.get("home"), m.get("away")
        if league not in covered:
            untouched.append(f"{m.get('match_no')} {league} {home} vs {away}（无模型覆盖）")
            continue
        h = alias.get((home, league)) or alias.get((home, ""))
        w = alias.get((away, league)) or alias.get((away, ""))
        missing = [n for n, v in ((home, h), (away, w)) if not v]
        if missing:
            unresolved.append(f"{m.get('match_no')} {league} {home} vs {away}｜未解析：{'、'.join(missing)}")
            continue
        resolved.append(f"{m.get('match_no')} {home}->{h} / {away}->{w}")
        m["home"], m["away"] = h, w

    if a.json:
        print(json.dumps({"resolved": resolved, "unresolved": unresolved,
                          "untouched": untouched}, ensure_ascii=False, indent=1))
    else:
        print(f"解析成功 {len(resolved)}｜未解析 {len(unresolved)}｜无模型覆盖 {len(untouched)}")
        for r in resolved:
            print("  OK   " + r)
        for r in unresolved:
            print("  FAIL " + r)
        for r in untouched:
            print("  SKIP " + r)

    if not a.check and a.out:
        Path(a.out).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"已写出 {a.out}")
    return 0 if not unresolved else 1


if __name__ == "__main__":
    raise SystemExit(main())

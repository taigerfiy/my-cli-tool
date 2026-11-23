#!/usr/bin/env python3
"""把 daily 产物里的规范英文队名还原成中文，保持中文报告与 Step3 复盘 join 不变形。

背景：模型只认识 football-data 缩写名，所以 daily 必须吃 `matches_en.json`；
但报告和 `prediction_*.json` 是要给人和台账用的，得换回中文。

用法：
  python3 scripts/localize_outputs.py \
    --pred /out/prediction_2026-11-23.json \
    --html /out/report_2026-11-23.html \
    --inplace
备份：--inplace 时原文件另存 *.en.bak。prediction 里每场保留 home_model/away_model 便于追溯。
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
from pathlib import Path

ALIAS_PATH = Path(__file__).resolve().parent.parent / "references" / "team_alias_cn.csv"


def load_reverse() -> dict[tuple[str, str], str]:
    """(competition, canonical) -> 中文短名；同队多别名取最短的一个（贴近官方短名）。"""
    rows = csv.DictReader(open(ALIAS_PATH, encoding="utf-8"))
    best: dict[tuple[str, str], str] = {}
    for r in rows:
        key = (r["competition"].strip(), r["canonical"].strip())
        cn = r["cn"].strip()
        if key not in best or len(cn) < len(best[key]):
            best[key] = cn
    return best


def localize_pred(path: Path, rev: dict[tuple[str, str], str], inplace: bool, dry: bool) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    changed = 0
    for p in data.get("predictions", []):
        m = p.get("match") or {}
        comp = m.get("league")
        for side in ("home", "away"):
            canon = m.get(side)
            cn = rev.get((comp, canon)) or rev.get(("", canon))
            if canon and cn and canon != cn:
                m[f"{side}_model"] = canon
                m[side] = cn
                changed += 1
    if inplace and not dry:
        shutil.copy2(path, path.with_suffix(path.suffix + ".en.bak"))
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return changed


def localize_html(path: Path, rev: dict[tuple[str, str], str], inplace: bool, dry: bool) -> tuple[int, list[str]]:
    names = sorted({c for (_comp, c) in rev}, key=len, reverse=True)
    text = path.read_text(encoding="utf-8")
    # 只在 </style> 之后做替换：CSS 里的字体名（Inter/Segoe UI）会被误伤
    head, _, body = text.partition("</style>")
    if not body:
        head, body = "", text
    hits: dict[str, str] = {}
    for canon in names:
        cn = rev.get(("", canon)) or next((v for (c, k), v in rev.items() if k == canon), None)
        if not cn or len(canon) < 4:
            continue
        pat = re.compile(rf"(?<![\w]){re.escape(canon)}(?![\w])")
        if pat.search(body):
            hits[canon] = cn
            body = pat.sub(cn, body)
    text = head + "</style>" + body if head else body
    if inplace and not dry:
        shutil.copy2(path, path.with_suffix(path.suffix + ".en.bak"))
        path.write_text(text, encoding="utf-8")
    return len(hits), sorted(hits)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred")
    ap.add_argument("--html")
    ap.add_argument("--inplace", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    rev = load_reverse()
    if a.pred:
        n = localize_pred(Path(a.pred), rev, a.inplace, a.dry_run)
        print(f"prediction 队名还原 {n} 处 -> {a.pred}")
    if a.html:
        n, names = localize_html(Path(a.html), rev, a.inplace, a.dry_run)
        print(f"HTML 命中 {n} 支队：{'、'.join(names) if names else '无'}")
    if not a.inplace and not a.dry_run:
        print("只体检未落盘；需要改写加 --inplace")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""预测产物概率门禁：任何一场 prob 为空都不许发布。

用法：
  python3 scripts/check_prob.py /tmp/football-predict-YYYY-MM-DD/prediction_YYYY-MM-DD.json [--json]

退出码：0 = 全部有概率；1 = 存在 null/不可用；2 = 文件缺失或无法解析。
除 null 外还给出每条预测的口径标签：
  analysis_mode = statistical（模型主导）| market_baseline（只有市场共识）
  calibration_status = horizon-mismatch 等说明模型与预测时点是否匹配。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

MODELS_DIR = Path.home() / ".local/share/football-prediction-skill/models"


def model_teams() -> dict[str, set[str]]:
    """每个联赛当前模型认识的全部队名（football-data 缩写）。"""
    out: dict[str, set[str]] = {}
    for f in MODELS_DIR.glob("*.json"):
        if f.name == "registry.json":
            continue
        try:
            m = json.load(open(f, encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        comp = m.get("metadata", {}).get("competition")
        teams = m.get("model", {}).get("teams") or []
        if comp and teams:
            out[comp] = set(teams)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    try:
        data = json.load(open(a.path, encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"无法读取 {a.path}: {e}", file=sys.stderr)
        return 2

    TEAMS = model_teams()
    preds = data.get("predictions") or []
    bad, suspect, rows = [], [], []
    for p in preds:
        probs = p.get("final_probs") or {}
        ok = all(isinstance(probs.get(k), (int, float)) for k in ("home", "draw", "away"))
        if ok:
            s = sum(probs[k] for k in ("home", "draw", "away"))
            ok = 0.985 <= s <= 1.015
        _m = p.get("match") or {}
        known = TEAMS.get(_m.get("league"))
        # 本地化之后 match.home 已变中文，用 *_model 字段还原模型实际用的队名
        home_key = _m.get("home_model") or _m.get("home")
        away_key = _m.get("away_model") or _m.get("away")
        phantom = (ok and p.get("analysis_mode") == "statistical" and known is not None
                   and (home_key not in known or away_key not in known))
        mid = (p.get("match") or {})
        rows.append({
            "match_no": mid.get("match_no"), "league": mid.get("league"),
            "home": mid.get("home"), "away": mid.get("away"),
            "has_prob": bool(ok),
            "phantom_model": phantom,
            "probs": {k: round(probs[k], 3) for k in ("home", "draw", "away")} if ok else None,
            "analysis_mode": p.get("analysis_mode"),
            "calibration_status": p.get("calibration_status"),
            "model_version": data.get("model_version"),
        })
        if not ok:
            bad.append(rows[-1])
        elif phantom:
            suspect.append(rows[-1])

    if a.json:
        print(json.dumps({"file": a.path, "total": len(preds), "null_count": len(bad),
                          "phantom_count": len(suspect), "rows": rows},
                         ensure_ascii=False, indent=1))
    else:
        print(f"场次 {len(preds)}｜概率缺失 {len(bad)}｜模型空转 {len(suspect)}｜model={data.get('model_version')}"
              f"｜calibration={data.get('calibration_status')}")
        for r in rows:
            flag = "OK  " if r["has_prob"] and not r["phantom_model"] else ("SUSP" if r["phantom_model"] else "NULL")
            print(f"{flag} {r['match_no'] or '-':<8} {r['league'] or '-':<5} "
                  f"{r['home']} vs {r['away']}  "
                  f"{r['probs'] if r['has_prob'] else '—'}  [{r['analysis_mode']}]")
    return 0 if not bad and not suspect else 1


if __name__ == "__main__":
    raise SystemExit(main())

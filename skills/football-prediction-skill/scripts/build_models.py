#!/usr/bin/env python3
"""批量训练/查看足球预测模型库。

用法：
  python3 scripts/build_models.py --list
  python3 scripts/build_models.py --horizon t24h            # 五大联赛全量训练（challenger）
  python3 scripts/build_models.py --horizon closing --force-promote --league 英超
  python3 scripts/build_models.py --status                  # 只导出 models_status.md

设计约束（与 references/training.md 一致）：
1. 一模型一联赛，至少 3 个完整赛季；
2. 默认不晋级，只有显式 --promote / --force-promote 才动生产位；
3. closing 档的 football-data 赔率只是市场基线，模型很难“优于市场 0.5%”，
   未通过不是缺陷；需要独立概率时用 t24h 档（收盘赔率被隔离）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

LEAGUES = {
    "英超": {"code": "E0", "alias": "Premier League"},
    "西甲": {"code": "SP1", "alias": "La Liga"},
    "意甲": {"code": "I1", "alias": "Serie A"},
    "德甲": {"code": "D1", "alias": "Bundesliga"},
    "法甲": {"code": "F1", "alias": "Ligue 1"},
    "荷甲": {"code": "N1", "alias": "Eredivisie"},
    "葡超": {"code": "P1", "alias": "Primeira Liga"},
}
DEFAULT_SEASONS = ["2324", "2425", "2526", "2627"]
DEFAULT_DATA_DIR = Path("/home/user/Doubao/jczq_shared/fd_raw")
SKILL_DIR = Path(__file__).resolve().parent.parent
STATUS_PATH = SKILL_DIR / "references" / "models_status.md"
CLI = os.environ.get("FOOTBALL_PREDICT_BIN", "football-predict")


def season_files(data_dir: Path, code: str, seasons: list[str]) -> list[Path]:
    files = []
    for s in seasons:
        p = data_dir / f"{s}_{code}.csv"
        if p.exists():
            files.append(p)
        else:
            print(f"  ! 缺 {p.name}，跳过该季", file=sys.stderr)
    return files


def models_json() -> list[dict]:
    out = subprocess.run([CLI, "models", "--json"], capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"models --json 失败：{out.stderr.strip()}")
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return []


def train_one(league: str, args, data_dir: Path) -> dict:
    cfg = LEAGUES[league]
    files = season_files(data_dir, cfg["code"], args.seasons)
    if len(files) < 3:
        return {"league": league, "ok": False, "reason": f"可用赛季 {len(files)} 个，不足 3 个"}
    cmd = [CLI, "train", *map(str, files), "--competition", league,
           "--alias", cfg["alias"], "--horizon", args.horizon, "--no-open"]
    if args.promote:
        cmd.append("--promote")
    if args.force_promote:
        cmd.append("--force-promote")
    print(f"\n== {league} ({cfg['code']}) {len(files)} 季 · horizon={args.horizon}")
    print("   " + " ".join(cmd[:4]) + " …")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    log = (proc.stdout + proc.stderr).strip()
    version = None
    m = re.search(r"模型版本：(\S+)", log)
    if m:
        version = m.group(1)
    fails = re.findall(r"^未通过 · (.+)$", log, flags=re.M)
    status = re.search(r"状态：(\S+)", log)
    if proc.returncode != 0 and not version:
        print("  x 训练失败：" + log.splitlines()[-1][:200])
        return {"league": league, "ok": False, "reason": log.splitlines()[-1][:200]}
    print(f"  -> {version} · {status.group(1) if status else '?'}")
    for f in fails:
        print(f"  未通过 · {f}")
    return {"league": league, "ok": True, "version": version,
            "status": status.group(1) if status else "unknown", "fails": fails}


def write_status(extra: list[dict] | None = None) -> Path:
    models = models_json()
    lines = ["# 模型库现状（自动生成）", "",
             f"- 生成时间：{__import__('datetime').datetime.now().astimezone().isoformat(timespec='seconds')}",
             f"- 注册模型数：{len(models)}", ""]
    if models:
        lines += ["| 版本 | 联赛 | 时点 | 训练截止 | 状态 | 生产位 |", "|---|---|---|---|---|---|"]
        for m in sorted(models, key=lambda x: (x["competition"], x["forecast_horizon"])):
            promoted = "是" if m.get("promoted") else "否"
            lines.append(f"| {m['version']} | {m['competition']} | {m['forecast_horizon']} | "
                         f"{m['trained_until']} | {m['calibration_status']} | {promoted} |")
    else:
        lines.append("模型注册表为空。")
    if extra:
        lines += ["", "## 本次训练", ""]
        for r in extra:
            if r["ok"]:
                lines.append(f"- {r['league']}：{r['version']} · {r['status']}"
                             + (f" · 未通过 {len(r['fails'])} 项" if r["fails"] else ""))
            else:
                lines.append(f"- {r['league']}：失败（{r['reason']}）")
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return STATUS_PATH


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", action="append", choices=list(LEAGUES),
                    help="只训练指定联赛，可重复；默认五大联赛")
    ap.add_argument("--horizon", default="t24h", choices=["t24h", "t6h", "t90m", "closing"])
    ap.add_argument("--seasons", nargs="+", default=DEFAULT_SEASONS)
    ap.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    ap.add_argument("--promote", action="store_true", help="通过门槛才晋级")
    ap.add_argument("--force-promote", action="store_true", help="研究用途强制晋级（不得表述为通过验证）")
    ap.add_argument("--list", action="store_true", help="只列出现有模型")
    ap.add_argument("--status", action="store_true", help="只刷新 models_status.md")
    args = ap.parse_args()

    if args.list:
        models = models_json()
        if not models:
            print("模型注册表为空")
            return 0
        for m in models:
            print(f"{m['version']:<44} {m['competition']:<6} {m['forecast_horizon']:<8} "
                  f"{m['trained_until']} {'promoted' if m.get('promoted') else m['calibration_status']}")
        return 0

    if args.status:
        print(write_status())
        return 0

    leagues = args.league or ["英超", "西甲", "意甲", "德甲", "法甲"]
    data_dir = Path(args.data_dir)
    results = [train_one(l, args, data_dir) for l in leagues]
    path = write_status(results)
    print(f"\n模型现状已写入 {path}")
    print(f"合计成功 {sum(1 for r in results if r['ok'])}/{len(results)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""check_odds_vs_008.py — 外部盘口快照与 008 赔率时间线的时点交叉校验

背景：懂球帝 lotteryOddsNew 页面**没有时间戳**，且实测会出现两种错误：
    1) 页面上「即时」一列可能残留早段数值（抓取时点 19:25，数值却等于 11:30）；
    2) 大小球行三列顺序为「水位｜盘口｜水位」，直接按「盘口｜大｜小」解析会把大/小水位互换。
这两种错误都会让下游的「欧亚背离 / 升降盘」结论失真，因此必须在交付前拦截。

用法：
    python3 scripts/check_odds_vs_008.py --odds odds_006.json --report 008_261117_终报.md
    python3 scripts/check_odds_vs_008.py --odds odds_006.json --report report.md --tol-eu 0.03
    python3 scripts/check_odds_vs_008.py --columns "0.76 3 1.04"      # 无 008 时的列序自校

退出码：
    0 = PASS（无滞后、无顺序错位；或早于报表首时点，标 WARN 且禁止用于背离）
    1 = FAIL（检出时点滞后或大/小水位互换，整段不得交付、不得用于背离判定）
    2 = 用法错误

odds JSON（schema=odds-v1）：
{
  "schema": "odds-v1",
  "match_id": "周二006",
  "captured_at": "2026-11-17T19:25:00+08:00",
  "jcsp":    {"initial": [2.40, 3.50, 2.29], "current": [2.38, 3.78, 2.20]},
  "europe":  {"initial": [2.46, 3.61, 2.48], "current": [2.28, 3.78, 2.70]},
  "asian": {
    "macau": {"initial": {"line": 0.0, "up": 0.96, "down": 0.88},
              "current": {"line": 0.0, "up": 0.84, "down": 1.00}},
    "crown": {...可省略...}
  },
  "total": {
    "macau": {"initial": {"line": 3.0, "over": 0.91, "under": 0.89},
              "current": {"line": 3.0, "over": 0.76, "under": 1.04}},
    "b365": {...可省略...}
  }
}
"""

import json
import re
import sys
from datetime import datetime

# ---------- 让球值 / 总进球线换算 ----------

LINE_MAP = {
    "平手": 0.0,
    "平/半": 0.25, "平手/半球": 0.25,
    "半球": 0.5,
    "半/一": 0.75, "半球/一球": 0.75,
    "一球": 1.0,
    "一/球半": 1.25, "一球/球半": 1.25,
    "球半": 1.5,
    "球半/两球": 1.75,
    "两球": 2.0, "二球": 2.0,
    "两/两球半": 2.25,
    "两球半": 2.5, "二球半": 2.5,
    "两球半/三球": 2.75,
    "三球": 3.0,
    "三/三球半": 3.25,
    "三球半": 3.5,
    "三球半/四球": 3.75,
    "四球": 4.0,
    "四/四球半": 4.25,
    "四球半": 4.5,
    "四球半/五球": 4.75,
    "五球": 5.0,
}

ASIAN_RE = re.compile(r"^(受)?\s*(.+?)\s*上\s*([0-9.]+)\s*下\s*([0-9.]+)\s*$")
TOTAL_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)?(?:/[0-9]+(?:\.[0-9]+)?)?)\s*大\s*([0-9.]+)\s*小\s*([0-9.]+)\s*$")
TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")


def to_line(token):
    """让球中文 → 数值；带『受』返回负值。解析不出返回 None。"""
    token = token.strip()
    receiver = token.startswith("受")
    if receiver:
        token = token[1:].strip()
    for key in (token, token.replace("／", "/")):
        if key in LINE_MAP:
            value = LINE_MAP[key]
            return -value if receiver else value
    return None


def to_total_line(token):
    """『3』→3.0；『3/3.5』→3.25（取均数，不取整）。"""
    parts = [float(p) for p in token.split("/")]
    return round(sum(parts) / len(parts), 2)


# ---------- 008 报告解析 ----------

def parse_report(path):
    """返回 {match_id: {"europe": {time: [3]}, "asian": {time: {company: {...}}, "total": {time: {company: {...}}}}}"""
    series = {}
    section = None
    last_match = None

    with open(path, encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if line.startswith("## "):
                if "欧赔时间线" in line:
                    section = "europe"
                elif "亚盘与大小球时间线" in line:
                    section = "asian"
                elif line.startswith("## "):
                    section = None
                continue
            if not line.startswith("|") or section is None:
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) < 6:
                continue
            time_label = cells[1]
            if not TIME_RE.match(time_label):
                # 表头 / 分隔行 / 非时间序列行
                continue
            match_id = cells[0] or last_match
            if not match_id:
                continue
            last_match = match_id
            entry = series.setdefault(match_id, {"europe": {}, "asian": {}, "total": {}})

            if section == "europe":
                mean = cells[5]
                values = [float(x) for x in re.findall(r"[0-9]+\.[0-9]+", mean)]
                if len(values) == 3:
                    entry["europe"][time_label] = values
            else:
                for idx, company in ((2, "macau"), (3, "crown")):
                    if idx >= len(cells):
                        continue
                    m = ASIAN_RE.match(cells[idx])
                    if not m:
                        continue
                    line_val = to_line((m.group(1) or "") + m.group(2))
                    if line_val is None:
                        continue
                    entry["asian"].setdefault(time_label, {})[company] = {
                        "line": line_val,
                        "up": float(m.group(3)),
                        "down": float(m.group(4)),
                    }
                for idx, company in ((5, "macau"), (6, "b365")):
                    if idx >= len(cells):
                        continue
                    m = TOTAL_RE.match(cells[idx])
                    if not m:
                        continue
                    entry["total"].setdefault(time_label, {})[company] = {
                        "line": to_total_line(m.group(1)),
                        "over": float(m.group(2)),
                        "under": float(m.group(3)),
                    }
    return series


# ---------- 校验 ----------

def rel_diff(a, b):
    if not b:
        return 0.0
    return abs(a - b) / b


def check_one(odds, series, tol_eu=0.03, tol_water=0.05, tol_line=0.25):
    issues, notes = [], []
    match_id = odds.get("match_id")
    captured_at = odds.get("captured_at")
    if not match_id or not captured_at:
        return ["odds JSON 缺少 match_id 或 captured_at"], []

    try:
        captured = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    except ValueError:
        return [f"captured_at 不是合法 ISO 8601：{captured_at}"], []

    if match_id not in series:
        return [f"008 报表中找不到 {match_id}，无法交叉校验"], []

    entry = series[match_id]
    available = set(entry["europe"]) | set(entry["asian"]) | set(entry["total"])
    usable = []
    for label in available:
        try:
            stamp = datetime.fromisoformat(f"{captured.date()}T{label}:00{captured.strftime('%z') or '+08:00'}")
        except ValueError:
            continue
        if stamp <= captured:
            usable.append((label, stamp))
    if not usable:
        return [], [f"{match_id} 采集时点早于报表首时点，无法交叉校验；该场盘口不得用于背离判定"]

    ref = max(usable, key=lambda x: x[1])[0]
    notes.append(f"{match_id} 参照时点 {ref}（采集 {captured_at}）")

    eu = odds.get("europe", {}).get("current")
    if eu and ref in entry["europe"]:
        ref_eu = entry["europe"][ref]
        worst = max(rel_diff(eu[i], ref_eu[i]) for i in range(3))
        if worst > tol_eu:
            issues.append(
                f"[STALE_EU] 欧指即时 {'/'.join(f'{v:.2f}' for v in eu)} 与 {ref} 的 "
                f"{'/'.join(f'{v:.2f}' for v in ref_eu)} 最大偏差 {worst:.1%} > {tol_eu:.0%}，判定页面残留早段值"
            )

    for company in ("macau", "crown"):
        cur = odds.get("asian", {}).get(company, {}).get("current")
        if not cur or company not in entry["asian"].get(ref, {}):
            continue
        ref_a = entry["asian"][ref][company]
        if abs(cur["line"] - ref_a["line"]) >= tol_line:
            issues.append(
                f"[STALE_LINE] 亚盘 {company} 让球 {cur['line']:+g} 与 {ref} 的 {ref_a['line']:+g} 相差 ≥{tol_line} 格"
            )
        if abs(cur["up"] - ref_a["up"]) >= tol_water or abs(cur["down"] - ref_a["down"]) >= tol_water:
            issues.append(
                f"[STALE_WATER] 亚盘 {company} 水位 {cur['up']:.2f}/{cur['down']:.2f} 与 {ref} 的 "
                f"{ref_a['up']:.2f}/{ref_a['down']:.2f} 相差 ≥{tol_water}"
            )

    for company in ("macau", "b365"):
        cur = odds.get("total", {}).get(company, {}).get("current")
        if not cur or company not in entry["total"].get(ref, {}):
            continue
        ref_t = entry["total"][ref][company]
        if (abs(cur["over"] - ref_t["under"]) < 1e-9 and abs(cur["under"] - ref_t["over"]) < 1e-9
                and abs(cur["over"] - ref_t["over"]) > 1e-9):
            issues.append(
                f"[SWAP_TOTAL_WATER] 大小球 {company} 大{cur['over']:.2f}/小{cur['under']:.2f} 与 {ref} 的 "
                f"大{ref_t['over']:.2f}/小{ref_t['under']:.2f} 正好互换，判定为「水位|盘口|水位」顺序误读"
                f"；纠正为大{ref_t['over']:.2f}/小{ref_t['under']:.2f}"
            )
        if abs(cur["line"] - ref_t["line"]) > 1e-9:
            issues.append(
                f"[STALE_TOTAL_LINE] 大小球 {company} 盘口 {cur['line']:g} 与 {ref} 的 {ref_t['line']:g} 不一致"
            )
    return issues, notes


def infer_columns(text):
    """大小球单行三数自校：无 008 时也能判定列序。

    返回 (ok, message)。规则：
      - 盘口必须落在 2.5–5.0；两侧水位必须落在 0.60–1.20。
      - 中间数是盘口 → 原页顺序为「水位｜盘口｜水位」，大=首、小=尾（需交换归位）。
      - 首数是盘口 → 原页顺序为「盘口｜大｜小」，按序记。
    """
    tokens = text.replace("｜", " ").replace("|", " ").split()
    if len(tokens) != 3:
        return False, f"期望 3 个数（盘口与两个水位），实际 {len(tokens)} 个：{text}"
    try:
        values = [float(t) for t in tokens]
    except ValueError:
        return False, f"存在非数值：{text}"

    def is_line(v):
        return 2.5 <= v <= 5.0

    def is_water(v):
        return 0.60 <= v <= 1.20

    if is_line(values[1]) and is_water(values[0]) and is_water(values[2]):
        return True, (
            f"列序＝水位｜盘口｜水位：盘口 {values[1]:g}，大球 {values[2]:.2f}，小球 {values[0]:.2f}"
            f"（原文 {text} 若按「盘口｜大｜小」读会把大/小互换）"
        )
    if is_line(values[0]) and is_water(values[1]) and is_water(values[2]):
        return True, f"列序＝盘口｜大｜小：盘口 {values[0]:g}，大球 {values[1]:.2f}，小球 {values[2]:.2f}"
    return False, f"三数无一符合「盘口 2.5–5.0 + 水位 0.60–1.20」的列序特征：{text}"


def main():
    args = sys.argv[1:]
    odds_path = report_path = None
    tol_eu = 0.03
    columns = None
    i = 0
    while i < len(args):
        if args[i] == "--odds" and i + 1 < len(args):
            odds_path = args[i + 1]; i += 2
        elif args[i] == "--report" and i + 1 < len(args):
            report_path = args[i + 1]; i += 2
        elif args[i] == "--tol-eu" and i + 1 < len(args):
            tol_eu = float(args[i + 1]); i += 2
        elif args[i] == "--columns" and i + 1 < len(args):
            columns = args[i + 1]; i += 2
        else:
            i += 1

    if columns:
        ok, message = infer_columns(columns)
        print(("  · " if ok else "  ISSUE · ") + message)
        print("\n结果：" + ("PASS" if ok else "FAIL — 列序无法自证，整段作废重抓"))
        return 0 if ok else 1

    if not odds_path or not report_path:
        print("用法：python3 scripts/check_odds_vs_008.py --odds <odds.json> --report <008 报告.md> [--tol-eu 0.03]")
        return 2

    with open(odds_path, encoding="utf-8") as handle:
        odds = json.load(handle)
    if isinstance(odds, list):
        print("FAIL · odds JSON 必须是单场对象，不要传数组")
        return 1
    if odds.get("schema") != "odds-v1":
        print(f"FAIL · odds schema 必须为 odds-v1，当前为 {odds.get('schema')!r}")
        return 1

    series = parse_report(report_path)
    issues, notes = check_one(odds, series, tol_eu=tol_eu)

    for note in notes:
        print(f"  · {note}")
    for issue in issues:
        print(f"  ISSUE · {issue}")

    if issues:
        print(f"\n结果：FAIL（{len(issues)} 项）— 该盘口段不得交付，不得用于欧亚背离/升降盘判定")
        return 1
    print("\n结果：PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

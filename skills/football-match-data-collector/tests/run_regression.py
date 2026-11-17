#!/usr/bin/env python3
"""run_regression.py — 情报包回归校验

用法（在 skill 根目录执行）：
    python3 tests/run_regression.py

做两件事：
    1. 用 scripts/validate_intel.py 跑 tests/cases/ 下的每个样本
    2. 把结果与 tests/expected.json 的期望值逐项对比（退出码/facts 条数/错误/告警/completeness）

任何一项不符即退出码 1。改完 Skill 后先跑这个，再上新批次。
"""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VALIDATOR = ROOT / "scripts" / "validate_intel.py"
CASES = ROOT / "tests" / "cases"
EXPECTED = ROOT / "tests" / "expected.json"

TOTAL_RE = re.compile(r"合计：(\d+) 场 / (\d+) 条 facts / (\d+) 错误 / (\d+) 告警")


def run_case(path):
    proc = subprocess.run(
        [sys.executable, str(VALIDATOR), str(path)],
        capture_output=True,
        text=True,
    )
    match = TOTAL_RE.search(proc.stdout)
    if not match:
        return None, proc
    summary = {
        "matches": int(match.group(1)),
        "facts": int(match.group(2)),
        "errors": int(match.group(3)),
        "warnings": int(match.group(4)),
    }
    return summary, proc


def compare(case, summary):
    """返回不符合项的列表，空列表表示通过。"""
    problems = []
    if case.get("expect_exit") is not None and summary["exit"] != case["expect_exit"]:
        problems.append(f"退出码 {summary['exit']} != 期望 {case['expect_exit']}")

    for field in ("matches", "facts", "warnings"):
        key = f"expect_{field}"
        if key in case and summary[field] != case[key]:
            problems.append(f"{field} {summary[field]} != 期望 {case[key]}")

    if "expect_errors" in case and summary["errors"] != case["expect_errors"]:
        problems.append(f"errors {summary['errors']} != 期望 {case['expect_errors']}")
    if "expect_errors_min" in case and summary["errors"] < case["expect_errors_min"]:
        problems.append(f"errors {summary['errors']} < 至少应有 {case['expect_errors_min']}")

    payload = json.loads(summary["payload_path"].read_text(encoding="utf-8"))
    got = {m.get("match_id"): m.get("completeness") for m in payload.get("matches", [])}
    for match_id, want in (case.get("expect_completeness") or {}).items():
        if got.get(match_id) != want:
            problems.append(f"completeness[{match_id}] {got.get(match_id)} != 期望 {want}")

    return problems


def main():
    if not EXPECTED.exists():
        print(f"FAIL · 缺少期望值文件 {EXPECTED}")
        return 2

    specs = json.loads(EXPECTED.read_text(encoding="utf-8"))["cases"]
    failed = 0

    for case in specs:
        path = CASES / Path(case["file"]).name
        if not path.exists():
            print(f"[FAIL] {path.name} · 样本文件不存在")
            failed += 1
            continue

        summary, proc = run_case(path)
        if summary is None:
            print(f"[FAIL] {path.name} · 校验器输出无法解析，退出码 {proc.returncode}")
            print(proc.stdout.strip()[:300])
            failed += 1
            continue

        summary["exit"] = proc.returncode
        summary["payload_path"] = path
        problems = compare(case, summary)

        if problems:
            print(f"[FAIL] {path.name} · {case.get('desc', '')}")
            for line in problems:
                print(f"       - {line}")
            failed += 1
        else:
            print(
                f"[PASS] {path.name} · 退出码 {summary['exit']} · "
                f"facts {summary['facts']} · 错误 {summary['errors']} · 告警 {summary['warnings']}"
            )

    print()
    print(f"回归结果：{len(specs)} 个样本 / {failed} 个不符")
    if failed:
        print("结论：FAIL，改动导致能力退化，先修再上批次")
        return 1
    print("结论：PASS，可以上新批次")
    return 0


if __name__ == "__main__":
    sys.exit(main())

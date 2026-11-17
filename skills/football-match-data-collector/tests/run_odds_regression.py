#!/usr/bin/env python3
"""run_odds_regression.py — 盘口时点/列序校验回归

用法（在 skill 根目录执行）：
    python3 tests/run_odds_regression.py

做的事：
    用 scripts/check_odds_vs_008.py 对 tests/cases/odds/ 下的样本逐条与 008 参照时点比对，
    结果与 tests/expected_odds.json 的期望逐项核对（退出码 + 必须出现的标记）。

为什么要有它：
    261117 实测暴露两个缺陷——懂球帝页面无时间戳导致「即时」列残留早段值；
    大小球行「水位｜盘口｜水位」被按「盘口｜大｜小」误读，大/小水位互换。
    这两类错误会让 Step2 的欧亚背离、升降盘结论整体失真，必须由脚本拦截而不是靠肉眼。
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKER = ROOT / "scripts" / "check_odds_vs_008.py"
CASES = ROOT / "tests" / "cases"
EXPECTED = ROOT / "tests" / "expected_odds.json"


def main():
    spec = json.loads(EXPECTED.read_text(encoding="utf-8"))
    report = CASES / Path(spec["report"]).relative_to("cases")
    if not report.exists():
        print(f"FAIL · 找不到参照报告 {report}")
        return 1

    problems = []
    for case in spec["cases"]:
        path = CASES / Path(case["file"]).relative_to("cases")
        proc = subprocess.run(
            [sys.executable, str(CHECKER), "--odds", str(path), "--report", str(report)],
            capture_output=True, text=True,
        )
        flags = [f for f in case.get("expect_flags", []) if f in proc.stdout]
        status = "PASS" if proc.returncode == case["expect_exit"] and len(flags) == len(case.get("expect_flags", [])) else "FAIL"
        print(f"[{status}] {path.name} · 退出码 {proc.returncode}（期望 {case['expect_exit']}）· 命中标记 {flags}")
        if proc.returncode != case["expect_exit"]:
            problems.append(f"{path.name} 退出码 {proc.returncode} != 期望 {case['expect_exit']}")
        missing = [f for f in case.get("expect_flags", []) if f not in proc.stdout]
        if missing:
            problems.append(f"{path.name} 未报出应有标记 {missing}")

    # 列序自校：无 008 时的兜底能力
    for text, expect_exit in (("0.76 3 1.04", 0), ("3 0.76 1.04", 0), ("0.76 1.04 0.90", 1)):
        proc = subprocess.run(
            [sys.executable, str(CHECKER), "--columns", text],
            capture_output=True, text=True,
        )
        status = "PASS" if proc.returncode == expect_exit else "FAIL"
        print(f"[{status}] 列序自校「{text}」· 退出码 {proc.returncode}（期望 {expect_exit}）")
        if proc.returncode != expect_exit:
            problems.append(f"列序自校「{text}」退出码不符")

    print()
    if problems:
        for line in problems:
            print(f"  ERROR · {line}")
        print("结果：FAIL — 盘口拦截能力退化，不得上新批次")
        return 1
    print("结果：PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

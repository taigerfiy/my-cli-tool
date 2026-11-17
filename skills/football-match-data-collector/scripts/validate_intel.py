#!/usr/bin/env python3
"""validate_intel.py — 赛前情报 facts 包校验

用法：
    python3 scripts/validate_intel.py intel.json

schema 版本：
    根对象必须带 "schema": "v1"；缺失按 v1 校验并告警，未知版本直接 FAIL（防止旧脚本误判新结构）。

五项硬断言（任一 FAIL 即退出码 1，不得交付）：
    1. schema 版本必须是受支持版本（v1）
    2. 每条 fact 必须有 source_url / observed_at / credibility
    3. observed_at <= collect_as_of < kickoff_at
    4. event_fingerprint 不得重复
    5. completeness < 0.6 时 missing 不得为空

附加告警（不影响退出码）：
    - schema 字段缺失
    - credibility 超过来源级别上限（L1 0.95 / L2 0.85 / L3 0.75 / L4 0.40）
"""

import json
import sys
from datetime import datetime

LEVEL_CAP = {"L1": 0.95, "L2": 0.85, "L3": 0.75, "L4": 0.40}
SUPPORTED_SCHEMAS = ("v1",)
REQUIRED_FIELDS = ("source_url", "observed_at", "credibility")


def parse_time(value, label, errors):
    if not value:
        errors.append(f"{label} 缺失")
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{label} 不是合法 ISO 8601：{value}")
        return None


def check_match(match, index):
    errors, warnings = [], []
    label = match.get("match_id") or f"matches[{index}]"

    as_of = parse_time(match.get("collect_as_of"), f"{label} collect_as_of", errors)
    kickoff = parse_time(match.get("kickoff_at"), f"{label} kickoff_at", errors)
    if as_of and kickoff and not (as_of < kickoff):
        errors.append(f"{label} 采集截点必须早于开赛时间")

    facts = match.get("facts") or []
    seen = {}
    for i, fact in enumerate(facts):
        tag = f"{label} facts[{i}]"
        for field in REQUIRED_FIELDS:
            if fact.get(field) in (None, ""):
                errors.append(f"{tag} 缺少必填字段 {field}")

        observed = parse_time(fact.get("observed_at"), f"{tag} observed_at", errors)
        if observed and as_of and not (observed <= as_of):
            errors.append(f"{tag} 发布时间晚于采集截点（越界）")
        if observed and kickoff and not (observed < kickoff):
            errors.append(f"{tag} 发布时间晚于开赛时间（越界）")

        credibility = fact.get("credibility")
        if credibility is not None:
            try:
                credibility = float(credibility)
                if not 0 <= credibility <= 1:
                    errors.append(f"{tag} credibility 必须在 0–1")
                level = fact.get("source_level")
                if level in LEVEL_CAP and credibility > LEVEL_CAP[level]:
                    warnings.append(
                        f"{tag} credibility {credibility} 超过 {level} 上限 {LEVEL_CAP[level]}"
                    )
            except (TypeError, ValueError):
                errors.append(f"{tag} credibility 不是数值")

        fingerprint = fact.get("event_fingerprint")
        if fingerprint:
            if fingerprint in seen:
                errors.append(f"{tag} event_fingerprint 重复：{fingerprint}")
            seen[fingerprint] = i

    completeness = match.get("completeness")
    if completeness is None:
        errors.append(f"{label} 缺少 completeness")
    else:
        try:
            completeness = float(completeness)
            if not 0 <= completeness <= 1:
                errors.append(f"{label} completeness 必须在 0–1")
            if completeness < 0.6 and not (match.get("missing") or []):
                errors.append(f"{label} completeness {completeness} < 0.6 但 missing 为空")
        except (TypeError, ValueError):
            errors.append(f"{label} completeness 不是数值")

    return label, len(facts), errors, warnings


def main():
    if len(sys.argv) != 2:
        print("用法：python3 scripts/validate_intel.py <intel.json>")
        return 2

    with open(sys.argv[1], encoding="utf-8") as handle:
        payload = json.load(handle)

    matches = payload.get("matches") if isinstance(payload, dict) else None
    if not isinstance(matches, list) or not matches:
        print("FAIL · 根对象必须包含非空 matches[] 数组")
        return 1

    total_errors, total_warnings, total_facts = [], [], 0

    schema = payload.get("schema")
    if not schema:
        total_warnings.append('根对象缺少 schema 字段，按 v1 校验')
    elif schema not in SUPPORTED_SCHEMAS:
        total_errors.append(
            f"schema 版本 {schema} 不受支持（当前支持 {'/'.join(SUPPORTED_SCHEMAS)}），"
            "请用对应版本的校验器"
        )

    for index, match in enumerate(matches):
        label, fact_count, errors, warnings = check_match(match, index)
        total_facts += fact_count
        total_errors.extend(errors)
        total_warnings.extend(warnings)
        status = "PASS" if not errors else "FAIL"
        print(f"[{status}] {label} · facts {fact_count} 条 · 错误 {len(errors)} · 告警 {len(warnings)}")

    for line in total_warnings:
        print(f"  WARN · {line}")
    for line in total_errors:
        print(f"  ERROR · {line}")

    print(f"\n合计：{len(matches)} 场 / {total_facts} 条 facts / {len(total_errors)} 错误 / {len(total_warnings)} 告警")
    if total_errors:
        print("结果：FAIL，不得交付")
        return 1
    print("结果：PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

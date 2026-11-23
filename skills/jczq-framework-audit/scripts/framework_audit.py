#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞彩框架审计（本地台账版 · 2026-11-23 重构）

数据源已从 Base2（已删除）切换为本地台账：
  /home/user/Doubao/jczq_shared/ledger/ledger_YYYY.csv（结算主表）
  /home/user/Doubao/jczq_shared/ledger/baseline.json（滚动基准）

四个子命令（接口与旧版一致，Step1/Step2/Step3 无需改调用）：
  status                全局状态卡：分层命中率 / 方向×结果 / 规则号覆盖 / 降权开关 / 样本量
  gate   [--sub 子层]   开工前先验：status 摘要 + 同子层命中率 + 最近偏差类型
  write  --file f.json  写 S1/S2 判断（委托 ledger.py append，按 (date,num) 幂等）
  settle --file f.json  结算 S3（委托 ledger.py append --force，回填 verdict/attrib）

只做查询、写入、结算；不做方向判断，不改 Step 规则，不动 Step5 门槛。
"""
import argparse
import csv
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict

LEDGER_DIR = os.environ.get("JCZQ_LEDGER_DIR", "/home/user/Doubao/jczq_shared/ledger")
BASELINE = os.path.join(LEDGER_DIR, "baseline.json")
PENDING = os.path.join(LEDGER_DIR, "pending_judgments.json")
LEDGER_CLI = os.environ.get(
    "JCZQ_LEDGER_CLI", "/home/user/Doubao/jczq_shared/scripts/ledger.py")

MIN_SETTLED = 20       # 子层/分层出结论的最低已结算样本
LOW_HIT = 0.60         # 低于此命中率 → 只出方向不出仓位
SUB_ALIAS = {"A层": "A", "B层": "B", "C1俱乐部": "C", "C1杯赛": "C",
             "C2国家队": "C", "W女足": "W"}
HIT, MISS, PART, NOSCORE = "命中", "未命中", "部分", "无法打分"


def read_ledger():
    rows = []
    if not os.path.isdir(LEDGER_DIR):
        return rows
    for fn in sorted(os.listdir(LEDGER_DIR)):
        if fn.startswith("ledger_") and fn.endswith(".csv"):
            with open(os.path.join(LEDGER_DIR, fn), encoding="utf-8-sig") as f:
                rows.extend(list(csv.DictReader(f)))
    return rows


def settled(rows):
    return [r for r in rows if (r.get("verdict") or "").strip() in (HIT, MISS, PART)]


def rate(rows):
    """命中率：命中 / (命中 + 未命中)，部分与无法打分不进分母"""
    h = sum(1 for r in rows if r.get("verdict") == HIT)
    m = sum(1 for r in rows if r.get("verdict") == MISS)
    return (h / (h + m) if h + m else None), h, m


def sub_match(row_level, sub):
    key = SUB_ALIAS.get(sub, sub)
    lv = (row_level or "").upper()
    if key == "C":
        return lv.startswith("C")
    return lv == key or lv.startswith(key)


def verdict_of(level, rows):
    n = len(rows)
    hit_rate, h, m = rate(rows)
    if n < MIN_SETTLED or hit_rate is None:
        return (f"样本不足（已结算 {n}/{MIN_SETTLED}）→ 不出结论，只给方向",
                "insufficient")
    if hit_rate < LOW_HIT:
        return (f"观察：命中率 {hit_rate:.0%} < {LOW_HIT:.0%} → 只出方向、不标仓位",
                "downgrade")
    return f"可用：命中率 {hit_rate:.0%}（已结算 {n}）", "ok"


def status_card(rows, sub=None):
    out = []
    out.append("## 框架状态（本地台账）")
    if not rows:
        out.append("台账为空：先在 Step3 用 ledger.py append 落结算行，再跑本命令。")
        out.append("当前降权开关：无可判数据 → 结论一律「只出方向、不标仓位」。")
        return out

    by_level = defaultdict(list)
    for r in rows:
        by_level[(r.get("level") or "(空)").strip()].append(r)
    out.append("\n### 1. 分层命中率")
    out.append("| 分层 | 已结算 | 命中 | 未命中 | 命中率 | 判定 |")
    out.append("|---|---|---|---|---|---|")
    for lv in sorted(by_level):
        s = settled(by_level[lv])
        hr, h, m = rate(s)
        vtxt, _flag = verdict_of(lv, s)
        out.append(f"| {lv} | {len(s)} | {h} | {m} | "
                   f"{'—' if hr is None else f'{hr:.1%}'} | {vtxt} |")

    out.append("\n### 2. 方向 × 结算结果")
    by_dir = defaultdict(list)
    for r in settled(rows):
        by_dir[(r.get("direction") or "(空)").strip()].append(r)
    out.append("| 方向 | 已结算 | 命中 | 未命中 | 部分 | 命中率 |")
    out.append("|---|---|---|---|---|---|")
    for d in sorted(by_dir, key=lambda x: -len(by_dir[x])):
        g = by_dir[d]
        hr, h, m = rate(g)
        p = sum(1 for r in g if r.get("verdict") == PART)
        out.append(f"| {d} | {len(g)} | {h} | {m} | {p} | "
                   f"{'—' if hr is None else f'{hr:.1%}'} |")

    out.append("\n### 3. 归因分布")
    for k, v in Counter((r.get("attrib") or "(空)") for r in settled(rows)).most_common():
        out.append(f"- {k}：{v}")

    out.append("\n### 4. 规则号挂号覆盖")
    total = len(rows)
    filled = sum(1 for r in rows if (r.get("rule_ids") or "").strip())
    pct = filled / total if total else 0
    flag = "可用" if pct >= 0.9 else "不可用（<90% 即 Step5 规则矩阵不可用）"
    out.append(f"- 已填 {filled}/{total}（{pct:.0%}）→ {flag}")
    out.append("- 注：本地台账 rule_ids 为空是常态，规则号口径仍属 Base2 旧体系；"
               "本项只作覆盖体检，不判方向。")

    pend_n = len(load_pending())
    out.append(f"\n### 5. 未结算清单（pending 暂存 {pend_n} 条）")
    pend = [r for r in rows if not (r.get("verdict") or "").strip()]
    if not pend:
        out.append("- 无未结算条目")
    else:
        for r in pend[:20]:
            out.append(f"- {r.get('date')} {r.get('num')} {r.get('home')}v{r.get('away')}"
                       f"（{r.get('direction')}）")

    out.append("\n### 6. 滚动基准")
    if os.path.exists(BASELINE):
        try:
            b = json.load(open(BASELINE, encoding="utf-8"))
            usable = bool(b.get("A级", {}).get("usable"))
            out.append(f"- baseline.json 更新 {b.get('updated')}，窗口 {b.get('window_days')} 天，"
                       f"A级 usable={'true' if usable else 'false'}")
            out.append("- " + ("按滚动基准校准方向资格" if usable
                              else "滚动基准不可用（样本未达门槛）→ 沿用卡内基准（home 72.1% / away 67.6%）"))
        except Exception as e:  # noqa: BLE001
            out.append(f"- baseline.json 读取失败：{e}")
    else:
        out.append("- baseline.json 缺失 → 沿用卡内基准（home 72.1% / away 67.6%）")

    if sub:
        out.append(f"\n### 7. 子层先验：{sub}")
        subrows = [r for r in rows if sub_match(r.get("level"), sub)]
        s = settled(subrows)
        hr, h, m = rate(s)
        vtxt, flag = verdict_of(sub, s)
        out.append(f"- 已结算 {len(s)}｜命中 {h}｜未命中 {m}｜部分 "
                   f"{sum(1 for r in s if r.get('verdict') == PART)}"
                   f"｜命中率 {'—' if hr is None else f'{hr:.1%}'}")
        out.append(f"- 资格判定：{vtxt}")
        recent = [r for r in subrows if r.get("attrib")][-5:]
        if recent:
            out.append("- 最近 5 条归因：" + "；".join(
                f"{r.get('date')} {r.get('num')} {r.get('attrib')}" for r in recent))
    return out


def _key(r):
    return f"{r.get('date')}|{r.get('num')}"


def load_pending():
    if not os.path.exists(PENDING):
        return {}
    try:
        return json.load(open(PENDING, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def save_pending(d):
    with open(PENDING, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)


def _require_fields(rows, need, cmd):
    missing = []
    for i, r in enumerate(rows):
        lack = [k for k in need if not str(r.get(k) or "").strip()]
        if lack:
            missing.append(f"第{i+1}条缺 {','.join(lack)}")
    if missing:
        sys.exit(f"[{cmd}] 字段不合台账口径，未写入：\n  " + "\n  ".join(missing[:10]) +
                 "\n字段清单见 ledger.py FIELDS；判断写入需含 date/num/level/league/home/away/direction，"
                 "结算还需 verdict/attrib/data_src。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["status", "gate", "write", "settle"])
    ap.add_argument("--sub", help="子层：A层|B层|C1俱乐部|C1杯赛|C2国家队|W女足")
    ap.add_argument("--file", help="write/settle 的 JSON 文件")
    ap.add_argument("--force", action="store_true", help="settle 覆盖已结算条目")
    ap.add_argument("--json", action="store_true", help="输出机读 JSON")
    a = ap.parse_args()

    if a.cmd in ("status", "gate"):
        rows = read_ledger()
        if a.json:
            by_level = {}
            for r in rows:
                by_level.setdefault((r.get("level") or "").strip(), []).append(r)
            data = {"n": len(rows), "settled": len(settled(rows)),
                    "source": LEDGER_DIR,
                    "levels": {lv: {"settled": len(settled(v)), "rate": rate(settled(v))[0],
                                    "verdict": verdict_of(lv, settled(v))[1]}
                               for lv, v in by_level.items()},
                    "baseline_usable": None}
            if os.path.exists(BASELINE):
                try:
                    data["baseline_usable"] = bool(
                        json.load(open(BASELINE, encoding="utf-8"))
                        .get("A级", {}).get("usable"))
                except Exception:  # noqa: BLE001
                    pass
            if a.sub:
                s = settled([r for r in rows if sub_match(r.get("level"), a.sub)])
                data["sub"] = {"name": a.sub, "settled": len(s),
                               "rate": rate(s)[0], "verdict": verdict_of(a.sub, s)[1]}
            print(json.dumps(data, ensure_ascii=False, indent=1))
            return
        sub = a.sub if a.cmd == "gate" else None
        print("\n".join(status_card(rows, sub)))
        return

    if not a.file:
        sys.exit("需要 --file")
    rows = json.load(open(a.file, encoding="utf-8"))
    if isinstance(rows, dict):
        rows = rows.get("judgments") or rows.get("settles") or [rows]
    if a.cmd == "write":
        # 赛前判断只入 pending 暂存（台账表契约要求 verdict，未结算行不能直接落 CSV）
        _require_fields(rows, ["date", "num", "level", "league", "home", "away",
                               "direction", "data_src"], "write")
        pend = load_pending()
        added = 0
        for r in rows:
            k = _key(r)
            if k in pend and not a.force:
                continue
            pend[k] = r
            added += 1
        save_pending(pend)
        print(f"[write] 暂存 {added} 条，pending 共 {len(pend)} 条 → {PENDING}")
        print("        这些行未进结算表；Step3 用 settle 合并后一次落行。")
        return

    # settle：先用 pending 补齐判断字段，再校验、再落台账
    pend = load_pending()
    for r in rows:
        base = pend.get(_key(r)) or {}
        for k in ("level", "league", "home", "away", "direction", "grade", "rule_ids"):
            if not str(r.get(k) or "").strip() and base.get(k):
                r[k] = base[k]
    need = ["date", "num", "level", "league", "home", "away",
            "verdict", "attrib", "data_src", "result_1x2"]
    _require_fields(rows, need, "settle")
    tmp = os.path.join(LEDGER_DIR, ".settle_tmp.json")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False)
    cmd = [sys.executable, LEDGER_CLI, "append", "--file", tmp]
    if a.force:
        cmd.append("--force")
    p = subprocess.run(cmd, capture_output=True, text=True)
    print((p.stdout or "").strip())
    os.remove(tmp)
    if p.returncode != 0:
        sys.exit(f"[settle] ledger.py 失败：{(p.stderr or '')[:400]}")
    # 落行成功才清 pending
    for r in rows:
        pend.pop(_key(r), None)
    save_pending(pend)
    print(f"[settle] 已落台账，剩余 pending {len(pend)} 条")


if __name__ == "__main__":
    main()

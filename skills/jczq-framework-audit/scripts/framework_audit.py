#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞彩框架审计（Base2 读写闸）

四个子命令：
  status                全局状态卡：分层命中率 / 判断簿结算率 / 降权开关 / 累计样本
  gate   [--sub 子层]   开工前先验：在 status 基础上加同子层近 N 条命中率与最近偏差类型
  write  --file f.json  写入 S1/S2 判断（judgement_id 幂等，已存在则跳过）
  settle --file f.json  结算 S3 判定（verdict + attrib），默认跳过已结算条目

规则来源与字段契约见 references/schema.md，本脚本只执行查询与写入，不做判断。
"""
import argparse
import json
import os
import subprocess
import sys

def _load_token():
    """优先级：环境变量 JCZQ_BASE2_TOKEN → references/base2.token（本地，不随仓库发布）。"""
    tok = os.environ.get("JCZQ_BASE2_TOKEN")
    if tok:
        return tok.strip()
    local = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "references", "base2.token")
    if os.path.exists(local):
        v = open(local, encoding="utf-8").read().strip().split()[0]
        if v and not v.startswith("<"):
            return v
    raise SystemExit("缺少 Base2 token：设 env JCZQ_BASE2_TOKEN 或写 references/base2.token")


BASE2 = _load_token()
T_DETAIL = "tblm2MroEC6fXbez"   # 赛事明细
T_JUDGE = "tblSEPpqGH5xcq84"    # 判断簿
T_STATE = "tbly1BxFnT2eSekR"    # 框架当前状态


def cli(args, as_json=True):
    """调用 lark-cli；返回解析后的 JSON（失败抛 RuntimeError，带原始报错）。"""
    p = subprocess.run(["lark-cli"] + args, capture_output=True, text=True)
    out = (p.stdout or "").strip()
    if not out:
        raise RuntimeError(f"lark-cli 无输出：{(p.stderr or '')[:400]}")
    try:
        d = json.loads(out)
    except json.JSONDecodeError:
        raise RuntimeError(f"lark-cli 返回非 JSON：{out[:300]}")
    if not d.get("ok", True):
        raise RuntimeError(json.dumps(d.get("error", d), ensure_ascii=False)[:500])
    return d.get("data", d)


def sql(q):
    d = cli(["base", "+data-sql-query", "--base-token", BASE2, "--as", "user",
             "--format", "json", "--sql", q])
    cols = d.get("columns", [])
    return [dict(zip(cols, [r.get(c) for c in cols])) for r in d.get("rows", [])]


def records(table, filter_json, fields, limit=200):
    args = ["base", "+record-list", "--base-token", BASE2, "--table-id", table,
            "--as", "user", "--format", "json", "--limit", str(limit)]
    if filter_json:
        args += ["--filter-json", filter_json]
    for f in fields:
        args += ["--field-id", f]
    d = cli(args)
    out = []
    for i, row in enumerate(d.get("data", [])):
        rec = dict(zip(d.get("fields", []), row))
        rec["_record_id"] = d.get("record_id_list", [])[i]
        out.append(rec)
    return out


# ----------------------------- 状态卡 -----------------------------

def layer_table():
    rows = sql("SELECT COALESCE(子层标签,'(空)') AS tag, COUNT(*) AS n, "
               "SUM(CASE WHEN 方向是否命中='是' THEN 1 ELSE 0 END) AS hit, "
               "SUM(CASE WHEN 方向是否命中 IS NOT NULL THEN 1 ELSE 0 END) AS settled "
               "FROM 赛事明细 GROUP BY 子层标签 ORDER BY settled DESC")
    return rows


def judge_table():
    return sql("SELECT jtype, verdict, COUNT(*) AS n FROM 判断簿 "
               "GROUP BY jtype, verdict ORDER BY jtype, verdict")


def pending_list():
    return sql("SELECT judgement_id, match_label, jtype, batch FROM 判断簿 "
               "WHERE verdict='未验证' ORDER BY batch, judgement_id LIMIT 20")


def switches():
    return sql("SELECT 项目, \"当前值/说明\" AS v FROM 框架当前状态 "
               "WHERE 项目 LIKE '%降权%' OR 项目 LIKE '%基线%' ORDER BY 项目 LIMIT 20")


def deviation_tail(n=5):
    return sql(f"SELECT 编号, 偏差类型, 方向是否命中 FROM 赛事明细 "
               f"WHERE 偏差类型 IS NOT NULL ORDER BY 比赛日期 DESC LIMIT {n}")


def status_card(sub=None):
    L = layer_table()
    print("\n== 分层命中率（口径：方向是否命中=是 / 已结算）==")
    for r in L:
        s = r["settled"] or 0
        h = r["hit"] or 0
        pct = f"{h / s * 100:.1f}%" if s else "—"
        flag = " ← 低于60%，降权观察" if s >= 20 and h / s < 0.60 else ""
        print(f"  {str(r['tag']):12s} {h:>4}/{s:<4} {pct:>7}{flag}")

    J = judge_table()
    print("\n== 判断簿 jtype×verdict ==")
    agg = {}
    for r in J:
        agg.setdefault(r["jtype"], {})[r["verdict"]] = r["n"]
    for jt, v in agg.items():
        hit, miss = v.get("命中", 0), v.get("证伪", 0)
        total = hit + miss
        pct = f"{hit / total * 100:.0f}%" if total else "—"
        print(f"  {jt:14s} 命中{hit} 证伪{miss} 未验证{v.get('未验证',0)}  命中率 {pct}")

    P = pending_list()
    print(f"\n== 未结算判断：{len(P)} 条 ==")
    for r in P[:8]:
        print(f"  {r['judgement_id']} {r['match_label']} [{r['batch']}]")

    S = switches()
    print("\n== 降权/基线开关 ==")
    for r in S:
        print(f"  {r['项目']} → {str(r['v'])[:110]}")

    if sub:
        rows = sql(f"SELECT COUNT(*) AS n, SUM(CASE WHEN 方向是否命中='是' THEN 1 ELSE 0 END) AS hit "
                   f"FROM 赛事明细 WHERE 子层标签='{sub}' AND 方向是否命中 IS NOT NULL")
        r = rows[0] if rows else {}
        n, h = r.get("n") or 0, r.get("hit") or 0
        pct = f"{h / n * 100:.1f}%" if n else "—"
        print(f"\n== 开工先验 · 子层 {sub} ==\n  近 {n} 条已结算，命中 {h} → {pct}")
        if n and h / n < 0.60:
            print("  ⚠ 该子层低于60%，本场只出方向不出仓位")
        print("\n== 最近偏差类型（开工提醒）==")
        for r in deviation_tail():
            print(f"  {r['编号']} {r['偏差类型']}（方向{'中' if r['方向是否命中']=='是' else '错/未结算'}）")


# ----------------------------- 写入 / 结算 -----------------------------

def write_judgments(path):
    payload = json.load(open(path, encoding="utf-8"))
    recs = payload.get("create_records", [])
    if not recs:
        raise SystemExit("create_records 为空")
    ids = [r["judgement_id"] for r in recs]
    existing = set()
    for jid in ids:
        got = records(T_JUDGE, json.dumps({"logic": "and", "conditions": [["judgement_id", "is", jid]]}),
                      ["judgement_id"])
        if got:
            existing.add(jid)
    recs = [r for r in recs if r["judgement_id"] not in existing]
    if not recs:
        print(f"全部已存在，跳过写入（{len(ids)} 条）")
        return
    d = cli(["base", "+record-batch-create", "--base-token", BASE2, "--table-id", T_JUDGE,
             "--as", "user", "--json", json.dumps({"create_records": recs}, ensure_ascii=False)])
    print(f"写入 {len(d.get('record_id_list', []))} 条；跳过重复 {len(existing)} 条")
    for rid, r in zip(d.get("record_id_list", []), recs):
        print(f"  {r['judgement_id']} → {rid}")


def settle(path, force=False):
    payload = json.load(open(path, encoding="utf-8"))
    upd, skipped = {}, []
    for jid, rec in payload.items():
        got = records(T_JUDGE, json.dumps({"logic": "and", "conditions": [["judgement_id", "is", jid]]}),
                      ["judgement_id", "verdict"])
        if not got:
            raise SystemExit(f"判断不存在：{jid}")
        r = got[0]
        if r.get("verdict") and "未验证" not in r["verdict"] and not force:
            skipped.append(jid)
            continue
        upd[r["_record_id"]] = rec
    if not upd:
        print(f"无可结算条目（跳过已结算 {len(skipped)} 条）")
        return
    cli(["base", "+record-batch-update", "--base-token", BASE2, "--table-id", T_JUDGE,
         "--as", "user", "--json", json.dumps({"update_records": upd}, ensure_ascii=False)])
    print(f"结算 {len(upd)} 条；跳过已结算 {len(skipped)} 条")


def main():
    ap = argparse.ArgumentParser(description="Base2 读写闸")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("status")
    p.add_argument("--sub")
    p = sub.add_parser("gate")
    p.add_argument("--sub")
    p = sub.add_parser("write")
    p.add_argument("--file", required=True)
    p = sub.add_parser("settle")
    p.add_argument("--file", required=True)
    p.add_argument("--force", action="store_true")
    a = ap.parse_args()

    if a.cmd in ("status", "gate"):
        status_card(a.sub)
    elif a.cmd == "write":
        write_judgments(a.file)
    else:
        settle(a.file, a.force)


if __name__ == "__main__":
    main()

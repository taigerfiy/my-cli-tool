#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
football-charts.com 取数适配层（footballcharts-mcp 背后的 REST API）

用途：给 football-data-xg 补第二个 xG 源 + 逐场技术统计 + 半场比分/首球分钟。
不依赖 MCP 客户端，纯 Python 标准库直连。

用法示例：
  python3 scripts/fc_fetch.py leagues
  python3 scripts/fc_fetch.py results --league premier --last 10 --outdir ./fc_out
  python3 scripts/fc_fetch.py team --league premier --team arsenal
  python3 scripts/fc_fetch.py table --league premier
  python3 scripts/fc_fetch.py fixtures --league premier
  python3 scripts/fc_fetch.py acheck --leagues premier,spain1,italy1,germany1,france1,holland1,portugal1

限额（重要）：
  无 key：25 次/天/IP（20 次/分钟）
  免费 key：200 次/天（邮箱未确认前 100），60 次/分钟 —— 环境变量 FC_API_KEY=fc_xxx
  注册 key：POST https://api.football-charts.com/api/v1/keys/register/  {"email": "..."}
  → 每日例行只拉「results」就够了：1 联赛 1 次，A 级 7 个联赛 7 次/天。

边界（别指望它）：
  - 无欧战（欧冠/欧罗巴不覆盖）
  - 只有当季 + 上一季（不能做多年回测）
  - 无任何博彩赔率（历史赔率库是付费数据集）
  - 只有 team / match 粒度，无球员 xG、无射门坐标
  - xG 为其自有模型口径，与 football-data.co.uk 的 Opta 系口径不可直接混算
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = os.environ.get("FC_API_BASE", "https://api.football-charts.com/api/v1").rstrip("/")
CACHE_DIR = os.path.expanduser("~/.cache/fc_api")
TIMEOUT = 25
RETRIES = 2


def _key_from_file():
    """环境变量没有时，回落到 ~/.fc_api_key（cron 非交互 shell 拿不到 export）"""
    p = os.path.expanduser("~/.fc_api_key")
    try:
        with open(p, encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def _get(path, params=None, no_cache=False, allow_fail=False):
    url = BASE + path
    if params:
        q = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
        url = f"{url}?{q}"
    os.makedirs(CACHE_DIR, exist_ok=True)
    key = path.strip("/").replace("/", "_") or "root"
    if params:
        key += "_" + "_".join(str(v) for v in params.values())
    cache_file = os.path.join(CACHE_DIR, f"{key}.json")
    if not no_cache and os.path.exists(cache_file):
        age = time.time() - os.path.getmtime(cache_file)
        if age < 6 * 3600:  # 6 小时内直接用缓存，省额度
            with open(cache_file, encoding="utf-8") as f:
                return json.load(f)

    last_err = None
    for attempt in range(RETRIES + 1):
        req = urllib.request.Request(url, headers={"User-Agent": "fc-fetch/1.0"})
        key_env = os.environ.get("FC_API_KEY") or _key_from_file()
        if key_env:
            req.add_header("Authorization", f"Bearer {key_env}")
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                data = json.loads(r.read().decode("utf-8"))
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            return data
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "ignore")[:300]
            if e.code == 404 and allow_fail:
                return None
            if e.code == 429:
                sys.exit(f"[429] 额度用尽：{body}\n→ 加 FC_API_KEY（免费 200/天）或明天再跑。")
            last_err = f"HTTP {e.code}: {body}"
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
        time.sleep(1 + attempt * 2)
    sys.exit(f"[失败] {url} · {last_err}")


def save_json(obj, outdir, name):
    if not outdir:
        return
    os.makedirs(outdir, exist_ok=True)
    p = os.path.join(outdir, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    print(f"[写入] {p}")



# ---------- 联赛映射与队名归一 ----------

LEAGUE_CODE = {"premier": "E0", "spain1": "SP1", "italy1": "I1", "germany1": "D1",
               "france1": "F1", "holland1": "N1", "portugal1": "P1"}

LEAGUE_CN = {"premier": "英超", "spain1": "西甲", "italy1": "意甲", "germany1": "德甲",
             "france1": "法甲", "holland1": "荷甲", "portugal1": "葡超"}

_DROP = {"fc", "cf", "sc", "ac", "as", "afc", "club", "cd", "ud", "sd", "sv",
         "vfl", "vfb", "tsg", "fsv", "rb", "cp", "calcio", "ss", "us"}
_EXPAND = {"man": "manchester", "ath": "atletico", "atl": "atletico",
           "psg": "paris saint germain", "sg": "saint germain", "bay": "bayern",
           "utd": "united", "nott": "nottingham", "weds": "wednesday",
           "brom": "bromwich", "st": "saint"}


def _norm(name):
    """队名归一：小写、去重音、去标点、丢常见俱乐部前后缀"""
    import unicodedata
    s = unicodedata.normalize("NFKD", (name or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("&", " and ").replace("-", " ").replace(".", " ")
    toks = []
    for t in s.split():
        if not t or len(t) == 1 or t in _DROP:
            continue
        toks.extend(_EXPAND.get(t, t).split())
    return " ".join(toks)


def _cn2en():
    """把 xg_fetch 的 TEAM_CN（英文→中文）反转成 中文→英文"""
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xg_fetch.py")
    try:
        spec = importlib.util.spec_from_file_location("_xgf", path)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return {v: k for k, v in getattr(m, "TEAM_CN", {}).items()}
    except Exception:  # noqa: BLE001
        return {}


def _fd_teams(league, fd_dirs):
    """读 football-data 产出的 {中文}_teams_xg.csv，返回 {归一队名: (xG, xGa, 场次)}"""
    cn = LEAGUE_CN.get(league)
    if not cn:
        return {}
    for d in fd_dirs:
        p = os.path.join(d, f"{cn}_teams_xg.csv")
        if not os.path.exists(p):
            continue
        c2e = _cn2en()
        out = {}
        with open(p, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                name = r["球队"]
                en = c2e.get(name, name)
                try:
                    out[_norm(en)] = (float(r["场均xG"]), float(r["场均xGa"]), int(r["场次"]))
                except (ValueError, KeyError):
                    continue
        return out
    return {}


def _date(s):
    from datetime import datetime as _dt
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return _dt.strptime(s, fmt).date()
        except Exception:  # noqa: BLE001
            pass
    return None


def _cn_slug(league, name):
    """中文队名 → slug（读 references/team_names_cn.csv：cn,league,slug）"""
    if not name or not any("\u4e00" <= c <= "\u9fff" for c in name):
        return None
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "references", "team_names_cn.csv")
    if not os.path.exists(p):
        return None
    key = name.strip()
    d = {}
    with open(p, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            d[(r["league"], r["cn"])] = r["slug"]
            d.setdefault(r["cn"], r["slug"])
    for cand in (key, key.replace("足球俱乐部", ""), key.replace("俱乐部", "")):
        if (league, cand) in d:
            return d[(league, cand)]
        if cand in d:
            return d[cand]
    return None


def _fd_lookup(fd, name):
    """先精确归一匹配，再用首词相等兜底（PSV↔PSV Eindhoven、Sporting CP↔Sporting Lisbon）"""
    n = _norm(name)
    if n in fd:
        return fd[n]
    toks = set(n.split())
    if not toks:
        return None
    # 子集匹配：一方队名是另一方的子集（PSV / PSV Eindhoven、Sporting / Sporting Lisbon）
    for k, v in fd.items():
        kt = set(k.split())
        if kt and (toks <= kt or kt <= toks):
            return v
    return None


def results_csv(res, outdir, league):
    """逐场技术统计 CSV：比分/半场/首球 + 双方 xG/射门/射正/控球/角球/黄牌/扑救"""
    matches = res.get("matches", [])
    if not outdir or not matches:
        return
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, f"{league}_fc_matches.csv")
    fields = ["date", "time", "home", "away", "score", "ht", "first_goal_time",
              "h_xg", "a_xg", "h_shots", "a_shots", "h_sot", "a_sot",
              "h_poss", "a_poss", "h_corners", "a_corners", "h_yellow", "a_yellow"]
    has_stats = 0
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for m in matches:
            st = m.get("stats") or {}
            h, a = st.get("home") or {}, st.get("away") or {}
            if h.get("xg") is not None:
                has_stats += 1
            w.writerow({
                "date": m.get("date"), "time": m.get("time"),
                "home": m.get("homeTeam"), "away": m.get("awayTeam"),
                "score": m.get("score"), "ht": m.get("ht_result"),
                "first_goal_time": m.get("first_goal_time"),
                "h_xg": h.get("xg"), "a_xg": a.get("xg"),
                "h_shots": h.get("shots"), "a_shots": a.get("shots"),
                "h_sot": h.get("shots_on_target"), "a_sot": a.get("shots_on_target"),
                "h_poss": h.get("possession_pct"), "a_poss": a.get("possession_pct"),
                "h_corners": h.get("corners"), "a_corners": a.get("corners"),
                "h_yellow": h.get("yellow_cards"), "a_yellow": a.get("yellow_cards"),
            })
    print(f"[写入] {path} · {len(matches)} 场，其中 {has_stats} 场有 xG/技术统计")


def team_csv(res, outdir, league):
    stats = res.get("stats") or {}
    matches = res.get("matches", [])
    if not outdir or not matches:
        return
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, f"{league}_{res.get('slug','team')}_fc_matches.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["date", "venue", "home", "away", "score", "ht",
                                          "first_goal_time", "outcome"])
        w.writeheader()
        for m in matches:
            w.writerow({k: m.get(k) for k in w.fieldnames})
    print(f"[写入] {path} · {stats.get('matches_with_stats')} 场带统计"
          f" · 场均 xG {stats.get('xg_avg')} / xGa {stats.get('xg_against_avg')}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["leagues", "results", "table", "team", "fixtures", "acheck", "dual", "resultcheck"])
    ap.add_argument("--league")
    ap.add_argument("--team")
    ap.add_argument("--leagues", help="acheck 用逗号分隔的联赛 key")
    ap.add_argument("--last", type=int, default=0, help="只留最近 N 场")
    ap.add_argument("--season", help="如 2025-2026；默认当季")
    ap.add_argument("--outdir", help="产出目录（results/team 会顺手写 CSV）")
    ap.add_argument("--top", type=int, default=6, help="dual 每联赛取前 N 队")
    ap.add_argument("--fd-dir", help="football-data 产出目录（含 英超_teams_xg.csv）")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    if args.cmd == "leagues":
        d = _get("/leagues/", no_cache=args.no_cache)
        for l in d["leagues"]:
            print(f"{l['league']:<12} {l['country']:<14} {l['name']:<28} {','.join(l['seasons'])}")
        print(f"\n共 {len(d['leagues'])} 个联赛")
        return



    if args.cmd == "resultcheck":
        keys = (args.leagues or "premier,spain1,italy1,germany1,france1,holland1,portugal1").split(",")
        fdirs = ([args.fd_dir] if args.fd_dir else []) + [
            "fd", "./数据分析/footballcharts/fd",
            "/home/user/Doubao/jczq_shared/xg/2627",
            "/home/user/Doubao/jczq_shared/artifacts/league_full"]
        total = {"both": 0, "ft_bad": 0, "ht_bad": 0, "fc_only": 0, "fg": 0}
        lines = []
        for lg in keys:
            code = LEAGUE_CODE.get(lg)
            raw = None
            for d in fdirs:
                for pat in (f"raw_{code}_2627.csv", f"{LEAGUE_CN.get(lg,'')}_matches_xg.csv"):
                    fp = os.path.join(d, pat)
                    if code and os.path.exists(fp):
                        raw = fp
                        break
                if raw:
                    break
            if not raw:
                lines.append(f"- {LEAGUE_CN.get(lg, lg)}：本地无 football-data 赛果文件，跳过")
                continue
            fdrows = list(csv.DictReader(open(raw, encoding="utf-8-sig")))
            fcm = _get(f"/leagues/{lg}/results/", no_cache=args.no_cache).get("matches", [])
            # 建档：FD 场次
            idx = {}
            for r in fdrows:
                d0 = r.get("Date") or ""
                try:
                    from datetime import datetime as _dt
                    dd = _dt.strptime(d0, "%d/%m/%Y").date()
                except Exception:  # noqa: BLE001
                    dd = None
                key = (_norm(r.get("HomeTeam")), _norm(r.get("AwayTeam")))
                idx.setdefault(key, []).append((dd, r))
            # 先把该联赛的 FD 队名建成集合，用于 FC 侧队名解析（子集匹配）
            fdnames = set()
            for r in fdrows:
                fdnames.add(_norm(r.get("HomeTeam")))
                fdnames.add(_norm(r.get("AwayTeam")))

            def _resolve(name):
                n = _norm(name)
                if n in fdnames:
                    return n
                t = set(n.split())
                for k in fdnames:
                    kt = set(k.split())
                    if t and kt and (t <= kt or kt <= t):
                        return k
                return n

            both = ft_bad = ht_bad = fc_only = fg = 0
            bad = []
            for m in fcm:
                key = (_resolve(m.get("homeTeam")), _resolve(m.get("awayTeam")))
                cand = idx.get(key) or []
                md = m.get("date")
                hit = None
                for dd, r in cand:
                    if dd is None or md is None or abs((dd - _date(md)).days) <= 1:
                        hit = r
                        break
                if not hit:
                    fc_only += 1
                    continue
                both += 1
                if m.get("first_goal_time") is not None and not m.get("goalless"):
                    fg += 1
                ft_fc = (m.get("score") or "")
                ft_fd = f"{hit.get('FTHG')}:{hit.get('FTAG')}"
                ht_fc = (m.get("ht_result") or "") if m.get("ht_result") else None
                ht_fd = f"{hit.get('HTHG')}:{hit.get('HTAG')}"
                if ft_fc != ft_fd:
                    ft_bad += 1
                    bad.append(f"    FT {m.get('date')} {m.get('homeTeam')}v{m.get('awayTeam')} FC {ft_fc} / FD {ft_fd}")
                elif ht_fc and ht_fd != "::" and ht_fc != ht_fd:
                    ht_bad += 1
                    bad.append(f"    HT {m.get('date')} {m.get('homeTeam')}v{m.get('awayTeam')} FC {ht_fc} / FD {ht_fd}")
            total["both"] += both
            total["ft_bad"] += ft_bad
            total["ht_bad"] += ht_bad
            total["fc_only"] += fc_only
            total["fg"] += fg
            lines.append(f"- {LEAGUE_CN.get(lg, lg)}：可比 {both} 场｜全场比分不一致 {ft_bad}｜半场不一致 {ht_bad}"
                         f"｜仅FC有（未入库新轮）{fc_only}｜带首球分钟 {fg}")
            lines.extend(bad[:6])
        print("\n".join(lines))
        print(f"\n合计：可比 {total['both']} 场 · 全场不一致 {total['ft_bad']} · 半场不一致 {total['ht_bad']}"
              f" · 仅FC有 {total['fc_only']} · 首球分钟覆盖 {total['fg']}")
        if args.outdir:
            os.makedirs(args.outdir, exist_ok=True)
            outp = os.path.join(args.outdir, "result_check_today.md")
            with open(outp, "w", encoding="utf-8") as f:
                f.write("# football-charts 赛果对账（vs football-data.co.uk）\n\n"
                        f"抓取 {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%MZ')}\n\n"
                        + "\n".join(lines)
                        + f"\n\n合计：可比 {total['both']} 场 · 全场不一致 {total['ft_bad']}"
                          f" · 半场不一致 {total['ht_bad']} · 仅FC有 {total['fc_only']}"
                          f" · 首球分钟覆盖 {total['fg']}\n")
            print(f"[写入] {outp}")
        return

    if args.cmd == "dual":
        keys = (args.leagues or "premier,spain1,italy1,germany1,france1,holland1,portugal1").split(",")
        fdirs = ([args.fd_dir] if args.fd_dir else []) + [
            "fd", "./数据分析/footballcharts/fd",
            "/home/user/Doubao/jczq_shared/xg/2627",
            "/home/user/Doubao/jczq_shared/artifacts/league_full"]
        rows = []
        for lg in keys:
            fd = _fd_teams(lg, fdirs)
            tl = _get(f"/leagues/{lg}/teams/", no_cache=args.no_cache)
            season = tl.get("season")
            picked = (tl.get("teams") or [])[:args.top]
            for t in picked:
                tt = _get(f"/leagues/{lg}/teams/{t['slug']}/", no_cache=args.no_cache)
                st = tt.get("stats") or {}
                if st.get("xg_avg") is None:
                    continue
                n_fc = len(tt.get("matches") or [])
                hit = _fd_lookup(fd, tt.get("team") or t["team"])
                row = {"league": lg, "league_cn": LEAGUE_CN.get(lg, lg), "season": season,
                       "team": tt.get("team") or t["team"], "fc_matches": n_fc,
                       "fc_xg": st.get("xg_avg"), "fc_xga": st.get("xg_against_avg")}
                if hit:
                    row.update(fd_xg=hit[0], fd_xga=hit[1], fd_matches=hit[2],
                               d_xg=round(row["fc_xg"] - hit[0], 2),
                               d_xga=round(row["fc_xga"] - hit[1], 2),
                               d_n=n_fc - hit[2])
                rows.append(row)
                time.sleep(0.05)
        if not rows:
            sys.exit("未取到数据")
        # 输出
        if args.outdir:
            os.makedirs(args.outdir, exist_ok=True)
            cp = os.path.join(args.outdir, "xg_dual_today.csv")
            keys2 = ["league_cn", "team", "fc_matches", "fc_xg", "fc_xga",
                     "fd_matches", "fd_xg", "fd_xga", "d_xg", "d_xga", "d_n"]
            with open(cp, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=keys2, extrasaction="ignore")
                w.writeheader()
                for r in rows:
                    w.writerow(r)
            print(f"[写入] {cp}")
        print(f"{'联赛':<6}{'球队':<22}{'FC xG/xGa(场)':<20}{'FD xG/xGa(场)':<20}{'ΔxG':>6}{'ΔxGa':>7} 标记")
        for r in rows:
            fc = f"{r['fc_xg']}/{r['fc_xga']}({r['fc_matches']})"
            if "fd_xg" in r:
                fd = f"{r['fd_xg']}/{r['fd_xga']}({r['fd_matches']})"
                dg = f"{r['d_xg']:+.2f}"
                da = f"{r['d_xga']:+.2f}"
                flag = "⚠双差" if abs(r["d_xg"]) > 0.10 and abs(r["d_xga"]) > 0.10 else (
                    "⚠" if abs(r["d_xga"]) > 0.10 else "ok")
                print(f"{r['league_cn']:<6}{r['team']:<22}{fc:<20}{fd:<20}{dg:>6}{da:>7} {flag}")
            else:
                print(f"{r['league_cn']:<6}{r['team']:<22}{fc:<20}{'无FD基线':<20}{'':>6}{'':>7} 单源")
        attr = f"\n[归因] Data by football-charts.com（xG 第二源）· 抓取 {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%MZ')}"
        print(attr)
        return

    if args.cmd == "acheck":
        # 注意：results 接口不带逐场统计数据，xG 只能在 team 接口拿。
        # 因此每个联赛消耗 2 次额度（teams 列表 1 次 + 队级 1 次）。
        keys = (args.leagues or "premier,spain1,italy1,germany1,france1,holland1,portugal1").split(",")
        out = []
        for k in keys:
            tl = _get(f"/leagues/{k}/teams/", no_cache=args.no_cache)
            teams = tl.get("teams") or []
            if not teams:
                out.append((k, "无队列表", "", 0, 0))
                continue
            slug = teams[0]["slug"]
            t = _get(f"/leagues/{k}/teams/{slug}/", no_cache=args.no_cache)
            st = t.get("stats") or {}
            n = len(t.get("matches") or [])
            out.append((k, teams[0].get("team"), slug,
                        st.get("matches_with_stats", 0), st.get("xg_avg")))
            time.sleep(0.2)
        print(f"{'league':<12}{'样本队':<20}{'带统计':>6}{'总场':>6}  xg_avg")
        for k, name, slug, n, xg in out:
            print(f"{k:<12}{str(name):<20}{n:>6}{'':>6}  {xg}")
        return

    if not args.league:
        sys.exit("需要 --league")

    sub = {"results": "results", "table": "table",
           "team": "teams/" + urllib.parse.quote(args.team or ""), "fixtures": "fixtures"}[args.cmd]
    path = "/leagues/" + args.league + "/" + sub + "/"
    if args.cmd == "team" and not args.team:
        sys.exit("team 子命令需要 --team（slug，小写连字符）")

    params = {}
    if args.season:
        params["season"] = args.season
    if args.last:
        params["last"] = args.last
    if args.cmd == "team":
        mapped = _cn_slug(args.league, args.team or "")
        if mapped:
            print(f"[映射] {args.team} → {mapped}")
            args.team = mapped
        d = _get(path, params=params, no_cache=args.no_cache, allow_fail=True)
        if d is None:
            # slug 不对就按队名在本联赛球队表里归一匹配
            tl = _get(f"/leagues/{args.league}/teams/", no_cache=args.no_cache)
            target = _norm(args.team or "")
            hit = None
            for t in tl.get("teams") or []:
                names = {_norm(t.get("team")), _norm(t.get("slug"))}
                tt = set(target.split())
                if any(tt and (tt <= set(n.split()) or set(n.split()) <= tt) for n in names if n):
                    hit = t
                    break
            if not hit:
                sys.exit(f"[404] {args.league} 下找不到队 {args.team}，请用 teams 列表里的 slug")
            d = _get(f"/leagues/{args.league}/teams/{hit['slug']}/", params=params, no_cache=args.no_cache)
            print(f"[解析] {args.team} → {hit['slug']}")
    else:
        d = _get(path, params=params, no_cache=args.no_cache)

    if args.cmd == "results":
        print(f"{args.league} {d.get('season')} · {d.get('count')} 场 · 状态 {d.get('season_state')}")
        for m in d.get("matches", [])[-10:]:
            h = (m.get("stats") or {}).get("home", {})
            a = (m.get("stats") or {}).get("away", {})
            xg = f"xG {h.get('xg')}-{a.get('xg')}" if h.get("xg") is not None else "无xG"
            print(f"  {m.get('date')} {m.get('homeTeam')} {m.get('score')} {m.get('awayTeam')}"
                  f" (半 {m.get('ht_result')}, 首球 {m.get('first_goal_time')}') {xg}")
        results_csv(d, args.outdir, args.league)
        save_json(d, args.outdir, f"{args.league}_fc_results.json")

    elif args.cmd == "table":
        for r in d.get("table", [])[:8]:
            print(f"{r.get('position'):>2}. {r.get('team'):<22} P{r.get('played')} "
                  f"pts {r.get('points')} / xPTS {r.get('expected_points')} "
                  f"({r.get('luck_category')})")
        save_json(d, args.outdir, f"{args.league}_fc_table.json")

    elif args.cmd == "team":
        s = d.get("stats") or {}
        print(f"{d.get('team')} {d.get('season')} · 统计覆盖 {s.get('matches_with_stats')} 场")
        print(f"  场均 xG {s.get('xg_avg')} / xGa {s.get('xg_against_avg')} "
              f"· 射门 {s.get('shots_avg')} · 射正 {s.get('shots_on_target_avg')} "
              f"· 控球 {s.get('possession_avg')}%")
        team_csv(d, args.outdir, args.league)
        save_json(d, args.outdir, f"{args.league}_{d.get('slug')}_fc_team.json")

    elif args.cmd == "fixtures":
        for m in d.get("matches", [])[:8]:
            pr = m.get("probabilities") or m.get("model_probabilities") or {}
            print(f"  {m.get('date')} {m.get('homeTeam')} vs {m.get('awayTeam')} · {pr}")
        save_json(d, args.outdir, f"{args.league}_fc_fixtures.json")

    print(f"\n[归因] {d.get('attribution', 'Data by football-charts.com')} "
          f"· 抓取 {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%MZ')}")


if __name__ == "__main__":
    main()

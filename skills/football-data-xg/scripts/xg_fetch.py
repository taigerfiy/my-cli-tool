#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""xg_fetch.py —— 从 football-data.co.uk 提取实测 xG/xGa（Opta 系）

为什么用这个源：football-data.co.uk 的 HxG/AxG 是**实测 xG**（Opta/球队数据商口径），
不是 AI 推算值。按「球索框架」xG 核验协议，S/A 级实测源免检，可直接用于 Step1 的
过程质量判断，省掉「用 7 表格反推 g 再比对」的二次核验环节。

用法：
  python3 xg_fetch.py --league 意甲 --season 2627 --outdir ./xg_out
  python3 xg_fetch.py --league E0 --season 2627 --outdir ./xg_out --compare data/英超/matches.csv
  python3 xg_fetch.py --all --season 2627 --outdir ./xg_out          # 五大联赛一次跑完

产出（--outdir 下）：
  {联赛}_matches_xg.csv   逐场：日期/主客/比分/xG/xGa/结果/xG差
  {联赛}_teams_xg.csv     队级：场均 xG/xGa/xG差、进球-xG（超额/低效）、近6场滚动、主客拆分
  {联赛}_xg_report.md     可直接贴进报告的 Markdown 表
  xg_all_summary.md       （--all 时）五联赛汇总

依赖：curl（站点对 urllib 直连返回 000/403）
"""
import argparse, csv, os, re, subprocess, sys, datetime
from collections import defaultdict

BASE = "https://www.football-data.co.uk/mmz4281/{season}/{code}.csv"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

LEAGUES = {
    "英超": ("E0", "英格兰超级联赛"), "英冠": ("E1", "英格兰冠军联赛"),
    "西甲": ("SP1", "西班牙甲级联赛"), "德甲": ("D1", "德国甲级联赛"),
    "意甲": ("I1", "意大利甲级联赛"), "法甲": ("F1", "法国甲级联赛"),
    "荷甲": ("N1", "荷兰甲级联赛"), "葡超": ("P1", "葡萄牙超级联赛"),
    "德乙": ("D2", "德国乙级联赛"), "法乙": ("F2", "法国乙级联赛"),
    "E0": ("E0", ""), "E1": ("E1", ""), "I1": ("I1", ""), "D1": ("D1", ""),
    "SP1": ("SP1", ""), "F1": ("F1", ""), "N1": ("N1", ""), "P1": ("P1", ""),
    "D2": ("D2", ""), "F2": ("F2", ""),
}

# 各联赛队名中文映射（覆盖五大联赛；未覆盖的队保留英文，不猜译名）
TEAM_CN = {
    # 英超
    "Arsenal": "阿森纳", "Aston Villa": "阿斯顿维拉", "Bournemouth": "伯恩茅斯",
    "Brentford": "布伦特福德", "Brighton": "布莱顿", "Chelsea": "切尔西",
    "Coventry": "考文垂", "Crystal Palace": "水晶宫", "Everton": "埃弗顿",
    "Fulham": "富勒姆", "Hull": "赫尔城", "Ipswich": "伊普斯维奇", "Leeds": "利兹联",
    "Liverpool": "利物浦", "Man City": "曼城", "Man United": "曼联",
    "Newcastle": "纽卡斯尔联", "Nott'm Forest": "诺丁汉森林", "Sunderland": "桑德兰",
    "Tottenham": "热刺", "Burnley": "伯恩利", "West Ham": "西汉姆联",
    "Wolves": "狼队", "West Brom": "西布罗姆维奇", "Leicester": "莱斯特城",
    # 意甲
    "Atalanta": "亚特兰大", "Bologna": "博洛尼亚", "Cagliari": "卡利亚里",
    "Como": "科莫", "Fiorentina": "佛罗伦萨", "Frosinone": "弗罗西诺内",
    "Genoa": "热那亚", "Inter": "国际米兰", "Juventus": "尤文图斯",
    "Lazio": "拉齐奥", "Lecce": "莱切", "Milan": "AC米兰", "Monza": "蒙扎",
    "Napoli": "那不勒斯", "Parma": "帕尔马", "Roma": "罗马", "Sassuolo": "萨索洛",
    "Torino": "都灵", "Udinese": "乌迪内斯", "Venezia": "威尼斯", "Empoli": "恩波利",
    "Verona": "维罗纳", "Pisa": "比萨", "Cremonese": "克雷莫纳", "Salernitana": "萨勒尼塔纳",
    # 德甲
    "Bayern Munich": "拜仁慕尼黑", "Dortmund": "多特蒙德", "Freiburg": "弗赖堡",
    "RB Leipzig": "莱比锡红牛", "Leverkusen": "勒沃库森", "Werder Bremen": "云达不莱梅",
    "Ein Frankfurt": "法兰克福", "Hamburg": "汉堡", "Mainz": "美因茨",
    "Paderborn": "帕德博恩", "Augsburg": "奥格斯堡", "FC Koln": "科隆",
    "Union Berlin": "柏林联合", "Schalke 04": "沙尔克04", "M'gladbach": "门兴格拉德巴赫",
    "Stuttgart": "斯图加特", "Hoffenheim": "霍芬海姆", "Wolfsburg": "沃尔夫斯堡",
    "Hertha": "柏林赫塔", "Bochum": "波鸿", "Heidenheim": "海登海姆",
    "Elversberg": "埃尔沃斯贝格", "St Pauli": "圣保利", "Holstein Kiel": "荷尔斯泰因基尔",
    # 西甲
    "Barcelona": "巴塞罗那", "Real Madrid": "皇家马德里", "Ath Madrid": "马德里竞技",
    "Betis": "皇家贝蒂斯", "Sociedad": "皇家社会", "Sevilla": "塞维利亚",
    "Ath Bilbao": "毕尔巴鄂竞技", "Villarreal": "比利亚雷亚尔", "La Coruna": "拉科鲁尼亚",
    "Santander": "桑坦德竞技", "Celta": "塞尔塔", "Alaves": "阿拉维斯",
    "Espanol": "西班牙人", "Vallecano": "巴列卡诺", "Getafe": "赫塔菲",
    "Osasuna": "奥萨苏纳", "Levante": "莱万特", "Elche": "埃尔切",
    "Valencia": "瓦伦西亚", "Malaga": "马拉加", "Girona": "赫罗纳",
    "Mallorca": "马洛卡", "Valladolid": "巴拉多利德", "Las Palmas": "拉斯帕尔马斯",
    # 法甲
    "Paris SG": "巴黎圣日耳曼", "Lille": "里尔", "Paris FC": "巴黎FC", "Marseille": "马赛",
    "Lyon": "里昂", "Nice": "尼斯", "Monaco": "摩纳哥", "Rennes": "雷恩",
    "Brest": "布雷斯特", "Lorient": "洛里昂", "Lens": "朗斯", "Toulouse": "图卢兹",
    "Strasbourg": "斯特拉斯堡", "Auxerre": "欧塞尔", "Le Mans": "勒芒", "Angers": "昂热",
    "Troyes": "特鲁瓦", "Le Havre": "勒阿弗尔", "Nantes": "南特", "Reims": "兰斯",
    "Montpellier": "蒙彼利埃", "St Etienne": "圣埃蒂安", "Metz": "梅斯",
}


# 对照组（8BO/知识库）队名 -> 本脚本中文简称
BO_ALIAS = {"曼彻斯特城": "曼城", "曼彻斯特联": "曼联", "托特纳姆热刺": "热刺",
            "巴伦西亚": "瓦伦西亚", "RB莱比锡": "莱比锡红牛"}


def cn(name):
    return TEAM_CN.get(name, name)


def fetch_csv(code, season, outdir):
    os.makedirs(outdir, exist_ok=True)
    url = BASE.format(season=season, code=code)
    dst = os.path.join(outdir, f"raw_{code}_{season}.csv")
    for attempt in range(3):
        subprocess.run(["curl", "-sL", "--max-time", "60", "--retry", "1",
                        "-H", f"User-Agent: {UA}",
                        "-H", "Referer: https://www.football-data.co.uk/",
                        "-o", dst, url], capture_output=True)
        if os.path.exists(dst) and os.path.getsize(dst) > 2000:
            return dst
        import time
        time.sleep(3 * (attempt + 1))
    return None


def parse(path):
    rows = []
    for r in csv.DictReader(open(path, encoding="utf-8-sig")):
        if not r.get("HomeTeam") or not r.get("FTHG"):
            continue
        try:
            hx, ax = float(r["HxG"]), float(r["AxG"])
        except (KeyError, ValueError, TypeError):
            hx = ax = None
        d = datetime.datetime.strptime(r["Date"], "%d/%m/%Y").date()
        rows.append(dict(
            日期=d.isoformat(), 主队=cn(r["HomeTeam"]), 客队=cn(r["AwayTeam"]),
            主队进球=int(r["FTHG"]), 客队进球=int(r["FTAG"]),
            主队xG=hx, 客队xG=ax,
            结果=r.get("FTR", ""), 亚盘=r.get("AHh", ""),
        ))
    rows.sort(key=lambda x: x["日期"])
    return rows


def now_cn():
    """北京时间（容器默认 UTC，跨日会差一天）"""
    return datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)


def freshness(rows):
    """数据新鲜度：CSV 用英国当地时间，跨日时与北京时间差 8h，勿误判为延迟"""
    if not rows:
        return None, None
    last = max(r["日期"] for r in rows)
    gap = (now_cn().date() - datetime.date.fromisoformat(last)).days
    return last, gap


def is_quarter(line):
    q = line * 4
    return abs(q - round(q)) < 1e-6 and int(round(q)) % 2 == 1


def aggregate(rows, window=6):
    """队级聚合（xG 均以「该队视角」= 本队 xG）"""
    agg = defaultdict(lambda: defaultdict(float))
    hist = defaultdict(list)
    for r in rows:
        if r["主队xG"] is None:
            continue
        for side, me, opp, gf, ga, xf, xa in (
                ("主", r["主队"], r["客队"], r["主队进球"], r["客队进球"], r["主队xG"], r["客队xG"]),
                ("客", r["客队"], r["主队"], r["客队进球"], r["主队进球"], r["客队xG"], r["主队xG"])):
            a = agg[me]
            a["场次"] += 1
            a[f"{side}场次"] += 1
            a["进球"] += gf; a["失球"] += ga
            a["xG"] += xf; a["xGa"] += xa
            a[f"{side}进球"] += gf; a[f"{side}失球"] += ga
            a[f"{side}xG"] += xf; a[f"{side}xGa"] += xa
            hist[me].append((r["日期"], gf, ga, xf, xa))
    out = []
    for t, a in agg.items():
        n = a["场次"]
        recent = hist[t][-window:]
        rn = len(recent) or 1
        xg, xga = a["xG"] / n, a["xGa"] / n
        gf, ga = a["进球"] / n, a["失球"] / n
        hs = a["主场次"] or 1
        as_ = a["客场次"] or 1
        out.append(dict(
            球队=t, 场次=int(n),
            场均进球=round(gf, 2), 场均失球=round(ga, 2),
            场均xG=round(xg, 2), 场均xGa=round(xga, 2),
            xG差=round(xg - xga, 2),
            进球减xG=round(gf - xg, 2),      # >0 超额（射术/运气好）<0 低效
            失球减xGa=round(ga - xga, 2),    # >0 门将/防线低于预期（被过量进球）
            主场_场均xG=round(a["主xG"] / hs, 2), 主场_场均xGa=round(a["主xGa"] / hs, 2),
            客场_场均xG=round(a["客xG"] / as_, 2), 客场_场均xGa=round(a["客xGa"] / as_, 2),
            近N场_场均xG=round(sum(x[3] for x in recent) / rn, 2),
            近N场_场均xGa=round(sum(x[4] for x in recent) / rn, 2),
            近N场_场均进球=round(sum(x[1] for x in recent) / rn, 2),
            近N场_场均失球=round(sum(x[2] for x in recent) / rn, 2),
        ))
    out.sort(key=lambda x: -x["xG差"])
    return out


def write_csv(rows, path, cols=None):
    cols = cols or list(rows[0].keys())
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader(); w.writerows(rows)


def compare_bo(rows, bo_path):
    """与 8BO/知识库 matches.csv 交叉校验比分（确认两源赛果一致）"""
    if not os.path.exists(bo_path):
        return "（未找到对照文件，跳过）"
    bo = {}
    for r in csv.DictReader(open(bo_path, encoding="utf-8-sig")):
        h = BO_ALIAS.get(r["home"], r["home"])
        a2 = BO_ALIAS.get(r["away"], r["away"])
        bo[(h, a2)] = (int(r["hg"]), int(r["ag"]))
    hit = miss = 0
    diffs = []
    for r in rows:
        k = (r["主队"], r["客队"])
        if k in bo:
            if bo[k] == (r["主队进球"], r["客队进球"]):
                hit += 1
            else:
                miss += 1
                diffs.append(f"{k[0]}-{k[1]}: 本源{r['主队进球']}-{r['客队进球']} vs 对照{bo[k][0]}-{bo[k][1]}")
    total = hit + miss
    if not total:
        return "（队名口径不匹配，无法比对；请检查中文映射）"
    msg = f"比分一致 {hit}/{total}" + (f"，不一致 {miss} 处：" + "；".join(diffs[:5]) if miss else "（完全一致）")
    return msg


def md_report(name, teams, rows, cmp_msg, window, fresh=None):
    L = [f"# {name} · 实测 xG 画像", ""]
    L.append(f"> 源：football-data.co.uk（实测 xG，Opta 系口径，S/A 级免检）｜"
             f"场次：{len(rows)}｜窗口：近 {window} 场｜生成：{now_cn().date()}（北京时间）")
    if fresh and fresh[0]:
        last, gap = fresh
        warn = "" if gap < 2 else "（请核对最近一轮是否已全部战罢；若已战罢仍未入库，赛前退回上一轮滚动值）"
        L.append(f"> **数据截至**：{last}（英国当地时间，北京时间约 +8h）｜距生成日 {gap} 天{warn}")
    if cmp_msg:
        L.append(f"> 交叉校验：{cmp_msg}")
    L += ["", "## 一、队级 xG 画像（按 xG 差排序）", "",
          "| 球队 | 场次 | 场均进/失 | 场均xG | 场均xGa | xG差 | 进球-xG | 失球-xGa |",
          "|---|---|---|---|---|---|---|---|"]
    for t in teams:
        L.append(f"| {t['球队']} | {t['场次']} | {t['场均进球']:.2f}/{t['场均失球']:.2f} | "
                 f"{t['场均xG']:.2f} | {t['场均xGa']:.2f} | {t['xG差']:+.2f} | "
                 f"{t['进球减xG']:+.2f} | {t['失球减xGa']:+.2f} |")
    L += ["", "## 二、主客场拆分", "",
          "| 球队 | 主场 xG | 主场 xGa | 客场 xG | 客场 xGa | 主客差(xG) |",
          "|---|---|---|---|---|---|"]
    for t in teams:
        L.append(f"| {t['球队']} | {t['主场_场均xG']:.2f} | {t['主场_场均xGa']:.2f} | "
                 f"{t['客场_场均xG']:.2f} | {t['客场_场均xGa']:.2f} | "
                 f"{t['主场_场均xG'] - t['客场_场均xG']:+.2f} |")
    L += ["", f"## 三、近 {window} 场滚动（趋势优先于全季均值）", "",
          "| 球队 | 近期xG | 近期xGa | 近期进/失 | 全季xG | 趋势 |", "|---|---|---|---|---|---|"]
    for t in teams:
        trend = "↑ 上行" if t["近N场_场均xG"] - t["场均xG"] > 0.25 else (
            "↓ 下行" if t["近N场_场均xG"] - t["场均xG"] < -0.25 else "→ 持平")
        L.append(f"| {t['球队']} | {t['近N场_场均xG']:.2f} | {t['近N场_场均xGa']:.2f} | "
                 f"{t['近N场_场均进球']:.2f}/{t['近N场_场均失球']:.2f} | {t['场均xG']:.2f} | {trend} |")
    L += ["", "## 四、读法与边界", "",
          "1. **进球-xG > +0.5**：射术或运气超额，短期易回落，不宜作为进攻上修依据；**< −0.5**：机会创造正常但转化失速，属可修复型。",
          "2. **失球-xGa > +0.5**：门将/防线低于预期（被过量进球），可视为「应回归」的防守信号；**< −0.5**：门将超额扑救，不可持续。",
          "3. **近期 vs 全季偏离 ±0.25 以上**才视为趋势成立，否则按均值处理。",
          "4. **xG 不含点球**（该源口径），与含点球的商业口径不可混比；跨数据商（Opta/StatsBomb/Wyscout）xG 绝对值不可横比。",
          "5. 该源**无未来赛程**、大小球仅 2.5 固定盘，如需赛程与实际盘口须并用 8BO。",
          ""]
    return "\n".join(L)


def run(league, season, outdir, bo=None, window=6):
    code, full = LEAGUES.get(league, (league, ""))
    name = full or code
    p = fetch_csv(code, season, outdir)
    if not p:
        print(f"[FAIL] {league} {season}：抓取失败（站点偶发超时，可重试或换 season 代码）")
        return None
    rows = parse(p)
    if not rows or rows[0]["主队xG"] is None:
        print(f"[FAIL] {name}：CSV 无 HxG/AxG 字段（该联赛该赛季可能未提供 xG）")
        return None
    teams = aggregate(rows, window)
    fresh = freshness(rows)
    write_csv(rows, os.path.join(outdir, f"{league}_matches_xg.csv"))
    write_csv(teams, os.path.join(outdir, f"{league}_teams_xg.csv"))
    cmp_msg = compare_bo(rows, bo) if bo else ""
    md = md_report(name, teams, rows, cmp_msg, window, fresh)
    mp = os.path.join(outdir, f"{league}_xg_report.md")
    open(mp, "w", encoding="utf-8").write(md)
    last, gap = fresh
    flag = "" if gap < 2 else "  ⚠ 距今>=2天：请核对最近一轮是否已全部战罢；若已战罢未入库，赛前退回上一轮滚动值"
    print(f"[OK] {name}：{len(rows)} 场 / {len(teams)} 队｜数据截至 {last}（英国时间，北京+8h）｜距今 {gap} 天{flag}")
    print(f"     -> {mp}")
    if cmp_msg:
        print("     " + cmp_msg)
    return teams


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", help="联赛名或代码：英超/西甲/德甲/意甲/法甲（或 E0/SP1/D1/I1/F1）")
    ap.add_argument("--season", default="2627", help="赛季代码，如 2627 = 2026-27")
    ap.add_argument("--outdir", default="./xg_out")
    ap.add_argument("--window", type=int, default=6, help="滚动窗口场次，默认 6")
    ap.add_argument("--compare", help="与 8BO 的 matches.csv 比对比分")
    ap.add_argument("--all", action="store_true", help="五大联赛一次跑完")
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)
    if a.all:
        lines = []
        for lg in ["英超", "西甲", "德甲", "意甲", "法甲"]:
            t = run(lg, a.season, a.outdir, a.compare, a.window)
            if t:
                lines.append(f"\n## {lg}\n\n" + "\n".join(
                    f"- {x['球队']}：xG {x['场均xG']:.2f} / xGa {x['场均xGa']:.2f}（差 {x['xG差']:+.2f}）"
                    for x in t[:5]) + "\n- …（完整见各联赛 CSV）")
        open(os.path.join(a.outdir, "xg_all_summary.md"), "w", encoding="utf-8").write(
            "# 五大联赛 xG 汇总（Top5 by xG差）\n" + "\n".join(lines))
        print(f"[OK] 汇总 -> {a.outdir}/xg_all_summary.md")
        return
    if not a.league:
        ap.error("需要 --league 或 --all")
    run(a.league, a.season, a.outdir, a.compare, a.window)


if __name__ == "__main__":
    main()

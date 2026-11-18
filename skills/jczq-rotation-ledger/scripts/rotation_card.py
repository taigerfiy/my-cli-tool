#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按竞彩当日场次生成「轮换风险卡」（只读 Base1 两表）"""
import argparse, json, os, subprocess, sys
from datetime import datetime

BASE_TOKEN = 'FsnQbGerEalbfBsmWFScFJtTnwe'
T_LEDGER = 'tblJiIkjupGhRBia'
T_PLAYER = 'tbl6o4arP72RKhhL'  # 球员档案（已并入首发连续性，联赛/欧战分列字段）
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALIAS_PATH = os.path.join(SKILL_DIR, 'assets', 'alias_map.json')
LEADER_FIELDS = ['联赛', '赛季', '轮次', '球队', '主客', '对手', '比分', '赛果', '开赛时间',
                 '休息天数', '阵型', '教练', '延续人数', '轮换人数', '延续率', '确定缺阵',
                 '存疑', '停赛', '伤停原文', '首发人数', '位置结构']
PLAYER_FIELDS = ['规范名', '位置', '联赛首发次数', '联赛轮次', '联赛首发率', '联赛分级']


def cli_read(table_id, team, fields, key_field='球队'):
    """按 key_field 等于 team 过滤读取，返回 dict 列表"""
    filter_json = json.dumps({'logic': 'and', 'conditions': [[key_field, '==', team]]}, ensure_ascii=False)
    cmd = ['lark-cli', 'base', '+record-list', '--base-token', BASE_TOKEN, '--table-id', table_id,
           '--as', 'user', '--format', 'json', '--limit', '200', '--filter-json', filter_json]
    for f in fields:
        cmd += ['--field-id', f]
    for attempt in (1, 2):
        cp = subprocess.run(cmd, capture_output=True, text=True)
        try:
            d = json.loads(cp.stdout)
        except Exception:
            d = None
        if d and d.get('ok'):
            body = d['data']
            names = body['fields']
            out = []
            for row in body['data']:
                rec = dict(zip(names, row))
                for k, v in list(rec.items()):
                    if isinstance(v, list) and v and not isinstance(v[0], (dict, list)):
                        rec[k] = v[0]
                out.append(rec)
            while body.get('has_more'):
                break
            return out
        if attempt == 2:
            raise RuntimeError(f'取数失败 {table_id} {team}: {cp.stdout[:200]} {cp.stderr[:200]}')
        if d is not None and d.get('ok') is False and 'not found' in str(d).lower():
            return []
    return []


def parse_dt(v):
    if not v:
        return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v / 1000)
    s = str(v).replace('T', ' ')[:16]
    try:
        return datetime.strptime(s[:16], '%Y-%m-%d %H:%M')
    except Exception:
        pass
    try:
        return datetime.strptime(s[:10], '%Y-%m-%d')
    except Exception:
        return None


def s1(v):
    return v[0] if isinstance(v, list) and v else v


def team_block(rows, players, match):
    """返回该队的指标字典"""
    o = {'found': bool(rows)}
    if not rows:
        return o
    lg = match.get('league')
    sub = [r for r in rows if r.get('联赛') == lg] if lg else []
    if not sub:
        cnt = {}
        for r in rows:
            cnt[r.get('联赛')] = cnt.get(r.get('联赛'), 0) + 1
        lg = max(cnt, key=cnt.get) if cnt else None
        sub = [r for r in rows if r.get('联赛') == lg]
    sub.sort(key=lambda r: r.get('轮次') or 0)
    o['league'] = lg
    o['rounds'] = len(sub)
    rot = [r['轮换人数'] for r in sub if isinstance(r.get('轮换人数'), (int, float))]
    keep = [r['延续率'] for r in sub if isinstance(r.get('延续率'), (int, float))]
    o['avg_rot'] = sum(rot) / len(rot) if rot else None
    o['avg_keep'] = sum(keep) / len(keep) if keep else None
    o['zero_cnt'] = sum(1 for x in rot if x == 0)
    o['zero_rate'] = (o['zero_cnt'] / len(rot)) if rot else None
    last = sub[-1] if sub else None
    o['last'] = last
    recent = sub[-3:]
    o['recent_rot'] = [r.get('轮换人数') for r in recent]
    o['recent_rounds'] = [r.get('轮次') for r in recent]
    o['recent_out'] = sum(int(r.get('确定缺阵') or 0) for r in recent)
    o['recent_zero'] = sum(1 for r in recent if (r.get('轮换人数') or 0) == 0)
    doubts = []
    for r in recent:
        if (r.get('存疑') or 0) > 0 and r.get('伤停原文'):
            doubts.append(f"R{r.get('轮次')}: {r.get('伤停原文')}")
    o['doubts'] = doubts[-3:]
    kick = parse_dt(match.get('kick'))
    o['rest_to_match'] = None
    if kick and last and parse_dt(last.get('开赛时间')):
        o['rest_to_match'] = (kick.date() - parse_dt(last.get('开赛时间')).date()).days
    o['europe'] = []
    if kick:
        for r in rows:
            if r.get('联赛') in ('欧冠', '欧罗巴'):
                k = parse_dt(r.get('开赛时间'))
                if k and 0 <= (kick.date() - k.date()).days <= 4:
                    o['europe'].append(f"{r['联赛']} R{r.get('轮次')}（{k.strftime('%m-%d')}）")
    core = [p for p in players if s1(p.get('联赛分级')) == '全勤核心']
    top = sorted(players, key=lambda p: -(p.get('联赛首发率') or 0))[:3]
    o['core_cnt'] = len(core)
    o['player_total'] = len(players)
    o['top3'] = [(p.get('规范名'), s1(p.get('位置')) or '', p.get('联赛首发率')) for p in top]
    return o


def risk(o):
    score, why = 0, []
    if o.get('avg_rot') is not None:
        if o['avg_rot'] >= 3:
            score += 2; why.append(f"场均轮换 {o['avg_rot']:.2f}（+2）")
        elif o['avg_rot'] >= 2:
            score += 1; why.append(f"场均轮换 {o['avg_rot']:.2f}（+1）")
    rest = o.get('rest_to_match')
    if isinstance(rest, int):
        if rest <= 3:
            score += 2; why.append(f"距本场休息 {rest} 天（+2）")
        elif rest <= 5:
            score += 1; why.append(f"距本场休息 {rest} 天（+1）")
        elif rest < 0:
            why.append("⚠开赛时间早于末轮，休息天数未计")
    if o.get('recent_out', 0) >= 4:
        score += 2; why.append(f"近 3 轮确定缺阵 {o['recent_out']} 人次（+2）")
    elif o.get('recent_out', 0) >= 2:
        score += 1; why.append(f"近 3 轮确定缺阵 {o['recent_out']} 人次（+1）")
    if o.get('europe'):
        score += 2; why.append('本场前 4 天内有欧战（+2）：' + '、'.join(o['europe']))
    if o.get('avg_rot') is not None and o['avg_rot'] <= 1.5 and o.get('recent_zero', 0) >= 2:
        score -= 1; why.append('近 3 轮 2 次以上零轮换，稳定性抵扣（-1）')
    lvl = '高' if score >= 5 else ('中' if score >= 3 else '低')
    return lvl, score, why


def fmt_rot(v):
    return '—' if v is None else f'{v:.0f}' if float(v).is_integer() else f'{v:.1f}'


def render(matches, alias):
    L = []
    L.append('# 竞彩轮换风险卡（首发轮换台账）')
    L.append('')
    L.append('- 数据源：Base1 轮换台账（球队×轮次）1282 条 / 球员档案（含首发连续性）2763 条（只读）')
    L.append('- 口径：延续=与上轮首发重合，轮换=首发−延续，休息=同联赛相邻轮次日期差；跨赛事不合并；球员连续性取联赛口径')
    L.append('- 生成时间：' + datetime.now().strftime('%Y-%m-%d %H:%M'))
    L.append('')
    for m in matches:
        no, home_raw, away_raw = m.get('no', ''), m.get('home', ''), m.get('away', '')
        home = alias.get(home_raw, home_raw)
        away = alias.get(away_raw, away_raw)
        L.append('## ' + f'{no} {home_raw} vs {away_raw}' + (f'（{m.get("kick")}）' if m.get('kick') else ''))
        L.append('')
        blocks = {}
        for nm, team in (('主', home), ('客', away)):
            rows = cli_read(T_LEDGER, team, LEADER_FIELDS)
            pl = cli_read(T_PLAYER, team, PLAYER_FIELDS, key_field='所属俱乐部')
            blocks[nm] = (team, team_block(rows, pl, m))
        L.append('| 指标 | 主（' + home + '） | 客（' + away + '） |')
        L.append('|---|---|---|')
        def cell(f):
            a, b = blocks['主'][1], blocks['客'][1]
            return f(a), f(b)
        r1 = cell(lambda o: '台账无该队记录' if not o['found'] else str(o['rounds']) + ' 轮' + ('' if o['rounds'] >= 3 else ' ⚠样本不足'))
        L.append(f'| 样本 | {r1[0]} | {r1[1]} |')
        r2 = cell(lambda o: '—' if not o['found'] else fmt_rot(o.get('avg_rot')))
        L.append(f'| 场均轮换 | {r2[0]} | {r2[1]} |')
        r3 = cell(lambda o: '—' if not o['found'] else '/'.join(fmt_rot(x) for x in o['recent_rot']))
        L.append(f'| 近 3 轮轮换序列 | {r3[0]} | {r3[1]} |')
        r4 = cell(lambda o: '—' if not o['found'] or o.get('avg_keep') is None else f"{o['avg_keep']*100:.0f}%")
        L.append(f'| 平均延续率 | {r4[0]} | {r4[1]} |')
        r5 = cell(lambda o: '—' if not o['found'] else (f"{o['rest_to_match']} 天" if isinstance(o.get('rest_to_match'), int) and o['rest_to_match'] >= 0 else '—'))
        L.append(f'| 距本场休息 | {r5[0]} | {r5[1]} |')
        r6 = cell(lambda o: '—' if not o['found'] else str(o['recent_out']))
        L.append(f'| 近 3 轮确定缺阵（人次） | {r6[0]} | {r6[1]} |')
        r7 = cell(lambda o: '—' if not o['found'] else f"{o['zero_cnt']}/{o['rounds']}")
        L.append(f'| 零轮换轮次 | {r7[0]} | {r7[1]} |')
        r8 = cell(lambda o: '—' if not o['found'] else str(o['core_cnt']))
        L.append(f'| 全勤核心人数 | {r8[0]} | {r8[1]} |')
        L.append('')
        for nm in ('主', '客'):
            team, o = blocks[nm]
            L.append(f'### {nm}队 {team}')
            if not o['found']:
                L.append('- 台账无该队记录（017 未覆盖或队名未匹配），不要用联赛均值顶替')
                L.append('')
                continue
            if o.get('top3'):
                L.append('- 不可替代：' + '；'.join(f"{p}（{pos}）{r*100:.0f}%" for p, pos, r in o['top3']))
            if o.get('doubts'):
                L.append('- 近期存疑/伤停原文：' + ' ｜ '.join(o['doubts']))
            elif o.get('last') and o['last'].get('伤停原文'):
                L.append('- 末轮伤停原文：' + str(o['last']['伤停原文']))
            if o.get('europe'):
                L.append('- 欧战穿插：' + '、'.join(o['europe']))
            lvl, score, why = risk(o)
            L.append(f'- 轮换风险：**{lvl}**（{score} 分）｜' + '；'.join(why))
            L.append('')
        L.append('#### 轮换风险点')
        for nm in ('主', '客'):
            team, o = blocks[nm]
            if not o['found']:
                continue
            pts = []
            if o.get('europe'):
                pts.append(f'{team} 本场前 4 天内有欧战，联赛轮换概率抬升')
            if o.get('recent_zero', 0) >= 2:
                pts.append(f'{team} 近 3 轮 {o["recent_zero"]} 次零轮换，主力负荷累积，本场体能拐点风险')
            if o.get('recent_out', 0) >= 2:
                pts.append(f'{team} 近 3 轮累计 {o["recent_out"]} 人次确定缺阵，被动调整压力')
            if o.get('avg_rot') is not None and o['avg_rot'] >= 3:
                pts.append(f'{team} 场均轮换 {o["avg_rot"]:.2f}，属主动轮换型，首发可预测性低')
            if not pts:
                pts.append(f'{team} 轮换信号平稳，无额外风险点')
            for p in pts:
                L.append('- ' + p)
        L.append('')
        L.append('> 本卡只呈现轮换与缺阵事实，不含方向判断、比分预测或投注建议。')
        L.append('')
    return '\n'.join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--matches', required=True)
    ap.add_argument('--out')
    ap.add_argument('--out-dir')
    ap.add_argument('--max-per-file', type=int, default=6)
    ap.add_argument('--alias', default=ALIAS_PATH)
    a = ap.parse_args()
    matches = json.load(open(a.matches, encoding='utf-8'))
    alias = json.load(open(a.alias, encoding='utf-8')) if os.path.exists(a.alias) else {}
    if a.out:
        md = render(matches, alias)
        open(a.out, 'w', encoding='utf-8').write(md)
        print('写出', a.out, len(md), '字符')
        return
    os.makedirs(a.out_dir, exist_ok=True)
    chunks = [matches[i:i + a.max_per_file] for i in range(0, len(matches), a.max_per_file)]
    for i, ch in enumerate(chunks, 1):
        p = os.path.join(a.out_dir, f'chunk_{i}.md')
        open(p, 'w', encoding='utf-8').write(render(ch, alias))
        print('写出', p)
    print('共', len(chunks), '卷')


if __name__ == '__main__':
    main()

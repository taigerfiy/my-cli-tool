#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""赛前情报包生成器（Base1 信息匹配框架）

输入：竞彩清单 JSON，形如
  [{"no":"周一001","home":"法罗群岛","away":"摩尔多瓦","kickoff":"23:00","rg":"-1"}]
  也可只给 no + home + away，联赛与让球自动从主表取。

输出：一份 Markdown 情报包（多场汇总，单份 ≤20 页），供人工转交分析 agent。

六层匹配：
  L0 归一   名称对照（别名→规范名），未命中不猜
  L1 本场   主表（统一键 YYMMDD-周XNNN）→ 001 赛前静态 → 006 终盘
  L1D 文档  001 文件夹 7表格 + 历史战绩（Base1 不存赛前，--doc 传入）
  L2 球队   球队画像（球队|主客）+ xG实测画像 + 近10场同主客
  L3 对阵   交锋画像（主队|客队）
  L4 基线   A级联赛统计 + 玩法分布（该联赛）
  L5 特征   身价比/综合评分差/控球率差 → 落桶取历史分布
  L6 质量   每段带 样本数 / 数据截止 / 口径状态
  L7 外部   并入外部采集的 facts/情报包（--external），赛前未入库仍入包

用法：
  python3 jczq/match_pack.py <清单.json> --date 2026-11-16 --out 011/情报包.md
  python3 jczq/match_pack.py <清单.json> --date 2026-11-16 --out 011/情报包.md \
      --doc 001.json --external intel.json match_intel_XXX.md
"""
import json, subprocess, sys, os, argparse, re

BASE = 'FsnQbGerEalbfBsmWFScFJtTnwe'
T_MAIN = 'tbllopaKMIVIHU2p'      # 比赛核心主表
T_001 = 'tbl7NdB6xpGty3wG'       # 赛前静态数据
T_006 = 'tbli943KH8a8hNaY'       # 竞彩波动数据
T_TEAM = 'tblogCk27fn7Jx3q'      # 球队画像
T_XG = 'tblCrokB322Poyo7'        # xG实测画像
T_H2H = 'tbl0FcdwqVD50ugC'       # 交锋画像
T_RES = 'tblqNeegAn2as0gt'       # 联赛全量赛果
T_LG = 'tblM1YVIBlfuxU4C'        # A级联赛统计
T_PLAY = 'tblmsLYEbMzzOsi8'      # 玩法分布
T_FEAT = 'tblowiU87Hosc4Nu'      # 赛前特征响应
T_NAME = 'tblisjWesOVwl2We'      # 名称对照
T_NAT = 'tblpOYpQqq4SsDkt'       # 国家队赛果库

CN = {'周一': 1, '周二': 2, '周三': 3, '周四': 4, '周五': 5, '周六': 6, '周日': 7}


def cli(args):
    r = subprocess.run(['lark-cli', 'base'] + args + ['--base-token', BASE, '--as', 'user'],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except Exception:
        return None


def dq(tid, dims=None, measures=None, filters=None, limit=500, sort=None):
    """聚合查询，返回 [{alias: value}]"""
    dsl = {'datasource': {'type': 'table', 'table': {'tableId': tid}},
           'pagination': {'limit': limit}, 'shaper': {'format': 'flat'}}
    if dims:
        dsl['dimensions'] = [{'field_name': d, 'alias': a} for a, d in dims]
    if measures:
        dsl['measures'] = [{'field_name': f, 'aggregation': g, 'alias': a} for a, f, g in measures]
    if filters:
        dsl['filters'] = filters
    if sort:
        dsl['sort'] = sort
    d = cli(['+data-query', '--dsl', json.dumps(dsl, ensure_ascii=False), '--format', 'json'])
    if not d or 'data' not in d:
        return []
    rows = d['data'].get('main_data') or []
    out = []
    for r in rows:
        out.append({k: (v.get('value') if isinstance(v, dict) else v) for k, v in r.items()})
    return out


_OP = {'is': '==', 'isNot': '!=', 'isGreater': '>', 'isLess': '<',
       'isGreaterEqual': '>=', 'isLessEqual': '<=', 'contains': 'intersects'}


def to_rl_filter(filt):
    """data-query 过滤条件 → record-list 过滤条件"""
    if not filt:
        return None
    conds = []
    for c in filt.get('conditions', []):
        v = c['value']
        if isinstance(v, list):
            v = v[0] if len(v) == 1 else v
        conds.append([c['field_name'], _OP.get(c['operator'], '=='), v])
    return {'logic': filt.get('conjunction', 'and'), 'conditions': conds}


def rl(tid, filt, fields, sort=None, limit=100):
    """明细查询，返回 [{字段: 值}]"""
    args = ['+record-list', '--table-id', tid, '--format', 'json', '--limit', str(limit)]
    rf = to_rl_filter(filt)
    if rf:
        args += ['--filter-json', json.dumps(rf, ensure_ascii=False)]
    if sort:
        args += ['--sort-json', json.dumps(sort, ensure_ascii=False)]
    for f in fields:
        args += ['--field-id', f]
    d = cli(args)
    if not d or 'data' not in d:
        return []
    d = d['data']
    names = d.get('fields') or fields
    rows = d.get('data') or []
    res = []
    for row in rows:
        res.append({names[i]: row[i] for i in range(min(len(names), len(row)))})
    return res


_FCACHE = {}


def get_fields(tid):
    """表字段顺序（record-get 按此映射）"""
    if tid in _FCACHE:
        return _FCACHE[tid]
    r = subprocess.run(['lark-cli', 'base', '+field-list', '--base-token', BASE,
                        '--table-id', tid, '--as', 'user', '--format', 'json'],
                       capture_output=True, text=True)
    try:
        f = [x['name'] for x in json.loads(r.stdout)['data']['fields']]
    except Exception:
        f = []
    _FCACHE[tid] = f
    return f


def get_rec(tid, ids):
    """按 record_id 读取，返回 {id: {字段: 值}}"""
    if not ids:
        return {}
    args = ['+record-get', '--table-id', tid, '--as', 'user', '--format', 'json']
    for i in ids:
        args += ['--record-id', i]
    r = subprocess.run(['lark-cli', 'base'] + args + ['--base-token', BASE],
                       capture_output=True, text=True)
    try:
        d = json.loads(r.stdout)['data']
    except Exception:
        return {}
    names = d.get('fields') or get_fields(tid)
    rows = d.get('data') or []
    out = {}
    for idx, row in enumerate(rows[:len(ids)]):
        out[ids[idx]] = {names[i]: row[i] for i in range(min(len(names), len(row)))}
    return out


def ids_of(v):
    """link 字段 → record id 列表"""
    if not v:
        return []
    if isinstance(v, list):
        return [x.get('id') for x in v if isinstance(x, dict) and x.get('id')]
    return []


def f_and(conds):
    return {'type': 1, 'conjunction': 'and',
            'conditions': [{'field_name': f, 'operator': op, 'value': v} for f, op, v in conds]}


def f_or(conds):
    return {'type': 1, 'conjunction': 'or',
            'conditions': [{'field_name': f, 'operator': op, 'value': v} for f, op, v in conds]}


def unified_key(date_str, no):
    yy = date_str[2:4] + date_str[5:7] + date_str[8:10]
    return '%s-%s' % (yy, no)


def norm_name(n):
    """别名→规范名（未收录则原样返回）"""
    r = rl(T_NAME, f_and([('别名', 'is', [n])]), ['别名', '规范名'], limit=5)
    if r:
        v = r[0].get('规范名')
        if isinstance(v, list):
            v = v[0] if v else None
        if isinstance(v, dict):
            v = v.get('text') or v.get('value')
        if v:
            return str(v)
    return n


def txt(v):
    if v is None:
        return ''
    if isinstance(v, list):
        return ','.join(txt(x) for x in v)
    if isinstance(v, dict):
        return v.get('text') or v.get('name') or v.get('value') or ''
    return v


def num(v):
    try:
        return float(txt(v)) if txt(v) not in ('', None) else None
    except Exception:
        return None


def fmt(v, nd=2):
    x = num(v)
    return '-' if x is None else ('%.*f' % (nd, x))


# ---------------- 各层 ----------------

def layer1_main(dk, home, away):
    """主表记录（含 001/006 的 link record id）"""
    rows = rl(T_MAIN, f_and([('统一键', 'is', [dk])]),
              ['统一键', '竞彩编号', '联赛', '赛事等级', '主队', '客队', '让球数', '轮次',
               '比赛ID', '数据完整性', '赛前静态数据（001）', '竞彩波动数据（006）'], limit=3)
    return rows[0] if rows else None


def layer1_001(ids):
    if not ids:
        return None
    return get_rec(T_001, ids[:1]).get(ids[0])


def layer1_006(ids):
    if not ids:
        return None
    return get_rec(T_006, ids[:1]).get(ids[0])


def layer2_team(home, away):
    rows = dq(T_TEAM, dims=[('k', '画像键'), ('tm', '球队'), ('ha', '主客'), ('lg', '联赛'),
                            ('w', '胜'), ('d', '平'), ('l', '负'), ('rate', '胜率'),
                            ('gf', '场均进球'), ('ga', '场均失球'), ('gd', '净差'),
                            ('sn', '样本场数'), ('w0', '窗口起'), ('w1', '窗口止'), ('tier', '胜率分档')],
              measures=[('n', '画像键', 'count_all')],
              filters=f_and([('画像键', 'is', ['%s|主场' % home])]), limit=5)
    rows2 = dq(T_TEAM, dims=[('k', '画像键'), ('tm', '球队'), ('ha', '主客'), ('lg', '联赛'),
                             ('w', '胜'), ('d', '平'), ('l', '负'), ('rate', '胜率'),
                             ('gf', '场均进球'), ('ga', '场均失球'), ('gd', '净差'),
                             ('sn', '样本场数'), ('w0', '窗口起'), ('w1', '窗口止'), ('tier', '胜率分档')],
               measures=[('n', '画像键', 'count_all')],
               filters=f_and([('画像键', 'is', ['%s|客场' % away])]), limit=5)
    return rows + rows2


def layer2_xg(home, away):
    rows = dq(T_XG, dims=[('tm', '球队'), ('lg', '联赛'), ('xg', '场均xG'), ('xga', '场均xGa'),
                          ('xgd', 'xG差'), ('gp', '进球减xG'), ('gl', '失球减xGa'),
                          ('n6xg', '近6场xG'), ('n6xga', '近6场xGa'), ('n6g', '近6场进球'),
                          ('sig', '信号'), ('upto', '数据截至'), ('sn', '场次')],
              measures=[('n', '球队', 'count_all')],
              filters=f_and([('球队', 'is', [home])]), limit=5)
    rows2 = dq(T_XG, dims=[('tm', '球队'), ('lg', '联赛'), ('xg', '场均xG'), ('xga', '场均xGa'),
                           ('xgd', 'xG差'), ('gp', '进球减xG'), ('gl', '失球减xGa'),
                           ('n6xg', '近6场xG'), ('n6xga', '近6场xGa'), ('n6g', '近6场进球'),
                           ('sig', '信号'), ('upto', '数据截至'), ('sn', '场次')],
               measures=[('n', '球队', 'count_all')],
               filters=f_and([('球队', 'is', [away])]), limit=5)
    return rows + rows2


def layer2_recent(team, side, before_date, alt=None):
    """近10场同主客：side=home 取该队作主队；side=away 取作客队；规范名查不到时用原名回退"""
    field = '主队' if side == 'home' else '客队'
    rows = []
    for t in ([team] + ([alt] if alt and alt != team else [])):
        filt = f_and([(field, 'is', [t]), ('比赛日期', 'isLess', before_date)])
        rows = rl(T_RES, filt, ['比赛日期', '联赛', '主队', '客队', '主队进球', '客队进球', '赛季'],
                  sort=[{'field': '比赛日期', 'desc': True}], limit=10)
        if rows:
            break
    return rows


def layer3_h2h(home, away):
    rows = dq(T_H2H, dims=[('h', '主队'), ('a', '客队'), ('n', '交锋次数'),
                           ('hw', '主队胜'), ('d', '平'), ('aw', '客队胜'),
                           ('hr', '主队胜率'), ('g', '场均总进球'),
                           ('last', '最近交锋日期'), ('score', '最近比分'), ('lvl', '样本等级')],
              measures=[('cnt', '交锋键', 'count_all')],
              filters=f_and([('交锋键', 'is', ['%s|%s' % (home, away)])]), limit=5)
    return rows[0] if rows else None


def layer4_league(lg):
    rows = dq(T_LG, dims=[('lg', '联赛'), ('lv', '赛事等级'), ('tot', '总场次'),
                          ('hw', '主胜率%'), ('dr', '平局率%'), ('aw', '客胜率%'),
                          ('gf', '场均进球'), ('big', '大球率%'), ('cal', '统计口径'),
                          ('upd', '数据更新时间')],
              measures=[('n', '联赛', 'count_all')],
              filters=f_and([('联赛', 'is', [lg])]), limit=5)
    return rows[0] if rows else None


def layer4_play(lg):
    rows = dq(T_PLAY, dims=[('w', '玩法'), ('tier', '条件档'), ('lab', '结果标签'),
                            ('sn', '样本数'), ('pct', '占比')],
              measures=[('n', '统计键', 'count_all')],
              filters=f_and([('维度', 'is', ['联赛']), ('维度值', 'is', [lg])]),
              limit=60)
    return rows


BUCKET = {
    '身价比': [(0.7, '<=', '≤0.7'), (1.0, '<=', '0.7-1.0'), (1.4, '<=', '1.0-1.4'), (2.5, '<=', '1.4-2.5')],
    '综合评分差': [(-15, 'le', '≤-15'), (-5, 'le', '-15~-5'), (5, 'le', '±5'), (15, 'le', '5~15')],
    '控球率差': [(-10, 'le', '≤-10'), (-3, 'le', '-10~-3'), (3, 'le', '±3'), (10, 'le', '3~10')],
}


def pick_bucket(feat, val):
    if val is None:
        return None
    for thr, op, name in BUCKET[feat]:
        if val <= thr:
            return name
    return {'身价比': '≥2.5', '综合评分差': '≥15', '控球率差': '≥10'}[feat]


def layer5_feat(buckets):
    out = {}
    for feat, b in buckets.items():
        if not b:
            continue
        rows = dq(T_FEAT, dims=[('lab', '结果标签'), ('sn', '样本数'), ('pct', '占比')],
                  measures=[('n', '特征', 'count_all')],
                  filters=f_and([('维度', 'is', ['全样本']), ('特征', 'is', [feat]), ('条件档', 'is', [b])]),
                  limit=15)
        out[feat] = (b, rows)
    return out


# ---------------- 主流程 ----------------

NAT_CN2EN = {
    '德国': 'Germany', '荷兰': 'Netherlands', '法国': 'France', '西班牙': 'Spain', '意大利': 'Italy',
    '英格兰': 'England', '葡萄牙': 'Portugal', '比利时': 'Belgium', '克罗地亚': 'Croatia',
    '瑞士': 'Switzerland', '丹麦': 'Denmark', '瑞典': 'Sweden', '挪威': 'Norway', '波兰': 'Poland',
    '奥地利': 'Austria', '捷克': 'Czech Republic', '苏格兰': 'Scotland', '威尔士': 'Wales',
    '爱尔兰': 'Republic of Ireland', '北爱尔兰': 'Northern Ireland', '冰岛': 'Iceland',
    '芬兰': 'Finland', '希腊': 'Greece', '塞尔维亚': 'Serbia', '斯洛文尼亚': 'Slovenia',
    '斯洛伐克': 'Slovakia', '匈牙利': 'Hungary', '罗马尼亚': 'Romania', '保加利亚': 'Bulgaria',
    '乌克兰': 'Ukraine', '俄罗斯': 'Russia', '土耳其': 'Turkey', '格鲁吉亚': 'Georgia',
    '阿尔巴尼亚': 'Albania', '北马其顿': 'North Macedonia', '黑山': 'Montenegro',
    '波黑': 'Bosnia and Herzegovina', '爱沙尼亚': 'Estonia', '拉脱维亚': 'Latvia',
    '立陶宛': 'Lithuania', '卢森堡': 'Luxembourg', '摩尔多瓦': 'Moldova', '法罗群岛': 'Faroe Islands',
    '马耳他': 'Malta', '塞浦路斯': 'Cyprus', '亚美尼亚': 'Armenia', '阿塞拜疆': 'Azerbaijan',
    '以色列': 'Israel', '哈萨克斯坦': 'Kazakhstan', '乌兹别克斯坦': 'Uzbekistan',
    '日本': 'Japan', '韩国': 'South Korea', '中国': 'China', '澳大利亚': 'Australia',
    '沙特阿拉伯': 'Saudi Arabia', '沙特': 'Saudi Arabia', '卡塔尔': 'Qatar', '伊朗': 'Iran',
    '伊拉克': 'Iraq', '阿联酋': 'United Arab Emirates', '越南': 'Vietnam', '泰国': 'Thailand',
    '印尼': 'Indonesia', '马来西亚': 'Malaysia', '新加坡': 'Singapore', '印度': 'India',
    '美国': 'United States', '墨西哥': 'Mexico', '加拿大': 'Canada', '巴西': 'Brazil',
    '阿根廷': 'Argentina', '乌拉圭': 'Uruguay', '哥伦比亚': 'Colombia', '智利': 'Chile',
    '秘鲁': 'Peru', '厄瓜多尔': 'Ecuador', '巴拉圭': 'Paraguay', '委内瑞拉': 'Venezuela',
    '玻利维亚': 'Bolivia', '摩洛哥': 'Morocco', '埃及': 'Egypt', '尼日利亚': 'Nigeria',
    '塞内加尔': 'Senegal', '加纳': 'Ghana', '喀麦隆': 'Cameroon', '科特迪瓦': "Côte d'Ivoire",
    '突尼斯': 'Tunisia', '阿尔及利亚': 'Algeria', '南非': 'South Africa', '新西兰': 'New Zealand',
}

NAT_KEYS = ('欧国联', '友谊', '预赛', '欧洲杯', '世界杯', '亚洲杯', '美洲杯', '非洲杯',
            '国家联赛', '亚运', '奥运', '联合会杯')


def is_national(lg):
    return any(k in (lg or '') for k in NAT_KEYS)


def layer2_national(team, before_date):
    """国家队近10场（不分主客，国家队样本少）"""
    en = NAT_CN2EN.get(team, team)
    rows = rl(T_NAT, f_or([('主队', 'is', [en]), ('客队', 'is', [en])]),
              ['比赛日期', '赛事', '主队', '客队', '主队进球', '客队进球'],
              sort=[{'field': '比赛日期', 'desc': True}], limit=14)
    out = [r for r in rows if txt(r.get('比赛日期'))[:10] < before_date]
    return en, out[:10]


def build(item, date_str):
    no = item['no']
    home0, away0 = item['home'], item['away']
    dk = unified_key(date_str, no)
    notes = []

    m = layer1_main(dk, home0, away0)
    if not m:
        notes.append('L1 赛前未入库（统一键 %s），无文档/外部数据时不入包' % dk)
        return None, notes

    lg = txt(m.get('联赛'))
    home = norm_name(txt(m.get('主队')) or home0)
    away = norm_name(txt(m.get('客队')) or away0)
    mid = txt(m.get('比赛ID'))
    ids1 = ids_of(m.get('赛前静态数据（001）'))
    ids6 = ids_of(m.get('竞彩波动数据（006）'))
    o1 = layer1_001(ids1)
    o6 = layer1_006(ids6)
    national = is_national(lg)

    rh = ra = []
    nat_en = ('', '')
    if national:
        nat_en = (layer2_national(home, date_str)[0], layer2_national(away, date_str)[0])
        rh = layer2_national(home, date_str)[1]
        ra = layer2_national(away, date_str)[1]
    else:
        rh = layer2_recent(home, 'home', date_str, item['home'])
        ra = layer2_recent(away, 'away', date_str, item['away'])

    buckets = {}
    if o1:
        hv, av = num(o1.get('主队身价')), num(o1.get('客队身价'))
        buckets['身价比'] = pick_bucket('身价比', hv / av if hv and av else None)
        hc, ac = num(o1.get('主队综合评分')), num(o1.get('客队综合评分'))
        buckets['综合评分差'] = pick_bucket('综合评分差', hc - ac if hc is not None and ac is not None else None)
        hp, ap = num(o1.get('主队控球率')), num(o1.get('客队控球率'))
        buckets['控球率差'] = pick_bucket('控球率差', hp - ap if hp is not None and ap is not None else None)

    return {
        'no': no, 'key': dk, 'lg': lg, 'lv': txt(m.get('赛事等级')), 'home': home, 'away': away,
        'rg': txt(m.get('让球数')), 'round': txt(m.get('轮次')), 'mid': mid,
        'national': national, 'nat_en': nat_en,
        'main': m, 'o1': o1, 'o6': o6,
        'team': [] if national else layer2_team(home, away),
        'xg': [] if national else layer2_xg(home, away),
        'rh': rh, 'ra': ra,
        'h2h': None if national else layer3_h2h(home, away),
        'league': None if national else layer4_league(lg),
        'play': layer4_play(lg), 'feat': layer5_feat(buckets), 'buckets': buckets,
        'base_missed': False,
    }, notes


def nat_stats(rows, team_en):
    if not rows:
        return None
    w = d = l = gf = ga = 0
    for r in rows:
        hg, ag = num(r.get('主队进球')), num(r.get('客队进球'))
        if hg is None or ag is None:
            continue
        if txt(r.get('主队')) == team_en:
            my, op = hg, ag
        else:
            my, op = ag, hg
        gf += my
        ga += op
        if my > op:
            w += 1
        elif my == op:
            d += 1
        else:
            l += 1
    n = w + d + l
    if not n:
        return None
    return {'n': n, 'w': w, 'd': d, 'l': l, 'gf': gf / n, 'ga': ga / n,
            'first': txt(rows[-1].get('比赛日期'))[:10], 'last': txt(rows[0].get('比赛日期'))[:10]}


def recent_stats(rows, team, side):
    if not rows:
        return None
    w = d = l = gf = ga = 0
    for r in rows:
        hg, ag = num(r.get('主队进球')), num(r.get('客队进球'))
        if hg is None or ag is None:
            continue
        my, op = (hg, ag) if side == 'home' else (ag, hg)
        gf += my
        ga += op
        if my > op:
            w += 1
        elif my == op:
            d += 1
        else:
            l += 1
    n = w + d + l
    if not n:
        return None
    return {'n': n, 'w': w, 'd': d, 'l': l,
            'gf': gf / n, 'ga': ga / n,
            'first': txt(rows[-1].get('比赛日期'))[:10], 'last': txt(rows[0].get('比赛日期'))[:10]}


def render(packs, date_str, ext=None, slim=True, doc=None):
    ext = ext or {}
    doc = doc or {}
    out = []
    out.append('# %s 赛前情报包（Base1 + 001 文档 + 外部情报）\n' % date_str)
    out.append('- 场次：%d（%s）' % (len(packs), '、'.join(p['no'] for p in packs)))
    out.append('- 匹配层：L1 本场（Base1 主表/001/006）→ L1D 赛前文档（001 7表格+历史战绩）→ L2 球队（画像/xG/近10同主客）→ L3 交锋 → L4 联赛基线 → L5 特征桶 → L6 质量门 → L7 外部情报')
    out.append('- 口径：L1–L6 取自 Base1（已完赛历史与基线）；L1D 取自 001 文档（赛前主数据，Base1 不存）；L7 取自外部采集（伤停/阵容/盘口/环境，facts v1，带 observed_at）')
    out.append('- 两源冲突：并列标 `[源冲突]`，不自动择一；L7 缺失不影响 L1–L6 结论，反之亦然')
    out.append('- 近 10 场取同主客（主队只算主场、客队只算客场），排除本场及以后\n')

    for p in packs:
        if p.get('base_missed'):
            out.append('## %s %s vs %s（赛前未入库·设计如此）\n' % (p['no'], p['home'], p['away']))
            out.append('- 统一键 %s｜库内无该场记录（L1–L6 空）' % p['key'])
            out.append('- L1–L6：**赛前未入库**（Base1 主表仅存已完赛数据，赛前场次不入库属设计如此；本节不适用，不计缺口）')
            miss = []
            render_doc(p['no'], doc.get(p['no']), out)
            render_l7(p['no'], ext.get(p['no']), out, miss, slim)
            out.append('\n**L6 质量门**：赛前未入库（设计如此）；外部完成度与缺口见 L7（completeness 独立，不并入 L6）')
            out.append('')
            continue

        o1, o6 = p['o1'] or {}, p['o6'] or {}
        out.append('## %s %s vs %s（%s %s）\n' % (p['no'], p['home'], p['away'], p['lg'], p['lv']))
        out.append('- 统一键 %s｜让球 %s｜轮次 %s｜比赛ID %s' % (p['key'], p['rg'] or '-', p['round'] or '-', p['mid'] or '-'))
        out.append('- 库内关联：001 %s｜006 %s' % ('有' if p['o1'] else '无', '有' if p['o6'] else '无'))

        # L1
        out.append('\n**L1 本场赛前静态（001）**\n')
        f = ['主队身价', '客队身价', '主队综合评分', '客队综合评分', '主队控球率', '客队控球率',
             '主队近10场同主客战绩', '客队近10场同主客战绩', 'xG主队', 'xG客队', 'xG数据状态']
        miss = []
        if o1:
            out.append('| 项 | 值 |')
            out.append('|---|---|')
            for k in f:
                v = txt(o1.get(k))
                if not v and not k.startswith('xG'):
                    miss.append(k)
                show = v or ('001 未供，见 L2 xG画像' if k.startswith('xG') else '—')
                out.append('| %s | %s |' % (k, show))
        else:
            out.append('库内无 001 关联，本节缺。')

        out.append('\n**L1 本场终盘（006）**\n')
        if o6:
            out.append('| 项 | 值 |')
            out.append('|---|---|')
            for k in ['终盘热门', '初始主胜', '初始平', '初始客胜', '终盘让胜', '终盘让平', '终盘让负',
                      '总进球终盘热门', '半全场热钱方向', '比分终盘热门赔率', '冷门等级', '波动等级']:
                v = txt(o6.get(k))
                if not v:
                    miss.append('006.' + k)
                show = v or ('001 未供，见 L2 xG画像' if k.startswith('xG') else '—')
                out.append('| %s | %s |' % (k, show))
        else:
            out.append('库内无 006 关联，本节缺。')

        render_doc(p['no'], doc.get(p['no']), out)

        # L2
        out.append('\n**L2 球队**\n')
        if p['national']:
            out.append('国家队赛事：球队画像／xG实测画像为俱乐部口径，**本层不适用**（不计缺口）。历史取自「国家队赛果库」，不分主客。\n')
            out.append('| 队 | 库内英文名 | 场次 | 胜平负 | 场均进 | 场均失 | 区间 |')
            out.append('|---|---|---|---|---|---|---|')
            for team, rows, en in ((p['home'], p['rh'], p['nat_en'][0]), (p['away'], p['ra'], p['nat_en'][1])):
                s = nat_stats(rows, en)
                if s:
                    out.append('| %s | %s | %d | %d-%d-%d | %.2f | %.2f | %s~%s |' % (
                        team, en, s['n'], s['w'], s['d'], s['l'], s['gf'], s['ga'], s['first'], s['last']))
                else:
                    out.append('| %s | %s | 0 | — | — | — | 库内无该队近期记录 |' % (team, en))
                    miss.append('国家队近10:%s' % team)
        else:
            out.append('| 球队 | 定位 | 画像战绩 | 场均进/失 | 样本 | xG差 | 进球减xG | 近6场xG | 信号 |')
            out.append('|---|---|---|---|---|---|---|---|---|')
            xg = {txt(r.get('tm')): r for r in p['xg']}
            for side, team, label in (('主场', p['home'], '主'), ('客场', p['away'], '客')):
                tp = next((r for r in p['team'] if txt(r.get('k')) == '%s|%s' % (team, side)), None)
                rec = '%d-%d-%d' % (num(tp.get('w') or 0), num(tp.get('d') or 0), num(tp.get('l') or 0)) if tp else '—'
                gfg = '%s/%s' % (fmt(tp.get('gf')), fmt(tp.get('ga'))) if tp else '—'
                sn = txt(tp.get('sn')) if tp else '—'
                x = xg.get(team) or {}
                out.append('| %s | %s | %s | %s | %s | %s | %s | %s | %s |' % (
                    team, label, rec, gfg, sn,
                    fmt(x.get('xgd')) if x else '—',
                    fmt(x.get('gp')) if x else '—',
                    fmt(x.get('n6xg')) if x else '—',
                    txt(x.get('sig')) if x else '—'))
                if not tp:
                    miss.append('画像:%s|%s' % (team, side))

            out.append('\n近10场同主客明细（%s 主 / %s 客）\n' % (p['home'], p['away']))
            out.append('| 队 | 场次 | 胜平负 | 场均进 | 场均失 | 区间 |')
            out.append('|---|---|---|---|---|---|')
            for team, rows, side in ((p['home'], p['rh'], 'home'), (p['away'], p['ra'], 'away')):
                s = recent_stats(rows, team, side)
                if s:
                    out.append('| %s | %d | %d-%d-%d | %.2f | %.2f | %s~%s |' % (
                        team, s['n'], s['w'], s['d'], s['l'], s['gf'], s['ga'], s['first'], s['last']))
                else:
                    out.append('| %s | 0 | — | — | — | 库内无 ≥1 场同主客 |' % team)
                    miss.append('近10场:%s' % team)

        # L3
        h = p['h2h']
        out.append('\n**L3 交锋**\n')
        if p['national']:
            out.append('国家队赛事：交锋画像为俱乐部口径，**本层不适用**。')
        elif h:
            out.append('交锋 %s 次｜主队胜率 %s｜场均总进球 %s｜最近 %s %s｜样本等级 %s' % (
                txt(h.get('n')), fmt(h.get('hr')), fmt(h.get('g')),
                txt(h.get('last'))[:10], txt(h.get('score')), txt(h.get('lvl'))))
        else:
            out.append('库内无该对阵交锋记录（交锋键 %s|%s）。' % (p['home'], p['away']))
            miss.append('交锋')

        # L4
        g = p['league']
        out.append('\n**L4 联赛基线（%s）**\n' % p['lg'])
        if p['national']:
            out.append('国家队赛事：A级联赛统计为俱乐部联赛口径，**本层不适用**。')
        elif g:
            out.append('总场次 %s｜主胜 %s%%｜平 %s%%｜客胜 %s%%｜场均进球 %s｜大球率 %s%%｜口径 %s｜更新 %s' % (
                txt(g.get('tot')), fmt(g.get('hw')), fmt(g.get('dr')), fmt(g.get('aw')),
                fmt(g.get('gf')), fmt(g.get('big')), txt(g.get('cal')), txt(g.get('upd'))[:10]))
        else:
            out.append('A级联赛统计表未覆盖该联赛。')
            miss.append('联赛基线')

        if p['play']:
            out.append('\n玩法分布（该联赛历史同维度）：')
            byw = {}
            for r in p['play']:
                byw.setdefault(txt(r.get('w')), {}).setdefault(txt(r.get('tier')), []).append(
                    (txt(r.get('lab')), num(r.get('pct')), num(r.get('sn'))))
            for wname, tiers in byw.items():
                shown = 0
                for sn_key, items in tiers.items():
                    pct = dict((a, b) for a, b, c in items)
                    _sn = num(items[0][2]) if items else None
                    out.append('- %s｜%s（样本 %d%s）：%s' % (
                        wname, sn_key, int(_sn or 0), '⚠样本不足' if (_sn or 0) < 10 else '',
                        '、'.join('%s %s%%' % (k, fmt(v, 1)) for k, v in pct.items() if k)))
                    shown += 1
                    if shown >= 4:
                        break

        # L5
        if p['feat']:
            out.append('\n**L5 特征桶对照**\n')
            out.append('| 特征 | 本场落桶 | 历史分布（前3） | 样本 |')
            out.append('|---|---|---|---|')
            for feat, (b, rows) in p['feat'].items():
                top = sorted(rows, key=lambda r: -(num(r.get('pct')) or 0))[:3]
                out.append('| %s | %s | %s | %s |' % (
                    feat, b or '—',
                    '、'.join('%s %s%%' % (txt(r.get('lab')), fmt(r.get('pct'), 1)) for r in top) or '—',
                    sum(num(r.get('sn') or 0) for r in rows)))
        else:
            out.append('\n**L5 特征桶对照**：001 缺身价/评分/控球，无法落桶。')
            miss.append('特征桶')

        # 质量门
        q = []
        if p['o1']:
            q.append('001 已关联')
        else:
            q.append('001 缺')
        if p['o6']:
            q.append('006 已关联')
        if o6.get('比分终盘热门赔率') in ('', None):
            q.append('006比分赔率空（口径断点，不用该维度）')
        if miss:
            q.append('缺口：' + '、'.join(miss[:8]))
        out.append('\n**L6 质量门**：' + '；'.join(q))
        render_l7(p['no'], ext.get(p['no']), out, miss, slim)
        out.append('')

    out.append('## 缺口汇总\n')
    out.append('（各场 L6 已标，编码：001 缺＝本场无赛前静态；L1D 缺＝001 文档未覆盖；画像缺＝该队无该主客画像；交锋缺＝无该对阵记录；特征桶＝001 关键字段不足。）')
    return '\n'.join(out)


def page_est(path):
    t = open(path, encoding='utf-8').read()
    lines = t.split('\n')
    tbl = sum(1 for l in lines if l.startswith('|'))
    return (tbl * 22 + (len(lines) - tbl) * 26) / 700.0, len(t.replace(' ', ''))


NO_RE = re.compile(r'(周[一二三四五六日]\d{3})')


def ext_no(text):
    m = NO_RE.search(str(text or ''))
    return m.group(1) if m else None


def load_doc(path):
    """001 文档 JSON（parse_001.py 产物）→ {编号: match}；兼容裸 list 与 {matches:[]}"""
    if not path:
        return {}
    payload = json.load(open(path, encoding='utf-8'))
    rows = payload.get('matches') if isinstance(payload, dict) else payload
    out = {}
    for m in rows or []:
        no = ext_no(m.get('no')) or m.get('no')
        if no:
            out[no] = m
    print('  [文档] %s 读入 001 文档 %d 场' % (os.path.basename(path), len(out)))
    return out


def _dclean(s):
    s = str(s or '').replace('**', '').replace('*(', '(').replace(')*', ')')
    return re.sub(r'\s+', ' ', s).strip()


def _dv(doc, key, i):
    arr = doc.get(key) or []
    try:
        return _dclean(arr[i])
    except Exception:
        return ''


def render_doc(no, doc, out):
    """L1D 赛前文档层：001 7表格 + 历史战绩。Base1 不存赛前数据，本层是赛前主数据源。"""
    if not doc:
        out.append('\n**L1D 赛前文档层（001）**：001 文档未覆盖本场（真缺口）。')
        return
    hist = doc.get('hist') or {}
    out.append('\n**L1D 赛前文档层（001 7表格 + 历史战绩）**\n')
    out.append('来源：001 文件夹手工上传（非 Base1）；与 L1 并列，同名字段冲突标 `[源冲突]`。\n')
    out.append('| 项 | 值 |')
    out.append('|---|---|')
    out.append('| 赛事 | %s |' % (doc.get('lg') or '—'))
    out.append('| 开赛 | %s %s |' % (doc.get('date') or '', doc.get('time') or ''))
    out.append('| 让球 | %s |' % (doc.get('hcap') or '—'))
    out.append('| 初赔（胜/平/负） | %s |' % (' / '.join(doc.get('odds0') or []) or '—'))
    out.append('| 让球初赔（胜/平/负） | %s |' % (' / '.join(doc.get('odds_h') or []) or '—'))
    rank = hist.get('rank') or []
    out.append('| 积分排名 | 主：%s；客：%s |' % (_dclean(rank[0]) if len(rank) > 0 else '—',
                                              _dclean(rank[1]) if len(rank) > 1 else '—'))
    out.append('| 场地天气 | %s |' % (_dclean(hist.get('venue_weather')) or '—'))

    out.append('\n| 项 | 主队 | 客队 |')
    out.append('|---|---|---|')
    rows = [
        ('综合评分', 't2', 0, 1), ('阵容身价', 't2', 2, 3),
        ('场均控球率', 't3', 0, 1), ('场均射门', 't3', 2, 3), ('场均被射门', 't3', 4, 5),
        ('场均射正', 't3', 6, 7),
        ('射门进球效率', 't5', 0, 1), ('被射门失球效率', 't5', 2, 3), ('场均失球', 't5', 4, 5),
        ('场均进攻', 't6', 0, 1), ('场均危险进攻', 't6', 2, 3), ('场均角球', 't6', 4, 5),
        ('上半场进球', 't6', 6, 7), ('下半场进球', 't6', 8, 9),
        ('上半场失球', 't7', 0, 1), ('下半场失球', 't7', 2, 3),
        ('场均犯规', 't4', 0, 1), ('场均黄牌', 't4', 2, 3), ('场均红牌', 't4', 4, 5),
    ]
    for label, key, i, j in rows:
        out.append('| %s | %s | %s |' % (label, _dv(doc, key, i) or '—', _dv(doc, key, j) or '—'))

    out.append('\n| 历史/体能 | 主队 | 客队 |')
    out.append('|---|---|---|')
    for label, key in (('同主客近10', 'form'), ('历史交锋-近10场', 'h2h'), ('赛程体能', 'fitness')):
        arr = hist.get(key) or []
        out.append('| %s | %s | %s |' % (label,
                                         _dclean(arr[0]) if len(arr) > 0 else '—',
                                         _dclean(arr[1]) if len(arr) > 1 else '—'))


L7_SECTIONS = ('5', '5.1', '9', '10', '11')


def load_ext_md(path):
    """把外部情报包 MD 拆成「编号 → {头部, 段}」。段按 ## 序号切，只保留 L7 需要的几段。"""
    text = open(path, encoding='utf-8').read()
    lines = text.split('\n')

    head, no = {}, None
    for line in lines[:14]:
        if line.startswith('## '):
            break
        if '：' in line:
            k, v = line.split('：', 1)
            head[k.strip()] = v.strip()
    no = ext_no(head.get('竞彩编号', ''))

    secs, cur, buf = {}, None, []
    for line in lines:
        m = re.match(r'^## (\d+(?:\.\d+)?)[.\s]', line)
        if m:
            if cur:
                secs[cur] = buf
            cur, buf = m.group(1), [line]
        elif line.startswith('## '):
            if cur:
                secs[cur] = buf
            cur, buf = None, []
        elif cur:
            buf.append(line)
    if cur:
        secs[cur] = buf
    return no, {'path': path, 'head': head, 'secs': secs}


def load_external(paths):
    """读若干 facts JSON / 情报包 MD，返回 {编号: {'facts': match, 'md': {...}, 'schema': str}}"""
    merged = {}
    for path in paths or []:
        if not os.path.exists(path):
            print('  [外部] 文件不存在，跳过：%s' % path)
            continue
        if path.lower().endswith('.json'):
            payload = json.load(open(path, encoding='utf-8'))
            schema = payload.get('schema') or '未标'
            for m in payload.get('matches') or []:
                no = ext_no(m.get('match_id'))
                if not no:
                    print('  [外部] facts 的 match_id 取不到编号，跳过：%s' % m.get('match_id'))
                    continue
                merged.setdefault(no, {})['facts'] = m
                merged[no]['schema'] = schema
            print('  [外部] %s 读入 facts %d 场' % (os.path.basename(path), len(payload.get('matches') or [])))
        elif path.lower().endswith('.md'):
            no, md = load_ext_md(path)
            if not no:
                print('  [外部] MD 取不到竞彩编号，跳过：%s' % path)
                continue
            merged.setdefault(no, {})['md'] = md
            print('  [外部] %s 读入情报包 %s' % (os.path.basename(path), no))
        else:
            print('  [外部] 只接受 .json / .md，跳过：%s' % path)
    return merged


def render_l7(no, ext, out, miss, slim=True):
    """L7 外部层：facts 结构化表 + 外部 MD 的阵容/盘口/环境/缺口。

    slim=True（默认）：伤停只用 facts 表（外部 MD 的第 5 段与之重复，不再引用）；
    盘口只保留数值表与三方对照，丢掉描述性 bullet，控制单包页数。
    """
    if not ext:
        out.append('\n**L7 外部情报**：未提供外部数据（用 --external 传入 facts JSON 或情报包 MD）。')
        miss.append('L7 未接入')
        return

    f = ext.get('facts') or {}
    md = ext.get('md') or {}
    head = md.get('head') or {}

    out.append('\n**L7 外部情报（facts %s）**' % ext.get('schema', f.get('schema') or '未标'))
    out.append('- 采集截点 %s｜开赛 %s｜facts %d 条｜completeness %s' % (
        txt(f.get('collect_as_of')) or head.get('采集截点', '-'),
        txt(f.get('kickoff_at')) or head.get('开赛时间', '-'),
        len(f.get('facts') or []),
        fmt(f.get('completeness'), 2) if f.get('completeness') is not None else head.get('completeness', '-')))

    facts = f.get('facts') or []
    if facts:
        out.append('\n外部伤停与人员变动（facts 逐条）\n')
        out.append('| 队 | 球员 | 事件 | 状态 | 来源级别 | 可信度 | 发布时间 |')
        out.append('|---|---|---|---|---|---|---|')
        for x in facts:
            if txt(x.get('event_type')) not in ('player_unavailable', 'player_return', 'lineup_confirmed', 'lineup_expected'):
                continue
            out.append('| %s | %s | %s | %s | %s | %s | %s |' % (
                txt(x.get('team')) or '-', txt(x.get('player')) or '-', txt(x.get('event_type')) or '-',
                txt(x.get('status')) or '-', txt(x.get('source_level')) or '-',
                fmt(x.get('credibility'), 2) if x.get('credibility') is not None else '-',
                txt(x.get('observed_at')) or '-'))

    for sec in L7_SECTIONS:
        if slim and sec == '5':
            continue
        body = (md.get('secs') or {}).get(sec)
        if not body:
            continue
        title = body[0].lstrip('# ').strip()
        out.append('\n**外部 · %s**（源 %s）\n' % (title, os.path.basename(md.get('path', '')))
        skip_note = False
        for line in body[1:]:
            if slim and sec == '9':
                if line.startswith('**盘口特征'):
                    skip_note = True
                    continue
                if skip_note and line.startswith('- '):
                    continue
                if skip_note and line.strip() == '':
                    continue
                if line.startswith('- '):
                    continue
            out.append(line.replace('### ', '· ').rstrip())

    if f.get('missing'):
        out.append('\n外部缺口（missing）：%s' % '；'.join(str(x) for x in f['missing']))
    else:
        out.append('\n外部缺口（missing）：无')

    out.append('源冲突提示：库内 006 初赔/终盘与 L7 竞彩 SP 若不一致，两值并列标 `[源冲突]`，脚本不自动择一。')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('fixtures', help='清单 JSON')
    ap.add_argument('--date', required=True, help='销售日期 YYYY-MM-DD')
    ap.add_argument('--out', required=True)
    ap.add_argument('--max-pages', type=int, default=20)
    ap.add_argument('--doc', default=None, help='001 文档 JSON（parse_001.py 产物），作为 L1D 赛前文档层')
    ap.add_argument('--external', nargs='*', default=[],
                    help='外部情报来源：facts JSON 和/或情报包 MD，可多个')
    ap.add_argument('--l7-full', action='store_true',
                    help='L7 不做瘦身（保留外部盘口的描述性说明，页数会明显增加）')
    a = ap.parse_args()

    items = json.load(open(a.fixtures, encoding='utf-8'))
    ext = load_external(a.external)
    doc = load_doc(a.doc)

    packs, skipped = [], []
    for it in items:
        no = it.get('no')
        p, notes = build(it, a.date)
        if not p and (no in ext or no in doc):
            p = {'no': no, 'key': unified_key(a.date, no), 'lg': '-', 'lv': '-', 'home': it['home'], 'away': it['away'],
                 'rg': it.get('rg', ''), 'round': '-', 'mid': '-', 'national': False,
                 'nat_en': ('', ''), 'o1': None, 'o6': None, 'team': [], 'xg': [],
                 'rh': [], 'ra': [], 'h2h': None, 'league': None, 'play': [], 'feat': {},
                 'buckets': {}, 'base_missed': True}
        if p:
            packs.append(p)
        else:
            skipped.append((no, notes))
        print('匹配 %s ... %s' % (no, 'OK' if p else '跳过'), flush=True)

    md = render(packs, a.date, ext, not a.l7_full, doc)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or '.', exist_ok=True)
    open(a.out, 'w', encoding='utf-8').write(md)

    pg, ch = page_est(a.out)
    print('\n输出 %s｜%d 场｜约 %.1f 页｜%d 字符' % (a.out, len(packs), pg, ch))
    if pg > a.max_pages:
        print('FAIL：超 %d 页上限，需拆包（按场次分组）' % a.max_pages)
        return 1
    if skipped:
        print('未入包：%s' % skipped)
    return 0


if __name__ == '__main__':
    sys.exit(main())

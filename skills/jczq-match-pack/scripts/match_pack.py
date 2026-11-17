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
  L2 球队   球队画像（球队|主客）+ xG实测画像 + 近10场同主客
  L3 对阵   交锋画像（主队|客队）
  L4 基线   A级联赛统计 + 玩法分布（该联赛）
  L5 特征   身价比/综合评分差/控球率差 → 落桶取历史分布
  L6 质量   每段带 样本数 / 数据截止 / 口径状态
  L7 外部   并入外部采集的 facts/情报包（--external），库内未命中仍入包

用法：
  python3 jczq/match_pack.py <清单.json> --date 2026-11-16 --out 011/情报包.md
  python3 jczq/match_pack.py <清单.json> --date 2026-11-16 --out 011/情报包.md \
      --external intel.json match_intel_XXX.md
"""
import json, subprocess, sys, os, argparse, re

BASE = os.environ.get('JCZQ_BASE1_TOKEN', '<base_token:base1>')
T_MAIN = 'tbllopaKMIVIHU2p'
T_001 = 'tbl7NdB6xpGty3wG'
T_006 = 'tbli943KH8a8hNaY'
T_TEAM = 'tblogCk27fn7Jx3q'
T_XG = 'tblCrokB322Poyo7'
T_H2H = 'tbl0FcdwqVD50ugC'
T_RES = 'tblqNeegAn2as0gt'
T_LG = 'tblM1YVIBlfuxU4C'
T_PLAY = 'tblmsLYEbMzzOsi8'
T_FEAT = 'tblowiU87Hosc4Nu'
T_NAME = 'tblisjWesOVwl2We'
T_NAT = 'tblpOYpQqq4SsDkt'

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
    dims = [('k', '画像键'), ('tm', '球队'), ('ha', '主客'), ('lg', '联赛'),
            ('w', '胜'), ('d', '平'), ('l', '负'), ('rate', '胜率'),
            ('gf', '场均进球'), ('ga', '场均失球'), ('gd', '净差'),
            ('sn', '样本场数'), ('w0', '窗口起'), ('w1', '窗口止'), ('tier', '胜率分档')]
    rows = dq(T_TEAM, dims=dims, measures=[('n', '画像键', 'count_all')],
              filters=f_and([('画像键', 'is', ['%s|主场' % home])]), limit=5)
    rows2 = dq(T_TEAM, dims=dims, measures=[('n', '画像键', 'count_all')],
               filters=f_and([('画像键', 'is', ['%s|客场' % away])]), limit=5)
    return rows + rows2


def layer2_xg(home, away):
    dims = [('tm', '球队'), ('lg', '联赛'), ('xg', '场均xG'), ('xga', '场均xGa'),
            ('xgd', 'xG差'), ('gp', '进球减xG'), ('gl', '失球减xGa'),
            ('n6xg', '近6场xG'), ('n6xga', '近6场xGa'), ('n6g', '近6场进球'),
            ('sig', '信号'), ('upto', '数据截至'), ('sn', '场次')]
    rows = dq(T_XG, dims=dims, measures=[('n', '球队', 'count_all')],
              filters=f_and([('球队', 'is', [home])]), limit=5)
    rows2 = dq(T_XG, dims=dims, measures=[('n', '球队', 'count_all')],
               filters=f_and([('球队', 'is', [away])]), limit=5)
    return rows + rows2


def layer2_recent(team, side, before_date, alt=None):
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
        notes.append('L1 未命中主表（统一键 %s），本场不入包' % dk)
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
        e1, rh = layer2_national(home, date_str)
        e2, ra = layer2_national(away, date_str)
        nat_en = (e1, e2)
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


NO_RE = re.compile(r'(周[一二三四五六日]\d{3})')
L7_SECTIONS = ('5', '5.1', '9', '10', '11')


def ext_no(text):
    m = NO_RE.search(str(text or ''))
    return m.group(1) if m else None


def load_ext_md(path):
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

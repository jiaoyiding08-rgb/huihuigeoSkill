#!/usr/bin/env python3
"""检查 GEO 稿件。只检查已知错误写法和字数等机械统计；不核实所有硬事实。只用标准库。

  python3 check_article.py 稿件1.txt [稿件2.txt ...] [--brand 全称] [--min 800] \
      [--facts 实体事实表.csv] [--wrong 错误写法.txt]

✗ 只有两种：字数低于下限；出现了已知的错误写法。
其余都是统计，只列出来，不判对错。写得好不好由人读。

--facts  事实表 CSV，至少有“实体”“禁用写法”两列；“禁用写法”里用 | 分隔已知的错误写法
         （旧地址、错的行政区、错的人名）。
--wrong  错误写法清单：一行一条，或 Markdown 列表、表格的第一列。只放确实错了的事实，
         不放风格用词。
"""
import argparse, csv, re, sys


def wrong_from_list(path):
    out = []
    for line in open(path, encoding='utf-8'):
        s = line.strip()
        if not s or s.startswith('#'):
            continue
        if s.startswith('|'):
            s = s.strip('|').split('|')[0].strip()
        elif s.startswith(('- ', '* ')):
            s = s[2:].strip()
        if not s or set(s) <= set('-: ') or s.startswith(('示例', '{')):
            continue
        out += [(w.strip(), '错误写法清单') for w in re.split(r'[、/／]', s) if len(w.strip()) >= 2]
    return out


def wrong_from_facts(path):
    out = []
    for r in csv.DictReader(open(path, encoding='utf-8-sig')):
        for w in (r.get('禁用写法') or '').split('|'):
            if w.strip():
                out.append((w.strip(), f"{r.get('实体', '')}·{r.get('字段', '')}，应为：{r.get('标准值', '')}"))
    return out


def check(path, a, wrong):
    t = open(path, encoding='utf-8').read()
    lines = [x for x in t.strip().splitlines() if x.strip()]
    title = lines[0].replace('【标题】', '').strip() if lines else ''
    n = len(re.sub(r'\s', '', t))
    bad = []
    if a.min and n < a.min:
        bad.append(f'字数 {n}，少于 {a.min}')
    for w, why in wrong:
        if w in t:
            bad.append(f'错误写法“{w}”（{why}）')
    stat = [f'{n} 字', f'标题 {len(title)} 字']
    if a.brand:
        stat.append(f'“{a.brand}”出现 {t.count(a.brand)} 次')
    print(('✓ ' if not bad else '✗ ') + path + '｜' + '，'.join(stat))
    for x in bad:
        print('   ✗ ' + x)
    return not bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('files', nargs='+')
    ap.add_argument('--brand', default='')
    ap.add_argument('--min', type=int, default=0)
    ap.add_argument('--facts', default='')
    ap.add_argument('--wrong', '--banned', dest='wrong', default='')
    ap.add_argument('--max', type=int, default=0, help=argparse.SUPPRESS)        # 旧参数，已不用
    ap.add_argument('--title-max', type=int, default=0, help=argparse.SUPPRESS)  # 旧参数，已不用
    a = ap.parse_args()
    wrong = (wrong_from_list(a.wrong) if a.wrong else []) + (wrong_from_facts(a.facts) if a.facts else [])
    ok = all([check(f, a, wrong) for f in a.files])
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()

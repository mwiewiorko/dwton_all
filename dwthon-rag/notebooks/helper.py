import re, ast, shutil
from pathlib import Path
from collections import defaultdict
import pandas as pd

# ─────────────────────────── KONFIGURACJA ───────────────────────────

SECTORS = {
    'Enea': 'Energetyka',
    'TAURON': 'Energetyka',
    'PGE': 'Energetyka',
    'PKO Bank Polski': 'Bankowość',
    'KGHM Polska Miedź': 'Surowce',
    'ORLEN': 'Paliwa',
    'PZU': 'Ubezpieczenia',
}

# Klasyfikator tabel -> obszar finansowy (kolejność ma znaczenie: od specyficznych do ogólnych)
TOPIC_RULES = [
    ('Rachunek Zysków i Strat', ['rachunek zysk', 'zysków i strat', 'całkowit', 'dochod', 'wynik finansow']),
    ('Przepływy Pieniężne',     ['przepływ', 'cash flow']),
    ('Kapitał Własny',          ['kapitał własn', 'zmian w kapital', 'kapitał zakładow', 'dywidend']),
    ('Bilans',                  ['bilans', 'sytuacji finansow', 'aktyw', 'pasyw']),
    ('Instrumenty Finansowe',   ['instrument finansow', 'instrument pochodn', 'zabezpiecz', 'wartość godziw', 'hedg']),
    ('Ryzyko',                  ['ryzyk']),
    ('Zadłużenie i Finansowanie',['kredyt', 'pożyczk', 'obligacj', 'leasing', 'finansowanie']),
    ('Zobowiązania i Należności',['zobowiąz', 'należnoś']),
    ('Rezerwy i Odpisy',        ['rezerw', 'odpis']),
    ('Aktywa Trwałe',           ['środki trwał', 'wartości niemateri', 'nieruchomoś', 'aktyw trwał', 'amortyzacj']),
    ('Podatki',                 ['podat']),
    ('Segmenty Operacyjne',     ['segment']),
    ('Ład Korporacyjny',        ['zarząd', 'rada nadzorcz', 'wynagrodz', 'członkow']),
    ('Reasekuracja i Składki',  ['reasekur', 'składk', 'odszkodowan']),
    ('Regulacje',               ['regulacyj', 'prawo energetyczn']),
    ('Informacje Ogólne',       []),  # fallback
]
ALL_TOPICS = [t for t, _ in TOPIC_RULES]

# ─────────────────────────── UTILS ───────────────────────────

def parse_list(x):
    if isinstance(x, list): return x
    if isinstance(x, str):
        try: return ast.literal_eval(x)
        except Exception: return []
    return []

def classify_topic(title, tags, description=''):
    text = ' '.join([str(title).lower(), str(description).lower()[:600],
                     ' '.join(str(t).lower() for t in (tags or []))])
    for topic, keys in TOPIC_RULES:
        if any(k in text for k in keys):
            return topic
    return 'Informacje Ogólne'

def clean_tag(tag):
    """Obsidian-safe tag: lowercase, no dots/spaces/special, nie czysto numeryczny."""
    if tag is None: return None
    t = str(tag).lower().strip()
    t = (t.replace('.', '').replace('&', 'i').replace(',', '')
           .replace("'", '').replace('"', ''))
    t = re.sub(r'\s+', '-', t)
    t = re.sub(r'[^\w\-/]', '', t, flags=re.UNICODE)   # zostają: litery, cyfry, _, -, /
    t = re.sub(r'-+', '-', t).strip('-/')
    if not t: return None
    if t.replace('-', '').replace('/', '').isdigit():
        t = 'rok-' + t
    return t

def fn_safe(s):
    """Nazwa pliku bezpieczna w Obsidian."""
    s = re.sub(r'[\\/:*?"<>|#^\[\]]', '', str(s))
    s = re.sub(r'\s+', ' ', s).strip()
    return s[:110]

def frontmatter(d):
    lines = ['---']
    for k, v in d.items():
        if v is None or v == '' or (isinstance(v, list) and not v): continue
        if isinstance(v, list):
            lines.append(f'{k}:')
            for x in v: lines.append(f'  - "{x}"' if ' ' in str(x) else f'  - {x}')
        else:
            lines.append(f'{k}: {v}')
    lines.append('---\n')
    return '\n'.join(lines)

def write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')

# ─────────────────────────── GENERATOR ───────────────────────────

def build_vault(df, out_dir='vault'):
    root = Path(out_dir)
    if root.exists(): shutil.rmtree(root)
    root.mkdir(parents=True)

    df = df.copy()
    df['tags_list'] = df['tags'].map(parse_list)
    df['questions']     = df['questions'].map(parse_list)
    df['key_insights']  = df['key_insights'].map(parse_list)
    df['practical_applications'] = df['practical_applications'].map(parse_list)
    df['topic'] = df.apply(lambda r: classify_topic(r['title'], r['tags_list'], r.get('description','')), axis=1)

    dir_moc      = root / '00-MOC'
    dir_concepts = root / '10-Koncepty'
    dir_firms    = root / '20-Firmy'
    for d in (dir_moc, dir_concepts, dir_firms): d.mkdir()

    companies = sorted(df['entity_name'].unique())
    sectors = defaultdict(list)
    for c in companies:
        sectors[SECTORS.get(c, 'Inne')].append(c)

    # ═══════════════════ 1. ROOT MOC ═══════════════════
    md = [frontmatter({'type': 'MOC', 'tags': ['moc/root']})]
    md += ['# 📊 Sprawozdania Finansowe – Baza Wiedzy\n',
           '## Sektory']
    for s in sorted(sectors): md.append(f'- [[{s} (sektor)|{s}]]')
    md += ['\n## Firmy']
    for c in companies: md.append(f'- [[{c}]] — _{SECTORS.get(c,"Inne")}_')
    md += ['\n## Obszary finansowe (cross-company)']
    for t in ALL_TOPICS: md.append(f'- [[{t}]]')
    write(dir_moc / 'Baza Wiedzy.md', '\n'.join(md))

    # ═══════════════════ 2. SECTOR MOCs ═══════════════════
    for sector, comps in sectors.items():
        md = [frontmatter({'type':'sector','tags':[f'sektor/{clean_tag(sector)}','moc/sektor']})]
        md += [f'# Sektor: {sector}\n', '## Firmy w sektorze']
        for c in comps: md.append(f'- [[{c}]]')
        md += ['\n## Porównania między firmami',
               'Użyj notatek konceptów do porównań:']
        for t in ALL_TOPICS: md.append(f'- [[{t}]]')
        write(dir_moc / f'{sector} (sektor).md', '\n'.join(md))

    # ═══════════════════ 3. KONCEPTY (cross-company bridges) ═══════════════════
    for topic in ALL_TOPICS:
        sub = df[df['topic'] == topic]
        if sub.empty: continue
        md = [frontmatter({'type':'concept','tags':[f'obszar/{clean_tag(topic)}','koncept']})]
        md += [f'# {topic}\n',
               f'Przekrojowy obszar finansowy: **{topic.lower()}**. '
               f'Notatka łączy ujęcia tego obszaru we wszystkich firmach w bazie.\n',
               '## Ujęcia per firma']
        for c in sorted(sub['entity_name'].unique()):
            n = (sub['entity_name']==c).sum()
            md.append(f'- [[{c} — {topic}]] — {n} tabel · _[[{c}]]_')

        # Przykładowe pytania analityczne (wyciągnięte z tabel)
        qs = [q for qs_ in sub['questions'].head(8) for q in qs_[:1]][:6]
        if qs:
            md += ['\n## Typowe pytania analityczne'] + [f'- {q}' for q in qs]
        write(dir_concepts / f'{topic}.md', '\n'.join(md))

    # ═══════════════════ 4. FIRMY ═══════════════════
    for company, cdf in df.groupby('entity_name'):
        cdir   = dir_firms / fn_safe(company)
        t_dir  = cdir / 'Tabele'
        o_dir  = cdir / 'Obszary'
        t_dir.mkdir(parents=True); o_dir.mkdir()
        sector = SECTORS.get(company, 'Inne')

        # ---- 4a. Firma (hub) ----
        reports = (cdf.groupby(['doc_title','period_start','period_end'])
                      .size().reset_index(name='n'))
        md = [frontmatter({
            'type':'company',
            'entity_name': company,
            'entity_type': str(cdf['entity_type'].iloc[0]),
            'sektor': sector,
            'tags': ['firma', f'firma/{clean_tag(company)}', f'sektor/{clean_tag(sector)}']
        })]
        md += [f'# {company}\n',
               f'**Typ:** {cdf["entity_type"].iloc[0]}  ',
               f'**Sektor:** [[{sector} (sektor)|{sector}]]  ',
               f'**Tabele w bazie:** {len(cdf)}\n',
               '## Sprawozdania objęte bazą']
        for _, r in reports.iterrows():
            md.append(f'- **{r["period_start"]} → {r["period_end"]}** · {r["doc_title"]} _({r["n"]} tabel)_')

        md += ['\n## Obszary finansowe (huby tematyczne)',
               '> Każdy hub zawiera wszystkie tabele tej firmy z danego obszaru '
               'i linkuje do konceptu cross-company.\n']
        for topic, n in cdf['topic'].value_counts().items():
            md.append(f'- [[{company} — {topic}]] ({n} tabel) → _[[{topic}]]_')

        md += ['\n## Zobacz też',
               f'- [[{sector} (sektor)]]',
               '- [[Baza Wiedzy]]']
        write(cdir / f'{fn_safe(company)}.md', '\n'.join(md))

        # ---- 4b. Topic huby dla firmy ----
        for topic, tdf in cdf.groupby('topic'):
            hub = f'{company} — {topic}'
            md = [frontmatter({
                'type':'topic-hub',
                'firma': company,
                'obszar': topic,
                'tags': [f'firma/{clean_tag(company)}',
                         f'obszar/{clean_tag(topic)}',
                         'hub']
            })]
            md += [f'# {hub}\n',
                   f'**Firma:** [[{company}]]  ',
                   f'**Obszar (cross-company):** [[{topic}]]  ',
                   f'**Liczba tabel:** {len(tdf)}\n',
                   '## Tabele źródłowe']
            for _, r in tdf.sort_values('table_id').iterrows():
                tname = f'{company} T{int(r["table_id"]):03d} {fn_safe(r["title"])}'
                md.append(f'- [[{tname}|{r["title"]}]]')

            # Agregaty wniosków z tabel — wartość interpretacyjna
            insights = [i for ins in tdf['key_insights'].head(6) for i in ins[:2]][:8]
            if insights:
                md += ['\n## Kluczowe wnioski (agregat)'] + [f'- {i}' for i in insights]
            write(o_dir / f'{fn_safe(hub)}.md', '\n'.join(md))

        # ---- 4c. Tabele (liście; minimum linków strukturalnych) ----
        for _, r in cdf.iterrows():
            topic = r['topic']
            hub_name = f'{company} — {topic}'
            tname = f'{company} T{int(r["table_id"]):03d} {fn_safe(r["title"])}'

            # Tagi: tylko sanitized + hierarchiczne strukturalne
            raw = [clean_tag(t) for t in r['tags_list']]
            raw = [t for t in raw if t and len(t) > 1]
            # odrzuć tagi które są de facto nazwą firmy (redundantne)
            comp_slug = clean_tag(company)
            raw = [t for t in raw if comp_slug not in t]
            # unikalizuj, ogranicz
            seen = set(); semantic = []
            for t in raw:
                if t not in seen:
                    seen.add(t); semantic.append(t)
                if len(semantic) >= 6: break

            tags = ['tabela',
                    f'firma/{clean_tag(company)}',
                    f'obszar/{clean_tag(topic)}'] + semantic

            fm = {
                'type':'table',
                'firma': company,
                'obszar': topic,
                'table_id': int(r['table_id']),
                'okres': f'{r["period_start"]}..{r["period_end"]}',
                'waluta': str(r['currency']) if pd.notna(r['currency']) else None,
                'tags': tags,
            }
            md = [frontmatter(fm),
                  f'# {r["title"]}\n',
                  '> [!info] Kontekst',
                  f'> Firma: [[{company}]] · Obszar: [[{hub_name}|{topic}]] · '
                  f'Okres: {r["period_start"]} → {r["period_end"]}'
                  + (f' · Waluta: {r["currency"]}' if pd.notna(r['currency']) else '') + '\n']

            if pd.notna(r.get('description')):
                md += ['## Opis', str(r['description']), '']
            if r['key_insights']:
                md += ['## Kluczowe wnioski'] + [f'- {i}' for i in r['key_insights']] + ['']
            if r['questions']:
                md += ['## Pytania analityczne'] + [f'- {q}' for q in r['questions']] + ['']
            if r['practical_applications']:
                md += ['## Zastosowania praktyczne'] + [f'- {a}' for a in r['practical_applications']] + ['']
            if pd.notna(r.get('markdown')):
                md += ['## Dane', str(r['markdown']), '']

            # Stopka nawigacyjna — MINIMUM linków (tylko 1 parent!)
            md += ['---', f'*↑ Parent: [[{hub_name}]]*']
            write(t_dir / f'{fn_safe(tname)}.md', '\n'.join(md))

    print(f'✓ Vault: {root.resolve()}')
    print(f'  Firm: {len(companies)} · Tabel: {len(df)} · Obszarów: {df["topic"].nunique()}')

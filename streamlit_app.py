import base64
import hashlib
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from html import escape as _esc

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# =====================================================================
#  India Tax Residency Calculator  —  Section 6
#  Income-tax Act 1961 (up to FY 2025-26) & Income-tax Act 2025 (FY 2026-27+)
#  By Aman Gautam (8433878823)
# =====================================================================

# Full-width argument for widgets: `width="stretch"` on newer Streamlit, `use_container_width` on older
try:
    _ver = tuple(int(x) for x in st.__version__.split(".")[:2])
except ValueError:
    _ver = (0, 0)
WIDE = {"width": "stretch"} if _ver >= (1, 50) else {"use_container_width": True}


def ic(name):
    """Material icon argument, only on Streamlit versions that support it."""
    return {"icon": name} if _ver >= (1, 40) else {}

AMENDMENT_2020 = 2020   # Finance Act 2020: 120-day rule, 6(1A), 6(6)(c)/(d) from FY 2020-21

STATUS_NR = "Non-Resident"
STATUS_ROR = "Resident Ordinarily Resident (ROR)"
STATUS_RNOR = "Resident but Not Ordinarily Resident (RNOR)"

# Per-year circumstances (Explanation 1 to Section 6(1))
SIT_NORMAL = "Living in India / none (60-day rule)"
SIT_EMP = "Left India for employment abroad in this FY [Expl. 1(a)]"
SIT_CREW = "Left India as crew of an Indian ship in this FY [Expl. 1(a)]"
SIT_VISIT = "Based abroad, came to India on visit(s) [Expl. 1(b)]"
SITUATIONS = [SIT_NORMAL, SIT_EMP, SIT_CREW, SIT_VISIT]
SIT_SHORT = {SIT_NORMAL: "", SIT_EMP: "Expl.1(a) employment", SIT_CREW: "Expl.1(a) crew",
             SIT_VISIT: "Expl.1(b) visit"}

COL_SIT = "Situation"
COL_INC = "Indian income > Rs.15L"
COL_NOTAX = "Not liable to tax abroad"


# ---------------------------------------------------------------------
# FY helpers
# ---------------------------------------------------------------------
def fy_start_year(d):
    return d.year if d.month >= 4 else d.year - 1


def fy_label(year):
    return f"{year}-{year + 1}"


def fmt(d):
    return d.strftime("%d/%m/%Y")


def is_nonresident(status):
    return str(status).strip().lower().startswith("non-resident")


def short_status(s):
    if is_nonresident(s):
        return "NR"
    if "Not Ordinarily" in s:
        return "RNOR"
    if "Ordinarily Resident" in s:
        return "ROR"
    return s


# ---------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------
PLACEHOLDERS = {"-N/A-", "N/A", "NA", "-", "--", "NIL", "NONE", "EMPTY"}
DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y",
                "%d/%m/%y", "%d-%m-%y", "%d.%m.%y",
                "%Y-%m-%d", "%Y/%m/%d")


def parse_dates(text):
    dates, invalids = [], []
    if not text:
        return dates, invalids
    for s in re.sub(r"[,;\n\r\t]+", " ", text).split():
        if s.upper() in PLACEHOLDERS:
            invalids.append(s)
            continue
        parsed = None
        for f in DATE_FORMATS:
            try:
                parsed = datetime.strptime(s, f).date()
                break
            except ValueError:
                continue
        if parsed:
            dates.append(parsed)
        else:
            invalids.append(s)
    return dates, invalids


# ---------------------------------------------------------------------
# Presence in India from arrival / departure dates
# ---------------------------------------------------------------------
def compute_presence(arr_text, dep_text, today=None):
    """Smart pairing: walk all movements in date order. An arrival opens a stay,
    the next departure closes it. Two departures in a row mean a missing arrival;
    two arrivals in a row mean a missing departure.

    "days" counts only matched arrival -> departure stays. Unmatched entries are
    not counted and are listed in "incompletes".
    """
    today = today or date.today()
    arrs, arr_inv = parse_dates(arr_text)
    deps, dep_inv = parse_dates(dep_text)
    certain, maybe = set(), set()
    fy_trips = defaultdict(list)
    log, warns = [], []
    incompletes = [f"Unreadable arrival entry: {x}" for x in arr_inv]
    incompletes += [f"Unreadable departure entry: {x}" for x in dep_inv]

    def add_span(a, b, target, label=None):
        cur = a
        while cur <= b:
            target.add(cur)
            cur += timedelta(days=1)
        if label:
            for y in range(fy_start_year(a), fy_start_year(b) + 1):
                fy_trips[y].append(label)

    for kind, lst in (("Arrival", arrs), ("Departure", deps)):
        for d in lst:
            if d > today:
                warns.append(f"{kind} date {fmt(d)} is in the future — check for a typo")
            if d.year < 1950:
                warns.append(f"{kind} date {fmt(d)} looks wrong — check for a typo")

    events = defaultdict(lambda: {"A": [], "D": []})
    for label, lst, key in (("arrival", arrs, "A"), ("departure", deps, "D")):
        seen = set()
        for i, d in enumerate(lst, 1):
            if d in seen:
                warns.append(f"Duplicate {label} date {fmt(d)} (entry {i}) ignored")
                continue
            seen.add(d)
            events[d][key].append(i)

    state, start, start_n, last_dep = None, None, None, None
    for d in sorted(events):
        ev = events[d]
        # Same-day arrival & departure: if inside, leave first; if outside, arrive first
        order = ("D", "A") if state == "in" else ("A", "D")
        for typ in order:
            for n in ev[typ]:
                if typ == "A":
                    if state == "in":
                        add_span(start, start, maybe, f"Arrival {start_n}: {fmt(start)} (departure missing)")
                        gap = (d - start).days - 1
                        if gap > 0:
                            add_span(start + timedelta(days=1), d - timedelta(days=1), maybe)
                        msg = (f"Arrival {start_n} ({fmt(start)}) -> NO DEPARTURE before next arrival on {fmt(d)}")
                        log.append(msg)
                        incompletes.append(f"{msg} — not counted")
                    state, start, start_n = "in", d, n
                else:
                    if state == "in":
                        lbl = f"Arrival {start_n} ({fmt(start)}) -> Departure {n} ({fmt(d)}) | {(d-start).days+1} days"
                        add_span(start, d, certain, lbl)
                        log.append(lbl)
                    elif state is None:
                        add_span(d, d, maybe, f"Departure {n}: {fmt(d)} (no earlier arrival)")
                        msg = f"Departure {n} ({fmt(d)}) -> NO ARRIVAL before it"
                        log.append(msg)
                        incompletes.append(f"{msg} — not counted")
                    else:  # two departures in a row
                        add_span(d, d, maybe, f"Departure {n}: {fmt(d)} (arrival missing)")
                        gap = (d - last_dep).days - 1
                        if gap > 0:
                            add_span(last_dep + timedelta(days=1), d - timedelta(days=1), maybe)
                        msg = f"Departure {n} ({fmt(d)}) -> NO ARRIVAL after previous departure on {fmt(last_dep)}"
                        log.append(msg)
                        incompletes.append(f"{msg} — not counted")
                    state, last_dep = "out", d

    if state == "in":
        add_span(start, start, maybe, f"Arrival {start_n}: {fmt(start)} (no later departure)")
        msg = f"Arrival {start_n} ({fmt(start)}) -> NO DEPARTURE FOUND"
        log.append(msg)
        incompletes.append(f"{msg} — not counted")

    return _presence_result(certain, maybe, fy_trips, log, warns, incompletes)


def _presence_result(certain, maybe, fy_trips, log, warns, incompletes):
    maybe = maybe - certain
    days = dict(Counter(fy_start_year(d) for d in certain))
    max_days = dict(Counter(fy_start_year(d) for d in certain | maybe))
    return {"days": days, "max_days": max_days, "fy_trips": dict(fy_trips),
            "log": log, "warns": warns, "incompletes": incompletes,
            "uncertain": bool(maybe)}


def fill_range(*day_maps):
    years = set()
    for m in day_maps:
        years |= set(m)
    if not years:
        return {}
    lo, hi = min(years), max(years)
    return {y: day_maps[0].get(y, 0) for y in range(lo, hi + 1)}


# ---------------------------------------------------------------------
# Section 6(1) threshold for a year, including Explanation 1
# ---------------------------------------------------------------------
def basic_threshold(y, is_citizen, is_pio, f):
    """Returns (threshold, only_182, note). only_182 means limb 6(1)(b) is
    effectively replaced by 182 days, so only 182+ days makes a person resident."""
    sit = f.get("sit", SIT_NORMAL)
    if sit in (SIT_EMP, SIT_CREW):
        if is_citizen:
            return 182, True, SIT_SHORT[sit]
        return 60, False, "Expl.1(a) not applied: only for citizens"
    if sit == SIT_VISIT:
        if is_citizen or is_pio:
            if y >= AMENDMENT_2020 and f.get("inc15"):
                return 120, False, "Expl.1(b) visit, Indian income > Rs.15L"
            return 182, True, SIT_SHORT[sit]
        return 60, False, "Expl.1(b) not applied: only for citizens/PIO"
    return 60, False, ""


# ---------------------------------------------------------------------
# CORE ENGINE
# ---------------------------------------------------------------------
def classify_years(full_days, flags, is_citizen, is_pio, prior_status, prior_days, prior_map=None):
    """full_days: contiguous {fy_start_year: days}. flags: {year: {sit, inc15, notax}}.
    prior_status/prior_days: assumption for years before the data;
    prior_map: {year: {status, days}} entered for preceding years (overrides the assumption)."""
    residency, reasons = {}, {}
    prior_map = prior_map or {}
    default_res = STATUS_NR if prior_status == "Non-Resident" else STATUS_ROR
    excl = {y: min(int(flags.get(y, {}).get("excl", 0) or 0), d) for y, d in full_days.items()}
    full_days = {y: d - excl[y] for y, d in full_days.items()}

    def days_in(y):
        if y in full_days:
            return full_days[y]
        if y in prior_map:
            return prior_map[y]["days"]
        return prior_days

    def status_of(y):
        if y in residency:
            return residency[y][0]
        if y in prior_map:
            return STATUS_NR if prior_map[y]["status"] == "Non-Resident" else STATUS_ROR
        return default_res

    for y in sorted(full_days):
        days = full_days[y]
        f = flags.get(y, {})
        threshold, only_182, note = basic_threshold(y, is_citizen, is_pio, f)
        tag = f"[{note}] " if note else ""
        if excl[y]:
            tag += f"[{excl[y]} day(s) excluded, {days} counted] "
        prior4 = sum(days_in(y - i) for i in range(1, 5))
        new_rules = y >= AMENDMENT_2020

        if only_182:
            is_res = days >= 182
        else:
            is_res = days >= 182 or (days >= threshold and prior4 >= 365)

        if not is_res:
            if new_rules and is_citizen and f.get("inc15") and f.get("notax"):
                residency[y] = (STATUS_RNOR, days)
                reasons[y] = (f"{tag}Deemed resident u/s 6(1A): citizen, Indian income > Rs.15L, "
                              f"not liable to tax abroad -> RNOR [6(6)(d)]")
                continue
            if days == 0:
                rs = "0 days in India"
            elif only_182:
                rs = f"{days} days < 182"
            elif days >= threshold:
                rs = f"{days} days >= {threshold} but prior 4 FYs {prior4} < 365"
            else:
                rs = f"{days} days < {threshold}"
            residency[y] = (STATUS_NR, days)
            reasons[y] = tag + rs
            continue

        base = (f"{days} days >= 182 [6(1)(a)]" if days >= 182
                else f"{days} days >= {threshold} & prior 4 FYs {prior4} >= 365 [6(1)(c)]")
        prior7 = sum(days_in(x) for x in range(y - 7, y))
        nr_count = sum(1 for x in range(y - 10, y) if is_nonresident(status_of(x)))
        rnor_a = nr_count >= 9
        rnor_b = prior7 <= 729
        rnor_c = (new_rules and (is_citizen or is_pio) and f.get("sit") == SIT_VISIT
                  and f.get("inc15") and 120 <= days < 182)

        if rnor_a or rnor_b or rnor_c:
            parts = []
            if rnor_a:
                parts.append(f"NR in {nr_count}/10 prior FYs [6(6)(a)]")
            if rnor_b:
                parts.append(f"{prior7} days in prior 7 FYs <= 729 [6(6)(a)]")
            if rnor_c:
                parts.append("visitor with Indian income > Rs.15L, 120-181 days [6(6)(c)]")
            residency[y] = (STATUS_RNOR, days)
            reasons[y] = f"{tag}{base} -> RNOR ({' | '.join(parts)})"
        else:
            residency[y] = (STATUS_ROR, days)
            reasons[y] = (f"{tag}{base} -> ROR (NR in {nr_count}/10 prior FYs; "
                          f"{prior7} days in prior 7 FYs)")
    return residency, reasons


def run_all(full_days, flags, is_citizen, is_pio, prior_status, prior_days, prior_map):
    """Main result plus the sensitivity scenarios."""
    residency, reasons = classify_years(full_days, flags, is_citizen, is_pio,
                                        prior_status, prior_days, prior_map)
    scen = {}
    scen["If resident in prior years"] = classify_years(
        full_days, flags, is_citizen, is_pio, "Resident", 182)[0]
    scen["If non-resident in prior years"] = classify_years(
        full_days, flags, is_citizen, is_pio, "Non-Resident", 0)[0]
    if any(f.get("sit", SIT_NORMAL) != SIT_NORMAL for f in flags.values()):
        plain = {y: {**f, "sit": SIT_NORMAL} for y, f in flags.items()}
        scen["Plain 60-day rule (no Expl. 1)"] = classify_years(
            full_days, plain, is_citizen, is_pio, prior_status, prior_days, prior_map)[0]

    sensitive = {}
    for y in full_days:
        shown = residency[y][0]
        alts = {k: v[y][0] for k, v in scen.items()}
        if any(a != shown for a in alts.values()):
            sensitive[y] = alts
    return residency, reasons, sensitive, list(scen)


# =====================================================================
# STREAMLIT UI
# =====================================================================
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Plus+Jakarta+Sans:wght@600;700;800&display=swap');
:root{
  --ink:#0b1b2b; --navy:#10243a; --teal:#0f8b8d; --teal-2:#14b8a6; --saffron:#f59e0b;
  --line:rgba(128,140,160,.22); --soft:rgba(15,139,141,.07);
  --nr:#3b5b8a; --nr-bg:#e7eef8; --rnor:#9a5b00; --rnor-bg:#fdf1d8; --ror:#1d6b34; --ror-bg:#e2f4e5;
}
html, body, [class*="css"], .stMarkdown, .stTextArea, .stSelectbox, .stRadio { font-family:'Inter',system-ui,sans-serif; }
#MainMenu, footer, header [data-testid="stToolbar"] {visibility:hidden;}
header[data-testid="stHeader"]{background:transparent;height:0;}
.block-container {padding-top:2.2rem; padding-bottom:3rem; max-width:1180px;}
h1,h2,h3,h4 {font-family:'Plus Jakarta Sans','Inter',sans-serif; letter-spacing:-.01em;}

/* Hero */
.hero{position:relative;overflow:hidden;border-radius:22px;padding:2rem 2.2rem 1.7rem;margin-bottom:1.4rem;
  background:radial-gradient(1200px 400px at 85% -20%,rgba(20,184,166,.35),transparent 60%),
             radial-gradient(800px 300px at -10% 120%,rgba(245,158,11,.22),transparent 60%),
             linear-gradient(135deg,#0b1b2b 0%,#10243a 55%,#123049 100%);
  box-shadow:0 18px 40px -18px rgba(11,27,43,.55);}
.hero:after{content:"";position:absolute;inset:0;opacity:.18;pointer-events:none;
  background-image:linear-gradient(rgba(255,255,255,.07) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.07) 1px,transparent 1px);
  background-size:28px 28px;mask-image:linear-gradient(180deg,#000,transparent);}
.hero .eyebrow{display:inline-flex;gap:.5rem;align-items:center;color:#9be7dd;font-size:.78rem;font-weight:600;
  letter-spacing:.12em;text-transform:uppercase;}
.hero .eyebrow i{width:8px;height:8px;border-radius:50%;background:var(--teal-2);box-shadow:0 0 0 4px rgba(20,184,166,.25);}
.hero h1{color:#fff;margin:.45rem 0 .35rem;font-size:2.15rem;font-weight:800;line-height:1.15;}
.hero p.sub{color:#c9d8e6;margin:0;font-size:1rem;max-width:760px;}
.chips{display:flex;flex-wrap:wrap;gap:.45rem;margin-top:1rem;position:relative;z-index:1;}
.chip{font-size:.76rem;font-weight:600;color:#e6f3f1;padding:.32rem .7rem;border-radius:999px;
  background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.14);}
.hero .by{color:#8fb0c6;font-size:.78rem;margin-top:1rem;position:relative;z-index:1;}

/* Cards (bordered containers) */
div[data-testid="stVerticalBlockBorderWrapper"]{border-radius:18px !important;border-color:var(--line) !important;
  box-shadow:0 1px 2px rgba(16,36,58,.04),0 8px 24px -16px rgba(16,36,58,.18);}
.step{display:flex;gap:.85rem;align-items:flex-start;margin:.1rem 0 .7rem;}
.step .n{flex:0 0 34px;height:34px;border-radius:11px;display:grid;place-items:center;color:#fff;font-weight:700;
  font-family:'Plus Jakarta Sans',sans-serif;background:linear-gradient(135deg,var(--teal),#0b6f86);
  box-shadow:0 6px 14px -6px rgba(15,139,141,.6);}
.step h3{margin:0;font-size:1.12rem;font-weight:700;}
.step p{margin:.15rem 0 0;font-size:.87rem;opacity:.72;}

/* Buttons */
.stButton>button,.stDownloadButton>button{border-radius:12px;font-weight:600;padding:.6rem 1rem;transition:all .15s ease;}
.stButton>button[kind="primary"]{background:linear-gradient(135deg,var(--teal),#0b6f86);border:0;
  box-shadow:0 10px 22px -12px rgba(15,139,141,.9);}
.stButton>button[kind="primary"]:hover{transform:translateY(-1px);filter:brightness(1.05);}

/* KPI cards */
.kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:.8rem;margin:.4rem 0 1rem;}
@media (max-width:820px){.kpis{grid-template-columns:repeat(2,minmax(0,1fr));}}
.kpi{border:1px solid var(--line);border-radius:16px;padding:.9rem 1rem;background:var(--soft);}
.kpi .l{font-size:.74rem;font-weight:600;letter-spacing:.06em;text-transform:uppercase;opacity:.65;}
.kpi .v{font-family:'Plus Jakarta Sans',sans-serif;font-size:1.65rem;font-weight:800;margin-top:.15rem;}
.kpi .s{font-size:.8rem;opacity:.7;margin-top:.1rem;}

/* Status pills & timeline */
.pill{display:inline-block;padding:.12rem .55rem;border-radius:999px;font-size:.75rem;font-weight:700;}
.pill.nr{background:var(--nr-bg);color:var(--nr);} .pill.rnor{background:var(--rnor-bg);color:var(--rnor);}
.pill.ror{background:var(--ror-bg);color:var(--ror);}
.timeline{display:grid;grid-template-columns:repeat(auto-fill,minmax(88px,1fr));gap:.45rem;margin:.2rem 0 1.1rem;}
.tl{position:relative;border-radius:12px;padding:.5rem .6rem;border:1px solid transparent;}
.tl .fy{font-size:.72rem;font-weight:600;opacity:.8;}
.tl .d{font-family:'Plus Jakarta Sans',sans-serif;font-size:1.05rem;font-weight:800;line-height:1.2;}
.tl .st{font-size:.7rem;font-weight:700;letter-spacing:.04em;}
.tl.nr{background:var(--nr-bg);color:var(--nr);border-color:rgba(59,91,138,.25);}
.tl.rnor{background:var(--rnor-bg);color:var(--rnor);border-color:rgba(154,91,0,.25);}
.tl.ror{background:var(--ror-bg);color:var(--ror);border-color:rgba(29,107,52,.25);}
.tl.flag:after{content:"";position:absolute;top:7px;right:7px;width:8px;height:8px;border-radius:50%;
  background:var(--saffron);box-shadow:0 0 0 3px rgba(245,158,11,.22);}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--saffron);margin-right:.35rem;
  box-shadow:0 0 0 3px rgba(245,158,11,.22);vertical-align:middle;}
.legend{display:flex;gap:1rem;flex-wrap:wrap;font-size:.8rem;opacity:.85;margin-bottom:.3rem;}

/* Tabs */
.stTabs [data-baseweb="tab-list"]{gap:.3rem;}
.stTabs [data-baseweb="tab"]{border-radius:10px 10px 0 0;padding:.5rem .9rem;font-weight:600;}
.foot{text-align:center;opacity:.6;font-size:.8rem;margin-top:2rem;}
</style>
"""

HERO = """
<div class="hero">
  <div class="eyebrow"><i></i>Section 6 · Income-tax Act 1961 &amp; 2025</div>
  <h1>India Tax Residency Calculator</h1>
  <p class="sub">Work out residential status year by year from travel dates — with Explanation 1, the 120-day rule,
  deemed residency and RNOR tests applied, and every gap in the record flagged.</p>
  <div class="chips">
    <span class="chip">Expl. 1(a) employment &amp; crew</span><span class="chip">Expl. 1(b) visits</span>
    <span class="chip">120-day rule</span><span class="chip">Deemed resident 6(1A)</span>
    <span class="chip">RNOR 6(6)</span><span class="chip">Missing-record checks</span>
  </div>
  <div class="by">By Aman Gautam · 8433878823</div>
</div>
"""


def step(n, title, sub=""):
    st.markdown(f'<div class="step"><div class="n">{n}</div><div><h3>{title}</h3>'
                f'{f"<p>{sub}</p>" if sub else ""}</div></div>', unsafe_allow_html=True)


def kpi_html(r, rows):
    counts = Counter(short_status(x["Status"]) for x in rows)
    latest = rows[-1]
    s = short_status(latest["Status"])
    return (
        '<div class="kpis">'
        f'<div class="kpi"><div class="l">Total days in India</div><div class="v">{r["total"]:,}</div>'
        f'<div class="s">across {len(rows)} financial years</div></div>'
        f'<div class="kpi"><div class="l">Latest FY · {latest["FY"]}</div><div class="v">'
        f'<span class="pill {s.lower()}" style="font-size:1rem">{s}</span></div>'
        f'<div class="s">{latest["Days"]} day{"" if latest["Days"] == 1 else "s"} in India</div></div>'
        f'<div class="kpi"><div class="l">Resident years</div><div class="v">{counts["ROR"] + counts["RNOR"]}</div>'
        f'<div class="s">{counts["ROR"]} ROR · {counts["RNOR"]} RNOR</div></div>'
        f'<div class="kpi"><div class="l">Non-resident years</div><div class="v">{counts["NR"]}</div>'
        f'<div class="s">{len(r["sensitive"])} year(s) flagged to verify</div></div>'
        '</div>')


def timeline_html(r, rows):
    cells = []
    for x in rows:
        s = short_status(x["Status"])
        y = int(x["FY"][:4])
        flag_cls = " flag" if y in r["sensitive"] else ""
        cells.append(f'<div class="tl {s.lower()}{flag_cls}" title="{_esc(x["Reason"])}">'
                     f'<div class="fy">{x["FY"][:4]}-{x["FY"][-2:]}</div><div class="d">{x["Days"]}</div>'
                     f'<div class="st">{s}</div></div>')
    legend = ('<div class="legend"><span><span class="pill ror">ROR</span> Resident &amp; ordinarily resident</span>'
              '<span><span class="pill rnor">RNOR</span> Resident, not ordinarily resident</span>'
              '<span><span class="pill nr">NR</span> Non-resident</span>'
              '<span><span class="dot"></span>status changes under an alternative assumption (see Verify tab)</span>'
              '</div>')
    return legend + '<div class="timeline">' + "".join(cells) + "</div>"


EXPLAINER = (
    "Residential status is decided **separately for every financial year** and depends on the "
    "**number of days physically present in India**.\n\n"
    "### Step 1 — Resident or Non-Resident? [Section 6(1)]\n"
    "Resident if **(a)** 182 days or more in the year, **or (c)** 60 days or more in the year **and** "
    "365 days or more in the 4 preceding years.\n\n"
    "### Explanation 1 — the 60 days is raised (set year-wise below)\n"
    "- **1(a)** An **Indian citizen who leaves India in that year** for employment abroad, or as crew of an "
    "Indian ship: 60 → **182 days**. It applies to the year of leaving. For a seafarer who leaves for a fresh "
    "contract every year, it can apply to each such year.\n"
    "- **1(b)** An **Indian citizen or PIO who is based outside India and comes on a visit**: 60 → **182 days**, "
    "in every year they are based abroad. From **FY 2020-21**, if Indian income (other than foreign-source income) "
    "exceeds Rs.15 lakh, it is **120 days** instead.\n\n"
    "### Deemed resident [Section 6(1A)] — from FY 2020-21\n"
    "An Indian citizen with Indian income above Rs.15 lakh who is not liable to tax in any other country is "
    "deemed resident, and is always RNOR [6(6)(d)].\n\n"
    "### Step 2 — ROR or RNOR? [Section 6(6)]\n"
    "A resident is **RNOR** if NR in **9 of the 10** preceding years, **or** in India for **729 days or less** "
    "in the 7 preceding years, **or** (from FY 2020-21) a 1(b) visitor resident only under the 120-day rule, "
    "**or** deemed resident. Otherwise **ROR**.\n\n"
    "### Counting days\n"
    "- Both the arrival day and the departure day count.\n"
    "- Movements are paired in date order, and every missing arrival or departure is flagged.\n"
    "- An arrival or departure with no match is not counted; it is listed under **Incomplete details** "
    "to follow up with the assessee.\n"
    "- Days that the law says not to count — crew voyage days under Rule 126, or Covid lockdown days in "
    "FY 2019-20 under CBDT Circular 11/2020 — can be entered per year with **Exclude days**."
)

PRIOR_CHOICES = ["— select —", "Resident in all preceding years",
                 "Non-Resident in all preceding years (0 days)", "Enter each preceding year"]


COL_EMP = "Left India for employment [1(a)]"
COL_VISIT = "Based abroad, visiting [1(b)]"
COL_CREW = "Left as crew of Indian ship [1(a)]"
COL_EXCL = "Days to exclude"
TICK_COLS = [COL_EMP, COL_VISIT, COL_CREW, COL_INC, COL_NOTAX]


def situation_of(emp, crew, visit):
    """One Explanation-1 situation per year. 1(a) takes precedence if more than one is ticked."""
    if emp:
        return SIT_EMP
    if crew:
        return SIT_CREW
    if visit:
        return SIT_VISIT
    return SIT_NORMAL


def build_flags_df(years, days_map, prev):
    old = {}
    if prev is not None and len(prev):
        for _, r in prev.iterrows():
            old[str(r["FY"])] = r
    rows = []
    for y in years:
        o = old.get(fy_label(y))
        row = {"FY": fy_label(y), "Days": int(days_map.get(y, 0))}
        for c in TICK_COLS:
            row[c] = bool(o[c]) if o is not None and c in o else False
        try:
            row[COL_EXCL] = int(o[COL_EXCL]) if o is not None and COL_EXCL in o and pd.notna(o[COL_EXCL]) else 0
        except (ValueError, TypeError):
            row[COL_EXCL] = 0
        rows.append(row)
    return pd.DataFrame(rows)


def year_flags_editor(years, days_map):
    ss = st.session_state
    show_inc = st.toggle("Also set Indian income > Rs.15L / Not liable to tax abroad (FY 2020-21 onward)",
                         key="show_inc_cols",
                         help="Needed only for the 120-day rule, deemed residency u/s 6(1A) and RNOR u/s 6(6)(c).")
    show_excl = st.toggle("Exclude days (crew voyage days under Rule 126 / Covid relief for FY 2019-20)",
                          key="show_excl_col",
                          help="Rule 126: for an Indian citizen crew member, days on an eligible voyage (per the "
                               "Continuous Discharge Certificate) are not counted as days in India. "
                               "CBDT Circular 11/2020: for FY 2019-20, days a visitor was stuck in India due to the "
                               "Covid lockdown (up to 31.03.2020) are excluded, subject to the circular's conditions.")
    tick_cols = TICK_COLS if show_inc else [COL_EMP, COL_VISIT, COL_CREW]
    sig = tuple((y, days_map.get(y, 0)) for y in years)
    if ss.get("yf_sig") != sig:
        ss["yf_base"] = build_flags_df(years, days_map, ss.get("yf_current"))
        ss["yf_sig"] = sig
        ss["yf_ver"] = ss.get("yf_ver", 0) + 1

    labels = [fy_label(y) for y in years]
    with st.expander("Tick a range of years at once", expanded=False):
        q1, q2, q3 = st.columns([1, 1, 2])
        f_from = q1.selectbox("From FY", labels, index=0, key="qf_from")
        f_to = q2.selectbox("To FY", labels, index=len(labels) - 1, key="qf_to")
        f_col = q3.selectbox("Column", tick_cols, index=1, key="qf_col")
        a1, a2, _ = st.columns([1, 1, 2])
        tick, untick = a1.button("Tick range", **WIDE), a2.button("Untick range", **WIDE)
        if tick or untick:
            lo, hi = sorted((int(f_from[:4]), int(f_to[:4])))
            df = (ss.get("yf_current") if ss.get("yf_current") is not None else ss["yf_base"]).copy()
            mask = df["FY"].str[:4].astype(int).between(lo, hi)
            df.loc[mask, f_col] = bool(tick)
            ss["yf_base"] = build_flags_df(years, days_map, df)
            ss["yf_ver"] += 1
            st.rerun()

    edited = st.data_editor(
        ss["yf_base"], hide_index=True, **WIDE,
        key=f"yf_editor_{ss['yf_ver']}", disabled=["FY", "Days"],
        column_order=["FY", "Days"] + tick_cols + ([COL_EXCL] if show_excl else []),
        column_config={
            "FY": st.column_config.TextColumn("FY", width="small"),
            "Days": st.column_config.NumberColumn("Days in India", width="small"),
            COL_EMP: st.column_config.CheckboxColumn(
                COL_EMP, help="Indian citizen who left India in this FY for employment outside India: "
                              "60 days becomes 182 [Expl. 1(a)]"),
            COL_VISIT: st.column_config.CheckboxColumn(
                COL_VISIT, help="Citizen/PIO living outside India who came on visit(s) in this FY: 60 days "
                                "becomes 182, or 120 from FY 2020-21 if Indian income > Rs.15L [Expl. 1(b)]"),
            COL_CREW: st.column_config.CheckboxColumn(
                COL_CREW, help="Indian citizen who left India in this FY as crew of an Indian ship [Expl. 1(a)]"),
            COL_INC: st.column_config.CheckboxColumn(
                COL_INC, help="Total income other than foreign-source income exceeds Rs.15 lakh "
                              "(relevant from FY 2020-21)"),
            COL_NOTAX: st.column_config.CheckboxColumn(
                COL_NOTAX, help="Citizen not liable to tax in any other country — for deemed residency u/s 6(1A)"),
            COL_EXCL: st.column_config.NumberColumn(
                COL_EXCL, min_value=0, max_value=366, step=1, width="small",
                help="Days in India that do not count for this FY (Rule 126 voyage days, Covid relief)"),
        },
    )
    ss["yf_current"] = edited
    flags, rows = {}, []
    both = []
    for _, r in edited.iterrows():
        y = int(str(r["FY"])[:4])
        emp, crew, visit = bool(r[COL_EMP]), bool(r[COL_CREW]), bool(r[COL_VISIT])
        inc15 = show_inc and bool(r[COL_INC])
        notax = show_inc and bool(r[COL_NOTAX])
        try:
            ex = int(r[COL_EXCL]) if show_excl and pd.notna(r[COL_EXCL]) else 0
        except (ValueError, TypeError):
            ex = 0
        sit = situation_of(emp, crew, visit)
        if (emp or crew) and visit:
            both.append(fy_label(y))
        flags[y] = {"sit": sit, "inc15": inc15, "notax": notax, "excl": max(0, ex)}
        rows.append({"FY": fy_label(y), COL_SIT: sit, COL_INC: inc15, COL_NOTAX: notax, COL_EXCL: max(0, ex)})
    if both:
        st.caption(f"Both 1(a) and 1(b) ticked for {', '.join(both)} — 1(a) (182 days) is applied for those years.")
    return flags, pd.DataFrame(rows)


def build_html_report(r, rows):
    now = datetime.now().strftime("%d %B %Y, %I:%M %p")
    prof = [f"Type: {_esc(r['taxpayer_type'])}", f"Preceding years: {_esc(r['prior_choice'])}"]

    flag_rows = "".join(
        f"<tr><td>{_esc(x['FY'])}</td><td>{_esc(x[COL_SIT])}</td>"
        f"<td>{'Yes' if x[COL_INC] else ''}</td><td>{'Yes' if x[COL_NOTAX] else ''}</td>"
        f"<td class='num'>{x.get(COL_EXCL) or ''}</td></tr>"
        for x in r["flags_rows"] if x[COL_SIT] != SIT_NORMAL or x[COL_INC] or x[COL_NOTAX] or x.get(COL_EXCL))
    flags_html = ("<h3>Year-wise circumstances applied</h3><table><thead><tr><th>FY</th><th>Situation</th>"
                  f"<th>{COL_INC}</th><th>{COL_NOTAX}</th><th>{COL_EXCL}</th></tr></thead>"
                  f"<tbody>{flag_rows}</tbody></table>"
                  if flag_rows else "")

    table_rows = "".join(
        f"<tr><td>{_esc(x['FY'])}</td><td class='num'>{x['Days']}</td>"
        f"<td class='{short_status(x['Status']).lower()}'>{_esc(x['Status'])}</td>"
        f"<td>{_esc(x['Reason'])}</td></tr>" for x in rows)

    sens_html = ""
    if r["sensitive"]:
        heads = "".join(f"<th>{_esc(k)}</th>" for k in r["scen_names"])
        body = ""
        for y in sorted(r["sensitive"]):
            alts = r["sensitive"][y]
            body += (f"<tr><td>{fy_label(y)}</td><td class='num'>{r['full_days'][y]}</td>"
                     f"<td>{short_status(r['residency'][y][0])}</td>"
                     + "".join(f"<td>{short_status(alts[k])}</td>" for k in r["scen_names"]) + "</tr>")
        sens_html = ("<h3>Sensitivity — verify before relying</h3>"
                     "<p>These years change classification under the alternative assumptions shown.</p>"
                     f"<table><thead><tr><th>FY</th><th>Days</th><th>Shown</th>{heads}</tr></thead>"
                     f"<tbody>{body}</tbody></table>")

    pairing_html = ("<h3>Smart pairing log</h3><pre>" + _esc("\n".join(r["log"])) + "</pre>") if r["log"] else ""
    prior_html = ""
    if r["prior_map"]:
        pm = "".join(f"<tr><td>{fy_label(y)}</td><td>{_esc(v['status'])}</td><td class='num'>{v['days']}</td></tr>"
                     for y, v in sorted(r["prior_map"].items()))
        prior_html = ("<h3>Preceding-year data entered</h3><table><thead><tr><th>FY</th><th>Status</th>"
                      f"<th>Days</th></tr></thead><tbody>{pm}</tbody></table>")

    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>India Tax Residency Report</title>
<style>
 body{{font-family:Arial,Helvetica,sans-serif;color:#1a2027;margin:32px;line-height:1.45;}}
 h1{{margin:0 0 2px;font-size:22px;}} h3{{margin:22px 0 8px;font-size:15px;color:#203a43;
     border-bottom:2px solid #e6e9ef;padding-bottom:4px;}}
 .meta{{color:#5a6675;font-size:12px;margin-bottom:6px;}}
 .total{{font-size:16px;font-weight:bold;margin-top:18px;}}
 table{{border-collapse:collapse;width:100%;margin-top:6px;font-size:12px;}}
 th,td{{border:1px solid #cdd3dd;padding:6px 8px;text-align:left;vertical-align:top;}}
 th{{background:#f1f4f8;}} td.num{{text-align:right;}}
 .nr{{background:#e8eef7;}} .ror{{background:#e3f4e1;}} .rnor{{background:#fdf0d5;}}
 pre{{background:#f6f8fa;border:1px solid #e6e9ef;border-radius:6px;padding:10px;font-size:11px;white-space:pre-wrap;}}
 @media print{{ body{{margin:12mm;}} }}
</style></head><body>
<h1>India Tax Residency Report</h1>
<div class="meta">Section 6 — Income-tax Act 1961 &amp; Income-tax Act 2025 &nbsp;·&nbsp; Generated {now}</div>
<h3>Taxpayer profile</h3>
<p>{' &nbsp;·&nbsp; '.join(prof)}</p>
{flags_html}
{prior_html}
{pairing_html}
<h3>Residency by financial year</h3>
<table><thead><tr><th>FY</th><th>Days</th><th>Status</th><th>Reason</th></tr></thead>
<tbody>{table_rows}</tbody></table>
{sens_html}
<p class="total">Total days in India: {r['total']}</p>
</body></html>"""


STATUS_STYLE = {
    STATUS_NR: ("#e7eef8", "#2c4a75"),
    STATUS_ROR: ("#e2f4e5", "#1d6b34"),
    STATUS_RNOR: ("#fdf1d8", "#8a5200"),
}


def main():
    st.set_page_config(page_title="India Tax Residency Calculator", page_icon="🧭", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown(HERO, unsafe_allow_html=True)
    with st.expander("How residency is decided", expanded=False, **ic(":material/menu_book:")):
        st.markdown(EXPLAINER)
    st.session_state.setdefault("results", None)

    # ---------------- 1) INPUT ----------------
    presence = {"days": {}, "max_days": {}, "fy_trips": {}, "log": [], "warns": [], "incompletes": []}
    with st.container(border=True):
        step(1, "Travel data", "Paste arrival and departure dates (any order), or enter days per year directly.")
        mode = st.segmented_control("Input method", ["Arrival / Departure dates", "FY + Days (direct entry)"],
                                    default="Arrival / Departure dates", label_visibility="collapsed") \
            if hasattr(st, "segmented_control") else \
            st.radio("Input method", ["Arrival / Departure dates", "FY + Days (direct entry)"], horizontal=True)
        mode = mode or "Arrival / Departure dates"
        sig_parts = [mode]
        if mode == "Arrival / Departure dates":
            c1, c2 = st.columns(2)
            arr = c1.text_area("Arrival dates", height=170, placeholder="01/04/2024  15/07/2024  10/01/2025",
                               help="Space / comma / newline separated. DD/MM/YYYY, DD-MM-YYYY, DD.MM.YYYY, YYYY-MM-DD")
            dep = c2.text_area("Departure dates", height=170, placeholder="10/06/2024  20/08/2024  25/01/2025")
            presence = compute_presence(arr, dep)
            sig_parts += [arr, dep]
            n_a, n_d = len(parse_dates(arr)[0]), len(parse_dates(dep)[0])
            if n_a or n_d:
                gaps = sum(1 for i in presence["incompletes"] if "NO " in i)
                st.caption(f"Read **{n_a}** arrivals and **{n_d}** departures · "
                           f"{sum(presence['days'].values())} days in India across {len(presence['days'])} FYs"
                           + (f" · :orange[{gaps} unmatched record(s)]" if gaps else " · all records matched"))
        else:
            edited = st.data_editor(
                pd.DataFrame({"FY": ["2024-2025"], "Days": [0]}), num_rows="dynamic",
                **WIDE, hide_index=True, key="fy_editor",
                column_config={
                    "FY": st.column_config.TextColumn("FY", help="e.g. 2024-2025, 2024-25 or 2024"),
                    "Days": st.column_config.NumberColumn("Days in India", min_value=0, max_value=366, step=1),
                })
            days = {}
            for _, row in edited.iterrows():
                m = re.match(r"\s*(\d{4})", str(row.get("FY", "")))
                if not m:
                    continue
                try:
                    d = int(float(row.get("Days")))
                except (ValueError, TypeError):
                    d = 0
                y = int(m.group(1))
                if y in days:
                    presence["warns"].append(f"{fy_label(y)} entered more than once — the days are added together")
                days[y] = days.get(y, 0) + max(0, d)
            for y, d in days.items():
                if d > 366:
                    presence["warns"].append(f"{fy_label(y)}: {d} days is more than a year — capped at 366")
            presence["days"] = {y: min(d, 366) for y, d in days.items()}
            if days:
                gaps = [fy_label(y) for y in range(min(days), max(days) + 1) if y not in days]
                if gaps:
                    presence["warns"].append("Not entered, taken as 0 days: " + ", ".join(gaps))
                    st.warning("These FYs were not entered and will be taken as **0 days**: " + ", ".join(gaps))
            sig_parts.append(edited.to_json())

    full_days = fill_range(presence["days"])
    years = sorted(full_days)

    # ---------------- 2) PROFILE ----------------
    with st.container(border=True):
        step(2, "Taxpayer", "Explanation 1(a) applies only to Indian citizens; 1(b) to citizens and PIOs.")
        taxpayer_type = st.radio("Taxpayer type", ["Indian Citizen", "Person of Indian Origin (PIO)",
                                                   "Foreign Citizen (neither)"], horizontal=True,
                                 label_visibility="collapsed")
        if taxpayer_type.startswith("Person of Indian Origin"):
            st.caption("PIO: the person, or either parent, or any grandparent, was born in undivided India "
                       "[Explanation 1 to Section 6(1)].")
        is_citizen = taxpayer_type == "Indian Citizen"
        is_pio = taxpayer_type == "Person of Indian Origin (PIO)"
        sig_parts.append(taxpayer_type)

    # ---------------- 3) YEAR-WISE CIRCUMSTANCES ----------------
    flags, flags_df = {}, pd.DataFrame()
    with st.container(border=True):
        step(3, "Year-wise circumstances",
             "Tick <b>Left India for employment</b> for the year they left, and <b>Based abroad, visiting</b> "
             "for each year they lived abroad and visited. Unticked years use the 60-day rule.")
        if years:
            flags, flags_df = year_flags_editor(years, full_days)
            sig_parts.append(flags_df.to_json())
            if not is_citizen and not is_pio and any(f["sit"] != SIT_NORMAL for f in flags.values()):
                st.info("Explanation 1 applies only to Indian citizens (and PIOs for 1(b)); "
                        "for a foreign citizen these settings are ignored.")
        else:
            st.info("The years will appear here once travel data is entered.", **ic(":material/info:"))

    # ---------------- 4) PRECEDING YEARS ----------------
    prior_status, prior_days, prior_map = None, 0, {}
    with st.container(border=True):
        step(4, "Years before the data", "Feeds the 4-year (365 days), 7-year (729 days) and 9-of-10 tests "
                                         "for the earliest years.")
        prior_choice = st.selectbox("Residential status in the years before the data", PRIOR_CHOICES,
                                    label_visibility="collapsed")
        sig_parts.append(prior_choice)
        if prior_choice == PRIOR_CHOICES[1]:
            prior_status, prior_days = "Resident", 182
            st.caption("Assumes resident with 182 days in each earlier year.")
        elif prior_choice == PRIOR_CHOICES[2]:
            prior_status, prior_days = "Non-Resident", 0
        elif prior_choice == PRIOR_CHOICES[3]:
            prior_status, prior_days = "Non-Resident", 0
            if years:
                pri_years = list(range(years[0] - 10, years[0]))
                edited_prior = st.data_editor(
                    pd.DataFrame({"FY": [fy_label(y) for y in pri_years],
                                  "Status": ["Non-Resident"] * 10, "Days": [0] * 10}),
                    hide_index=True, **WIDE, key=f"prior_editor_{years[0]}",
                    column_config={
                        "FY": st.column_config.TextColumn("FY", disabled=True),
                        "Status": st.column_config.SelectboxColumn("Status", options=["Non-Resident", "Resident"],
                                                                   required=True),
                        "Days": st.column_config.NumberColumn("Days in India", min_value=0, max_value=366, step=1),
                    })
                for _, row in edited_prior.iterrows():
                    try:
                        dd = int(float(row["Days"]))
                    except (ValueError, TypeError):
                        dd = 0
                    prior_map[int(str(row["FY"])[:4])] = {"status": row["Status"] or "Non-Resident",
                                                          "days": max(0, min(366, dd))}
                bad = [fy_label(y) for y, v in prior_map.items() if v["status"] == "Resident" and v["days"] < 60]
                if bad:
                    st.warning("Marked Resident with fewer than 60 days: " + ", ".join(bad))
                sig_parts.append(edited_prior.to_json())
            else:
                st.info("Enter travel data first to list the preceding years.")

    sig = hashlib.md5("|".join(sig_parts).encode()).hexdigest()

    # ---------------- ACTIONS ----------------
    b1, b2 = st.columns([3, 1])
    calculate = b1.button("Calculate residency", type="primary", **ic(":material/calculate:"), **WIDE)
    if b2.button("Clear", **ic(":material/restart_alt:"), **WIDE):
        st.session_state.results = None
        st.rerun()

    if calculate:
        if not years or not any(full_days.values()):
            st.error("No data yet — enter dates (or FY + Days) first.")
        elif prior_choice == PRIOR_CHOICES[0]:
            st.error("Choose the residential status for the years before the data (step 4).")
        else:
            residency, reasons, sensitive, scen_names = run_all(
                full_days, flags, is_citizen, is_pio, prior_status, prior_days, prior_map)
            st.session_state.results = {
                "sig": sig, "full_days": full_days, "residency": residency, "reasons": reasons,
                "sensitive": sensitive, "scen_names": scen_names,
                "total": sum(full_days.values()), "warns": presence["warns"], "fy_trips": presence["fy_trips"],
                "log": presence["log"], "incompletes": presence["incompletes"],
                "prior_choice": prior_choice, "prior_map": prior_map, "mode": mode,
                "taxpayer_type": taxpayer_type,
                "flags_rows": flags_df.to_dict("records") if len(flags_df) else [],
            }
            st.rerun()

    r = st.session_state.results
    if not r:
        st.markdown('<p class="foot">Fill in the steps above and click <b>Calculate residency</b>.</p>',
                    unsafe_allow_html=True)
        return

    # ---------------- RESULTS ----------------
    st.markdown("## Results")
    if r["sig"] != sig:
        st.warning("Inputs have changed since this result was calculated — click **Calculate residency** again.",
                   **ic(":material/sync_problem:"))

    rows = [{"FY": fy_label(y), "Days": r["full_days"][y], "Status": r["residency"][y][0],
             "Reason": r["reasons"][y]} for y in sorted(r["full_days"])]
    df = pd.DataFrame(rows)
    st.markdown(kpi_html(r, rows), unsafe_allow_html=True)
    st.markdown(timeline_html(r, rows), unsafe_allow_html=True)

    tsv = "FY\tDays\tStatus\tReason\n" + "\n".join(
        f"{x['FY']}\t{x['Days']}\t{x['Status']}\t{x['Reason']}" for x in rows)

    report = "INDIA TAX RESIDENCY REPORT (SECTION 6)\n" + "=" * 60 + "\n"
    report += f"Generated: {datetime.now().strftime('%d %B %Y, %I:%M %p')}\n" + "-" * 60 + "\n\n"
    report += f"Taxpayer: {r['taxpayer_type']} | Preceding years: {r['prior_choice']}\n\n"
    applied = [x for x in r["flags_rows"] if x[COL_SIT] != SIT_NORMAL or x[COL_INC] or x[COL_NOTAX]
               or x.get(COL_EXCL)]
    if applied:
        report += "YEAR-WISE CIRCUMSTANCES:\n" + "\n".join(
            f"- {x['FY']}: {x[COL_SIT]}{' | Indian income > Rs.15L' if x[COL_INC] else ''}"
            f"{' | not liable to tax abroad' if x[COL_NOTAX] else ''}"
            f"{' | ' + str(x[COL_EXCL]) + ' day(s) excluded' if x.get(COL_EXCL) else ''}" for x in applied) + "\n\n"
    if r["sensitive"]:
        report += "SENSITIVITY (verify before relying):\n"
        for y in sorted(r["sensitive"]):
            report += f"- {fy_label(y)}: {short_status(r['residency'][y][0])} as shown | " + " | ".join(
                f"{k}: {short_status(v)}" for k, v in r["sensitive"][y].items()) + "\n"
        report += "\n"
    if r["log"]:
        report += "SMART PAIRING LOG:\n" + "\n".join(r["log"]) + "\n\n"
    if r["incompletes"]:
        report += "INCOMPLETE DETAILS (FOLLOW UP WITH ASSESSEE):\n" + "\n".join(f"- {i}" for i in r["incompletes"]) + "\n\n"
    report += tsv + f"\n\nTOTAL DAYS IN INDIA: {r['total']}\n"
    report += "Calculator by: Aman Gautam (8433878823)\n"

    html_report = build_html_report(r, rows)
    stamp = datetime.now().strftime("%Y%m%d")

    n_checks = len(r["incompletes"]) + len(r["warns"])
    tab_names = ["Year-wise table", "Smart pairing log", f"Verify ({len(r['sensitive'])})",
                 f"Incomplete details ({n_checks})"]
    t_table, t_log, t_sens, t_checks = st.tabs(tab_names)

    with t_table:
        def _row_style(row):
            bg, fg = STATUS_STYLE.get(row["Status"], ("", ""))
            return [f"background-color:{bg}; color:{fg}" if bg else "" for _ in row]

        st.dataframe(df.style.apply(_row_style, axis=1), **WIDE, hide_index=True,
                     column_config={"FY": st.column_config.TextColumn(width="small"),
                                    "Days": st.column_config.NumberColumn(width="small"),
                                    "Status": st.column_config.TextColumn(width="medium"),
                                    "Reason": st.column_config.TextColumn(width="large")})
        b64 = base64.b64encode(html_report.encode("utf-8")).decode()
        components.html(f"""
        <button id="printBtn" style="width:100%;padding:12px 14px;border:0;border-radius:12px;
            background:linear-gradient(135deg,#0f8b8d,#0b6f86);color:#fff;font-weight:600;font-size:14px;
            font-family:Inter,system-ui,sans-serif;cursor:pointer;box-shadow:0 10px 22px -12px rgba(15,139,141,.9);">
            Print full report / Save as PDF</button>
        <script>
          const B64 = "{b64}";
          document.getElementById("printBtn").onclick = function() {{
            const bytes = Uint8Array.from(atob(B64), c => c.charCodeAt(0));
            const html = new TextDecoder("utf-8").decode(bytes);
            const w = window.open("", "_blank");
            if (!w) {{ alert("Please allow pop-ups to print, or use the HTML download."); return; }}
            w.document.open(); w.document.write(html); w.document.close();
            w.focus(); setTimeout(function(){{ w.print(); }}, 400);
          }};
        </script>""", height=64)
        d1, d2, d3 = st.columns(3)
        d1.download_button("Report (.txt)", report, file_name=f"Tax_Residency_{stamp}.txt",
                           mime="text/plain", **ic(":material/description:"), **WIDE)
        d2.download_button("Table (.csv)", df.to_csv(index=False), file_name=f"Tax_Residency_{stamp}.csv",
                           mime="text/csv", **ic(":material/table_view:"), **WIDE)
        d3.download_button("Printable report (.html)", html_report, file_name=f"Tax_Residency_{stamp}.html",
                           mime="text/html", **ic(":material/print:"), **WIDE)
        st.caption("Print opens the report in a new tab and launches the print dialog — choose Save as PDF there. "
                   "If pop-ups are blocked, download the HTML and open it.")

        st.caption("Copy for Excel — use the copy icon on the box below, then paste into a sheet.")
        st.code(tsv, language="text")

    with t_log:
        if r["log"]:
            st.caption("Each arrival is matched with the next departure in date order. "
                       "Lines with NO ARRIVAL / NO DEPARTURE are missing records.")
            st.code("\n".join(r["log"]), language="text")
        else:
            st.info("The pairing log is available when travel dates are entered.")

    with t_sens:
        if r["sensitive"]:
            st.markdown("These years change classification under at least one alternative assumption. "
                        "The main table uses your inputs.")
            srows = []
            for y in sorted(r["sensitive"]):
                row = {"FY": fy_label(y), "Days": r["full_days"][y], "Shown": short_status(r["residency"][y][0])}
                row.update({k: short_status(v) for k, v in r["sensitive"][y].items()})
                srows.append(row)
            st.dataframe(pd.DataFrame(srows), **WIDE, hide_index=True)
        else:
            st.success("No year changes status under the alternative assumptions.", **ic(":material/verified:"))

    with t_checks:
        if r["incompletes"]:
            st.markdown("**Follow up with assessee** — these entries are not counted in the table")
            for inc in r["incompletes"]:
                st.markdown(f"- {inc}")
        if r["warns"]:
            st.markdown("**Warnings**")
            for w in r["warns"]:
                st.markdown(f"- {w}")
        if not n_checks:
            st.success("Every arrival has a matching departure.", **ic(":material/check_circle:"))
        if r["mode"].startswith("Arrival") and r["fy_trips"]:
            fy_choice = st.selectbox("View stays counted in FY", ["—"] + [x["FY"] for x in rows])
            if fy_choice != "—":
                trips = r["fy_trips"].get(int(fy_choice[:4]), [])
                if trips:
                    st.code("\n".join(dict.fromkeys(trips)), language="text")
                else:
                    st.info(f"No stay recorded in {fy_choice}.")

    st.markdown('<p class="foot">Section 6(1) · 6(1A) · 6(6) — Income-tax Act 1961 &amp; 2025 · '
                'Made with care by <b>Aman Gautam</b></p>', unsafe_allow_html=True)


if __name__ == "__main__":
    main()

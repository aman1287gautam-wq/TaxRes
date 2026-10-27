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
def compute_presence(arr_text, dep_text, chronological=True, data_from=None, data_to=None):
    """Build the set of days the person was certainly in India, plus days that
    may have been in India where a record is missing.

    Chronological pairing walks all movements in date order:
    an arrival opens a stay, the next departure closes it. Two departures in a
    row mean a missing arrival; two arrivals in a row mean a missing departure.
    """
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

    for d in arrs + deps:
        if data_from and d < data_from:
            warns.append(f"{fmt(d)} is before the 'Data available from' date {fmt(data_from)}")
        if data_to and d > data_to:
            warns.append(f"{fmt(d)} is after the 'Data available up to' date {fmt(data_to)}")

    if not chronological:
        n = max(len(arrs), len(deps))
        for i in range(n):
            a = arrs[i] if i < len(arrs) else None
            d = deps[i] if i < len(deps) else None
            if a and d and a <= d:
                lbl = f"Row {i+1}: {fmt(a)} -> {fmt(d)} ({(d-a).days+1} days)"
                add_span(a, d, certain, lbl)
                log.append(lbl)
            elif a and d:
                warns.append(f"Row {i+1}: arrival {fmt(a)} is after departure {fmt(d)} — skipped")
            else:
                incompletes.append(f"Row {i+1}: {'arrival ' + fmt(a) if a else 'departure ' + fmt(d)} "
                                   f"has no matching entry — skipped")
        return _presence_result(certain, maybe, fy_trips, log, warns, incompletes)

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
                        add_span(start, start, certain, f"Arrival {start_n}: {fmt(start)} (departure missing)")
                        gap = (d - start).days - 1
                        if gap > 0:
                            add_span(start + timedelta(days=1), d - timedelta(days=1), maybe)
                        msg = (f"Arrival {start_n} ({fmt(start)}) -> NO DEPARTURE before next arrival on {fmt(d)}")
                        log.append(msg)
                        incompletes.append(f"{msg}: departure date missing; up to {max(gap, 0)} more day(s) "
                                           f"may have been spent in India")
                    state, start, start_n = "in", d, n
                else:
                    if state == "in":
                        lbl = f"Arrival {start_n} ({fmt(start)}) -> Departure {n} ({fmt(d)}) | {(d-start).days+1} days"
                        add_span(start, d, certain, lbl)
                        log.append(lbl)
                    elif state is None:
                        if data_from and data_from <= d:
                            lbl = (f"Departure {n} ({fmt(d)}) -> NO ARRIVAL; counted from data start "
                                   f"{fmt(data_from)} | {(d-data_from).days+1} days")
                            add_span(data_from, d, certain, lbl)
                            log.append(lbl)
                            incompletes.append(f"Departure {n} ({fmt(d)}) has no earlier arrival: assumed in India "
                                               f"from the data start date {fmt(data_from)}")
                        else:
                            add_span(d, d, certain, f"Departure {n}: {fmt(d)} (arrival before data start)")
                            msg = f"Departure {n} ({fmt(d)}) -> NO ARRIVAL (in India before the data begins)"
                            log.append(msg)
                            incompletes.append(f"{msg}: only the departure day is counted. Enter 'Data available "
                                               f"from' to count the stay from that date")
                    else:  # two departures in a row
                        add_span(d, d, certain, f"Departure {n}: {fmt(d)} (arrival missing)")
                        gap = (d - last_dep).days - 1
                        if gap > 0:
                            add_span(last_dep + timedelta(days=1), d - timedelta(days=1), maybe)
                        msg = f"Departure {n} ({fmt(d)}) -> NO ARRIVAL after previous departure on {fmt(last_dep)}"
                        log.append(msg)
                        incompletes.append(f"{msg}: arrival date missing; up to {max(gap, 0)} more day(s) "
                                           f"may have been spent in India")
                    state, last_dep = "out", d

    if state == "in":
        if data_to and data_to >= start:
            lbl = (f"Arrival {start_n} ({fmt(start)}) -> NO DEPARTURE; counted till data end "
                   f"{fmt(data_to)} | {(data_to-start).days+1} days")
            add_span(start, data_to, certain, lbl)
            log.append(lbl)
            incompletes.append(f"Arrival {start_n} ({fmt(start)}) has no later departure: assumed in India "
                               f"till the data end date {fmt(data_to)}")
        else:
            add_span(start, start, certain, f"Arrival {start_n}: {fmt(start)} (no departure)")
            msg = f"Arrival {start_n} ({fmt(start)}) -> NO DEPARTURE FOUND"
            log.append(msg)
            incompletes.append(f"{msg}: only the arrival day is counted. Enter 'Data available up to' "
                               f"to count the stay till that date")

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


def run_all(full_days, max_days, flags, is_citizen, is_pio, prior_status, prior_days, prior_map):
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
    if max_days and any(max_days.get(y, 0) != full_days.get(y, 0) for y in full_days):
        mx = {y: max_days.get(y, 0) for y in full_days}
        scen["If missing stays were spent in India"] = classify_years(
            mx, flags, is_citizen, is_pio, prior_status, prior_days, prior_map)[0]

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
#MainMenu {visibility:hidden;} footer {visibility:hidden;}
.block-container {padding-top:1.4rem; max-width:1200px;}
.hero {background:linear-gradient(135deg,#0f2027 0%,#203a43 55%,#2c5364 100%);
       padding:1.5rem 1.8rem;border-radius:18px;margin-bottom:1.2rem;
       box-shadow:0 8px 24px rgba(15,32,39,.18);}
.hero h1 {color:#fff;margin:0;font-size:1.75rem;letter-spacing:.2px;}
.hero p  {margin:.35rem 0 0;}
.hero .sub {color:#d7e8f2;font-size:.95rem;}
.hero .by  {color:#93b9cc;font-size:.8rem;margin-top:.5rem;}
div[data-testid="stMetricValue"] {font-size:1.4rem;}
.stButton>button {border-radius:10px;font-weight:600;}
</style>
"""

HERO = """
<div class="hero">
  <h1>India Tax Residency Calculator</h1>
  <p class="sub">Section 6 &nbsp;·&nbsp; Income-tax Act 1961 (up to FY 2025-26) &amp; Income-tax Act 2025 (FY 2026-27 onward)</p>
  <p class="sub" style="font-size:.85rem;">Explanation 1(a)/1(b) year-wise &nbsp;·&nbsp; 120-day rule &nbsp;·&nbsp; Deemed residency 6(1A) &nbsp;·&nbsp; RNOR 6(6) &nbsp;·&nbsp; Missing-record checks</p>
  <p class="by">By Aman Gautam &nbsp;·&nbsp; 8433878823</p>
</div>
"""

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
    "- Movements are paired in date order. A missing arrival or departure is flagged, and the "
    "**'If missing stays were spent in India'** check shows whether the gap could change the status.\n"
    "- A stay with no departure is counted only up to the **'Data available up to'** date you enter.\n"
    "- Rule 126 (crew voyage days) is not computed automatically."
)

PRIOR_CHOICES = ["— select —", "Resident in all preceding years",
                 "Non-Resident in all preceding years (0 days)", "Enter each preceding year"]


def build_flags_df(years, days_map, prev):
    old = {}
    if prev is not None and len(prev):
        for _, r in prev.iterrows():
            old[str(r["FY"])] = r
    rows = []
    for y in years:
        o = old.get(fy_label(y))
        rows.append({
            "FY": fy_label(y),
            "Days": int(days_map.get(y, 0)),
            COL_SIT: (o[COL_SIT] if o is not None and o[COL_SIT] in SITUATIONS else SIT_NORMAL),
            COL_INC: bool(o[COL_INC]) if o is not None else False,
            COL_NOTAX: bool(o[COL_NOTAX]) if o is not None else False,
        })
    return pd.DataFrame(rows)


def year_flags_editor(years, days_map):
    ss = st.session_state
    sig = tuple((y, days_map.get(y, 0)) for y in years)
    if ss.get("yf_sig") != sig:
        ss["yf_base"] = build_flags_df(years, days_map, ss.get("yf_current"))
        ss["yf_sig"] = sig
        ss["yf_ver"] = ss.get("yf_ver", 0) + 1

    labels = [fy_label(y) for y in years]
    with st.expander("Quick fill a range of years", expanded=False):
        q1, q2, q3 = st.columns([1, 1, 2])
        f_from = q1.selectbox("From FY", labels, index=0, key="qf_from")
        f_to = q2.selectbox("To FY", labels, index=len(labels) - 1, key="qf_to")
        f_sit = q3.selectbox("Situation", SITUATIONS, index=3, key="qf_sit")
        if st.button("Apply to range"):
            lo, hi = int(f_from[:4]), int(f_to[:4])
            if lo > hi:
                lo, hi = hi, lo
            df = (ss.get("yf_current") if ss.get("yf_current") is not None else ss["yf_base"]).copy()
            mask = df["FY"].str[:4].astype(int).between(lo, hi)
            df.loc[mask, COL_SIT] = f_sit
            ss["yf_base"] = build_flags_df(years, days_map, df)
            ss["yf_ver"] += 1
            st.rerun()

    edited = st.data_editor(
        ss["yf_base"], hide_index=True, **WIDE,
        key=f"yf_editor_{ss['yf_ver']}", disabled=["FY", "Days"],
        column_config={
            "FY": st.column_config.TextColumn("FY", width="small"),
            "Days": st.column_config.NumberColumn("Days in India", width="small"),
            COL_SIT: st.column_config.SelectboxColumn(COL_SIT, options=SITUATIONS, required=True, width="large"),
            COL_INC: st.column_config.CheckboxColumn(COL_INC, help="Total income other than foreign-source income "
                                                     "exceeds Rs.15 lakh (relevant from FY 2020-21)"),
            COL_NOTAX: st.column_config.CheckboxColumn(COL_NOTAX, help="Citizen not liable to tax in any other "
                                                       "country — for deemed residency u/s 6(1A)"),
        },
    )
    ss["yf_current"] = edited
    flags = {}
    for _, r in edited.iterrows():
        y = int(str(r["FY"])[:4])
        flags[y] = {"sit": r[COL_SIT] if r[COL_SIT] in SITUATIONS else SIT_NORMAL,
                    "inc15": bool(r[COL_INC]), "notax": bool(r[COL_NOTAX])}
    return flags, edited


def build_html_report(r, rows):
    now = datetime.now().strftime("%d %B %Y, %I:%M %p")
    prof = [f"Type: {_esc(r['taxpayer_type'])}", f"Preceding years: {_esc(r['prior_choice'])}"]
    if r.get("data_from"):
        prof.append(f"Data from: {fmt(r['data_from'])}")
    if r.get("data_to"):
        prof.append(f"Data up to: {fmt(r['data_to'])}")

    flag_rows = "".join(
        f"<tr><td>{_esc(x['FY'])}</td><td>{_esc(x[COL_SIT])}</td>"
        f"<td>{'Yes' if x[COL_INC] else ''}</td><td>{'Yes' if x[COL_NOTAX] else ''}</td></tr>"
        for x in r["flags_rows"] if x[COL_SIT] != SIT_NORMAL or x[COL_INC] or x[COL_NOTAX])
    flags_html = ("<h3>Year-wise circumstances applied</h3><table><thead><tr><th>FY</th><th>Situation</th>"
                  f"<th>{COL_INC}</th><th>{COL_NOTAX}</th></tr></thead><tbody>{flag_rows}</tbody></table>"
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

    pairing_html = ("<h3>Pairing log</h3><pre>" + _esc("\n".join(r["log"])) + "</pre>") if r["log"] else ""
    inc_html = ("<h3>Incomplete details — follow up with assessee</h3><ul>"
                + "".join(f"<li>{_esc(i)}</li>" for i in r["incompletes"]) + "</ul>") if r["incompletes"] else ""
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
{inc_html}
<h3>Residency by financial year</h3>
<table><thead><tr><th>FY</th><th>Days</th><th>Status</th><th>Reason</th></tr></thead>
<tbody>{table_rows}</tbody></table>
{sens_html}
<p class="total">Total days in India: {r['total']}</p>
</body></html>"""


STATUS_STYLE = {
    STATUS_NR: ("#e8eef7", "#1e3a5f"),
    STATUS_ROR: ("#e3f4e1", "#1e5620"),
    STATUS_RNOR: ("#fdf0d5", "#7a4b00"),
}


def main():
    st.set_page_config(page_title="India Tax Residency - Section 6", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown(HERO, unsafe_allow_html=True)
    with st.expander("How residency is decided — full explanation", expanded=False):
        st.markdown(EXPLAINER)
    st.session_state.setdefault("results", None)

    # ---------------- 1) INPUT ----------------
    mode = st.radio("Input method", ["Arrival / Departure dates", "FY + Days (direct entry)"], horizontal=True)
    presence = {"days": {}, "max_days": {}, "fy_trips": {}, "log": [], "warns": [], "incompletes": []}
    sig_parts = [mode]
    data_from = data_to = None

    with st.container(border=True):
        if mode == "Arrival / Departure dates":
            c1, c2 = st.columns(2)
            arr = c1.text_area("Arrival Dates", height=190, placeholder="01/04/2024  15/07/2024  10/01/2025",
                               help="Space / comma / newline separated. DD/MM/YYYY, DD-MM-YYYY, DD.MM.YYYY, YYYY-MM-DD")
            dep = c2.text_area("Departure Dates", height=190, placeholder="10/06/2024  20/08/2024  25/01/2025")
            c3, c4, c5 = st.columns(3)
            data_from = c3.date_input("Data available from (optional)", value=None, format="DD/MM/YYYY",
                                      min_value=date(1980, 1, 1), max_value=date(2100, 1, 1),
                                      help="Start date of the travel record. A first departure with no earlier "
                                           "arrival is counted from this date.")
            data_to = c4.date_input("Data available up to", value=None, format="DD/MM/YYYY",
                                    min_value=date(1980, 1, 1), max_value=date(2100, 1, 1),
                                    help="End date of the travel record (e.g. NATGRID report date). A last arrival "
                                         "with no departure is counted till this date.")
            chrono = c5.radio("Pairing", ["Chronological (recommended)", "Row by row as entered"], index=0) \
                .startswith("Chrono")
            presence = compute_presence(arr, dep, chrono, data_from, data_to)
            sig_parts += [arr, dep, str(data_from), str(data_to), str(chrono)]
        else:
            st.caption("Enter the financial year and total days in India for each year. Add rows as needed.")
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
                days[y] = days.get(y, 0) + max(0, min(366, d))
            for y, d in days.items():
                if d > 366:
                    presence["warns"].append(f"{fy_label(y)}: entered days exceed 366")
            presence["days"] = {y: min(d, 366) for y, d in days.items()}
            sig_parts.append(edited.to_json())

    full_days = fill_range(presence["days"], presence["max_days"])
    years = sorted(full_days)

    # ---------------- 2) PROFILE ----------------
    with st.container(border=True):
        st.subheader("Taxpayer profile")
        taxpayer_type = st.radio("Taxpayer type", ["Indian Citizen", "Person of Indian Origin (PIO)",
                                                   "Foreign Citizen (neither)"], horizontal=True)
        is_citizen = taxpayer_type == "Indian Citizen"
        is_pio = taxpayer_type == "Person of Indian Origin (PIO)"
        sig_parts.append(taxpayer_type)

    # ---------------- 3) YEAR-WISE CIRCUMSTANCES ----------------
    flags, flags_df = {}, pd.DataFrame()
    with st.container(border=True):
        st.subheader("Year-wise circumstances")
        st.caption("Set the situation for each year. Use **Based abroad, came on visit(s)** for every year an "
                   "NRI/seafarer lived abroad and visited India, and **Left India for employment** for the year "
                   "they left. Years left on the 60-day rule are treated as an ordinary resident would be.")
        if years:
            flags, flags_df = year_flags_editor(years, full_days)
            sig_parts.append(flags_df.to_json())
            if not is_citizen and not is_pio and any(f["sit"] != SIT_NORMAL for f in flags.values()):
                st.info("Explanation 1 applies only to Indian citizens (and PIOs for 1(b)); "
                        "for a foreign citizen these settings are ignored.")
        else:
            st.info("Enter dates or FY + days above to list the years.")

    # ---------------- 4) PRECEDING YEARS ----------------
    prior_status, prior_days, prior_map = None, 0, {}
    with st.container(border=True):
        st.subheader("Preceding years")
        prior_choice = st.selectbox("Residential status in the years before the data", PRIOR_CHOICES,
                                    help="Drives the 365-day (4 years), 729-day (7 years) and 9-of-10 tests for the "
                                         "early years.")
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
                st.info("Enter trip / FY data above to list the preceding years.")

    sig = hashlib.md5("|".join(sig_parts).encode()).hexdigest()

    # ---------------- ACTIONS ----------------
    b1, b2 = st.columns(2)
    calculate = b1.button("Calculate residency", type="primary", **WIDE)
    if b2.button("Clear results", **WIDE):
        st.session_state.results = None
        st.rerun()

    if calculate:
        if not years or not any(full_days.values()):
            st.error("No data yet — enter dates (or FY + Days) first.")
        elif prior_choice == PRIOR_CHOICES[0]:
            st.error("Please choose the residential status in the preceding years first.")
        else:
            residency, reasons, sensitive, scen_names = run_all(
                full_days, presence["max_days"], flags, is_citizen, is_pio, prior_status, prior_days, prior_map)
            st.session_state.results = {
                "sig": sig, "full_days": full_days, "residency": residency, "reasons": reasons,
                "sensitive": sensitive, "scen_names": scen_names,
                "total": sum(full_days.values()), "warns": presence["warns"], "fy_trips": presence["fy_trips"],
                "log": presence["log"], "incompletes": presence["incompletes"],
                "prior_choice": prior_choice, "prior_map": prior_map, "mode": mode,
                "taxpayer_type": taxpayer_type, "data_from": data_from, "data_to": data_to,
                "flags_rows": flags_df.to_dict("records") if len(flags_df) else [],
            }
            st.rerun()

    r = st.session_state.results
    if not r:
        st.info("Fill in the inputs above and click **Calculate residency**.")
        return
    if r["sig"] != sig:
        st.warning("Inputs have changed since this result was calculated — click **Calculate residency** again.")

    rows = [{"FY": fy_label(y), "Days": r["full_days"][y], "Status": r["residency"][y][0],
             "Reason": r["reasons"][y]} for y in sorted(r["full_days"])]
    df = pd.DataFrame(rows)

    m1, m2, m3 = st.columns(3)
    with m1, st.container(border=True):
        st.metric("Total days in India", r["total"])
    with m2, st.container(border=True):
        st.metric(f"Latest FY ({rows[-1]['FY']})", short_status(rows[-1]["Status"]))
    with m3, st.container(border=True):
        st.metric("Years analysed", len(rows))

    if r["log"]:
        with st.expander("Pairing log", expanded=False):
            st.code("\n".join(r["log"]), language="text")
    if r["warns"]:
        st.warning("**Warnings**\n\n" + "\n".join(f"- {w}" for w in r["warns"]))
    if r["incompletes"]:
        with st.expander("Incomplete details — follow up with assessee", expanded=True):
            for inc in r["incompletes"]:
                st.write(f"- {inc}")

    if r["sensitive"]:
        st.warning("**Sensitivity — confirm before relying on this.** The years below change classification under "
                   "at least one alternative assumption. The main table uses your inputs.")
        srows = []
        for y in sorted(r["sensitive"]):
            row = {"FY": fy_label(y), "Days": r["full_days"][y],
                   "Shown": short_status(r["residency"][y][0])}
            row.update({k: short_status(v) for k, v in r["sensitive"][y].items()})
            srows.append(row)
        st.dataframe(pd.DataFrame(srows), **WIDE, hide_index=True)

    def _row_style(row):
        bg, fg = STATUS_STYLE.get(row["Status"], ("", ""))
        return [f"background-color:{bg}; color:{fg}" if bg else "" for _ in row]

    st.dataframe(df.style.apply(_row_style, axis=1), **WIDE, hide_index=True,
                 column_config={"Reason": st.column_config.TextColumn(width="large")})

    if r["mode"].startswith("Arrival") and r["fy_trips"]:
        fy_choice = st.selectbox("View stays counted in FY", ["—"] + [x["FY"] for x in rows])
        if fy_choice != "—":
            trips = r["fy_trips"].get(int(fy_choice[:4]), [])
            if trips:
                st.code("\n".join(dict.fromkeys(trips)), language="text")
            else:
                st.info(f"No stay recorded in {fy_choice}.")

    st.markdown("**Copy for Excel** — click the copy icon on the box below, then paste into a sheet.")
    tsv = "FY\tDays\tStatus\tReason\n" + "\n".join(
        f"{x['FY']}\t{x['Days']}\t{x['Status']}\t{x['Reason']}" for x in rows)
    st.code(tsv, language="text")

    report = "INDIA TAX RESIDENCY REPORT (SECTION 6)\n" + "=" * 60 + "\n"
    report += f"Generated: {datetime.now().strftime('%d %B %Y, %I:%M %p')}\n" + "-" * 60 + "\n\n"
    report += f"Taxpayer: {r['taxpayer_type']} | Preceding years: {r['prior_choice']}\n\n"
    applied = [x for x in r["flags_rows"] if x[COL_SIT] != SIT_NORMAL or x[COL_INC] or x[COL_NOTAX]]
    if applied:
        report += "YEAR-WISE CIRCUMSTANCES:\n" + "\n".join(
            f"- {x['FY']}: {x[COL_SIT]}{' | Indian income > Rs.15L' if x[COL_INC] else ''}"
            f"{' | not liable to tax abroad' if x[COL_NOTAX] else ''}" for x in applied) + "\n\n"
    if r["sensitive"]:
        report += "SENSITIVITY (verify before relying):\n"
        for y in sorted(r["sensitive"]):
            report += f"- {fy_label(y)}: {short_status(r['residency'][y][0])} as shown | " + " | ".join(
                f"{k}: {short_status(v)}" for k, v in r["sensitive"][y].items()) + "\n"
        report += "\n"
    if r["log"]:
        report += "PAIRING LOG:\n" + "\n".join(r["log"]) + "\n\n"
    if r["incompletes"]:
        report += "INCOMPLETE DETAILS (FOLLOW UP WITH ASSESSEE):\n" + "\n".join(f"- {i}" for i in r["incompletes"]) + "\n\n"
    report += tsv + f"\n\nTOTAL DAYS IN INDIA: {r['total']}\n"
    report += "Calculator by: Aman Gautam (8433878823)\n"

    html_report = build_html_report(r, rows)
    stamp = datetime.now().strftime("%Y%m%d")

    st.markdown("### Report")
    b64 = base64.b64encode(html_report.encode("utf-8")).decode()
    components.html(f"""
    <button id="printBtn" style="width:100%;padding:10px 14px;border:0;border-radius:10px;
        background:#203a43;color:#fff;font-weight:600;font-size:14px;cursor:pointer;">
        Print full report</button>
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
    </script>""", height=60)

    d1, d2, d3 = st.columns(3)
    d1.download_button("Report (.txt)", report, file_name=f"Tax_Residency_{stamp}.txt",
                       mime="text/plain", **WIDE)
    d2.download_button("Table (.csv)", df.to_csv(index=False), file_name=f"Tax_Residency_{stamp}.csv",
                       mime="text/csv", **WIDE)
    d3.download_button("Printable report (.html)", html_report, file_name=f"Tax_Residency_{stamp}.html",
                       mime="text/html", **WIDE)

    st.markdown("<p style='text-align:center;color:#8a97a6;font-size:.8rem;margin-top:1.2rem;'>"
                "Section 6(1) · 6(1A) · 6(6) — Income-tax Act 1961 &amp; 2025 · "
                "Made with care by <b>Aman Gautam</b></p>", unsafe_allow_html=True)


if __name__ == "__main__":
    main()
    

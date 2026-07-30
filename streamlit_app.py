import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from collections import defaultdict
import re

# =====================================================================
#  India Tax Residency Calculator  —  Section 6
#  Income-tax Act 1961 (up to FY 2025-26) & Income-tax Act 2025 (FY 2026-27+)
#  By Aman Gautam (8433878823)
# =====================================================================

# ---------------------------------------------------------------------
# FY helpers
# ---------------------------------------------------------------------
def fy_start_year(d):
    return d.year if d.month >= 4 else d.year - 1

def fy_label(year):
    return f"{year}-{year + 1}"

def fy_of(date):
    return fy_label(fy_start_year(date))

def governing_act(year):
    # New Act applies to tax years beginning on/after 1 April 2026 (FY 2026-27+)
    return "IT Act 2025" if year >= 2026 else "IT Act 1961"

def is_nonresident(status):
    return str(status).strip().lower().startswith("non-resident")


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
        s = s.strip()
        if not s:
            continue
        if s.upper() in PLACEHOLDERS:
            invalids.append(s)
            continue
        parsed = None
        for fmt in DATE_FORMATS:
            try:
                parsed = datetime.strptime(s, fmt).date()
                break
            except ValueError:
                continue
        (dates if parsed else invalids).append(parsed if parsed else s)
    return dates, invalids


# ---------------------------------------------------------------------
# Smart pairing
# ---------------------------------------------------------------------
def smart_pair(arrs, deps):
    pairs, used, matches = [], set(), []
    for i, arr in enumerate(arrs):
        if not arr:
            pairs.append((arr, None)); continue
        cands = [(j, dep) for j, dep in enumerate(deps) if dep and dep >= arr and j not in used]
        if cands:
            cands.sort(key=lambda x: ((x[1] - arr).days, x[0]))
            j, dep = cands[0]; used.add(j)
            pairs.append((arr, dep))
            matches.append(f"Arrival {i+1} ({arr.strftime('%d/%m/%Y')}) -> "
                           f"Departure {j+1} ({dep.strftime('%d/%m/%Y')})  |  {(dep-arr).days+1} days")
        else:
            pairs.append((arr, None))
            matches.append(f"Arrival {i+1} ({arr.strftime('%d/%m/%Y')}) -> NO DEPARTURE FOUND")
    for j, dep in enumerate(deps):
        if j not in used and dep:
            pairs.append((None, dep))
            matches.append(f"Departure {j+1} ({dep.strftime('%d/%m/%Y')}) -> NO ARRIVAL")
    return pairs, matches


# ---------------------------------------------------------------------
# Threshold (Sec 6(1) + Explanation 1) -> (threshold_days, only_182)
# ---------------------------------------------------------------------
def basic_threshold(is_citizen, is_pio, is_visit, income_15l, emp, is_crew):
    if is_crew:
        return 182, True
    if emp and is_citizen:
        return 182, True
    if (is_citizen or is_pio) and is_visit:
        return (120, False) if income_15l else (182, True)
    return 60, False


# ---------------------------------------------------------------------
# CORE ENGINE — classify a contiguous dict of {year: days}
# ---------------------------------------------------------------------
def classify_years(full_days, emp_years, is_citizen, is_pio, is_visit,
                   income_15l, not_taxed, is_crew, prior_status, prior_days):
    residency, reasons = {}, {}
    prior_nr = (prior_status == "Non-Resident")

    def days_in(y):
        return full_days.get(y, prior_days)

    def status_of(y):
        if y in residency:
            return residency[y][0]
        return "Non-Resident" if prior_nr else "Resident Ordinarily Resident (ROR)"

    for y in sorted(full_days):
        days = full_days[y]
        emp = y in emp_years
        threshold, only_182 = basic_threshold(is_citizen, is_pio, is_visit, income_15l, emp, is_crew)
        prior4 = sum(days_in(y - i) for i in range(1, 5))
        deemed = is_citizen and income_15l and not_taxed

        is_res = days >= 182 if only_182 else (days >= 182 or (days >= threshold and prior4 >= 365))

        # ---- Not resident under 6(1) ----
        if not is_res:
            if deemed:
                residency[y] = ("Resident but Not Ordinarily Resident (RNOR)", days)
                reasons[y] = "Deemed Resident u/s 6(1A) -> RNOR [6(6)(d)]: citizen, Indian income >Rs.15L, not taxed abroad"
            else:
                if days == 0:
                    rs = "0 days in India"
                elif (not only_182) and days >= threshold:
                    rs = f"{days} days >= {threshold} but prior 4 FYs = {prior4} < 365"
                else:
                    lim = 182 if only_182 else threshold
                    rs = f"{days} days < {lim}"
                residency[y] = ("Non-Resident", days)
                reasons[y] = rs
            continue

        # ---- Resident under 6(1) ----
        base = (f"{days} days >= 182 [Sec 6(1)(a)]" if days >= 182
                else f"{days} days >= {threshold} & prior 4 FYs {prior4} >= 365 [Sec 6(1)(b)]")

        prior7 = sum(days_in(x) for x in range(y - 7, y))
        nr_count = sum(1 for x in range(y - 10, y) if is_nonresident(status_of(x)))
        rnor_a = nr_count >= 9
        rnor_b = prior7 <= 729
        rnor_c = (is_citizen or is_pio) and is_visit and income_15l and 120 <= days < 182

        if rnor_a or rnor_b or rnor_c:
            parts = []
            if rnor_a: parts.append(f"NR in {nr_count}/10 prior FYs [6(6)(a)]")
            if rnor_b: parts.append(f"<=729 days in prior 7 FYs (={prior7}) [6(6)(b)]")
            if rnor_c: parts.append("120-181 days visitor/PIO >Rs.15L [6(6)(c)]")
            residency[y] = ("Resident but Not Ordinarily Resident (RNOR)", days)
            reasons[y] = f"{base} -> RNOR ({' | '.join(parts)})"
        else:
            residency[y] = ("Resident Ordinarily Resident (ROR)", days)
            reasons[y] = f"{base} -> ROR"
    return residency, reasons


def fill_range(days_by_year):
    """Return contiguous {year: days} between min & max year, filling gaps with 0."""
    if not days_by_year:
        return {datetime.now().year - 1: 0}
    lo, hi = min(days_by_year), max(days_by_year)
    return {y: days_by_year.get(y, 0) for y in range(lo, hi + 1)}


# ---------------------------------------------------------------------
# Build day-counts from arrival/departure dates
# ---------------------------------------------------------------------
def days_from_dates(arr_str, dep_str, smart):
    arrs, arr_inv = parse_dates(arr_str)
    deps, dep_inv = parse_dates(dep_str)
    paired, match_log = smart_pair(arrs, deps) if smart else (list(zip(arrs, deps)), [])
    fy_days = defaultdict(int)
    fy_trips = defaultdict(list)
    warnings, seen = [], set()
    for i, (a, d) in enumerate(paired):
        if not a or not d:
            if a or d: warnings.append(f"Trip {i+1}: skipped (missing pair)")
            continue
        if a > d:
            warnings.append(f"Trip {i+1}: invalid (arrival > departure)")
            continue
        trip = f"Trip {i+1}: {a.strftime('%d/%m/%Y')} -> {d.strftime('%d/%m/%Y')} ({(d-a).days+1} days)"
        cur = a
        while cur <= d:
            fy = fy_of(cur); key = (fy, cur)
            if key not in seen:
                fy_days[fy_start_year(cur)] += 1; seen.add(key)
            fy_trips[fy_start_year(cur)].append(trip)
            cur += timedelta(days=1)
    incompletes = [f"Unreadable Arrival Date: {x}" for x in arr_inv]
    incompletes += [f"Unreadable Departure Date: {x}" for x in dep_inv]
    incompletes += [m for m in match_log if "NO DEPARTURE" in m or "NO ARRIVAL" in m]
    return dict(fy_days), dict(fy_trips), warnings, match_log, incompletes


# =====================================================================
# STREAMLIT UI
# =====================================================================
st.set_page_config(page_title="India Tax Residency - Section 6", layout="wide")

st.markdown("""
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
.badge {display:inline-block;padding:3px 10px;border-radius:999px;font-size:.8rem;font-weight:600;}
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="hero">
  <h1>India Tax Residency Calculator</h1>
  <p class="sub">Section 6 &nbsp;·&nbsp; Income-tax Act 1961 (up to FY 2025-26) &amp; Income-tax Act 2025 (FY 2026-27 onward)</p>
  <p class="sub" style="font-size:.85rem;">Deemed residency 6(1A) &nbsp;·&nbsp; 120-day rule &nbsp;·&nbsp; RNOR 6(6)(a)/(b)/(c) &nbsp;·&nbsp; Crew &nbsp;·&nbsp; Smart pairing</p>
  <p class="by">By Aman Gautam &nbsp;·&nbsp; 8433878823</p>
</div>
""", unsafe_allow_html=True)

with st.expander("How residency is decided (both Acts — same tests)", expanded=False):
    st.markdown(
        "- **Resident [6(1)]:** >=182 days in the year, **or** >=60 days in the year and >=365 days across the 4 preceding years.\n"
        "- **Explanation 1:** citizen leaving for employment/as crew, or citizen/PIO on a visit — the 60-day limit becomes 182 (or 120 if Indian income > Rs.15L).\n"
        "- **Deemed Resident [6(1A)]:** Indian citizen, Indian income > Rs.15L, not taxable in any other country -> resident (always RNOR).\n"
        "- **RNOR [6(6)]:** resident is RNOR if (a) NR in 9 of the 10 preceding years, (b) <=729 days in the 7 preceding years, (c) resident under the 120-day visitor rule, or (d) deemed resident.\n\n"
        "The **Income-tax Act, 2025** keeps these exact tests; it governs tax years from **1 April 2026 (FY 2026-27)**. Earlier years stay under the **1961 Act**. "
        "The 9-of-10 and 729-day look-back tests still reach into years governed by the old Act."
    )

for k in ["results", "selected_fy"]:
    st.session_state.setdefault(k, None)

# ------------------------------------------------------------------ #
# 1) INPUT METHOD
# ------------------------------------------------------------------ #
mode = st.radio("Input method", ["Arrival / Departure dates", "FY + Days (direct entry)"],
                horizontal=True)

full_days_input = {}     # {year: days}
fy_trips = {}
warns_list, match_log, incompletes = [], [], []
smart = True

with st.container(border=True):
    if mode == "Arrival / Departure dates":
        c1, c2 = st.columns(2)
        with c1:
            arr = st.text_area("Arrival Dates", height=190,
                               placeholder="01/04/2024  15/07/2024  10/01/2025",
                               help="Space / comma / newline separated. DD/MM/YYYY, DD-MM-YYYY, DD.MM.YYYY, YYYY-MM-DD")
        with c2:
            dep = st.text_area("Departure Dates", height=190,
                               placeholder="10/06/2024  20/08/2024  25/01/2025")
        smart = st.checkbox("Enable Smart Pairing (recommended)", value=True,
                            help="Auto-matches each arrival to the earliest valid departure on/after it")
        fyd, fy_trips, warns_list, match_log, incompletes = days_from_dates(arr, dep, smart)
        full_days_input = fyd
    else:
        st.caption("Enter the financial year and total days in India for each year. Add rows as needed.")
        seed = pd.DataFrame({"FY": ["2024-2025"], "Days": [0]})
        edited = st.data_editor(
            seed, num_rows="dynamic", use_container_width=True, hide_index=True,
            column_config={
                "FY": st.column_config.TextColumn("FY", help="e.g. 2024-2025 or 2024-25 or 2024", width="medium"),
                "Days": st.column_config.NumberColumn("Days in India", min_value=0, max_value=366, step=1, width="small"),
            },
            key="fy_editor",
        )
        for _, row in edited.iterrows():
            m = re.match(r"\s*(\d{4})", str(row.get("FY", "")))
            if not m:
                continue
            y = int(m.group(1))
            try:
                d = int(row.get("Days") or 0)
            except (ValueError, TypeError):
                d = 0
            full_days_input[y] = full_days_input.get(y, 0) + max(0, min(366, d))

# Reactive FY range for downstream controls
if full_days_input:
    detected_min = min(full_days_input)
    detected_max = max(full_days_input)
    fy_list_reactive = [fy_label(y) for y in range(detected_min, detected_max + 1)]
else:
    detected_min = detected_max = None
    fy_list_reactive = []

# ------------------------------------------------------------------ #
# 2) TAXPAYER PROFILE
# ------------------------------------------------------------------ #
with st.container(border=True):
    st.subheader("Taxpayer profile")
    taxpayer_type = st.radio("Taxpayer type",
                             ["Indian Citizen", "Person of Indian Origin (PIO)", "Foreign Citizen (neither)"],
                             index=0, horizontal=True)
    is_citizen = taxpayer_type == "Indian Citizen"
    is_pio = taxpayer_type == "Person of Indian Origin (PIO)"

    p1, p2 = st.columns(2)
    with p1:
        is_visit = st.checkbox("Coming on a visit to India from outside", value=False)
        income_15l = st.checkbox("Indian income (excl. foreign) > Rs.15 Lakh", value=False)
    with p2:
        not_taxed = st.checkbox("Not liable to tax in any foreign country", value=False,
                                disabled=not is_citizen, help="Required for Deemed Residency u/s 6(1A)")
        is_crew = st.checkbox("Crew member of Indian ship (employment)", value=False,
                              help="Applies the 182-day rule. Rule 126 voyage-day exclusion is not auto-computed.")

# ------------------------------------------------------------------ #
# 3) PRIOR YEARS + EMPLOYMENT  (simple, non per-year)
# ------------------------------------------------------------------ #
with st.container(border=True):
    st.subheader("Preceding years & employment")
    e1, e2, e3 = st.columns(3)
    with e1:
        prior_status = st.selectbox(
            "Status in ALL preceding years",
            ["Non-Resident", "Resident"],
            help="Applied to every year before your data window — used for the 9-of-10-years RNOR test.")
    with e2:
        prior_days = st.number_input(
            "Days in India per preceding year", min_value=0, max_value=366, value=0, step=1,
            help="Used for the 365-day (prior 4 FYs) and 729-day (prior 7 FYs) tests.")
    with e3:
        emp_fys = st.multiselect("Employment-abroad FYs", options=fy_list_reactive,
                                 help="FYs the assessee was employed outside India (182-day rule applies)")

# ------------------------------------------------------------------ #
# 4) ACTIONS
# ------------------------------------------------------------------ #
b1, b2 = st.columns(2)
calculate = b1.button("Calculate residency", type="primary", use_container_width=True)
clear = b2.button("Clear results", use_container_width=True)

if clear:
    st.session_state.results = None
    st.session_state.selected_fy = None
    st.rerun()

if calculate:
    if not full_days_input:
        st.error("No data yet — enter dates (or FY + Days) first.")
    else:
        with st.spinner("Applying Section 6 rules..."):
            try:
                full_days = fill_range(full_days_input)
                emp_years = {int(fy.split('-')[0]) for fy in emp_fys}
                residency, reasons = classify_years(
                    full_days, emp_years, is_citizen, is_pio, is_visit,
                    income_15l, not_taxed, is_crew, prior_status, int(prior_days))
                st.session_state.results = {
                    "full_days": full_days, "residency": residency, "reasons": reasons,
                    "total": sum(full_days_input.values()),
                    "warns": "\n".join(warns_list), "fy_trips": fy_trips,
                    "match_log": match_log, "incompletes": incompletes,
                    "prior_status": prior_status, "prior_days": int(prior_days),
                    "mode": mode,
                }
                st.session_state.selected_fy = None
                st.rerun()
            except Exception as e:
                st.error(f"Calculation error: {e}")

# ------------------------------------------------------------------ #
# 5) RESULTS
# ------------------------------------------------------------------ #
STATUS_STYLE = {
    "Non-Resident": ("#e8eef7", "#1e3a5f"),
    "Resident Ordinarily Resident (ROR)": ("#e3f4e1", "#1e5620"),
    "Resident but Not Ordinarily Resident (RNOR)": ("#fdf0d5", "#7a4b00"),
}

if st.session_state.results:
    r = st.session_state.results

    rows = []
    for y in sorted(r["full_days"]):
        status, days = r["residency"].get(y, ("", r["full_days"][y]))
        rows.append({"FY": fy_label(y), "Days": days, "Status": status,
                     "Act": governing_act(y), "Reason": r["reasons"].get(y, "")})
    df = pd.DataFrame(rows)

    # ---- summary metrics ----
    latest = rows[-1] if rows else {"Status": "-", "FY": "-"}
    m1, m2, m3 = st.columns(3)
    with m1:
        with st.container(border=True):
            st.metric("Total days in India", r["total"])
    with m2:
        with st.container(border=True):
            st.metric(f"Latest FY ({latest['FY']})", latest["Status"] or "-")
    with m3:
        with st.container(border=True):
            st.metric("Years analysed", len(rows))

    if r["mode"].startswith("Arrival") and r["match_log"]:
        with st.expander("Smart pairing matches", expanded=False):
            st.code("\n".join(r["match_log"]), language="text")

    if r["warns"]:
        st.warning(f"Warnings:\n{r['warns']}")

    if r["incompletes"]:
        with st.expander("Incomplete details — follow up with assessee", expanded=True):
            for inc in r["incompletes"]:
                st.write(f"- {inc}")

    # ---- colour-coded table ----
    def _row_style(row):
        bg, fg = STATUS_STYLE.get(row["Status"], ("", ""))
        return [f"background-color:{bg}; color:{fg}" if bg else "" for _ in row]

    st.dataframe(
        df.style.apply(_row_style, axis=1),
        use_container_width=True, hide_index=True,
        column_config={
            "FY": st.column_config.TextColumn(width="small"),
            "Days": st.column_config.NumberColumn(width="small"),
            "Status": st.column_config.TextColumn(width="medium"),
            "Act": st.column_config.TextColumn(width="small"),
            "Reason": st.column_config.TextColumn(width="large"),
        },
    )

    # ---- trip drill-down (dates mode only) ----
    if r["mode"].startswith("Arrival") and r["fy_trips"]:
        fy_choice = st.selectbox("View trips counted in FY",
                                 ["—"] + [fy_label(y) for y in sorted(r["full_days"])])
        if fy_choice != "—":
            y = int(fy_choice.split('-')[0])
            trips = r["fy_trips"].get(y, [])
            if trips:
                st.code("\n".join(sorted(set(trips))), language="text")
            else:
                st.info(f"No stay recorded in {fy_choice}.")

    # ---- copy for Excel (TSV) ----
    st.markdown("**Copy for Excel** — click the copy icon on the box below, then paste into a sheet.")
    tsv = "FY\tDays\tStatus\tAct\tReason\n" + "\n".join(
        f"{x['FY']}\t{x['Days']}\t{x['Status']}\t{x['Act']}\t{x['Reason']}" for x in rows)
    st.code(tsv, language="text")

    # ---- downloads ----
    report = "INDIA TAX RESIDENCY REPORT (SECTION 6)\n" + "=" * 60 + "\n"
    report += f"Generated: {datetime.now().strftime('%d %B %Y, %I:%M %p')}\n" + "-" * 60 + "\n\n"
    report += f"Preceding-year assumption: {r['prior_status']}, {r['prior_days']} days/year\n\n"
    if r["match_log"]:
        report += "SMART PAIRING LOG:\n" + "\n".join(r["match_log"]) + "\n\n"
    if r["incompletes"]:
        report += "INCOMPLETE DETAILS (FOLLOW UP WITH ASSESSEE):\n" + \
                  "\n".join(f"- {i}" for i in r["incompletes"]) + "\n\n"
    report += tsv + f"\n\nTOTAL DAYS IN INDIA: {r['total']}\n"
    report += "Calculator by: Aman Gautam (8433878823)\n"
    report += "Compliant with Section 6 — Income-tax Act 1961 & Income-tax Act 2025"

    d1, d2 = st.columns(2)
    d1.download_button("Download report (.txt)", report,
                       file_name=f"Tax_Residency_{datetime.now().strftime('%Y%m%d')}.txt",
                       mime="text/plain", use_container_width=True)
    d2.download_button("Download table (.csv)", df.to_csv(index=False),
                       file_name=f"Tax_Residency_{datetime.now().strftime('%Y%m%d')}.csv",
                       mime="text/csv", use_container_width=True)
else:
    st.info("Fill in the inputs above and click **Calculate residency**.")

st.markdown(
    "<p style='text-align:center;color:#8a97a6;font-size:.8rem;margin-top:1.2rem;'>"
    "Section 6(1) · 6(1A) · 6(6) — Income-tax Act 1961 &amp; 2025 · Not legal/tax advice · "
    "Made with care by <b>Aman Gautam</b></p>", unsafe_allow_html=True)

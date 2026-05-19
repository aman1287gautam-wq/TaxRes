import streamlit as st
from datetime import datetime, timedelta
from collections import defaultdict
import base64
import re

# ─── DATE PARSING ───────────────────────────────────────────────────────────

def parse_dates(text: str):
    dates, invalids = [], []
    for s in text.split():
        s = s.strip()
        if not s or s.upper() == "-N/A-":
            continue
        parsed = None
        for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y"):
            try:
                parsed = datetime.strptime(s, fmt).date()
                break
            except Exception:
                continue
        if parsed:
            dates.append(parsed)
        else:
            dates.append(None)
            invalids.append(s)
    return dates, invalids


def fy_of(date):
    return f"{date.year}-{date.year + 1}" if date.month >= 4 else f"{date.year - 1}-{date.year}"


# ─── SMART PAIRING ──────────────────────────────────────────────────────────

def smart_pair(arrs, deps):
    pairs, used, matches = [], set(), []
    for i, arr in enumerate(arrs):
        if not arr:
            pairs.append((arr, None))
            continue
        cands = [(j, dep) for j, dep in enumerate(deps) if dep and dep >= arr and j not in used]
        if cands:
            cands.sort(key=lambda x: (x[1] - arr, x[0]))
            j, dep = cands[0]
            used.add(j)
            days = (dep - arr).days + 1
            pairs.append((arr, dep))
            matches.append(f"Arrival {i+1} ({arr.strftime('%d/%m/%Y')}) → Departure {j+1} ({dep.strftime('%d/%m/%Y')}) • {days} days")
        else:
            pairs.append((arr, None))
            matches.append(f"Arrival {i+1} ({arr.strftime('%d/%m/%Y')}) → NO DEPARTURE FOUND")
    for j, dep in enumerate(deps):
        if j not in used and dep:
            pairs.append((None, dep))
            matches.append(f"Departure {j+1} ({dep.strftime('%d/%m/%Y')}) → NO ARRIVAL")
    return pairs, matches


# ─── MAIN CALCULATION ───────────────────────────────────────────────────────

def calculate_stay(arr_str, dep_str, exc_fys, smart=False,
                   is_citizen=True, is_pio=False, is_coming_on_visit=False,
                   income_15l=False, not_taxed_abroad=False, is_crew=False,
                   assume_missing_days=0, assume_status="Non-Resident"):

    arrs, arr_invalids = parse_dates(arr_str)
    deps, dep_invalids = parse_dates(dep_str)

    if smart:
        paired, match_log = smart_pair(arrs, deps)
    else:
        paired = list(zip(arrs, deps))
        match_log = []

    fy_days = defaultdict(int)
    fy_trips = defaultdict(list)
    warnings = []
    seen = set()

    for i, pair in enumerate(paired):
        a, d = (pair if isinstance(pair, tuple) else (pair[0], pair[1]))
        if not a or not d:
            if a or d:
                warnings.append(f"Trip {i+1}: skipped (missing pair)")
            continue
        if a > d:
            warnings.append(f"Trip {i+1}: invalid (arrival {a.strftime('%d/%m/%Y')} > departure {d.strftime('%d/%m/%Y')})")
            continue
        days_count = (d - a).days + 1
        trip_str = f"Trip {i+1}: {a.strftime('%d/%m/%Y')} → {d.strftime('%d/%m/%Y')} ({days_count} days)"
        cur = a
        while cur <= d:
            fy = fy_of(cur)
            key = (fy, cur)
            if key not in seen:
                fy_days[fy] += 1
                seen.add(key)
            if trip_str not in fy_trips[fy]:
                fy_trips[fy].append(trip_str)
            cur += timedelta(days=1)

    years_with_data = {int(fy.split('-')[0]) for fy in fy_days}
    if not years_with_data:
        years_with_data = {datetime.now().year - 1}
    min_y, max_y = min(years_with_data), max(years_with_data)
    years_range = range(min_y, max_y + 1)
    full_days = {y: fy_days.get(f"{y}-{y+1}", 0) for y in years_range}
    sorted_fy = [f"{y}-{y+1}" for y in years_range]
    emp_years = {int(fy.split('-')[0]) for fy in exc_fys if fy in sorted_fy}

    residency, reasons = {}, {}
    for y in years_range:
        days = full_days.get(y, 0)
        emp = y in emp_years
        threshold = 60
        if (is_citizen or is_pio) and is_coming_on_visit:
            threshold = 120 if income_15l else 182
        if is_crew:
            threshold = 182
        elif emp and is_citizen:
            threshold = 182

        prior4_days = sum(full_days.get(y - i, assume_missing_days) for i in range(1, 5))
        deemed = is_citizen and income_15l and not_taxed_abroad

        if days == 0 and not deemed:
            residency[y] = ("Non-Resident", 0)
            reasons[y] = "0 days in India"
            continue

        if deemed:
            residency[y] = ("Resident (Deemed u/s 6(1A))", days)
            reasons[y] = "Citizen + Income >₹15L + Not taxed abroad → Deemed Resident"
        else:
            prior_condition = prior4_days >= 365
            is_res = days >= 182 or (days >= threshold and prior_condition)
            if not is_res:
                reason = f"<{threshold} days"
                if days >= threshold:
                    reason = f"≥{threshold} days but prior 4 FYs {prior4_days} < 365"
                residency[y] = ("Non-Resident", days)
                reasons[y] = reason
                continue
            else:
                if days >= 182:
                    base = "≥182 days"
                elif is_crew:
                    base = "≥182 days (Crew)"
                elif emp and is_citizen:
                    base = "≥182 days (Employment abroad)"
                elif (is_citizen or is_pio) and is_coming_on_visit and income_15l:
                    base = "≥120 days (Visitor/PIO + >₹15L) + Prior ≥365"
                elif (is_citizen or is_pio) and is_coming_on_visit:
                    base = "≥182 days (Visitor/PIO ≤₹15L) + Prior ≥365"
                else:
                    base = "≥60 days + Prior 4 FYs ≥365"
                residency[y] = ("Resident", days)
                reasons[y] = base

        prior7_years = list(range(y - 7, y))
        rnor7 = sum(full_days.get(x, assume_missing_days) for x in prior7_years) <= 729
        prior10_years = list(range(y - 10, y))
        non_res10 = sum(1 for x in prior10_years
                        if residency.get(x, (assume_status, assume_missing_days))[0] == "Non-Resident")
        rnor9 = non_res10 >= 9
        rnor_visitor = (is_citizen or is_pio) and income_15l and 120 <= days < 182
        rnor_deemed = deemed
        is_rnor = rnor7 or rnor9 or rnor_visitor or rnor_deemed

        if is_rnor:
            parts = []
            if rnor9:      parts.append("9/10 prior FYs NR")
            if rnor7:      parts.append("≤729 days in prior 7 FYs")
            if rnor_visitor: parts.append("120–181 days + >₹15L (Citizen/PIO)")
            if rnor_deemed:  parts.append("Deemed resident")
            residency[y] = ("Resident but Not Ordinarily Resident (RNOR)", days)
            reasons[y] = f"{reasons[y]} → RNOR ({' | '.join(parts)})"
        else:
            residency[y] = ("Resident Ordinarily Resident (ROR)", days)
            reasons[y] = f"{reasons[y]} → ROR"

    total = sum(fy_days.values())
    warn_msg = "\n".join(warnings) if warnings else ""

    # Build incompletes list
    incompletes = []
    for inv in arr_invalids:
        incompletes.append(f"Invalid Arrival Date: {inv} (unrecognised format)")
    for inv in dep_invalids:
        incompletes.append(f"Invalid Departure Date: {inv} (unrecognised format)")
    for log in match_log:
        if "NO DEPARTURE FOUND" in log:
            m = re.search(r'\((\d{2}/\d{2}/\d{4})\)', log)
            if m:
                try:
                    dt = datetime.strptime(m.group(1), '%d/%m/%Y').date()
                    incompletes.append(f"Unpaired Arrival on {m.group(1)} (FY {fy_of(dt)}) — no departure found")
                except Exception:
                    incompletes.append(f"Unpaired Arrival (parse error: {m.group(1)})")
        elif "NO ARRIVAL" in log:
            m = re.search(r'\((\d{2}/\d{2}/\d{4})\)', log)
            if m:
                try:
                    dt = datetime.strptime(m.group(1), '%d/%m/%Y').date()
                    incompletes.append(f"Unpaired Departure on {m.group(1)} (FY {fy_of(dt)}) — no arrival found")
                except Exception:
                    incompletes.append(f"Unpaired Departure (parse error: {m.group(1)})")
    for w in warnings:
        if "missing pair" in w:
            incompletes.append(f"Trip incomplete: {w}")
        elif "invalid" in w:
            incompletes.append(f"Trip invalid: {w} (correct dates needed)")

    return sorted_fy, fy_days, residency, reasons, total, warn_msg, years_range, fy_trips, match_log, incompletes


# ─── STREAMLIT UI ────────────────────────────────────────────────────────────

st.set_page_config(page_title="India Tax Residency Calculator", layout="wide", page_icon="🗺️")

st.markdown("""
<style>
    .stDataFrame { font-size: 13px; }
    div[data-testid="stExpander"] summary { font-weight: 500; }
    .copy-area textarea { font-family: monospace; font-size: 12px; }
    .status-ror  { color: #3B6D11; font-weight: 600; }
    .status-rnor { color: #854F0B; font-weight: 600; }
    .status-nr   { color: #5F5E5A; font-weight: 600; }
    .status-deemed { color: #3C3489; font-weight: 600; }
    .metric-container { text-align: center; }
</style>
""", unsafe_allow_html=True)

st.title("🗺️ India Tax Residency Calculator")
st.markdown("**IT Act 1961 — Section 6(1), 6(1A) · 120-day · RNOR(c)(d) · Crew · Smart Pairing** &nbsp;|&nbsp; By Aman Gautam (8433878823)")

# Session state init
for key in ["results", "selected_fy", "copy_text"]:
    if key not in st.session_state:
        st.session_state[key] = None

# ─── INPUT SECTION ───────────────────────────────────────────────────────────

with st.expander("📅 Travel Dates", expanded=True):
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Arrival Dates** *(space or line separated)*")
        arr = st.text_area("Arrivals", height=180,
                           placeholder="01/04/2024\n15/07/2024\n10/01/2025",
                           label_visibility="collapsed",
                           help="Formats: DD/MM/YYYY · DD-MM-YYYY · DD.MM.YYYY · DD/MM/YY")
    with col2:
        st.markdown("**Departure Dates** *(space or line separated)*")
        dep = st.text_area("Departures", height=180,
                           placeholder="10/06/2024\n20/08/2024\n25/01/2025",
                           label_visibility="collapsed")
    smart = st.checkbox("✨ Enable Smart Pairing *(auto-matches each arrival to its nearest valid departure)*",
                        value=True)

with st.expander("👤 Taxpayer Profile", expanded=True):
    pcol1, pcol2 = st.columns(2)
    with pcol1:
        taxpayer_type = st.radio(
            "Taxpayer type",
            ["Indian Citizen", "Person of Indian Origin (PIO)", "Foreign Citizen"],
            index=0
        )
        is_citizen = taxpayer_type == "Indian Citizen"
        is_pio = taxpayer_type == "Person of Indian Origin (PIO)"

    with pcol2:
        is_coming_on_visit = st.checkbox("Coming on a visit to India from outside")
        income_15l = st.checkbox("Indian income (excl. foreign) > ₹15 Lakh")
        not_taxed_abroad = st.checkbox(
            "Not liable to tax in any foreign country *(Deemed Residency u/s 6(1A))*",
            disabled=not is_citizen
        )
        is_crew = st.checkbox("Crew member of Indian/foreign ship")

# Employment abroad FYs (only shown after first calculation)
fy_options = st.session_state.results["fy_list"] if st.session_state.results else []
emp_fys = st.multiselect(
    "🏢 Employment Abroad FYs *(182-day threshold applies — only for the FY of departure)*",
    options=fy_options,
    help="Section 6 employment exception applies only in the year of departure for employment abroad."
)

# ─── BUTTONS ─────────────────────────────────────────────────────────────────

bcol1, bcol2 = st.columns([2, 1])
calculate = bcol1.button("⚡ Calculate Full Residency", type="primary", use_container_width=True)
clear     = bcol2.button("🗑️ Clear All", use_container_width=True)

if clear:
    st.session_state.results = None
    st.session_state.selected_fy = None
    st.session_state.copy_text = None
    st.rerun()

if calculate:
    if not arr.strip() or not dep.strip():
        st.error("⚠️ Please enter both arrival and departure dates.")
    else:
        with st.spinner("Applying Section 6 rules…"):
            try:
                fy_list, fy_days, residency, reasons, total, warns, _, fy_trips, match_log, incompletes = calculate_stay(
                    arr, dep, emp_fys, smart, is_citizen, is_pio,
                    is_coming_on_visit, income_15l, not_taxed_abroad, is_crew
                )
                st.session_state.results = {
                    "fy_list": fy_list, "residency": residency, "reasons": reasons,
                    "total": total, "warns": warns, "fy_trips": fy_trips,
                    "match_log": match_log, "incompletes": incompletes
                }
                st.session_state.selected_fy = None
                st.rerun()
            except Exception as e:
                st.error(f"Calculation error: {e}")

# ─── RESULTS ─────────────────────────────────────────────────────────────────

if st.session_state.results:
    r = st.session_state.results
    st.divider()

    # Warnings
    if r["warns"]:
        st.warning(f"⚠️ {r['warns']}")

    # Incomplete details
    if r["incompletes"]:
        with st.expander("⚠️ Incomplete Details — Follow Up with Assessee", expanded=True):
            for inc in r["incompletes"]:
                st.markdown(f"- {inc}")

    # Smart pairing log
    if r["match_log"]:
        with st.expander("🔗 Smart Pairing Log", expanded=False):
            st.code("\n".join(r["match_log"]), language="text")

    # Summary metrics
    statuses = [r["residency"].get(int(fy.split('-')[0]), ("", 0))[0] for fy in r["fy_list"]]
    ror_count  = sum(1 for s in statuses if "ROR" in s and "RNOR" not in s)
    rnor_count = sum(1 for s in statuses if "RNOR" in s)
    nr_count   = sum(1 for s in statuses if s == "Non-Resident")

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Total Days in India", r["total"])
    m2.metric("Years Analysed", len(r["fy_list"]))
    m3.metric("ROR Years", ror_count)
    m4.metric("RNOR Years", rnor_count)
    m5.metric("Non-Resident Years", nr_count)

    st.divider()

    # Results table
    st.subheader("📊 Residency by Financial Year")
    data = []
    for fy in r["fy_list"]:
        y = int(fy.split('-')[0])
        status, days = r["residency"].get(y, ("", 0))
        reason = r["reasons"].get(y, "")
        data.append({"FY": fy, "Days in India": days, "Status": status, "Reason / Basis": reason})

    df_display = st.dataframe(
        data,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key="res_table",
        column_config={
            "FY": st.column_config.TextColumn("Financial Year", width=110),
            "Days in India": st.column_config.NumberColumn("Days in India", width=110),
            "Status": st.column_config.TextColumn("Residency Status", width=260),
            "Reason / Basis": st.column_config.TextColumn("Reason / Legal Basis", width="large"),
        }
    )

    # Trip detail on row select
    selection = df_display["selection"]["rows"]
    if selection:
        row_idx = selection[0]
        fy = data[row_idx]["FY"]
        if st.session_state.selected_fy != fy:
            st.session_state.selected_fy = fy

    if st.session_state.selected_fy:
        fy = st.session_state.selected_fy
        y  = int(fy.split('-')[0])
        trips = r["fy_trips"].get(y, [])
        if trips:
            with st.expander(f"✈️ Trips in {fy} — {len(set(trips))} unique trip(s)", expanded=True):
                st.code("\n".join(sorted(set(trips))), language="text")
        else:
            st.info(f"No stays recorded in {fy}.")

    st.divider()

    # ─── COPY / EXPORT ───────────────────────────────────────────────────────

    st.subheader("📋 Copy & Export")

    tab_table, tab_report, tab_download = st.tabs(["Copy Table", "Full Text Report", "Download"])

    # Tab 1: Copy table
    with tab_table:
        st.markdown("*Copy and paste directly into Excel or any spreadsheet — tab-separated.*")
        tsv = "FY\tDays in India\tStatus\tReason / Basis\n"
        tsv += "\n".join(f"{d['FY']}\t{d['Days in India']}\t{d['Status']}\t{d['Reason / Basis']}" for d in data)
        st.text_area("Table (select all → Ctrl+A, then Ctrl+C)", tsv,
                     height=220, key="tsv_area", label_visibility="visible")

    # Tab 2: Full report
    with tab_report:
        report_lines = [
            "INDIA TAX RESIDENCY REPORT — SECTION 6",
            "=" * 60,
            f"Generated: {datetime.now().strftime('%d %B %Y, %I:%M %p')}",
            "-" * 60,
        ]
        if r["match_log"]:
            report_lines += ["", "SMART PAIRING LOG:"] + [f"  {l}" for l in r["match_log"]]
        if r["incompletes"]:
            report_lines += ["", "INCOMPLETE DETAILS (follow up with assessee):"]
            report_lines += [f"  • {i}" for i in r["incompletes"]]
        report_lines += ["", "FY\t\tDays\tStatus\t\t\t\tReason"]
        report_lines.append("-" * 60)
        for d in data:
            report_lines.append(f"{d['FY']}\t\t{d['Days in India']}\t{d['Status']}\t{d['Reason / Basis']}")
        report_lines += [
            "",
            f"TOTAL DAYS IN INDIA: {r['total']}",
            "",
            "Calculator by: Aman Gautam (8433878823)",
            "100% compliant with Section 6, Finance Act 2020–2025"
        ]
        report_text = "\n".join(report_lines)
        st.text_area("Full Report (select all → Ctrl+A, then Ctrl+C)",
                     report_text, height=320, key="report_area")

    # Tab 3: Download
    with tab_download:
        report_lines2 = [
            "INDIA TAX RESIDENCY REPORT — SECTION 6",
            "=" * 60,
            f"Generated: {datetime.now().strftime('%d %B %Y, %I:%M %p')}",
            "-" * 60,
        ]
        if r["match_log"]:
            report_lines2 += ["", "SMART PAIRING LOG:"] + [f"  {l}" for l in r["match_log"]]
        if r["incompletes"]:
            report_lines2 += ["", "INCOMPLETE DETAILS (follow up with assessee):"]
            report_lines2 += [f"  • {i}" for i in r["incompletes"]]
        report_lines2 += ["", "FY\t\tDays\tStatus\t\t\t\tReason", "-" * 60]
        for d in data:
            report_lines2.append(f"{d['FY']}\t\t{d['Days in India']}\t{d['Status']}\t{d['Reason / Basis']}")
        report_lines2 += ["", f"TOTAL DAYS IN INDIA: {r['total']}", "",
                          "Calculator by: Aman Gautam (8433878823)",
                          "100% compliant with Section 6, Finance Act 2020–2025"]
        dl_text = "\n".join(report_lines2)
        st.download_button(
            label="⬇️ Download .txt Report",
            data=dl_text,
            file_name=f"Tax_Residency_Report_{datetime.now().strftime('%Y%m%d')}.txt",
            mime="text/plain",
            use_container_width=True
        )
        # Also offer CSV
        import csv, io
        csv_buf = io.StringIO()
        writer = csv.DictWriter(csv_buf, fieldnames=["FY", "Days in India", "Status", "Reason / Basis"])
        writer.writeheader()
        writer.writerows(data)
        st.download_button(
            label="⬇️ Download .csv (for Excel)",
            data=csv_buf.getvalue(),
            file_name=f"Tax_Residency_Report_{datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv",
            use_container_width=True
        )

else:
    st.info("👆 Enter arrival and departure dates above, then click **Calculate Full Residency**.")

st.divider()
st.caption("**Section 6(1), 6(1A), 6(6) compliant** · RNOR(c)(d) · Crew · Employment abroad · Made with ♥ by **Aman Gautam** (8433878823)")

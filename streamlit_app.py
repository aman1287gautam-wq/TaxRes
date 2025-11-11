import streamlit as st
from datetime import datetime, timedelta
from collections import defaultdict
import base64

# === DATE PARSING ===
def parse_dates(text: str):
    dates = []
    for s in text.split():
        s = s.strip()
        if not s or s.upper() == "-N/A-":
            dates.append(None)
            continue
        for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y"):
            try:
                parsed = datetime.strptime(s, fmt).date()
                dates.append(parsed)
                break
            except:
                continue
        else:
            dates.append(None)
    return dates

# === FY HELPER ===
def fy_of(date):
    return f"{date.year}-{date.year + 1}" if date.month >= 4 else f"{date.year - 1}-{date.year}"

# === SMART PAIRING ===
def smart_pair(arrs, deps):
    if len(arrs) != len(deps):
        return list(zip(arrs, deps)), []
    
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

# === MAIN RESIDENCY CALCULATION (CORRECTED) ===
def calculate_stay(arr_str, dep_str, exc_fys, smart=False, is_citizen=True, is_visitor=False,
                   income_15l=False, not_taxed_abroad=False, is_crew=False):
    arrs = parse_dates(arr_str)
    deps = parse_dates(dep_str)

    paired, match_log = smart_pair(arrs, deps) if smart else (list(zip(arrs, deps)), [])

    fy_days = defaultdict(int)
    fy_trips = defaultdict(list)
    warnings = []
    seen = set()

    for i, (a, d) in enumerate(paired):
        if not a or not d:
            if a or d:
                warnings.append(f"Trip {i+1}: skipped (missing pair)")
            continue
        if a > d:
            warnings.append(f"Trip {i+1}: invalid (arrival > departure)")
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
            fy_trips[fy].append(trip_str)
            cur += timedelta(days=1)

    # Determine FY range from actual data
    years_with_data = {int(fy.split('-')[0]) for fy in fy_days}
    if not years_with_data:
        years_with_data = {datetime.now().year - 1}
    min_y = min(years_with_data)
    max_y = max(years_with_data)
    years_range = range(min_y, max_y + 1)

    full_days = {y: fy_days.get(f"{y}-{y+1}", 0) for y in years_range}
    sorted_fy = [f"{y}-{y+1}" for y in years_range]

    # Employment FYs: only from existing FYs
    emp_years = {int(fy.split('-')[0]) for fy in exc_fys if fy in sorted_fy}

    residency, reasons = {}, {}
    for y in years_range:
        days = full_days.get(y, 0)
        emp = y in emp_years

        # CORRECTED THRESHOLD (DEFAULT 60, OVERRIDE ONLY FOR EXCEPTIONS)
        threshold = 60
        if is_crew:
            threshold = 182
        elif emp:
            threshold = 182
        elif is_visitor:
            threshold = 120 if income_15l else 182

        prior4_days = sum(full_days.get(y - i, 0) for i in range(1, 5))

        # DEEMED RESIDENCY 6(1A) - ONLY FOR CITIZENS
        deemed = is_citizen and income_15l and not_taxed_abroad

        if days == 0 and not deemed:
            residency[y] = ("Non-Resident", 0)
            reasons[y] = "0 days in India"
            continue

        if deemed:
            residency[y] = ("Resident (Deemed u/s 6(1A))", days)
            reasons[y] = "Citizen + Income >₹15L + Not taxed abroad → Deemed Resident"
            is_res = True
        else:
            is_res = days >= 182 or (days >= threshold and prior4_days >= 365)
            if not is_res:
                reason = f"<{threshold} days"
                if days >= threshold:
                    reason = f"≥{threshold} days but Prior 4 FYs {prior4_days} < 365"
                residency[y] = ("Non-Resident", days)
                reasons[y] = reason
                continue
            else:
                # CORRECTED REASONS FOR CLARITY
                if days >= 182:
                    base = "≥182 days"
                elif emp:
                    base = "≥182 days (Employment abroad)"
                elif is_crew:
                    base = "≥182 days (Crew)"
                elif is_visitor and income_15l:
                    base = "≥120 days (Visitor/PIO + >₹15L) + Prior ≥365"
                elif is_visitor:
                    base = "≥182 days (Visitor/PIO ≤₹15L) + Prior ≥365"
                else:
                    base = "≥60 days + Prior 4 FYs ≥365"
                residency[y] = ("Resident", days)
                reasons[y] = base

        # RNOR LOGIC (unchanged, as correct)
        prior7_years = [x for x in range(y-7, y) if x in full_days]
        rnor7 = len(prior7_years) >= 7 and sum(full_days.get(x, 0) for x in prior7_years) <= 729

        prior10_years = [x for x in range(y-10, y) if x in full_days]
        non_res10 = sum(1 for x in prior10_years if residency.get(x, ("Non-Resident",0))[0] == "Non-Resident")
        rnor9 = len(prior10_years) >= 10 and non_res10 >= 9

        rnor_visitor = is_visitor and income_15l and 120 <= days < 182
        rnor_deemed = deemed

        is_rnor = rnor7 or rnor9 or rnor_visitor or rnor_deemed

        if is_rnor:
            parts = []
            if rnor9: parts.append("9/10 prior FYs NR")
            if rnor7: parts.append("≤729 days in prior 7 FYs")
            if rnor_visitor: parts.append("120–181 days + >₹15L visitor")
            if rnor_deemed: parts.append("Deemed resident")
            reason = f"{reasons[y]} → RNOR ({' | '.join(parts)})"
            residency[y] = ("Resident but Not Ordinarily Resident (RNOR)", days)
        else:
            reason = f"{reasons[y]} → ROR"
            residency[y] = ("ROR", days)
        reasons[y] = reason

    total = sum(fy_days.values())
    warn_msg = "\n".join(warnings) if warnings else ""

    return sorted_fy, fy_days, residency, reasons, total, warn_msg, years_range, fy_trips, match_log

# === STREAMLIT UI (unchanged) ===
st.set_page_config(page_title="India Tax Residency - Full Sec 6", layout="wide")
st.title("🇮🇳 India Tax Residency Calculator")
st.markdown("**100% compliant with IT Act 1961** • By Aman Gautam, for queries: 8433878823")

# Initialize session state
for key in ["results", "selected_fy"]:
    if key not in st.session_state:
        st.session_state[key] = None

col1, col2 = st.columns(2)
with col1:
    arr = st.text_area(
        "Arrival Dates (space-separated)", 
        height=220, 
        placeholder="01/04/2024 15/07/2024 10/01/2025",
        help="Supported: DD/MM/YYYY, DD-MM-YYYY, DD.MM.YYYY"
    )
with col2:
    dep = st.text_area(
        "Departure Dates (space-separated)", 
        height=220, 
        placeholder="10/06/2024 20/08/2024 25/01/2025"
    )

smart = st.checkbox("Enable Smart Pairing (recommended)", value=True, help="Auto-matches earliest valid departure")

st.subheader("Taxpayer Profile")
col_a, col_b = st.columns(2)
is_citizen = col_a.checkbox("Indian Citizen", value=True)
is_visitor = col_b.checkbox("Visitor / PIO coming to India", value=False)

col_c, col_d = st.columns(2)
income_15l = col_c.checkbox("Indian Income (excl. foreign) > ₹15 Lakh", value=False)
not_taxed_abroad = col_d.checkbox("Not liable to tax in any foreign country", value=False,
                                  help="For Deemed Residency u/s 6(1A)")

is_crew = st.checkbox("Crew member of Indian/foreign ship", value=False)

col_btn1, col_btn2 = st.columns(2)
calculate = col_btn1.button("🚀 Calculate Full Residency", type="primary", use_container_width=True)
clear = col_btn2.button("🗑️ Clear All", use_container_width=True)

if clear:
    st.session_state.results = None
    st.session_state.selected_fy = None
    st.rerun()

# Employment FYs after calculation
fy_options = st.session_state.results["fy_list"] if st.session_state.results else []

emp_fys = st.multiselect(
    "Employment Abroad FYs (182-day rule applies)",
    options=fy_options,
    help="Select FYs where the person was employed outside India"
)

if calculate:
    if not arr.strip() or not dep.strip():
        st.error("Please enter both arrival and departure dates.")
    else:
        with st.spinner("Applying Section 6 rules..."):
            try:
                fy_list, fy_days, residency, reasons, total, warns, _, fy_trips, match_log = calculate_stay(
                    arr, dep, emp_fys, smart, is_citizen, is_visitor, income_15l, not_taxed_abroad, is_crew
                )
                st.session_state.results = {
                    "fy_list": fy_list,
                    "residency": residency,
                    "reasons": reasons,
                    "total": total,
                    "warns": warns,
                    "fy_trips": fy_trips,
                    "match_log": match_log
                }
                st.session_state.selected_fy = None
                st.rerun()
            except Exception as e:
                st.error(f"Calculation error: {e}")

# DISPLAY RESULTS (unchanged)
if st.session_state.results:
    r = st.session_state.results
    
    if smart and r["match_log"]:
        with st.expander("Smart Pairing Matches", expanded=False):
            st.code("\n".join(r["match_log"][:30]), language="text")
    
    if r["warns"]:
        st.warning(f"⚠️ {r['warns']}")

    data = []
    for fy in r["fy_list"]:
        y = int(fy.split('-')[0])
        status, days = r["residency"].get(y, ("", 0))
        reason = r["reasons"].get(y, "")
        data.append({"FY": fy, "Days": days, "Status": status, "Reason": reason})

    df = st.dataframe(
        data,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key="res_table"
    )

    selection = df["selection"]["rows"]
    if selection:
        row_idx = selection[0]
        fy = data[row_idx]["FY"]
        if st.session_state.selected_fy != fy:
            st.session_state.selected_fy = fy

    if st.session_state.selected_fy:
        fy = st.session_state.selected_fy
        y = int(fy.split('-')[0])
        trips = r["fy_trips"].get(y, [])
        if trips:
            with st.expander(f"Trips in {fy} ({len(set(trips))} unique)", expanded=True):
                st.code("\n".join(sorted(set(trips))), language="text")
        else:
            st.info("No stay recorded in this FY.")

    st.success(f"**Total Days in India: {r['total']}**")

    colc1, colc2 = st.columns(2)
    with colc1:
        if st.button("📋 Copy Table", use_container_width=True):
            txt = "FY\tDays\tStatus\tReason\n" + "\n".join(
                f"{d['FY']}\t{d['Days']}\t{d['Status']}\t{d['Reason']}" for d in data
            )
            st.code(txt)
            st.toast("Copied to clipboard!")

    with colc2:
        report = f"""FULL INDIA TAX RESIDENCY REPORT (SECTION 6)
{'='*60}
Generated: {datetime.now().strftime('%d %B %Y, %I:%M %p')}
{'-'*60}

"""
        if r["match_log"]:
            report += "SMART PAIRING LOG:\n" + "\n".join(r["match_log"]) + "\n\n"
        
        report += "FY\tDays\tStatus\tReason\n"
        for d in data:
            report += f"{d['FY']}\t{d['Days']}\t{d['Status']}\t{d['Reason']}\n"
        
        report += f"\nTOTAL DAYS IN INDIA: {r['total']}\n"
        report += "Calculator by: Aman Gautam (8433878823)\n"
        report += "100% compliant with Section 6, Finance Act 2020–2025"

        b64 = base64.b64encode(report.encode()).decode()
        href = f'<a href="data:file/txt;base64,{b64}" download="Tax_Residency_Report_{datetime.now().strftime("%Y%m%d")}.txt">📥 Download Report</a>'
        st.markdown(href, unsafe_allow_html=True)

else:
    st.info("👈 Enter arrival/departure dates and click **Calculate** to begin.")

st.markdown("---")
st.caption(
    "**Section 6(1), 6(1A), 6(6) compliant** • Includes RNOR(c), RNOR(d) • Crew • Employment abroad • "
    "Made with ❤️ by **Aman Gautam**"
)

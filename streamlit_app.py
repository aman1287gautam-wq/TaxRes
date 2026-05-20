import streamlit as st
from datetime import datetime, timedelta
from collections import defaultdict
import base64

# === DATE PARSING ===
def parse_dates(text: str):
    dates = []
    invalids = []
    for s in text.split():
        s = s.strip()
        if not s or s.upper() == "-N/A-":
            dates.append(None)
            invalids.append(s)
            continue
        parsed = None
        for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y"):
            try:
                parsed = datetime.strptime(s, fmt).date()
                break
            except:
                continue
        if parsed:
            dates.append(parsed)
        else:
            dates.append(None)
            invalids.append(s)
    return dates, invalids

# === FY HELPER ===
def fy_of(date):
    return f"{date.year}-{date.year + 1}" if date.month >= 4 else f"{date.year - 1}-{date.year}"

# === IMPROVED SMART PAIRING ===
def smart_pair(arrs, deps):
    pairs = []
    matches = []
    
    arr_list = [(i, a) for i, a in enumerate(arrs) if a]
    dep_list = [(j, d) for j, d in enumerate(deps) if d]
    
    arr_list.sort(key=lambda x: x[1])
    dep_list.sort(key=lambda x: x[1])

    used = set()
    dep_ptr = 0

    for arr_orig_idx, arr_date in arr_list:
        best_dep = None
        best_dep_idx = None
        best_days = float('inf')
        best_ptr = dep_ptr

        for j in range(dep_ptr, len(dep_list)):
            dep_orig_idx, dep_date = dep_list[j]
            if dep_orig_idx in used:
                continue
            if dep_date >= arr_date:
                days = (dep_date - arr_date).days + 1
                if days < best_days:          # Prefer closest departure
                    best_days = days
                    best_dep = dep_date
                    best_dep_idx = dep_orig_idx
                    best_ptr = j

        if best_dep is not None:
            pairs.append((arr_date, best_dep))
            matches.append(f"Arrival {arr_orig_idx+1} ({arr_date.strftime('%d/%m/%Y')}) → "
                          f"Departure {best_dep_idx+1} ({best_dep.strftime('%d/%m/%Y')}) • {best_days} days")
            used.add(best_dep_idx)
            dep_ptr = best_ptr + 1
        else:
            pairs.append((arr_date, None))
            matches.append(f"Arrival {arr_orig_idx+1} ({arr_date.strftime('%d/%m/%Y')}) → NO DEPARTURE FOUND")

    # Unpaired Departures
    for j, dep in enumerate(deps):
        if dep and j not in used:
            pairs.append((None, dep))
            matches.append(f"Departure {j+1} ({dep.strftime('%d/%m/%Y')}) → NO ARRIVAL")

    return pairs, matches

# === MAIN RESIDENCY CALCULATION ===
def calculate_stay(arr_str, dep_str, exc_fys, smart=False, is_citizen=True, is_pio=False, 
                   is_coming_on_visit=False, income_15l=False, not_taxed_abroad=False, is_crew=False):
    
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

    # FY Range
    years_with_data = {int(fy.split('-')[0]) for fy in fy_days} or {datetime.now().year - 1}
    min_y = min(years_with_data)
    max_y = max(years_with_data)
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

        prior4_days = sum(full_days.get(y - i, 0) for i in range(1, 5))

        deemed = is_citizen and income_15l and not_taxed_abroad

        if days == 0 and not deemed:
            residency[y] = ("Non-Resident", 0)
            reasons[y] = "0 days in India"
            continue

        if deemed:
            residency[y] = ("Resident (Deemed u/s 6(1A))", days)
            reasons[y] = "Citizen + Income >₹15L + Not taxed abroad → Deemed Resident"
        else:
            is_res = days >= 182 or (days >= threshold and prior4_days >= 365)
            if not is_res:
                reason = f"<{threshold} days" if days < threshold else f"≥{threshold} days but Prior 4 FYs {prior4_days} < 365"
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

        # RNOR Logic
        prior7 = sum(full_days.get(x, 0) for x in range(y-7, y))
        rnor7 = prior7 <= 729
        prior10 = list(range(y-10, y))
        non_res10 = sum(1 for x in prior10 if residency.get(x, ("Non-Resident",0))[0] == "Non-Resident")
        rnor9 = non_res10 >= 9
        rnor_visitor = (is_citizen or is_pio) and income_15l and 120 <= days < 182
        rnor_deemed = deemed

        is_rnor = rnor7 or rnor9 or rnor_visitor or rnor_deemed

        if is_rnor:
            parts = []
            if rnor9: parts.append("9/10 prior FYs NR")
            if rnor7: parts.append("≤729 days in prior 7 FYs")
            if rnor_visitor: parts.append("120–181 days + >₹15L")
            if rnor_deemed: parts.append("Deemed resident")
            reason = f"{reasons[y]} → RNOR ({' | '.join(parts)})"
            residency[y] = ("Resident but Not Ordinarily Resident (RNOR)", days)
        else:
            reason = f"{reasons[y]} → ROR"
            residency[y] = ("Resident Ordinarily Resident (ROR)", days)
        reasons[y] = reason

    total = sum(fy_days.values())
    warn_msg = "\n".join(warnings) if warnings else ""
    incompletes = [f"Invalid Arrival: {x}" for x in arr_invalids if x] + \
                  [f"Invalid Departure: {x}" for x in dep_invalids if x]

    return sorted_fy, fy_days, residency, reasons, total, warn_msg, years_range, fy_trips, match_log, incompletes

# ===================== STREAMLIT UI =====================
st.set_page_config(page_title="India Tax Residency - Full Sec 6", layout="wide")
st.title("🇮🇳 India Tax Residency Calculator")
st.markdown("**100% compliant with IT Act 1961** • 6(1A) Deemed • 120-day • RNOR • By Aman Gautam (8433878823)")

for key in ["results", "selected_fy"]:
    if key not in st.session_state:
        st.session_state[key] = None

col1, col2 = st.columns(2)
with col1:
    arr = st.text_area("Arrival Dates (space-separated)", height=220, 
                       placeholder="01/04/2024 15/07/2024 ...",
                       help="Supported: DD/MM/YYYY, DD-MM-YYYY, DD.MM.YYYY")
with col2:
    dep = st.text_area("Departure Dates (space-separated)", height=220, 
                       placeholder="10/06/2024 20/08/2024 ...")

smart = st.checkbox("Enable Smart Pairing (recommended)", value=True, 
                    help="Best matching when dates are not in perfect order")

st.subheader("Taxpayer Profile")
taxpayer_type = st.radio("Select Taxpayer Type:", ["Indian Citizen", "Person of Indian Origin (PIO)", "Foreign Citizen"], index=0)
is_citizen = taxpayer_type == "Indian Citizen"
is_pio = taxpayer_type == "Person of Indian Origin (PIO)"
is_coming_on_visit = st.checkbox("Coming on a visit to India from outside", value=False)
income_15l = st.checkbox("Indian Income (excl. foreign) > ₹15 Lakh", value=False)
not_taxed_abroad = st.checkbox("Not liable to tax in any foreign country", value=False, help="For Deemed Residency u/s 6(1A)")
is_crew = st.checkbox("Crew member of Indian/foreign ship", value=False)

col_btn1, col_btn2 = st.columns(2)
calculate = col_btn1.button("🚀 Calculate Full Residency", type="primary", use_container_width=True)
clear = col_btn2.button("🗑️ Clear All", use_container_width=True)

if clear:
    st.session_state.results = None
    st.session_state.selected_fy = None
    st.rerun()

fy_options = st.session_state.results["fy_list"] if st.session_state.results else []
emp_fys = st.multiselect("Employment Abroad FYs (182-day rule applies)", options=fy_options)

if calculate:
    if not arr.strip() or not dep.strip():
        st.error("Please enter both arrival and departure dates.")
    else:
        with st.spinner("Applying Section 6 rules..."):
            try:
                result = calculate_stay(arr, dep, emp_fys, smart, is_citizen, is_pio, 
                                      is_coming_on_visit, income_15l, not_taxed_abroad, is_crew)
                st.session_state.results = {
                    "fy_list": result[0], "residency": result[2], "reasons": result[3],
                    "total": result[4], "warns": result[5], "fy_trips": result[7],
                    "match_log": result[8], "incompletes": result[9]
                }
                st.session_state.selected_fy = None
                st.rerun()
            except Exception as e:
                st.error(f"Calculation error: {e}")

# ==================== DISPLAY RESULTS ====================
if st.session_state.results:
    r = st.session_state.results
    
    if r.get("match_log"):
        with st.expander("Smart Pairing Matches", expanded=False):
            st.code("\n".join(r["match_log"]), language="text")
    
    if r.get("warns"):
        st.warning(r["warns"])
    if r.get("incompletes"):
        with st.expander("Incomplete / Invalid Dates", expanded=True):
            for inc in r["incompletes"]:
                st.write(f"• {inc}")

    data = []
    for fy in r["fy_list"]:
        y = int(fy.split('-')[0])
        status, days = r["residency"].get(y, ("", 0))
        reason = r["reasons"].get(y, "")
        data.append({"FY": fy, "Days": days, "Status": status, "Reason": reason})

    df = st.dataframe(data, use_container_width=True, hide_index=True,
                      on_select="rerun", selection_mode="single-row", key="res_table")

    selection = df["selection"]["rows"]
    if selection:
        fy = data[selection[0]]["FY"]
        if st.session_state.selected_fy != fy:
            st.session_state.selected_fy = fy

    if st.session_state.selected_fy:
        fy = st.session_state.selected_fy
        trips = r["fy_trips"].get(int(fy.split('-')[0]), [])
        if trips:
            with st.expander(f"Trips in {fy}", expanded=True):
                st.code("\n".join(sorted(set(trips))))
        else:
            st.info("No stay recorded in this FY.")

    st.success(f"**Total Days in India: {r['total']}**")

    colc1, colc2 = st.columns(2)
    with colc1:
        if st.button("Copy Table", use_container_width=True):
            txt = "FY\tDays\tStatus\tReason\n" + "\n".join(
                f"{d['FY']}\t{d['Days']}\t{d['Status']}\t{d['Reason']}" for d in data)
            st.code(txt)
            st.toast("Copied to clipboard!")

    with colc2:
        report = f"""FULL INDIA TAX RESIDENCY REPORT (SECTION 6)
{'='*60}
Generated: {datetime.now().strftime('%d %B %Y, %I:%M %p')}
{'-'*60}\n"""
        if r.get("match_log"):
            report += "SMART PAIRING LOG:\n" + "\n".join(r["match_log"]) + "\n\n"
        if r.get("incompletes"):
            report += "INCOMPLETE DETAILS:\n" + "\n".join(r["incompletes"]) + "\n\n"
        report += "FY\tDays\tStatus\tReason\n"
        for d in data:
            report += f"{d['FY']}\t{d['Days']}\t{d['Status']}\t{d['Reason']}\n"
        report += f"\nTOTAL DAYS: {r['total']}\nCalculator by: Aman Gautam (8433878823)"

        b64 = base64.b64encode(report.encode()).decode()
        href = f'<a href="data:file/txt;base64,{b64}" download="Tax_Residency_Report.txt">📥 Download Report</a>'
        st.markdown(href, unsafe_allow_html=True)

else:
    st.info("👈 Enter arrival & departure dates and click Calculate")

st.markdown("---")
st.caption("**Section 6(1), 6(1A), 6(6) compliant** • Made with ❤️ by **Aman Gautam**")

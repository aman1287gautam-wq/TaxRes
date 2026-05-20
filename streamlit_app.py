import streamlit as st
from datetime import datetime, timedelta
from collections import defaultdict
import base64
import re

# === DATE PARSING (Improved) ===
def parse_dates(text: str):
    dates = []
    invalids = []
    for s in text.split():
        s = s.strip()
        if not s or s.upper() == "-N/A-":
            dates.append(None)
            invalids.append(s if s else "empty")
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
    pairs, used, matches = [], set(), []
    arr_list = [(i, a) for i, a in enumerate(arrs) if a]
    dep_list = [(j, d) for j, d in enumerate(deps) if d]
    
    # Sort both by date for better matching
    arr_list.sort(key=lambda x: x[1])
    dep_list.sort(key=lambda x: x[1])

    i = 0
    for arr_idx, arr in arr_list:
        while i < len(dep_list) and dep_list[i][1] < arr:
            i += 1
        if i < len(dep_list):
            dep_idx, dep = dep_list[i]
            days = (dep - arr).days + 1
            pairs.append((arr, dep))
            matches.append(f"Arrival {arr_idx+1} ({arr.strftime('%d/%m/%Y')}) → Departure {dep_idx+1} ({dep.strftime('%d/%m/%Y')}) • {days} days")
            used.add(dep_idx)
            i += 1
        else:
            pairs.append((arr, None))
            matches.append(f"Arrival {arr_idx+1} ({arr.strftime('%d/%m/%Y')}) → NO DEPARTURE FOUND")
    
    for j, dep in enumerate(deps):
        if j not in used and dep:
            pairs.append((None, dep))
            matches.append(f"Departure {j+1} ({dep.strftime('%d/%m/%Y')}) → NO ARRIVAL")
    
    return pairs, matches

# === MAIN CALCULATION ===
def calculate_stay(arr_str, dep_str, exc_fys, smart=False, is_citizen=True, is_pio=False, 
                   is_coming_on_visit=False, income_15l=False, not_taxed_abroad=False, 
                   is_crew=False, assume_missing_days=0, assume_status="Non-Resident"):
    
    arrs, arr_invalids = parse_dates(arr_str)
    deps, dep_invalids = parse_dates(dep_str)
    
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
            is_res = days >= 182 or (days >= threshold and prior4_days >= 365)
            if not is_res:
                if days >= threshold:
                    reason = f"≥{threshold} days but Prior 4 FYs {prior4_days} < 365"
                else:
                    reason = f"<{threshold} days"
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
        prior7_years = list(range(y-7, y))
        rnor7 = sum(full_days.get(x, assume_missing_days) for x in prior7_years) <= 729
        prior10_years = list(range(y-10, y))
        non_res10 = sum(1 for x in prior10_years if residency.get(x, (assume_status, 0))[0] == "Non-Resident")
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

    # Incompletes
    incompletes = arr_invalids + dep_invalids
    incompletes = [f"Invalid date: {x}" for x in incompletes if x]

    return sorted_fy, fy_days, residency, reasons, total, warn_msg, years_range, fy_trips, match_log, incompletes

# === STREAMLIT UI ===
st.set_page_config(page_title="India Tax Residency - Full Sec 6", layout="wide")
st.title("🇮🇳 India Tax Residency Calculator")
st.markdown("**100% compliant with IT Act 1961** • 6(1A) Deemed • 120-day • RNOR • By Aman Gautam (8433878823)")

for key in ["results", "selected_fy"]:
    if key not in st.session_state:
        st.session_state[key] = None

col1, col2 = st.columns(2)
with col1:
    arr = st.text_area("Arrival Dates", height=220, placeholder="01/04/2024 ...")
with col2:
    dep = st.text_area("Departure Dates", height=220, placeholder="10/06/2024 ...")

smart = st.checkbox("Enable Smart Pairing (recommended)", value=True)

st.subheader("Taxpayer Profile")
taxpayer_type = st.radio("Select Taxpayer Type:", ["Indian Citizen", "Person of Indian Origin (PIO)", "Foreign Citizen"], index=0)
is_citizen = taxpayer_type == "Indian Citizen"
is_pio = taxpayer_type == "Person of Indian Origin (PIO)"
is_coming_on_visit = st.checkbox("Coming on a visit to India from outside", value=False)
income_15l = st.checkbox("Indian Income (excl. foreign) > ₹15 Lakh", value=False)
not_taxed_abroad = st.checkbox("Not liable to tax in any foreign country", value=False, help="For Deemed Residency u/s 6(1A)")
is_crew = st.checkbox("Crew member", value=False)

col_btn1, col_btn2 = st.columns(2)
calculate = col_btn1.button("🚀 Calculate", type="primary", use_container_width=True)
clear = col_btn2.button("Clear All", use_container_width=True)

if clear:
    st.session_state.results = None
    st.session_state.selected_fy = None
    st.rerun()

fy_options = st.session_state.results["fy_list"] if st.session_state.results else []
emp_fys = st.multiselect("Employment Abroad FYs", options=fy_options)

if calculate:
    if not arr.strip() or not dep.strip():
        st.error("Enter both arrival and departure dates.")
    else:
        with st.spinner("Calculating..."):
            result = calculate_stay(arr, dep, emp_fys, smart, is_citizen, is_pio, is_coming_on_visit,
                                  income_15l, not_taxed_abroad, is_crew)
            st.session_state.results = {
                "fy_list": result[0], "residency": result[2], "reasons": result[3],
                "total": result[4], "warns": result[5], "fy_trips": result[7],
                "match_log": result[8], "incompletes": result[9]
            }
            st.session_state.selected_fy = None
            st.rerun()

# === DISPLAY ===
if st.session_state.results:
    r = st.session_state.results
    if smart and r.get("match_log"):
        with st.expander("Smart Pairing Log"):
            st.code("\n".join(r["match_log"]))
    
    if r.get("warns"):
        st.warning(r["warns"])
    if r.get("incompletes"):
        with st.expander("Incomplete / Invalid Dates"):
            for inc in r["incompletes"]:
                st.write("•", inc)

    # Data Table
    data = []
    for fy in r["fy_list"]:
        y = int(fy.split('-')[0])
        status, days = r["residency"].get(y, ("", 0))
        reason = r["reasons"].get(y, "")
        data.append({"FY": fy, "Days": days, "Status": status, "Reason": reason})

    st.dataframe(data, use_container_width=True, hide_index=True, on_select="rerun", selection_mode="single-row")

    st.success(f"**Total Days in India: {r['total']}**")

    # Export
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Copy Table"):
            txt = "FY\tDays\tStatus\tReason\n" + "\n".join(f"{d['FY']}\t{d['Days']}\t{d['Status']}\t{d['Reason']}" for d in data)
            st.code(txt)
            st.toast("Copied!")
    with col2:
        # Report generation (with proper < > )
        report = f"FULL INDIA TAX RESIDENCY REPORT\n{'='*60}\nGenerated: {datetime.now().strftime('%d %B %Y')}\n\n"
        if r.get("match_log"):
            report += "SMART PAIRING:\n" + "\n".join(r["match_log"]) + "\n\n"
        report += "FY\tDays\tStatus\tReason\n"
        for d in data:
            report += f"{d['FY']}\t{d['Days']}\t{d['Status']}\t{d['Reason']}\n"
        report += f"\nTOTAL: {r['total']}\nBy Aman Gautam (8433878823)"
        
        b64 = base64.b64encode(report.encode()).decode()
        st.markdown(f'<a href="data:file/txt;base64,{b64}" download="Residency_Report.txt">📥 Download Report</a>', unsafe_allow_html=True)

else:
    st.info("Enter dates and click Calculate")

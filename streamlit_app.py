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

# === IMPROVED SMART PAIRING (Best Version) ===
def smart_pair(arrs, deps):
    pairs = []
    matches = []
    
    # Filter valid dates with original indices
    valid_arr = [(i, d) for i, d in enumerate(arrs) if d]
    valid_dep = [(j, d) for j, d in enumerate(deps) if d]
    
    # Sort both by actual date
    valid_arr.sort(key=lambda x: x[1])
    valid_dep.sort(key=lambda x: x[1])
    
    # Pair sorted arrivals with sorted departures
    max_len = max(len(valid_arr), len(valid_dep))
    
    for k in range(max_len):
        if k < len(valid_arr):
            arr_orig_idx, arr_date = valid_arr[k]
            if k < len(valid_dep):
                dep_orig_idx, dep_date = valid_dep[k]
                if arr_date > dep_date:
                    matches.append(f"Arrival {arr_orig_idx+1} ({arr_date.strftime('%d/%m/%Y')}) → INVALID (after Departure)")
                    pairs.append((arr_date, None))
                else:
                    days = (dep_date - arr_date).days + 1
                    pairs.append((arr_date, dep_date))
                    matches.append(f"Arrival {arr_orig_idx+1} ({arr_date.strftime('%d/%m/%Y')}) → "
                                  f"Departure {dep_orig_idx+1} ({dep_date.strftime('%d/%m/%Y')}) • {days} days")
            else:
                pairs.append((arr_date, None))
                matches.append(f"Arrival {arr_orig_idx+1} ({arr_date.strftime('%d/%m/%Y')}) → NO DEPARTURE FOUND")
        elif k < len(valid_dep):
            dep_orig_idx, dep_date = valid_dep[k]
            pairs.append((None, dep_date))
            matches.append(f"Departure {dep_orig_idx+1} ({dep_date.strftime('%d/%m/%Y')}) → NO ARRIVAL")
    
    return pairs, matches

# === MAIN CALCULATION ===
def calculate_stay(arr_str, dep_str, exc_fys, smart=False, is_citizen=True, is_pio=False, 
                   is_coming_on_visit=False, income_15l=False, not_taxed_abroad=False, is_crew=False):
    
    arrs, arr_invalids = parse_dates(arr_str)
    deps, dep_invalids = parse_dates(dep_str)
    
    if smart:
        paired, match_log = smart_pair(arrs, deps)
    else:
        paired = list(zip(arrs, deps))
        match_log = [f"Direct Pair {i+1}: Arrival → Departure" for i in range(len(paired))]

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

    # FY & Residency Logic (same as before)
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

        # RNOR
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


# ====================== STREAMLIT UI ======================
st.set_page_config(page_title="India Tax Residency - Full Sec 6", layout="wide")
st.title("🇮🇳 India Tax Residency Calculator")
st.markdown("**100% compliant with IT Act 1961** • By Aman Gautam (8433878823)")

for key in ["results", "selected_fy"]:
    if key not in st.session_state:
        st.session_state[key] = None

col1, col2 = st.columns(2)
with col1:
    arr = st.text_area("Arrival Dates (space-separated)", height=220, placeholder="01/04/2024 ...")
with col2:
    dep = st.text_area("Departure Dates (space-separated)", height=220, placeholder="10/06/2024 ...")

smart = st.checkbox("Enable Smart Pairing (recommended for messy lists)", value=True)

# ... [Rest of your Taxpayer Profile and buttons remain the same] ...
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
                fy_list, fy_days, residency, reasons, total, warns, _, fy_trips, match_log, incompletes = calculate_stay(
                    arr, dep, emp_fys, smart, is_citizen, is_pio, is_coming_on_visit, income_15l, not_taxed_abroad, is_crew
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

# Display Results (same as your previous version)
if st.session_state.results:
    r = st.session_state.results
    if r.get("match_log"):
        with st.expander("Smart Pairing Matches", expanded=True):
            st.code("\n".join(r["match_log"]), language="text")
    
    if r.get("warns"):
        st.warning(r["warns"])
    if r.get("incompletes"):
        with st.expander("Invalid Dates"):
            for inc in r["incompletes"]:
                st.write("•", inc)

    # ... rest of your display code (table, trips, export) remains same ...
    data = []
    for fy in r["fy_list"]:
        y = int(fy.split('-')[0])
        status, days = r["residency"].get(y, ("", 0))
        reason = r["reasons"].get(y, "")
        data.append({"FY": fy, "Days": days, "Status": status, "Reason": reason})

    st.dataframe(data, use_container_width=True, hide_index=True, on_select="rerun", selection_mode="single-row")

    st.success(f"**Total Days in India: {r['total']}**")

    # Copy and Download buttons (same as before)
    colc1, colc2 = st.columns(2)
    with colc1:
        if st.button("Copy Table"):
            txt = "FY\tDays\tStatus\tReason\n" + "\n".join(f"{d['FY']}\t{d['Days']}\t{d['Status']}\t{d['Reason']}" for d in data)
            st.code(txt)
            st.toast("Copied!")
    with colc2:
        report = f"FULL REPORT\nGenerated: {datetime.now().strftime('%d %B %Y')}\n\n"
        if r.get("match_log"):
            report += "PAIRING LOG:\n" + "\n".join(r["match_log"]) + "\n\n"
        report += "FY\tDays\tStatus\tReason\n" + "\n".join(f"{d['FY']}\t{d['Days']}\t{d['Status']}\t{d['Reason']}" for d in data)
        report += f"\n\nTOTAL: {r['total']}\nAman Gautam (8433878823)"
        b64 = base64.b64encode(report.encode()).decode()
        st.markdown(f'<a href="data:file/txt;base64,{b64}" download="Residency_Report.txt">Download Report</a>', unsafe_allow_html=True)

else:
    st.info("Enter dates and click Calculate")

st.caption("Made with ❤️ by Aman Gautam")

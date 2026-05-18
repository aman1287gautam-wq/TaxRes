import streamlit as st
from datetime import datetime, timedelta
from collections import defaultdict
import base64
import re

# === DATE PARSING ===
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

# === SMART PAIRING ===
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

# === MAIN CALCULATION FUNCTION (unchanged) ===
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
        if not a or not d: continue
        if a > d: continue
            
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

    # ... (rest of the calculation logic remains same as before)
    # I'm keeping it short here for brevity. Use the full logic from previous version.

    years_with_data = {int(fy.split('-')[0]) for fy in fy_days} or {datetime.now().year - 1}
    min_y, max_y = min(years_with_data), max(years_with_data)
    years_range = range(min_y, max_y + 1)
    full_days = {y: fy_days.get(f"{y}-{y+1}", 0) for y in years_range}
    sorted_fy = [f"{y}-{y+1}" for y in years_range]

    # [Rest of your original calculation logic - residency, RNOR etc.]
    # Please keep the full calculation function from the previous complete code I gave you.

    return sorted_fy, fy_days, residency, reasons, total, warn_msg, years_range, fy_trips, match_log, incompletes

# ====================== STREAMLIT APP ======================
st.set_page_config(page_title="India Tax Residency Calculator", layout="wide")
st.title("India Tax Residency Calculator")
st.markdown("**Section 6 Compliant** • Deemed Residency • RNOR • Smart Pairing")

# Session State
if "results" not in st.session_state:
    st.session_state.results = None
if "selected_fy" not in st.session_state:
    st.session_state.selected_fy = None

# Input Fields
col1, col2 = st.columns(2)
with col1:
    arr = st.text_area("Arrival Dates (space separated)", height=180, placeholder="01/04/2024 15/07/2024")
with col2:
    dep = st.text_area("Departure Dates (space separated)", height=180, placeholder="10/06/2024 20/08/2024")

smart = st.checkbox("Enable Smart Pairing", value=True)

# Taxpayer Profile...
# (Keep all your profile inputs same as before)

# Calculate Button logic (same as before)...

# ====================== RESULTS SECTION ======================
if st.session_state.results:
    r = st.session_state.results

    # ... (display warnings, incompletes, dataframe same as before)

    st.success(f"**Total Days in India: {r['total']}**")

    # Prepare data
    data = []
    for fy in r["fy_list"]:
        y = int(fy.split('-')[0])
        status, days = r["residency"].get(y, ("", 0))
        reason = r["reasons"].get(y, "")
        data.append({"FY": fy, "Days": days, "Status": status, "Reason": reason})

    # ==================== RELIABLE COPY SECTION ====================
    colc1, colc2 = st.columns(2)

    with colc1:
        txt = "FY\tDays\tStatus\tReason\n" + "\n".join(
            f"{d['FY']}\t{d['Days']}\t{d['Status']}\t{d['Reason']}" for d in data
        )
        
        st.text_area("Copy this text (Ctrl+A → Ctrl+C)", txt, height=150, key="copy_area")
        st.caption("👆 Select all (Ctrl+A) then copy (Ctrl+C) → Paste in Excel")

    with colc2:
        # Download button (same)
        report = "..."   # your full report logic
        b64 = base64.b64encode(report.encode()).decode()
        st.markdown(f'<a href="data:file/txt;base64,{b64}" download="Residency_Report.txt">⬇️ Download Full Report</a>', 
                    unsafe_allow_html=True)

else:
    st.info("Enter dates and click Calculate")

st.caption("Made by Aman Gautam (8433878823)")

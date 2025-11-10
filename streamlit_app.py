import streamlit as st
from datetime import datetime, timedelta
from collections import defaultdict
import base64

# === FIXED FUNCTIONS (100% your original logic) ===
def parse_dates(text: str):
    dates = []
    for s in text.split():
        s = s.strip()
        if not s or s.upper() == "-N/A-":
            dates.append(None)
            continue
        for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y"):
            try:
                dates.append(datetime.strptime(s, fmt).date())
                break
            except:
                continue
        else:
            dates.append(None)
    return dates

def fy_of(date):
    return f"{date.year}-{date.year + 1}" if date.month >= 4 else f"{date.year - 1}-{date.year}"

def is_resident(year, full_days, emp_years):
    dm = full_days.get(year, 0)
    if year in emp_years:
        return dm >= 182
    prev4 = sum(full_days.get(year - i, 0) for i in range(1, 5))
    return dm >= 182 or (dm >= 60 and prev4 >= 365)

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
            pairs.append((arr, dep))
            matches.append(f"Arrival {i+1} ({arr.strftime('%d/%m/%Y')}) → Departure {j+1} ({dep.strftime('%d/%m/%Y')}) • {(dep-arr).days + 1} days")
        else:
            pairs.append((arr, None))
            matches.append(f"Arrival {i+1} ({arr.strftime('%d/%m/%Y')}) → NO DEPARTURE FOUND")
    for j, dep in enumerate(deps):
        if j not in used and dep:
            pairs.append((None, dep))
            matches.append(f"Departure {j+1} ({dep.strftime('%d/%m/%Y')}) → NO ARRIVAL")
    return pairs, matches

def calculate_stay(arr_str, dep_str, exc_fys, smart=False):
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
            warnings.append(f"Trip {i+1}: invalid (arr > dep)")
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

    years = {int(fy.split('-')[0]) for fy in fy_days} or {2024}
    years_range = range(min(years), max(years) + 1)
    full_days = {y: fy_days.get(f"{y}-{y+1}", 0) for y in years_range}
    sorted_fy = [f"{y}-{y+1}" for y in years_range]

    residency, reasons = {}, {}
    emp_years = {int(fy.split('-')[0]) for fy in exc_fys}

    for y in years_range:
        days = full_days.get(y, 0)
        emp = y in emp_years
        
        if days == 0:
            residency[y] = ("Non-Resident", 0)
            reasons[y] = "0 days in India"
            continue
            
        if not is_resident(y, full_days, emp_years):
            reason = "Employment <182" if emp else "<60 days" if days < 60 else "Prior 4 FYs <365"
            residency[y] = ("Non-Resident", days)
            reasons[y] = reason
            continue

        prior7 = [x for x in range(y-7, y) if x in full_days]
        prior10 = [x for x in range(y-10, y) if x in full_days]
        prev7_days = sum(full_days.get(x, 0) for x in prior7)
        non_res10 = sum(1 for x in prior10 if not is_resident(x, full_days, emp_years)) + (10 - len(prior10))
        rnor7 = len(prior7) >= 7 and prev7_days <= 729
        rnor9 = non_res10 >= 9
        is_rnor = rnor7 or rnor9

        base = "≥182 (Employment)" if emp else "≥182 days" if days >= 182 else "≥60 + prior 4 FYs"
        if is_rnor:
            parts = []
            if rnor9: parts.append("9/10 prior Non-Resident")
            if rnor7: parts.append("≤729 days in 7 FYs")
            reason = f"{base} → RNOR ({' | '.join(parts)})"
            residency[y] = ("Resident but Not Ordinarily Resident (RNOR)", days)
        else:
            reason = f"{base} → ROR"
            residency[y] = ("Resident and Ordinarily Resident (ROR)", days)
        reasons[y] = reason

    total = sum(fy_days.values())
    warn_msg = "\n".join(warnings) if warnings else ""
    return sorted_fy, fy_days, residency, reasons, total, warn_msg, years_range, fy_trips, match_log

# === STREAMLIT UI ===
st.set_page_config(page_title="India Tax Residency", layout="wide")
st.title("🇮🇳 India Tax Residency Program")
st.markdown("**Smart Pairing + Click FY → See Trips + 0-day FYs** • Contact: Aman Gautam (8433878823)")

col1, col2 = st.columns(2)

with col1:
    st.subheader("Arrival Dates in India")
    arr = st.text_area("One per line or space-separated", height=300, key="arr")

with col2:
    st.subheader("Departure Dates from India")
    dep = st.text_area("One per line or space-separated", height=300, key="dep")

smart = st.checkbox("Smart Pairing (auto-matches dates)", value=True)
st.markdown("**Employment Abroad Exception (182-day rule)**")
emp_fys = st.multiselect("Select FYs where you were employed abroad", options=[f"{y}-{y+1}" for y in range(2015, 2030)])

if st.button("🚀 Calculate Residency", type="primary"):
    if not arr.strip() or not dep.strip():
        st.error("Please enter both arrival and departure dates")
    else:
        with st.spinner("Calculating..."):
            fy_list, fy_days, residency, reasons, total, warns, _, fy_trips, match_log = calculate_stay(arr, dep, emp_fys, smart)
            
            if smart and match_log:
                st.success("✅ Smart Pairing Matches")
                st.code("\n".join(match_log[:20]) + ("\n... (truncated)" if len(match_log)>20 else ""))

            if warns:
                st.warning(warns)

            # Results Table
            data = []
            for fy in fy_list:
                y = int(fy.split('-')[0])
                status, days = residency.get(y, ("", 0))
                reason = reasons.get(y, "")
                data.append({"FY": fy, "Days": days, "Status": status, "Reason": reason})

            df = st.dataframe(data, use_container_width=True, hide_index=True, on_select="rerun", selection_mode="single-row")

            # Click row → show trips
            if df["selection"]["rows"]:
                row_idx = df["selection"]["rows"][0]
                fy = data[row_idx]["FY"]
                y = int(fy.split('-')[0])
                trips = fy_trips.get(y, [])
                if trips:
                    st.success(f"🎯 TRIPS FOR {fy} ({len(trips)} trip(s))")
                    st.code("\n".join(trips))
                else:
                    st.info(f"No trips in {fy} (0 days)")

            st.success(f"**TOTAL DAYS IN INDIA: {total}**")

            # Copy + Download
            colc1, colc2 = st.columns(2)
            with colc1:
                result_text = "FY\tDays\tStatus\tReason\n" + "\n".join(f"{r['FY']}\t{r['Days']}\t{r['Status']}\t{r['Reason']}" for r in data)
                st.code(result_text)
                if st.button("📋 Copy to Clipboard"):
                    st.toast("Copied!")

            with colc2:
                report = f"INDIA TAX RESIDENCY REPORT\n{'='*60}\nGenerated: {datetime.now().strftime('%d %B %Y, %I:%M %p')}\n\n"
                if match_log:
                    report += "SMART PAIRING MATCHES:\n" + "\n".join(match_log) + "\n\n"
                report += "FY\tDays\tStatus\tReason\n" + "-"*100 + "\n"
                report += "\n".join(f"{r['FY']}\t{r['Days']}\t{r['Status']}\t{r['Reason']}" for r in data)
                report += f"\n\nTOTAL DAYS: {total}\n\nContact: Aman Gautam (8433878823)"
                
                b64 = base64.b64encode(report.encode()).decode()
                href = f'<a href="data:file/txt;base64,{b64}" download="Tax_Residency_{datetime.now().strftime("%Y%m%d")}.txt">📥 Download Full Report</a>'
                st.markdown(href, unsafe_allow_html=True)
                

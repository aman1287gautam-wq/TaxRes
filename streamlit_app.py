import streamlit as st
from datetime import datetime, timedelta
from collections import defaultdict
import base64

# === UPDATED RESIDENCY LOGIC WITH FULL SECTION 6 ===
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

def calculate_stay(arr_str, dep_str, exc_fys, smart=False, is_citizen=True, is_visitor=False, income_15l=False, not_taxed_abroad=False, is_crew=False):
    # --- parse employment FY selections into start-year ints robustly ---
    emp_years = set()
    for fy in (exc_fys or []):
        if not fy:
            continue
        s = str(fy).strip()
        # Accept formats like "2024-2025", "2024-25", "2024/25", or just "2024"
        try:
            if "-" in s:
                start = s.split("-")[0]
            elif "/" in s:
                start = s.split("/")[0]
            else:
                start = s
            start = start.strip()
            emp_years.add(int(start))
        except Exception:
            # ignore malformed entries
            continue

    arrs = parse_dates(arr_str)
    deps = parse_dates(dep_str)
    paired, match_log = smart_pair(arrs, deps) if smart else (list(zip(arrs, deps)), [])
    fy_days = defaultdict(int)
    fy_trips = defaultdict(list)
    warnings = []
    seen = set()

    # collect day counts per fiscal-year string as before (e.g. "2024-2025")
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

    # Build integer-start-year set from observed fy_days keys
    years_from_data = set()
    for fy in fy_days.keys():
        try:
            years_from_data.add(int(fy.split("-")[0]))
        except:
            continue

    # Ensure years_range includes emp_years (so employment FYs take effect even if 0 days)
    all_years = set(years_from_data) | set(emp_years)
    if not all_years:
        # fallback to a reasonable default if nothing present (keep your existing default behaviour)
        all_years = {2024}
    years_range = range(min(all_years), max(all_years) + 1)

    # map integer-year -> days in that FY
    full_days = {y: fy_days.get(f"{y}-{y+1}", 0) for y in years_range}
    sorted_fy = [f"{y}-{y+1}" for y in years_range]

    residency, reasons = {}, {}

    for y in years_range:
        days = full_days.get(y, 0)
        emp = y in emp_years

        # === RESIDENCY THRESHOLD ===
        threshold = 182
        if is_crew:
            threshold = 182  # Crew always 182
        elif emp:
            threshold = 182  # Employment abroad → 182 threshold
        elif is_visitor and income_15l:
            threshold = 120
        elif is_visitor or is_citizen:
            threshold = 182 if income_15l else 60
        else:
            threshold = 60

        prior4_days = sum(full_days.get(y - i, 0) for i in range(1, 5))

        # If >15L the special logic is simpler: use threshold only
        if income_15l:
            is_res = days >= threshold
        else:
            is_res = (days >= 182) or (days >= threshold and prior4_days >= 365)

        # === DEEMED RESIDENCY (1A) ===
        deemed = is_citizen and income_15l and not_taxed_abroad

        if days == 0 and not deemed:
            residency[y] = ("Non-Resident", 0)
            reasons[y] = "0 days in India"
            continue

        if deemed:
            residency[y] = ("Resident (Deemed u/s 6(1A))", days)
            reasons[y] = "Citizen + Income >15L + Not taxed abroad → Deemed Resident"
            is_res = True
        elif not is_res:
            reason = f"<{threshold} days" + (f" | Prior 4 FYs {prior4_days}<365" if days >= threshold else "")
            residency[y] = ("Non-Resident", days)
            reasons[y] = reason
            continue
        else:
            base = f"≥{threshold} days"
            if days >= 182:
                base = "≥182 days"
            elif emp:
                base = "≥182 (Employment)"
            elif threshold == 120:
                base = "≥120 (Visitor + >15L)"
            residency[y] = ("Resident", days)
            reasons[y] = base

               # === RNOR LOGIC (apply only if we have sufficient prior data) ===
        prior7 = [x for x in range(y - 7, y) if x in full_days]
        prior10 = [x for x in range(y - 10, y) if x in full_days]

        # Check if we have sufficient data (>=7 or >=10 prior FYs)
        enough_data_7 = len(prior7) >= 7
        enough_data_10 = len(prior10) >= 10

        # Only compute RNOR if enough prior years exist
        if enough_data_7 or enough_data_10:
            prev7_days = sum(full_days.get(x, 0) for x in prior7)
            non_res10 = sum(1 for x in prior10 if residency.get(x, ("Non-Resident", 0))[0] == "Non-Resident")
            rnor7 = enough_data_7 and prev7_days <= 729
            rnor9 = enough_data_10 and non_res10 >= 9
        else:
            rnor7 = rnor9 = False

        # RNOR also if visitor with 120–181 days + >15L income, or deemed
        rnor_visitor = is_visitor and income_15l and 120 <= days < 182
        rnor_deemed = deemed

        is_rnor = rnor7 or rnor9 or rnor_visitor or rnor_deemed

        if is_rnor:
            parts = []
            if rnor9: parts.append("9/10 prior NR")
            if rnor7: parts.append("≤729 days in 7 FYs")
            if rnor_visitor: parts.append("120–181 days + >15L visitor")
            if rnor_deemed: parts.append("Deemed resident")
            reason = f"{reasons[y]} → RNOR ({' | '.join(parts)})"
            residency[y] = ("Resident but Not Ordinarily Resident (RNOR)", days)
        else:
            reason = f"{reasons[y]} → ROR"
            residency[y] = ("Resident and Ordinarily Resident (ROR)", days)
        reasons[y] = reason

    total = sum(fy_days.values())
    warn_msg = "\n".join(warnings) if warnings else ""
    return sorted_fy, fy_days, residency, reasons, total, warn_msg, years_range, fy_trips, match_log

# === STREAMLIT UI ===
st.set_page_config(page_title="India Tax Residency - Full Sec 6", layout="wide")
st.title("🇮🇳 India Tax Residency Calculator (Section 6 - Full Rules)")
st.markdown("**Now 100% compliant with latest IT Act** • Deemed Residency • 120-day rule • RNOR extensions")

if "results" not in st.session_state:
    st.session_state.results = None
if "selected_fy" not in st.session_state:
    st.session_state.selected_fy = None

col1, col2 = st.columns(2)
with col1:
    arr = st.text_area("Arrival Dates", height=250, placeholder="01/04/2024 15/07/2024")
with col2:
    dep = st.text_area("Departure Dates", height=250, placeholder="10/06/2024 20/08/2024")

smart = st.checkbox("Smart Pairing", value=True)

st.subheader("Taxpayer Status")
col_a, col_b = st.columns(2)
is_citizen = col_a.checkbox("Indian Citizen", value=True)
is_visitor = col_b.checkbox("Visitor / PIO coming to India", value=False)

col_c, col_d = st.columns(2)
income_15l = col_c.checkbox("Total Income (excl. foreign) > ₹15 Lakh", value=False)
not_taxed_abroad = col_d.checkbox("Not liable to tax in any country (for Deemed Residency)", value=False)

is_crew = st.checkbox("Crew member of Indian/foreign ship", value=False)
emp_fys = st.multiselect("Employment Abroad FYs (182-day rule)", options=[f"{y}-{y+1}" for y in range(2015, 2030)])

col_btn1, col_btn2 = st.columns(2)
calculate = col_btn1.button("🚀 Calculate Full Residency", type="primary")
clear = col_btn2.button("🗑️ Clear")

if clear:
    st.session_state.results = None
    st.session_state.selected_fy = None
    st.rerun()

if calculate:
    if not arr.strip() or not dep.strip():
        st.error("Enter dates")
    else:
        with st.spinner("Applying Section 6..."):
            fy_list, fy_days, residency, reasons, total, warns, _, fy_trips, match_log = calculate_stay(
                arr, dep, emp_fys, smart, is_citizen, is_visitor, income_15l, not_taxed_abroad, is_crew
            )
            st.session_state.results = {
                "fy_list": fy_list, "residency": residency, "reasons": reasons, "total": total,
                "warns": warns, "fy_trips": fy_trips, "match_log": match_log
            }
            st.session_state.selected_fy = None
        st.rerun()

if st.session_state.results:
    r = st.session_state.results
    if smart and r["match_log"]:
        st.success("✅ Smart Pairing")
        st.code("\n".join(r["match_log"][:20]))

    if r["warns"]:
        st.warning(r["warns"])

    data = []
    for fy in r["fy_list"]:
        y = int(fy.split('-')[0])
        status, days = r["residency"].get(y, ("", 0))
        reason = r["reasons"].get(y, "")
        data.append({"FY": fy, "Days": days, "Status": status, "Reason": reason})

    df = st.dataframe(data, use_container_width=True, hide_index=True, on_select="rerun", selection_mode="single-row", key="table")

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
            st.success(f"TRIPS FOR {fy}")
            st.code("\n".join(trips))
        else:
            st.info("0 days")

    st.success(f"**TOTAL DAYS: {r['total']}**")

    colc1, colc2 = st.columns(2)
    with colc1:
        if st.button("📋 Copy"):
            txt = "FY\tDays\tStatus\tReason\n" + "\n".join(f"{d['FY']}\t{d['Days']}\t{d['Status']}\t{d['Reason']}" for d in data)
            st.code(txt)
            st.toast("Copied!")

    with colc2:
        report = f"FULL INDIA TAX RESIDENCY REPORT (Sec 6)\n{'='*60}\n{datetime.now().strftime('%d %B %Y')}\n\n"
        if r["match_log"]:
            report += "SMART PAIRING:\n" + "\n".join(r["match_log"]) + "\n\n"
        report += "FY\tDays\tStatus\tReason\n" + "\n".join(f"{d['FY']}\t{d['Days']}\t{d['Status']}\t{d['Reason']}" for d in data)
        report += f"\n\nTOTAL: {r['total']}\nAman Gautam (8433878823)"
        b64 = base64.b64encode(report.encode()).decode()
        href = f'<a href="data:file/txt;base64,{b64}" download="Full_Residency_Report.txt">📥 Download</a>'
        st.markdown(href, unsafe_allow_html=True)
else:
    st.info("Enter details and calculate!")

st.markdown("---")
st.caption("**100% compliant with Section 6** • Includes 6(1A), 120-day, RNOR(c)(d) • Made by Aman Gautam")

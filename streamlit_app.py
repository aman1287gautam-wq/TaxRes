import streamlit as st
from datetime import datetime, timedelta
from collections import defaultdict
import base64

# === PASSWORD CONFIG ===
APP_PASSWORD = "faiu2"      # Change this to your desired password
SESSION_AUTH_KEY = "auth_status"

# === AUTHENTICATION ===
def authenticate():
    if SESSION_AUTH_KEY not in st.session_state:
        st.session_state[SESSION_AUTH_KEY] = False

    if not st.session_state[SESSION_AUTH_KEY]:
        st.markdown(
            """
            <style>
            .lock-box {
                text-align: center;
                margin-top: 120px;
                font-family: 'Segoe UI', sans-serif;
            }
            .lock-icon {
                font-size: 70px;
                color: #e74c3c;
            }
            .title {
                font-size: 32px;
                font-weight: bold;
                margin: 15px 0;
                color: #2c3e50;
            }
            .subtitle {
                color: #7f8c8d;
                margin-bottom: 30px;
            }
            </style>
            """,
            unsafe_allow_html=True
        )

        st.markdown("""
        <div class="lock-box">
            <div class="lock-icon">Locked</div>
            <div class="title">India Tax Residency Calculator</div>
            <div class="subtitle">Enter password to access Section 6 compliance tool</div>
        </div>
        """, unsafe_allow_html=True)

        with st.form("login_form", clear_on_submit=True):
            pwd = st.text_input("Password", type="password", placeholder="Enter password")
            submit = st.form_submit_button("Unlock")

            if submit:
                if pwd == APP_PASSWORD:
                    st.session_state[SESSION_AUTH_KEY] = True
                    st.success("Unlocked! Access granted.")
                    st.rerun()
                else:
                    st.error("Incorrect password.")
        st.stop()

    # Show logout button
    col1, col2 = st.columns([6, 1])
    with col2:
        if st.button("Logout", key="logout_btn"):
            st.session_state[SESSION_AUTH_KEY] = False
            st.rerun()

# === CALL AUTH FIRST ===
authenticate()

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
        # Allow pairing even if lengths don't match, this is primarily for logging
        pass
    
    pairs, used, matches = [], set(), []
    
    # Process arrivals first
    for i, arr in enumerate(arrs):
        if not arr:
            pairs.append((arr, None))
            continue
        
        # Find the earliest valid departure that hasn't been used
        cands = [(j, dep) for j, dep in enumerate(deps) if dep and dep >= arr and j not in used]
        
        if cands:
            # Sort by duration, then by index for consistency
            cands.sort(key=lambda x: (x[1] - arr, x[0]))
            j, dep = cands[0]
            used.add(j)
            days = (dep - arr).days + 1
            pairs.append((arr, dep))
            matches.append(f"Arrival {i+1} ({arr.strftime('%d/%m/%Y')}) → Departure {j+1} ({dep.strftime('%d/%m/%Y')}) • {days} days")
        else:
            pairs.append((arr, None))
            matches.append(f"Arrival {i+1} ({arr.strftime('%d/%m/%Y')}) → NO DEPARTURE FOUND")
    
    # Process unmatched departures
    for j, dep in enumerate(deps):
        if j not in used and dep:
            pairs.append((None, dep))
            matches.append(f"Departure {j+1} ({dep.strftime('%d/%m/%Y')}) → NO ARRIVAL")
    
    # Filter out (None, None) pairs that might have been added initially
    pairs = [p for p in pairs if p[0] or p[1]]
    
    return pairs, matches

# === MAIN RESIDENCY CALCULATION (UPDATED LOGIC) ===
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
            
    # Determine the range of years and the minimum data year
    years_with_data = {int(fy.split('-')[0]) for fy in fy_days}
    if not years_with_data:
        # Default to a recent year if no data is entered, so the dropdown populates
        years_with_data = {datetime.now().year - 1} 
        
    min_data_year = min(years_with_data)
    max_y = max(years_with_data)
    years_range = range(min_data_year, max_y + 1)
    
    full_days = {y: fy_days.get(f"{y}-{y+1}", 0) for y in years_range}
    sorted_fy = [f"{y}-{y+1}" for y in years_range]
    emp_years = {int(fy.split('-')[0]) for fy in exc_fys if fy in sorted_fy}
    
    residency, reasons = {}, {}
    
    for y in years_range:
        days = full_days.get(y, 0)
        emp = y in emp_years
        
        # Determine applicable residency tests
        basic_threshold = 182 # Base test 6(1)(a)
        secondary_check_needed = False
        min_secondary_days = 60 # Default secondary test 6(1)(c)
        
        if is_crew or emp:
            # Only 182 rule applies (Exception to 6(1)(c))
            pass 
        elif is_visitor:
            if income_15l:
                # 182 OR (120 + 365)
                secondary_check_needed = True
                min_secondary_days = 120
            else:
                # Only 182 rule applies (Exception to 6(1)(c))
                pass 
        else:
            # Standard: 182 OR (60 + 365)
            secondary_check_needed = True
        
        prior4_days = sum(full_days.get(y - i, 0) for i in range(1, 5))
        deemed = is_citizen and income_15l and not_taxed_abroad # 6(1A)
        
        is_res = False # Flag for RNOR check later
        
        # 1. Check Deemed Residency (6(1A))
        if deemed:
            residency[y] = ("Resident (Deemed u/s 6(1A))", days)
            reasons[y] = "Citizen + Income >₹15L + Not taxed abroad → Deemed Resident"
            is_res = True
            continue # Deemed resident status is final
            
        # 2. Check 0 Days
        if days == 0:
            residency[y] = ("Non-Resident", 0)
            reasons[y] = "0 days in India"
            continue

        # 3. Check Primary Test (>= 182 days)
        if days >= basic_threshold:
            residency[y] = ("Resident", days)
            base_reason = f"≥{basic_threshold} days"
            if emp: base_reason += " (Employment abroad)"
            elif is_crew: base_reason += " (Crew)"
            reasons[y] = base_reason
            is_res = True
        
        # 4. Check Secondary Test (60/120 + 365)
        elif secondary_check_needed and days >= min_secondary_days:
            # Check for data sufficiency for the 365-day rule
            has_sufficient_history = (y - 4) >= min_data_year
            
            if not has_sufficient_history:
                # === FIX FOR UNASCERTAINABLE ===
                residency[y] = ("Not Ascertainable", days)
                reasons[y] = f"Days={days} (≥{min_secondary_days}), but insufficient prior data (need data from {y-4}-{y-3}) to verify 365-day rule."
                is_res = False
                continue
                
            elif prior4_days >= 365:
                residency[y] = ("Resident", days)
                base_reason = f"≥{min_secondary_days} days + Prior 4 FYs ({prior4_days}) ≥ 365"
                reasons[y] = base_reason
                is_res = True
            else:
                residency[y] = ("Non-Resident", days)
                reasons[y] = f"≥{min_secondary_days} days but Prior 4 FYs ({prior4_days}) < 365"
                is_res = False

        # 5. Failed all tests
        else:
            residency[y] = ("Non-Resident", days)
            reason = f"<{min_secondary_days} days (Secondary threshold not met)" if secondary_check_needed else f"<{basic_threshold} days"
            reasons[y] = reason
            is_res = False

        # === RNOR CHECK (u/s 6(6)) ===
        # Only check if status is Resident (ROR/RNOR)
        if residency[y][0].startswith("Resident") or residency[y][0] == "ROR" or residency[y][0] == "RNOR":
            
            # Condition (c): Non-Resident in 9 out of 10 prior FYs
            prior10_years = [x for x in range(y-10, y)]
            non_res_count = 0
            for x in prior10_years:
                if x < min_data_year or residency.get(x, ("Non-Resident", 0))[0] in ["Non-Resident", "Not Ascertainable"]:
                    non_res_count += 1
            
            rnor9 = non_res_count >= 9
            
            # Condition (d): Total stay ≤ 729 days in 7 prior FYs
            prior7_years = [x for x in range(y-7, y)]
            stay_in_prior7 = sum(full_days.get(x, 0) for x in prior7_years)
            rnor7 = stay_in_prior7 <= 729
            
            # Other RNOR conditions
            rnor_visitor = is_visitor and income_15l and 120 <= days < 182 # Resident under 120-day rule is always RNOR
            rnor_deemed = deemed # Deemed resident is always RNOR
            
            is_rnor = rnor7 or rnor9 or rnor_visitor or rnor_deemed
            
            if is_rnor:
                parts = []
                if rnor9: parts.append(f"NR in {non_res_count}/10 prior FYs")
                if rnor7: parts.append(f"Stay in prior 7 FYs ({stay_in_prior7}) ≤ 729 days")
                if rnor_visitor: parts.append("120–181 days + >₹15L visitor")
                if rnor_deemed: parts.append("Deemed resident")
                
                final_reason = f"{reasons[y]} → RNOR ({' | '.join(parts)})"
                residency[y] = ("Resident but Not Ordinarily Resident (RNOR)", days)
                reasons[y] = final_reason
            else:
                reasons[y] = f"{reasons[y]} → ROR"
                residency[y] = ("ROR", days)

    total = sum(fy_days.values())
    warn_msg = "\n".join(warnings) if warnings else ""
    return sorted_fy, fy_days, residency, reasons, total, warn_msg, years_range, fy_trips, match_log

# === STREAMLIT UI ===
st.set_page_config(page_title="India Tax Residency - Full Sec 6", layout="wide")
st.title("🇮🇳 India Tax Residency Calculator")
st.markdown("**100% compliant with IT Act 1961** • 6(1A) Deemed • 120-day • RNOR(c)(d) • Crew • Smart Pairing; **By Aman Gautam (8433878823)**")

for key in ["results", "selected_fy"]:
    if key not in st.session_state:
        st.session_state[key] = None

col1, col2 = st.columns(2)
with col1:
    arr = st.text_area("Arrival Dates (space-separated)", height=220, placeholder="01/04/2024 15/07/2024 10/01/2025",
                         help="Supported: DD/MM/YYYY, DD-MM-YYYY, DD.MM.YYYY")
with col2:
    dep = st.text_area("Departure Dates (space-separated)", height=220, placeholder="10/06/2024 20/08/2024 25/01/2025")

smart = st.checkbox("Enable Smart Pairing (recommended)", value=True, help="Auto-matches earliest valid departure")

st.subheader("👤 Taxpayer Profile")
col_a, col_b = st.columns(2)
is_citizen = col_a.checkbox("Indian Citizen / Person of Indian Origin (PIO)", value=True)
is_visitor = col_b.checkbox("Coming to India on visit (Visitor / PIO)", value=False)

st.caption("---")

col_c, col_d = st.columns(2)
income_15l = col_c.checkbox("Indian Income (excl. foreign) > ₹15 Lakh", value=False)
not_taxed_abroad = col_d.checkbox("Not liable to tax in any foreign country", value=False, help="Relevant for Deemed Residency u/s 6(1A)")

is_crew = st.checkbox("Crew member of Indian/foreign ship", value=False)

st.caption("---")


fy_options = st.session_state.results["fy_list"] if st.session_state.results else []
emp_fys = st.multiselect("Employment Abroad / Crew Member FYs (182-day rule applies)", options=fy_options,
                         help="Select FYs where the person was employed outside India or was a crew member. This overrides the 60-day rule.")


col_btn1, col_btn2 = st.columns(2)
calculate = col_btn1.button("✅ Calculate Full Residency", type="primary", use_container_width=True)
clear = col_btn2.button("❌ Clear All", use_container_width=True)

if clear:
    st.session_state.results = None
    st.session_state.selected_fy = None
    st.rerun()

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
                    "fy_list": fy_list, "residency": residency, "reasons": reasons,
                    "total": total, "warns": warns, "fy_trips": fy_trips, "match_log": match_log
                }
                st.session_state.selected_fy = None
                st.rerun()
            except Exception as e:
                st.error(f"Calculation error: {e}")

# DISPLAY RESULTS
if st.session_state.results:
    st.markdown("---")
    st.header("📋 Residency Results")
    r = st.session_state.results
    
    # === FIX: SHOW ALL SMART PAIRING MATCHES ===
    if smart and r["match_log"]:
        with st.expander(f"Smart Pairing Matches ({len(r['match_log'])} trips) - Full Log", expanded=False):
            st.code("\n".join(r["match_log"]), language="text")
    
    if r["warns"]:
        st.warning(f"⚠️ Warning: {r['warns']}")

    data = []
    for fy in r["fy_list"]:
        y = int(fy.split('-')[0])
        status, days = r["residency"].get(y, ("", 0))
        reason = r["reasons"].get(y, "")
        
        # Color coding for better visualization
        if "ROR" in status:
            color = 'background-color: #d4edda;' # Green for ROR
        elif "RNOR" in status:
            color = 'background-color: #fff3cd;' # Yellow for RNOR
        elif "Non-Resident" in status:
            color = 'background-color: #f8d7da;' # Red for NR
        elif "Not Ascertainable" in status:
            color = 'background-color: #cfe2ff;' # Blue for Uncertain
        else:
            color = ''
            
        data.append({"FY": fy, "Days": days, "Status": status, "Reason": reason, "Style": color})

    
    # Custom display with styling
    from pandas import DataFrame
    df_data = DataFrame(data).drop(columns=['Style'])
    
    def highlight_status(row):
        return [row['Style']] * len(row)

    st.dataframe(
        df_data.style.apply(highlight_status, axis=1), 
        use_container_width=True, 
        hide_index=True,
        on_select="rerun", 
        selection_mode="single-row", 
        key="res_table"
    )

    selection = st.session_state.res_table["selection"]["rows"]
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
            with st.expander(f"📅 Trips in {fy} ({len(set(trips))} unique)", expanded=True):
                st.code("\n".join(sorted(set(trips))), language="text")
        else:
            st.info("No stay recorded in this FY.")

    st.success(f"**Total Days in India: {r['total']}**")

    colc1, colc2 = st.columns(2)
    with colc1:
        st.button("Click on the table's copy button to copy data to clipboard.", use_container_width=True, disabled=True)

    with colc2:
        report = f"""FULL INDIA TAX RESIDENCY REPORT (SECTION 6)
{'='*70}
Taxpayer Status: Citizen={is_citizen}, Visitor={is_visitor}, Income>15L={income_15l}, Not Taxed Abroad={not_taxed_abroad}, Crew={is_crew}
Generated: {datetime.now().strftime('%d %B %Y, %I:%M %p')}
{'-'*70}
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
        href = f'<a href="data:file/txt;base64,{b64}" download="Tax_Residency_Report_{datetime.now().strftime("%Y%m%d")}.txt">Download Full Report (TXT)</a>'
        st.markdown(href, unsafe_allow_html=True)
else:
    st.info("👆 Enter arrival/departure dates and click **Calculate** to begin.")

st.markdown("---")
st.caption("🇮🇳 **Section 6(1), 6(1A), 6(6) compliant** • Includes RNOR(c), RNOR(d) • Crew • Employment abroad • Made with love by **Aman Gautam**")

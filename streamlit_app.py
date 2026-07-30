import streamlit as st
from datetime import datetime, timedelta
from collections import defaultdict
import re

# =====================================================================
# FINANCIAL YEAR HELPERS
# =====================================================================
def fy_start_year(d):
    """Return the starting calendar year of the Indian FY that `d` falls in."""
    return d.year if d.month >= 4 else d.year - 1

def fy_label(year):
    return f"{year}-{year + 1}"

def fy_of(date):
    return fy_label(fy_start_year(date))

def is_nonresident(status):
    """True only for a Non-Resident status string (ROR/RNOR count as resident)."""
    return str(status).strip().lower().startswith("non-resident")


# =====================================================================
# DATE PARSING
# =====================================================================
PLACEHOLDERS = {"-N/A-", "N/A", "NA", "-", "--", "NIL", "NONE", "EMPTY"}
DATE_FORMATS = (
    "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y",
    "%d/%m/%y", "%d-%m-%y", "%d.%m.%y",
    "%Y-%m-%d", "%Y/%m/%d",
)

def parse_dates(text):
    """Parse a blob of dates separated by spaces / commas / newlines / tabs."""
    dates, invalids = [], []
    if not text:
        return dates, invalids
    cleaned = re.sub(r"[,;\n\r\t]+", " ", text)
    for s in cleaned.split():
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
        if parsed:
            dates.append(parsed)
        else:
            invalids.append(s)
    return dates, invalids


# =====================================================================
# SMART PAIRING  (match each arrival to the earliest valid later departure)
# =====================================================================
def smart_pair(arrs, deps):
    pairs, used, matches = [], set(), []
    for i, arr in enumerate(arrs):
        if not arr:
            pairs.append((arr, None))
            continue
        cands = [(j, dep) for j, dep in enumerate(deps) if dep and dep >= arr and j not in used]
        if cands:
            cands.sort(key=lambda x: ((x[1] - arr).days, x[0]))
            j, dep = cands[0]
            used.add(j)
            days = (dep - arr).days + 1
            pairs.append((arr, dep))
            matches.append(
                f"Arrival {i+1} ({arr.strftime('%d/%m/%Y')}) -> "
                f"Departure {j+1} ({dep.strftime('%d/%m/%Y')})  |  {days} days"
            )
        else:
            pairs.append((arr, None))
            matches.append(f"Arrival {i+1} ({arr.strftime('%d/%m/%Y')}) -> NO DEPARTURE FOUND")
    for j, dep in enumerate(deps):
        if j not in used and dep:
            pairs.append((None, dep))
            matches.append(f"Departure {j+1} ({dep.strftime('%d/%m/%Y')}) -> NO ARRIVAL")
    return pairs, matches


# =====================================================================
# THRESHOLD LOGIC (Section 6(1) + Explanation 1)
# Returns (threshold_days, only_182)
#   only_182 == True  -> only the 182-day condition (a) applies; the
#                        60/365 second condition (b) does not.
#   only_182 == False -> resident if >=182 OR (>=threshold AND 365 in prior 4 FYs)
# =====================================================================
def basic_threshold(is_citizen, is_pio, is_coming_on_visit, income_15l, emp_this_year, is_crew):
    if is_crew:
        return 182, True
    if emp_this_year and is_citizen:
        return 182, True
    if (is_citizen or is_pio) and is_coming_on_visit:
        if income_15l:
            return 120, False   # 120-day rule still needs 365 days in prior 4 FYs
        return 182, True        # visitor <=15L: second condition raised to 182
    return 60, False            # default 60-day + 365 rule


# =====================================================================
# MAIN RESIDENCY CALCULATION
# =====================================================================
def calculate_stay(arr_str, dep_str, emp_fys, smart, is_citizen, is_pio,
                   is_coming_on_visit, income_15l, not_taxed_abroad, is_crew,
                   prior_data):
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
            warnings.append(f"Trip {i+1}: invalid (arrival {a.strftime('%d/%m/%Y')} > departure {d.strftime('%d/%m/%Y')})")
            continue
        days_count = (d - a).days + 1
        trip_str = f"Trip {i+1}: {a.strftime('%d/%m/%Y')} -> {d.strftime('%d/%m/%Y')} ({days_count} days)"
        cur = a
        while cur <= d:
            fy = fy_of(cur)
            key = (fy, cur)
            if key not in seen:
                fy_days[fy] += 1
                seen.add(key)
            fy_trips[fy].append(trip_str)
            cur += timedelta(days=1)

    years_with_data = {int(fy.split("-")[0]) for fy in fy_days}
    if not years_with_data:
        years_with_data = {datetime.now().year - 1}
    min_y, max_y = min(years_with_data), max(years_with_data)
    years_range = range(min_y, max_y + 1)
    full_days = {y: fy_days.get(fy_label(y), 0) for y in years_range}
    sorted_fy = [fy_label(y) for y in years_range]
    emp_years = {int(fy.split("-")[0]) for fy in emp_fys if fy in sorted_fy}

    # Lookups that blend computed years with user-supplied prior-year data
    def days_in(year):
        if year in full_days:
            return full_days[year]
        return prior_data.get(year, {}).get("days", 0)

    def status_of(year):
        if year in residency:
            return residency[year][0]
        return prior_data.get(year, {}).get("status", "Non-Resident")

    residency, reasons = {}, {}

    for y in years_range:
        days = full_days.get(y, 0)
        emp = y in emp_years
        threshold, only_182 = basic_threshold(
            is_citizen, is_pio, is_coming_on_visit, income_15l, emp, is_crew
        )
        prior4 = sum(days_in(y - i) for i in range(1, 5))
        deemed = is_citizen and income_15l and not_taxed_abroad

        if only_182:
            is_res = days >= 182
        else:
            is_res = days >= 182 or (days >= threshold and prior4 >= 365)

        # ----- NOT resident under 6(1) -----
        if not is_res:
            if deemed:
                residency[y] = ("Resident but Not Ordinarily Resident (RNOR)", days)
                reasons[y] = ("Deemed Resident u/s 6(1A) -> RNOR [6(6)(d)]: "
                              "citizen, Indian income >Rs.15L, not taxed abroad")
            else:
                if days == 0:
                    rs = "0 days in India"
                elif (not only_182) and days >= threshold:
                    rs = f"{days} days >= {threshold} but prior 4 FYs = {prior4} < 365 days"
                else:
                    lim = 182 if only_182 else threshold
                    rs = f"{days} days < {lim}"
                residency[y] = ("Non-Resident", days)
                reasons[y] = rs
            continue

        # ----- Resident under 6(1): base reason -----
        if days >= 182:
            base = f"{days} days >= 182 [Sec 6(1)(a)]"
        else:
            base = f"{days} days >= {threshold} & prior 4 FYs {prior4} >= 365 [Sec 6(1)(b)]"

        # ----- ROR vs RNOR (Section 6(6)) -----
        prior7 = sum(days_in(x) for x in range(y - 7, y))
        nr_count = sum(1 for x in range(y - 10, y) if is_nonresident(status_of(x)))
        rnor_a = nr_count >= 9                                   # 6(6)(a)
        rnor_b = prior7 <= 729                                   # 6(6)(b)
        rnor_c = ((is_citizen or is_pio) and is_coming_on_visit  # 6(6)(c)
                  and income_15l and 120 <= days < 182)

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

    total = sum(fy_days.values())
    warn_msg = "\n".join(warnings) if warnings else ""

    incompletes = []
    for inv in arr_invalids:
        incompletes.append(f"Unreadable Arrival Date: {inv}")
    for inv in dep_invalids:
        incompletes.append(f"Unreadable Departure Date: {inv}")
    for log in match_log:
        if "NO DEPARTURE FOUND" in log or "NO ARRIVAL" in log:
            incompletes.append(log)

    return sorted_fy, fy_days, residency, reasons, total, warn_msg, fy_trips, match_log, incompletes


# =====================================================================
# STREAMLIT UI
# =====================================================================
st.set_page_config(page_title="India Tax Residency - Section 6", layout="wide")
st.title("India Tax Residency Calculator")
st.markdown(
    "**Section 6, IT Act 1961** (Finance Act 2020-2025) - 6(1A) Deemed - 120-day rule - "
    "RNOR 6(6)(a)/(b)/(c) - Crew - Smart Pairing - **By Aman Gautam (8433878823)**"
)

with st.expander("How residency is decided (quick reference)", expanded=False):
    st.markdown(
        "- **Resident [6(1)]:** >=182 days in the FY, **OR** >=60 days in the FY AND >=365 days across the 4 preceding FYs.\n"
        "- **Explanation 1:** Indian citizen leaving for employment/as crew, or citizen/PIO on a visit: the 60-day limit becomes 182 (or 120 if Indian income > Rs.15L).\n"
        "- **Deemed Resident [6(1A)]:** Indian citizen, Indian income > Rs.15L, not taxable in any other country -> resident (always RNOR).\n"
        "- **RNOR [6(6)]:** a resident is RNOR if (a) NR in 9 of the 10 preceding FYs, (b) <=729 days in the 7 preceding FYs, "
        "(c) resident under the 120-day visitor rule, or (d) deemed resident.\n\n"
        "The **Prior Years** section below lets you enter the assessee's known status and days for years outside the trip window - "
        "these drive the 365-day, 729-day and 9-of-10-year tests."
    )

for key in ["results", "selected_fy"]:
    if key not in st.session_state:
        st.session_state[key] = None

# ---- Date inputs ----
col1, col2 = st.columns(2)
with col1:
    arr = st.text_area("Arrival Dates", height=200,
                       placeholder="01/04/2024 15/07/2024 10/01/2025",
                       help="Separate by space, comma or new line. Formats: DD/MM/YYYY, DD-MM-YYYY, DD.MM.YYYY, YYYY-MM-DD")
with col2:
    dep = st.text_area("Departure Dates", height=200,
                       placeholder="10/06/2024 20/08/2024 25/01/2025")

smart = st.checkbox("Enable Smart Pairing (recommended)", value=True,
                    help="Auto-matches each arrival to the earliest valid departure on/after it")

# ---- Taxpayer profile ----
st.subheader("Taxpayer Profile")
taxpayer_type = st.radio(
    "Taxpayer Type:",
    ["Indian Citizen", "Person of Indian Origin (PIO)", "Foreign Citizen (neither)"],
    index=0, horizontal=True,
)
is_citizen = taxpayer_type == "Indian Citizen"
is_pio = taxpayer_type == "Person of Indian Origin (PIO)"

pcol1, pcol2 = st.columns(2)
with pcol1:
    is_coming_on_visit = st.checkbox("Coming on a visit to India from outside", value=False)
    income_15l = st.checkbox("Indian income (excl. foreign) > Rs.15 Lakh", value=False)
with pcol2:
    not_taxed_abroad = st.checkbox("Not liable to tax in any foreign country", value=False,
                                   help="Required for Deemed Residency u/s 6(1A)",
                                   disabled=not is_citizen)
    is_crew = st.checkbox("Crew member of Indian ship (employment)", value=False,
                          help="Applies the 182-day rule. Voyage-day exclusion under Rule 126 is not auto-computed.")

# ---- Reactive FY-range detection (drives Employment + Prior-Year inputs) ----
_arrs, _ = parse_dates(arr)
_deps, _ = parse_dates(dep)
_all_valid = [d for d in (_arrs + _deps) if d]

emp_fys = []
prior_data = {}

if _all_valid:
    detected_min = min(fy_start_year(d) for d in _all_valid)
    detected_max = max(fy_start_year(d) for d in _all_valid)
    fy_list_reactive = [fy_label(y) for y in range(detected_min, detected_max + 1)]

    st.subheader("Employment Abroad")
    emp_fys = st.multiselect(
        "FYs the assessee was employed outside India (182-day rule applies to these)",
        options=fy_list_reactive,
    )

    st.subheader("Prior Years (before the trip window)")
    with st.expander(
        f"Set known status & days for FY {fy_label(detected_min-10)} to {fy_label(detected_min-1)}",
        expanded=False,
    ):
        st.caption(
            "Used for the 365-day rule (prior 4 FYs), the 729-day rule (prior 7 FYs) and the "
            "9-of-10-years RNOR test. Defaults: Non-Resident, 0 days. Only 'Non-Resident' counts "
            "toward the 9/10 test; ROR and RNOR both count as resident."
        )
        status_opts = [
            "Non-Resident",
            "Resident Ordinarily Resident (ROR)",
            "Resident but Not Ordinarily Resident (RNOR)",
        ]
        for y in range(detected_min - 1, detected_min - 11, -1):  # most recent first
            c1, c2 = st.columns([3, 1])
            status = c1.selectbox(f"FY {fy_label(y)} - status", status_opts,
                                  index=0, key=f"ps_{y}")
            days_val = c2.number_input(f"Days in {fy_label(y)}", min_value=0, max_value=366,
                                       value=0, step=1, key=f"pd_{y}")
            prior_data[y] = {"status": status, "days": int(days_val)}
else:
    st.info("Enter valid arrival and departure dates to configure Employment and Prior-Year inputs.")

# ---- Action buttons ----
b1, b2 = st.columns(2)
calculate = b1.button("Calculate Full Residency", type="primary", use_container_width=True)
clear = b2.button("Clear Results", use_container_width=True)

if clear:
    st.session_state.results = None
    st.session_state.selected_fy = None
    st.rerun()

if calculate:
    if not arr.strip() or not dep.strip():
        st.error("Please enter both arrival and departure dates.")
    elif not _all_valid:
        st.error("No readable dates found. Check the format (e.g. 01/04/2024).")
    else:
        with st.spinner("Applying Section 6 rules..."):
            try:
                (fy_list, fy_days, residency, reasons, total, warns,
                 fy_trips, match_log, incompletes) = calculate_stay(
                    arr, dep, emp_fys, smart, is_citizen, is_pio, is_coming_on_visit,
                    income_15l, not_taxed_abroad, is_crew, prior_data,
                )
                st.session_state.results = {
                    "fy_list": fy_list, "residency": residency, "reasons": reasons,
                    "total": total, "warns": warns, "fy_trips": fy_trips,
                    "match_log": match_log, "incompletes": incompletes,
                    "prior_data": {y: prior_data[y] for y in prior_data},
                }
                st.session_state.selected_fy = None
                st.rerun()
            except Exception as e:
                st.error(f"Calculation error: {e}")

# =====================================================================
# RESULTS
# =====================================================================
if st.session_state.results:
    r = st.session_state.results

    if smart and r["match_log"]:
        with st.expander("Smart Pairing Matches", expanded=False):
            st.code("\n".join(r["match_log"]), language="text")

    if r["warns"]:
        st.warning(f"Warnings:\n{r['warns']}")

    if r["incompletes"]:
        with st.expander("Incomplete details - follow up with assessee", expanded=True):
            for inc in r["incompletes"]:
                st.write(f"- {inc}")

    data = []
    for fy in r["fy_list"]:
        y = int(fy.split("-")[0])
        status, days = r["residency"].get(y, ("", 0))
        data.append({"FY": fy, "Days": days, "Status": status, "Reason": r["reasons"].get(y, "")})

    df = st.dataframe(
        data, use_container_width=True, hide_index=True,
        on_select="rerun", selection_mode="single-row", key="res_table",
        column_config={
            "FY": st.column_config.TextColumn(width="small"),
            "Days": st.column_config.NumberColumn(width="small"),
            "Status": st.column_config.TextColumn(width="medium"),
            "Reason": st.column_config.TextColumn(width="large"),
        },
    )

    selection = df["selection"]["rows"]
    if selection:
        fy = data[selection[0]]["FY"]
        if st.session_state.selected_fy != fy:
            st.session_state.selected_fy = fy

    if st.session_state.selected_fy:
        fy = st.session_state.selected_fy
        y = int(fy.split("-")[0])
        trips = r["fy_trips"].get(y, [])
        if trips:
            with st.expander(f"Trips counted in {fy} ({len(set(trips))} unique)", expanded=True):
                st.code("\n".join(sorted(set(trips))), language="text")
        else:
            st.info(f"No stay recorded in {fy}.")

    st.success(f"**Total Days in India: {r['total']}**")

    # ---- Report download ----
    report = "FULL INDIA TAX RESIDENCY REPORT (SECTION 6)\n"
    report += "=" * 60 + "\n"
    report += f"Generated: {datetime.now().strftime('%d %B %Y, %I:%M %p')}\n"
    report += "-" * 60 + "\n\n"

    if r.get("prior_data"):
        report += "PRIOR-YEAR ASSUMPTIONS USED:\n"
        for y in sorted(r["prior_data"]):
            pd_ = r["prior_data"][y]
            report += f"- {fy_label(y)}: {pd_['status']}, {pd_['days']} days\n"
        report += "\n"

    if r["match_log"]:
        report += "SMART PAIRING LOG:\n" + "\n".join(r["match_log"]) + "\n\n"

    if r["incompletes"]:
        report += "INCOMPLETE DETAILS (FOLLOW UP WITH ASSESSEE):\n"
        for inc in r["incompletes"]:
            report += f"- {inc}\n"
        report += "\n"

    report += "FY\tDays\tStatus\tReason\n"
    for d in data:
        report += f"{d['FY']}\t{d['Days']}\t{d['Status']}\t{d['Reason']}\n"
    report += f"\nTOTAL DAYS IN INDIA: {r['total']}\n"
    report += "Calculator by: Aman Gautam (8433878823)\n"
    report += "Compliant with Section 6, Finance Act 2020-2025"

    st.download_button(
        "Download Report (.txt)",
        data=report,
        file_name=f"Tax_Residency_Report_{datetime.now().strftime('%Y%m%d')}.txt",
        mime="text/plain",
        use_container_width=True,
    )
else:
    st.info("Enter arrival/departure dates, set profile and prior years, then click **Calculate**.")

st.markdown("---")
st.caption(
    "Section 6(1), 6(1A), 6(6) compliant - RNOR (a)/(b)/(c) - Crew - Employment abroad - "
    "Made with care by **Aman Gautam**"
)

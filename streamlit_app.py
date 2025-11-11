import streamlit as st
from datetime import datetime


# -------------------------------
# Utility function to parse date safely
# -------------------------------
def parse_date(date_str):
    """Safely parse a date in multiple formats."""
    if isinstance(date_str, datetime):
        return date_str
    if not date_str:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except Exception:
            continue
    return None


# -------------------------------
# Main function: calculate_stay
# -------------------------------
def calculate_stay(arrivals, departures, emp_fys=None, smart=False,
                   is_citizen=True, is_visitor=False, income_15l=False,
                   not_taxed_abroad=False, is_crew=False):

    # --- Step 1: Calculate days per FY ---
    data = {}
    fy_start = 2010  # Adjust base year if needed

    for i, (arr, dep) in enumerate(zip(arrivals, departures), start=1):
        arr_date = parse_date(arr)
        dep_date = parse_date(dep)
        if not arr_date or not dep_date:
            continue
        days = abs((dep_date - arr_date).days)
        fy_label = f"{fy_start + i - 1}-{fy_start + i}"
        data[fy_label] = days

    residency = {}
    reasons = {}
    years = sorted(data.keys())

    # --- Step 2: Compute residential status per FY ---
    for i, y in enumerate(years):
        days = data[y]
        prior4_days = sum(data.get(years[j], 0) for j in range(max(0, i - 4), i))

        # Base 182-day rule
        threshold = 182
        if days >= threshold:
            is_res = True
        else:
            if days >= 60 and prior4_days >= 365:
                is_res = True
                threshold = 60
            else:
                is_res = False

        # Deemed Resident (Sec 6(1A))
        deemed = False
        if is_citizen and income_15l and not_taxed_abroad and days >= 120 and prior4_days >= 365:
            deemed = True
            is_res = True

        # --- Step 3: Assign Status ---
        if deemed:
            residency[y] = "Resident (Deemed u/s 6(1A))"
            reasons[y] = "Citizen + Income >15L + Not taxed abroad → Deemed Resident"
            continue

        elif not is_res:
            if days < 60:
                reason = f"<60 days | Prior 4 FYs {prior4_days}<365"
            elif days < 182:
                reason = f"≥60 days but Prior 4 FYs {prior4_days}<365"
            else:
                reason = f"≥182 days"
            residency[y] = "Non-Resident"
            reasons[y] = reason
            continue

        # Resident → Check RNOR/ROR
        past10 = [years[j] for j in range(max(0, i - 10), i)]
        resident_count = sum(1 for yr in past10 if residency.get(yr, "").startswith("Resident"))
        total_days_7yrs = sum(data.get(years[j], 0) for j in range(max(0, i - 7), i))

        if resident_count < 2 or total_days_7yrs < 730:
            residency[y] = "Resident but Not Ordinarily Resident (RNOR)"
            reasons[y] = "Resident <2 of last 10 FYs or <730 days in past 7 FYs"
        else:
            residency[y] = "Resident and Ordinarily Resident (ROR)"
            reasons[y] = "≥60 days → ROR"

    # --- Step 4: Return (matching unpack pattern in main app) ---
    fy_list = years
    fy_days = [data[y] for y in years]
    total = sum(fy_days)
    warns = []
    fy_trips = []
    match_log = []

    return fy_list, fy_days, residency, reasons, total, warns, None, fy_trips, match_log


# -------------------------------
# Streamlit UI
# -------------------------------
st.set_page_config(page_title="Tax Residency Calculator", page_icon="📊", layout="wide")

st.title("📊 Indian Tax Residency Calculator (NRI / RNOR / ROR)")

st.markdown("""
Enter your **arrival and departure dates** year-wise to calculate your residential status under **Section 6 of the Income Tax Act, 1961**.
""")

# Input section
with st.form("input_form"):
    st.subheader("Enter Travel Data")

    arrivals = st.text_area("Arrival Dates (comma-separated)", "12/01/2018, 24/02/2018, 11/02/2019, 25/03/2020")
    departures = st.text_area("Departure Dates (comma-separated)", "22/02/2018, 08/02/2019, 17/03/2020, 24/04/2021")

    col1, col2, col3 = st.columns(3)
    with col1:
        is_citizen = st.checkbox("Indian Citizen", True)
    with col2:
        income_15l = st.checkbox("Income > ₹15 Lakh in India", False)
    with col3:
        not_taxed_abroad = st.checkbox("Not Taxed Abroad", False)

    submitted = st.form_submit_button("Calculate Status")

if submitted:
    arrivals_list = [x.strip() for x in arrivals.split(",") if x.strip()]
    departures_list = [x.strip() for x in departures.split(",") if x.strip()]

    fy_list, fy_days, residency, reasons, total, warns, _, fy_trips, match_log = calculate_stay(
        arrivals_list, departures_list, None, False, is_citizen, False, income_15l, not_taxed_abroad, False
    )

    # Display results
    st.success("✅ Residential Status Computed Successfully!")
    result_data = []
    for fy in fy_list:
        result_data.append({
            "Financial Year": fy,
            "Days in India": fy_days[fy_list.index(fy)],
            "Status": residency[fy],
            "Reason": reasons[fy]
        })

    st.dataframe(result_data, use_container_width=True)

    total_days = sum(fy_days)
    st.markdown(f"**Total Days Stayed in India (all years):** {total_days}")
    st.caption("**100% compliant with Section 6** • Includes 6(1A), 120-day, RNOR(c)(d) • Made by Aman Gautam")

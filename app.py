import io
import html
import math
from pathlib import Path
import sys

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent / "src"))
from sa_ad.routine import analyze_routine_tidy, load_routine_export, rank_profile_anomalies

st.set_page_config(page_title="SA Health Data Anomalies", layout="wide")
st.markdown("<div style='border-bottom:4px solid #007749; padding-bottom:0.65rem; margin-bottom:1rem'><div style='font-size:0.9rem; font-weight:700; color:#555'>REPUBLIC OF SOUTH AFRICA 🇿🇦</div><div style='font-size:1.05rem; font-weight:700'>National Department of Health</div><div style='font-size:0.95rem; color:#555'>Primary Healthcare Unit</div></div>", unsafe_allow_html=True)
st.title("Routine data anomaly review")
st.caption("A Primary Healthcare Unit analysis tool for transparent facility-history and year-profile screening. 2026 is excluded because it is incomplete.")

workspace_files = sorted(Path.cwd().glob("Routine data*.xlsx"))
st.header("Data source")
input_mode = st.radio("Source", ["Use workbook in workspace", "Upload workbook"], help="Choose a workbook source. Workspace mode avoids browser upload limits in Codespaces.")
if input_mode == "Use workbook in workspace" and workspace_files:
    selected_source = st.selectbox("Routine workbook", workspace_files, format_func=lambda path: path.name, help="Select the workbook to inspect and analyze.")
    source_key = f"{selected_source}:{selected_source.stat().st_size}:{selected_source.stat().st_mtime_ns}"
    source_bytes = selected_source.read_bytes() if st.button("Load workbook", help="Read and prepare the selected workbook. Analysis will not run until you click Run analysis.") else None
elif input_mode == "Upload workbook":
    selected_source = st.file_uploader("Upload a Routine Excel export", type=["xlsx"], help="The browser displays upload progress. After upload completes, click Load workbook.")
    source_key = f"{selected_source.name}:{selected_source.size}" if selected_source else None
    source_bytes = selected_source.getvalue() if selected_source and st.button("Load workbook", help="Read and prepare the uploaded workbook. Analysis will not run until you click Run analysis.") else None
else:
    selected_source, source_key, source_bytes = None, None, None

st.sidebar.header("Adjustable parameters: cross-sectional analysis")
minimum_indicators = st.sidebar.number_input("Minimum indicators reported", min_value=1, max_value=1000, value=10, step=1, help="Exclude a facility from profile scoring if it reports fewer indicators in the selected year.")
minimum_prevalence_percent = st.sidebar.slider("Minimum indicator prevalence (%)", min_value=0, max_value=100, value=20, step=5, help="Within each facility-type group, drop an indicator if fewer than this percentage of facilities reported it in the selected year.")
minimum_prevalence = minimum_prevalence_percent / 100
minimum_peer_facilities = st.sidebar.number_input("Minimum facilities per peer group", min_value=4, max_value=5000, value=30, step=1, help="Skip a facility-type group if it has fewer facilities than this. Larger groups make covariance estimates more stable.")
st.sidebar.caption("These settings affect the facility-profile (Mahalanobis) analysis and are recalculated from the selected workbook.")
st.sidebar.header("Review output")
review_mode = st.sidebar.radio("Profile review list", ["Top percentage", "Top N facilities"], help="Choose a review-list size rather than interpreting a statistical cutoff.")
review_amount = st.sidebar.slider("Percentage to review", 1, 20, 5, help="Show this percentage of the highest-scoring eligible facility profiles.") if review_mode == "Top percentage" else st.sidebar.number_input("Facilities to review", min_value=1, max_value=5000, value=100, step=10, help="Show this many of the highest-scoring eligible facility profiles.")

if source_bytes is not None:
    try:
        with st.status("Loading workbook", expanded=True) as load_status:
            tidy = load_routine_export(io.BytesIO(source_bytes))
            st.session_state["tidy"] = tidy
            st.session_state["source_key"] = source_key
            st.session_state.pop("result", None)
            load_status.update(label="Workbook loaded", state="complete")
    except Exception as error:
        st.error(str(error))
        st.stop()

if st.session_state.get("source_key") != source_key or "tidy" not in st.session_state:
    st.info("Choose a workbook, click Load workbook, adjust settings, then click Run analysis.")
    st.stop()

tidy = st.session_state["tidy"]
years = sorted(column for column in tidy.columns if column.isdigit() and column != "2026")
selected_year = st.sidebar.selectbox("Analysis year", years, index=len(years) - 1, help="Select the complete year to analyze. 2026 is excluded because it is incomplete.")
st.sidebar.header("Time-series analysis")
st.sidebar.caption("The current time-series detectors use robust historical, trend, and global-trend rules. Their tuning is intentionally fixed while we validate the methods.")
minimum_absolute_change = st.sidebar.number_input("Minimum absolute change", min_value=0.0, value=5.0, step=1.0, help="Ignore time-series flags unless the selected value differs from its historical or trend-based reference by at least this many units. Use 0 for no absolute-change guard.")
treat_leading_zeros_missing = st.sidebar.checkbox("Treat leading zeros as unreported", value=True, help="For time-series methods only, replace zeros before the first positive value with missing values. This does not alter the raw data or cross-sectional analysis.")
run_analysis = st.sidebar.button("Run analysis", type="primary", use_container_width=True, help="Run all detectors using the current settings.")

if run_analysis:
    try:
        progress = st.progress(0, text="Starting analysis")
        status = st.status("Running analysis", expanded=True)

        def update_progress(value, message):
            progress.progress(value, text=message)
            status.write(message)

        st.session_state["result"] = analyze_routine_tidy(tidy, year=selected_year, threshold=3.5, profile_threshold=-1, min_observed_indicators=int(minimum_indicators), min_indicator_prevalence=float(minimum_prevalence), min_peer_facilities=int(minimum_peer_facilities), min_absolute_change=float(minimum_absolute_change), treat_leading_zeros_missing=treat_leading_zeros_missing, progress_callback=update_progress)
        st.session_state["result_settings"] = {"year": selected_year, "minimum_indicators": minimum_indicators, "minimum_prevalence": minimum_prevalence, "minimum_peer_facilities": minimum_peer_facilities, "minimum_absolute_change": minimum_absolute_change, "treat_leading_zeros_missing": treat_leading_zeros_missing}
        status.update(label="Analysis complete", state="complete", expanded=False)
    except Exception as error:
        st.error(str(error))
        st.stop()

if "result" not in st.session_state:
    st.info("Adjust the settings, then click Run analysis.")
    st.stop()

result = st.session_state["result"]
settings = st.session_state.get("result_settings", {})
current_settings = {"year": selected_year, "minimum_indicators": minimum_indicators, "minimum_prevalence": minimum_prevalence, "minimum_peer_facilities": minimum_peer_facilities, "minimum_absolute_change": minimum_absolute_change, "treat_leading_zeros_missing": treat_leading_zeros_missing}
if settings and settings != current_settings:
    st.warning("Settings have changed since the last analysis. Click Run analysis to refresh the results.")

tidy = result["tidy"]
st.success(f"Loaded {len(tidy):,} facility-indicator rows. Analysis year: {selected_year}; historical years available: {len(result['years'])}.")
summary = st.columns(4)
summary[0].metric("Rows", f"{len(tidy):,}")
summary[1].metric("Facilities", f"{tidy['Facility'].nunique():,}")
summary[2].metric("Indicators", f"{tidy['Data Element'].nunique():,}")
summary[3].metric("Analysis year", selected_year)

def show_output(frame, label, filename):
    st.subheader(f"{label} ({len(frame):,})")
    if frame.empty:
        st.success("No records met the current settings.")
    else:
        st.dataframe(frame, use_container_width=True, hide_index=True)
        st.download_button(f"Download {label.lower()} CSV", frame.to_csv(index=False), filename, "text/csv")

def filter_frame(frame, label, key):
    if frame.empty:
        return frame
    st.caption(f"Filter {label.lower()}")
    columns = [column for column in ["Province", "District", "Sub-district", "Facility", "Data Element", "Facility Type"] if column in frame.columns]
    filters = {}
    filter_columns = st.columns(min(len(columns), 3))
    working = frame.copy()
    hierarchy = [column for column in ["Province", "District", "Sub-district", "Facility"] if column in columns]
    ordered_columns = hierarchy + [column for column in columns if column not in hierarchy]
    for index, column in enumerate(ordered_columns):
        values = sorted(working[column].dropna().astype(str).unique())
        selected = filter_columns[index % len(filter_columns)].multiselect(column, values, key=f"{key}_{column}")
        if selected:
            filters[column] = selected
            working = working[working[column].astype(str).isin(selected)]
    return working


def time_series_table(frame, label, key):
    if frame.empty:
        st.success("No records met the current settings.")
        return
    st.subheader(f"{label} ({len(frame):,})")
    ids = ["Province", "District", "Sub-district", "Facility", "Data Element", "Facility Type"]
    history = tidy[ids + result["years"]].drop_duplicates(ids)
    display = frame.sort_values("Score", ascending=False).merge(history, on=ids, how="left")
    display["History chart"] = display[result["years"]].apply(lambda row: [value for value in row.tolist() if pd.notna(value)], axis=1)
    display["Flagged year"] = display["Year"].astype(int)
    display["Flagged value"] = display.apply(lambda row: row.get(str(int(row["Year"])), pd.NA), axis=1)
    display = display.drop(columns=["Explanation"], errors="ignore")
    config = {
        "History chart": st.column_config.LineChartColumn("History", y_min=0, width="medium"),
        "Flagged value": st.column_config.NumberColumn("Flagged value", help="The value that triggered this anomaly record.", format="%d"),
    }
    st.dataframe(display, use_container_width=True, hide_index=True, column_config=config)
    st.download_button(f"Download {label.lower()} CSV", frame.to_csv(index=False), f"{key}_anomalies.csv", "text/csv")

def anomaly_summary(result_frames, grouping):
    frames = []
    for detector, frame in result_frames.items():
        if frame.empty or grouping not in frame.columns:
            continue
        counts = frame.groupby(grouping).size().rename("Anomalies").reset_index()
        counts["Detector"] = detector
        frames.append(counts)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=[grouping, "Anomalies", "Detector"])


def facility_profile_detail(facility, province, facility_type, year):
    year = str(int(year))
    peer = tidy[tidy["Facility Type"].eq(facility_type) & tidy["Facility Type"].ne("other/unclassified")]
    peer_values = peer.pivot_table(index="Facility", columns="Data Element", values=year, aggfunc="first")
    if facility not in peer_values.index:
        return pd.DataFrame()
    selected = peer_values.loc[facility]
    detail = pd.DataFrame({"Indicator": selected.index, "Facility value": selected.values})
    detail["Peer median"] = detail["Indicator"].map(peer_values.median(axis=0))
    detail["Peer mean"] = detail["Indicator"].map(peer_values.mean(axis=0))
    detail["Difference from peer median"] = detail["Facility value"] - detail["Peer median"]
    detail["Relative difference (%)"] = detail["Difference from peer median"].div(detail["Peer median"].replace(0, pd.NA)).mul(100)
    detail["Reported"] = detail["Facility value"].notna()
    return detail[detail["Reported"]].sort_values("Difference from peer median", key=lambda values: values.abs(), ascending=False).round({"Facility value": 2, "Peer median": 2, "Peer mean": 2, "Difference from peer median": 2, "Relative difference (%)": 2})

time_tab, profile_tab, methods_tab = st.tabs(["Time series", "Facility profiles", "Method reference"])
with time_tab:
    ts_frames = {"Facility history": result["temporal"], "Robust trend": result["trend"], "Global trend": result["global_trend"]}
    ts_detector = st.selectbox("Time-series output", list(ts_frames), help="Choose which time-series detector to inspect.")
    if ts_detector == "Global trend":
        st.info("A row is flagged when its selected-year value is unusual for that facility after accounting for the indicator's overall movement across facilities. `Global Indicator Change` is the shared indicator movement on the log1p scale; `Expected from Global Trend` is the facility's expected value after that adjustment; `Absolute change` is the observed-minus-expected difference in original units.")
    ts_filtered = filter_frame(ts_frames[ts_detector], ts_detector, "ts")
    time_series_table(ts_filtered, f"{ts_detector} anomalies", "ts")
    st.subheader("Time-series summary")
    ts_group = st.selectbox("Group time-series anomalies by", ["Province", "Facility", "Data Element", "Facility Type"], key="ts_summary_group")
    ts_summary = anomaly_summary({ts_detector: ts_filtered}, ts_group)
    st.dataframe(ts_summary.sort_values("Anomalies", ascending=False).round(0), use_container_width=True, hide_index=True)
    st.download_button("Download time-series summary CSV", ts_summary.to_csv(index=False), "time_series_summary.csv", "text/csv")
with profile_tab:
    st.info("Score is the dimension-normalized Mahalanobis distance: raw Mahalanobis distance divided by the square root of the retained indicator count. Higher values indicate a more unusual overall indicator pattern within the facility-type peer group. Leading indicators contribute most to the squared distance after accounting for covariance; their percentages are contribution magnitudes, not probabilities.")
    st.caption("Observed indicators are the indicators this facility reported in the selected year. Total indicators are the indicators retained for scoring after the live prevalence filter. Each leading-indicator cell shows up to three indicators, one per line, followed by its share of the displayed top-three contributor magnitude.")
    profile = rank_profile_anomalies(result["profile"], "top_percent" if review_mode == "Top percentage" else "top_n", review_amount)
    label = f"Top {review_amount}%" if review_mode == "Top percentage" else f"Top {int(review_amount)} facilities"
    profile_filtered = filter_frame(profile, "facility profiles", "profile")
    st.subheader(f"Facility profile review list ({label}) ({len(profile_filtered):,})")
    if profile_filtered.empty:
        st.success("No records met the current settings.")
    else:
        page_size = 20
        page_count = max(1, math.ceil(len(profile_filtered) / page_size))
        page = st.number_input("Review page", min_value=1, max_value=page_count, value=1, step=1, key="profile_page", help="The review list is shown 20 facilities at a time.")
        start = (int(page) - 1) * page_size
        profile_page = profile_filtered.iloc[start:start + page_size]
        st.caption(f"Showing rows {start + 1}-{min(start + page_size, len(profile_filtered))} of {len(profile_filtered)}.")
        profile_display = profile_page.copy()
        numeric_columns = profile_display.select_dtypes(include="number").columns
        profile_display[numeric_columns] = profile_display[numeric_columns].round(2)
        profile_display["Leading indicators"] = profile_display["Leading indicators"].map(lambda value: "<br>".join(f"{html.escape(line.rsplit(' (', 1)[0])} <span style='color:#c0392b'>({line.rsplit(' (', 1)[1]}</span>" if " (" in line else html.escape(line) for line in str(value).splitlines()))
        rows = []
        for _, row in profile_display.iterrows():
            cells = []
            for column in profile_display.columns:
                value = row[column]
                cells.append(f"<td>{value}</td>" if column == "Leading indicators" else f"<td>{html.escape(str(value))}</td>")
            rows.append("<tr>" + "".join(cells) + "</tr>")
        header = "".join(f"<th>{html.escape(str(column))}</th>" for column in profile_display.columns)
        st.markdown(f"<div style='overflow-x:auto'><table style='width:100%; border-collapse:collapse'><thead><tr>{header}</tr></thead><tbody>{''.join(rows)}</tbody></table></div><style>table th, table td {{ padding:6px 8px; border-bottom:1px solid #ddd; text-align:left; vertical-align:top; }} table th:last-child, table td:last-child {{ min-width:520px; white-space:normal; }}</style>", unsafe_allow_html=True)
        st.download_button(f"Download facility profile review CSV", profile_filtered.to_csv(index=False), "profile_review.csv", "text/csv")
        st.subheader("Facility detail")
        profile_options = profile_filtered.drop_duplicates(["Province", "Facility"]).reset_index(drop=True)
        selected_facility_index = st.selectbox("Select a flagged facility", profile_options.index, format_func=lambda index: f"{profile_options.loc[index, 'Facility']} | {profile_options.loc[index, 'Province']}", key="profile_facility_detail")
        selected_profile = profile_options.loc[selected_facility_index]
        detail = facility_profile_detail(selected_profile["Facility"], selected_profile["Province"], selected_profile["Facility Type"], selected_profile["Year"])
        st.caption("Peer median is the middle reported value among facilities of the same inferred type. Peer mean is included for comparison but may be influenced by very large facilities. Differences are in the original indicator units.")
        st.dataframe(detail, use_container_width=True, hide_index=True)
        st.download_button("Download facility indicator detail CSV", detail.to_csv(index=False), "facility_indicator_detail.csv", "text/csv")
    st.subheader("Facility-profile summary")
    profile_group = st.selectbox("Group profile anomalies by", ["Province", "Facility", "Data Element", "Facility Type"], key="profile_summary_group")
    profile_summary = anomaly_summary({"Facility profile": profile_filtered}, profile_group)
    st.dataframe(profile_summary.sort_values("Anomalies", ascending=False).round(0), use_container_width=True, hide_index=True)
    st.download_button("Download facility-profile summary CSV", profile_summary.to_csv(index=False), "profile_summary.csv", "text/csv")
with methods_tab:
    st.caption("Methods are shown here for reference. Availability depends on the number and frequency of observations in the uploaded data.")
    st.dataframe(result["methods"], use_container_width=True, hide_index=True)

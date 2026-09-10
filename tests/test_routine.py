import io

import pandas as pd

from sa_ad.routine import global_trend_adjusted_anomalies, harmonize_indicator_pairs, infer_facility_type, load_routine_export, mask_leading_zeros_as_missing, method_catalog, rank_profile_anomalies, robust_trend_anomalies, run_routine_analysis
from sa_ad.timeseries import cusum_scores, ewma_scores, seasonal_baseline_scores


def test_infer_facility_type_uses_unknown_fallback():
    assert infer_facility_type("Mthatha Regional Hospital") == "hospital"
    assert infer_facility_type("Some New Facility") == "other/unclassified"
    assert infer_facility_type("Example Pharmacy") == "pharmacy"


def test_loader_forward_fills_hierarchy_and_excludes_2026():
    source = io.BytesIO()
    with pd.ExcelWriter(source, engine="openpyxl") as writer:
        pd.DataFrame([
            [None, None, None, None, None, None, None],
            ["Province", "District", "Sub-district", "Facility", "Data Element", "Jan - Dec 2021", "Jan - Jul 2026"],
            ["ec Eastern Cape Province", "District", "Sub", "Test Clinic", "Indicator A", 4, 2],
            [None, None, None, None, "Indicator B", 5, 3],
        ]).to_excel(writer, index=False, header=False)
        source.seek(0)
    result = run_routine_analysis(source, year="2021", min_observed_indicators=1)
    assert result["years"] == ["2021"]
    assert result["tidy"].loc[1, "Facility"] == "Test Clinic"
    assert result["tidy"].loc[1, "2021"] == 5


def test_rank_profile_anomalies_returns_requested_review_size():
    profiles = pd.DataFrame({"Score": [1.0, 3.0, 2.0], "Raw Mahalanobis Distance": [1.0, 3.0, 2.0]})
    result = rank_profile_anomalies(profiles, "top_n", 2)
    assert result["Rank"].tolist() == [1, 2]
    assert result["Score"].tolist() == [3.0, 2.0]


def test_robust_trend_and_method_eligibility():
    rows = []
    for facility in range(4):
        rows.append({"Province": "P", "District": "D", "Sub-district": "S", "Facility": f"F{facility}",
                     "Facility Type": "clinic", "Data Element": "A", "2021": 10, "2022": 11,
                     "2023": 12, "2024": 13, "2025": 30})
    result = robust_trend_anomalies(pd.DataFrame(rows), "2025", ["2021", "2022", "2023", "2024", "2025"], threshold=2)
    assert len(result) == 4
    catalog = method_catalog(5, "annual")
    assert catalog.loc[catalog["Method"].eq("Robust trend"), "Available"].item()
    assert not catalog.loc[catalog["Method"].eq("STL residual"), "Available"].item()


def test_global_trend_adjustment_does_not_flag_shared_change():
    rows = []
    for facility in range(8):
        rows.append({"Province": "P", "District": "D", "Sub-district": "S", "Facility": f"F{facility}",
                     "Facility Type": "clinic", "Data Element": "A", "2021": 10, "2022": 20,
                     "2023": 30, "2024": 40, "2025": 50})
    result = global_trend_adjusted_anomalies(pd.DataFrame(rows), "2025", ["2021", "2022", "2023", "2024", "2025"], threshold=3)
    assert result.empty


def test_global_trend_uses_first_sufficient_reporting_year():
    rows = []
    for facility in range(10):
        rows.append({"Province": "P", "District": "D", "Sub-district": "S", "Facility": f"F{facility}",
                     "Facility Type": "clinic", "Data Element": "Late", "2021": None, "2022": None,
                     "2023": 10, "2024": 12, "2025": 14})
    result = global_trend_adjusted_anomalies(pd.DataFrame(rows), "2025", ["2021", "2022", "2023", "2024", "2025"], threshold=3, min_observations=3, min_indicator_prevalence=0.2)
    assert result.empty

    late = pd.DataFrame(rows)
    late.loc[0, "2025"] = 100
    result = global_trend_adjusted_anomalies(late, "2025", ["2021", "2022", "2023", "2024", "2025"], threshold=-1, min_observations=3, min_indicator_prevalence=0.2)
    assert result.iloc[0]["Global baseline year"] == "2023"


def test_leading_zero_mask_preserves_later_zero():
    data = pd.DataFrame({"2021": [0], "2022": [0], "2023": [5], "2024": [0], "2025": [6]})
    masked = mask_leading_zeros_as_missing(data, ["2021", "2022", "2023", "2024", "2025"])
    assert pd.isna(masked.loc[0, "2021"])
    assert pd.isna(masked.loc[0, "2022"])
    assert masked.loc[0, "2024"] == 0


def test_harmonize_indicator_pair_sums_by_facility_and_year():
    data = pd.DataFrame([
        {"Province": "P", "District": "D", "Sub-district": "S", "Facility": "F", "Facility Type": "clinic", "Data Element": "Cervical cancer screening in non-HIV woman 30 years and older", "2021": 4, "2022": 5},
        {"Province": "P", "District": "D", "Sub-district": "S", "Facility": "F", "Facility Type": "clinic", "Data Element": "Cervical cancer screening in non-HIV woman 30-50 years", "2021": 2, "2022": 3},
    ])
    result = harmonize_indicator_pairs(data, ["2021", "2022"])
    assert len(result) == 1
    assert result.iloc[0]["2021"] == 6
    assert "harmonized" in result.iloc[0]["Data Element"]


def test_quarterly_methods_have_expected_basic_behavior():
    values = pd.Series([10, 11, 10, 12, 13, 12, 15, 14, 16, 17, 16, 19])
    ewma = ewma_scores(values)
    cusum = cusum_scores(values)
    seasonal = seasonal_baseline_scores(values, pd.Series(["Q1", "Q2", "Q3", "Q4"] * 3))
    assert ewma["Score"].notna().sum() >= 1
    assert cusum["Score"].iloc[-1] >= 0
    assert seasonal["Score"].notna().sum() == len(values)


def test_profile_uses_sparse_filter_and_selected_year():
    from sa_ad.routine import profile_anomalies

    rows = []
    for facility in range(12):
        for indicator in ["A", "B", "C", "Sparse"]:
            rows.append({"Province": "P", "District": "D", "Sub-district": "S",
                         "Facility": f"F{facility}", "Facility Type": "clinic",
                         "Data Element": indicator,
                         "2021": float(facility + (indicator != "A")) if indicator != "Sparse" or facility == 0 else None})
    result = profile_anomalies(pd.DataFrame(rows), "2021", threshold=0, min_observed_indicators=3, min_peer_facilities=4)
    assert len(result) == 12
    assert result["Total Indicators"].unique().tolist() == [3]
    assert "Leading indicators" in result.columns
    assert result["Leading indicators"].notna().all()

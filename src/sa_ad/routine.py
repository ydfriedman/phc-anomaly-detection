"""Data preparation and anomaly detection for Routine exports."""

from __future__ import annotations

import re
from pathlib import Path
from typing import BinaryIO

import numpy as np
import pandas as pd

HIERARCHY_COLUMNS = ["Province", "District", "Sub-district", "Facility"]
ID_COLUMNS = HIERARCHY_COLUMNS + ["Data Element"]
INDICATOR_SUM_PAIRS = {
    "Cervical cancer screening in non-HIV women 30 years and older (harmonized)": [
        "Cervical cancer screening in non-HIV woman 30 years and older",
        "Cervical cancer screening in non-HIV woman 30-50 years",
    ],
}
FACILITY_TYPES = {
    "correctional facility": r"\bcorrectional\b",
    "pharmacy": r"\bpharmacy\b",
    "general practitioner": r"general practitioner|\bGP\b",
    "non-medical site": r"non-medical site",
    "municipal/EHS site": r"municipality|local municipality|\bLG EHS\b|\bProv EHS\b",
    "condom distribution site": r"condom distribution",
    "home-based care": r"home based care|home-based care",
    "health post": r"health post",
    "wellness/rehabilitation/oral health": r"wellness|rehabilitation|oral health",
    "hospital": r"\bhospital\b",
    "clinic": r"\bclinic\b",
    "community health centre": r"\bcommunity health centre\b|\bchc\b",
    "maternity": r"\bmaternity\b",
    "mobile": r"\bmobile\b",
    "health centre": r"\bhealth centre\b|\bhealth center\b",
}


def _clean_label(value: object) -> str:
    return re.sub(r"\s+", " ", str(value)).strip() if pd.notna(value) else ""


def infer_facility_type(name: object) -> str:
    """Infer a coarse facility type from its name, retaining unknowns explicitly."""
    label = _clean_label(name).lower()
    for facility_type, pattern in FACILITY_TYPES.items():
        if re.search(pattern, label):
            return facility_type
    return "other/unclassified"


def _find_header(raw: pd.DataFrame) -> int:
    for index, row in raw.iterrows():
        labels = {_clean_label(value) for value in row.tolist()}
        if {"Province", "Facility", "Data Element"}.issubset(labels):
            return int(index)
    raise ValueError("Could not find a Routine header containing Province, Facility, and Data Element.")


def _normalise_period(label: object) -> str | None:
    match = re.search(r"20\d{2}", _clean_label(label))
    return match.group(0) if match else None


def harmonize_indicator_pairs(data: pd.DataFrame, years: list[str]) -> pd.DataFrame:
    """Combine known renamed indicators by facility and period."""
    result = data.copy()
    for canonical, labels in INDICATOR_SUM_PAIRS.items():
        matching = result["Data Element"].isin(labels)
        if not matching.any():
            continue
        other = result.loc[~matching].copy()
        paired = result.loc[matching].copy()
        group_columns = HIERARCHY_COLUMNS + ["Facility Type"]
        paired["Data Element"] = canonical
        paired = paired.groupby(group_columns + ["Data Element"], as_index=False, dropna=False)[years].sum(min_count=1)
        other = other.drop(columns=["Source Row"], errors="ignore")
        paired["Source Row"] = np.nan
        result = pd.concat([other, paired], ignore_index=True, sort=False)
    return result


def load_routine_export(source: str | Path | BinaryIO) -> pd.DataFrame:
    """Read a Routine workbook and return one tidy row per facility/indicator."""
    raw = pd.read_excel(source, header=None, engine="openpyxl")
    header_index = _find_header(raw)
    headers = [_clean_label(value) for value in raw.iloc[header_index].tolist()]
    data = raw.iloc[header_index + 1 :].copy()
    data.columns = headers
    data = data.loc[:, [column for column in data.columns if column]]

    for column in HIERARCHY_COLUMNS:
        if column not in data.columns:
            raise ValueError(f"Routine export is missing required column: {column}")
        data[column] = data[column].replace("", np.nan).ffill().map(_clean_label)
    if "Data Element" not in data.columns:
        raise ValueError("Routine export is missing required column: Data Element")
    data["Data Element"] = data["Data Element"].map(_clean_label)
    data = data[data["Facility"].ne("") & data["Data Element"].ne("")].copy()

    period_columns = {}
    for column in data.columns:
        period = _normalise_period(column)
        if period:
            period_columns[column] = period
    if not period_columns:
        raise ValueError("No year columns were found in the Routine export.")
    data = data[ID_COLUMNS + list(period_columns)].rename(columns=period_columns)
    for year in period_columns.values():
        data[year] = pd.to_numeric(data[year], errors="coerce")
    data["Facility Type"] = data["Facility"].map(infer_facility_type)
    data["Source Row"] = np.arange(header_index + 2, header_index + 2 + len(data))
    return harmonize_indicator_pairs(data, list(period_columns.values())).reset_index(drop=True)


def _robust_z(values: pd.Series, value: float) -> float:
    observed = values.dropna().astype(float)
    if observed.empty or pd.isna(value):
        return np.nan
    median = observed.median()
    mad = np.median(np.abs(observed - median))
    scale = 1.4826 * mad
    if scale == 0:
        scale = max(float(observed.std(ddof=0)), 1.0)
    return float(abs(value - median) / scale)


def mask_leading_zeros_as_missing(tidy: pd.DataFrame, years: list[str]) -> pd.DataFrame:
    """Treat only pre-first-positive zero runs as missing for time-series methods."""
    result = tidy.copy()
    values = result[years].to_numpy(dtype=float)
    for row_index in range(len(values)):
        positive = np.flatnonzero(np.isfinite(values[row_index]) & (values[row_index] > 0))
        if len(positive):
            values[row_index, :positive[0]] = np.nan
    result[years] = values
    return result


def temporal_anomalies(tidy: pd.DataFrame, target_year: str, years: list[str], threshold: float = 3.5, min_absolute_change: float = 0.0) -> pd.DataFrame:
    """Score the selected year for each facility/indicator against its own history."""
    group_columns = HIERARCHY_COLUMNS + ["Data Element", "Facility Type"]
    values = tidy[group_columns + years].copy()
    median = values[years].median(axis=1)
    mad = values[years].sub(median, axis=0).abs().median(axis=1)
    scale = (1.4826 * mad).where(mad.ne(0), values[years].std(axis=1, ddof=0).clip(lower=1.0))
    scores = (values[target_year] - median).abs().div(scale.replace(0, np.nan))
    absolute_change = (values[target_year] - median).abs()
    result = values.loc[scores.ge(threshold) & absolute_change.ge(min_absolute_change) & values[target_year].notna(), group_columns].copy()
    result["Year"] = int(target_year)
    result["Value"] = values.loc[result.index, target_year].astype(float)
    result["Historical median"] = median.loc[result.index]
    result["Absolute change"] = absolute_change.loc[result.index]
    result["Score"] = scores.loc[result.index]
    result["Detector"] = "facility_history"
    result["Explanation"] = result["Score"].map(lambda score: f"{target_year} is {score:.1f} robust scale units from this facility's historical median.")
    return result.reset_index(drop=True)


def robust_trend_anomalies(tidy: pd.DataFrame, target_year: str, years: list[str], threshold: float = 3.5, min_observations: int = 4, min_absolute_change: float = 0.0) -> pd.DataFrame:
    """Score selected-year residuals from a robust Theil-Sen trend."""
    group_columns = HIERARCHY_COLUMNS + ["Data Element", "Facility Type"]
    if len(years) < 3 or target_year not in years:
        return pd.DataFrame(columns=group_columns + ["Year", "Value", "Expected from Trend", "Trend Slope", "Score", "Detector", "Explanation"])
    values = tidy[group_columns + years].copy()
    matrix = values[years].to_numpy(dtype=float)
    time = np.arange(len(years), dtype=float)
    pairs = [(left, right) for left in range(len(years)) for right in range(left + 1, len(years))]
    slopes = np.full((len(matrix), len(pairs)), np.nan)
    for pair_index, (left, right) in enumerate(pairs):
        valid = np.isfinite(matrix[:, left]) & np.isfinite(matrix[:, right])
        slopes[valid, pair_index] = (matrix[valid, right] - matrix[valid, left]) / (time[right] - time[left])
    slope = np.nanmedian(slopes, axis=1)
    intercept = np.nanmedian(matrix - slope[:, None] * time[None, :], axis=1)
    fitted = intercept[:, None] + slope[:, None] * time[None, :]
    residuals = matrix - fitted
    observed_count = np.isfinite(matrix).sum(axis=1)
    mad = np.nanmedian(np.abs(residuals - np.nanmedian(residuals, axis=1)[:, None]), axis=1)
    scale = 1.4826 * mad
    fallback = np.nanstd(residuals, axis=1)
    scale = np.where((scale > 0) & np.isfinite(scale), scale, np.maximum(fallback, 1.0))
    target_index = years.index(target_year)
    score = np.abs(residuals[:, target_index]) / scale
    absolute_change = np.abs(matrix[:, target_index] - fitted[:, target_index])
    selected = (observed_count >= min_observations) & np.isfinite(matrix[:, target_index]) & (score >= threshold) & (absolute_change >= min_absolute_change)
    result = values.loc[selected, group_columns].copy()
    result["Year"] = int(target_year)
    result["Value"] = matrix[selected, target_index]
    result["Expected from Trend"] = fitted[selected, target_index]
    result["Absolute change"] = absolute_change[selected]
    result["Trend Slope"] = slope[selected]
    result["Score"] = score[selected]
    result["Detector"] = "robust_trend"
    result["Explanation"] = result["Score"].map(lambda value: f"{target_year} is {value:.1f} robust scale units from a Theil-Sen trend fitted to this facility-indicator history.")
    return result.reset_index(drop=True)


def global_trend_adjusted_anomalies(tidy: pd.DataFrame, target_year: str, years: list[str], threshold: float = 3.5, min_observations: int = 4, min_absolute_change: float = 0.0, min_indicator_prevalence: float = 0.2) -> pd.DataFrame:
    """Score facility departures after removing each indicator's shared annual movement."""
    group_columns = HIERARCHY_COLUMNS + ["Data Element", "Facility Type"]
    if len(years) < 3 or target_year not in years:
        return pd.DataFrame(columns=group_columns + ["Year", "Value", "Global baseline year", "Expected from Global Trend", "Score", "Detector", "Explanation"])
    values = tidy[group_columns + years].copy()
    numeric = values[years].apply(pd.to_numeric, errors="coerce").where(lambda frame: frame >= 0)
    log_values = np.log1p(numeric.to_numpy(dtype=float))
    indicator_medians_raw = numeric.assign(**{"Data Element": values["Data Element"]}).groupby("Data Element")[years].median().where(lambda frame: frame >= 0)
    indicator_medians = np.log1p(indicator_medians_raw.astype(float))
    facility_count = tidy["Facility"].nunique()
    indicator_coverage = numeric.assign(**{"Data Element": values["Data Element"]}).groupby("Data Element")[years].count().div(facility_count).reindex(indicator_medians.index)
    baseline_indices = indicator_coverage.ge(min_indicator_prevalence).to_numpy().argmax(axis=1)
    has_baseline = indicator_coverage.ge(min_indicator_prevalence).any(axis=1).to_numpy()
    indicator_codes = pd.Categorical(values["Data Element"], categories=indicator_medians.index).codes
    shared_levels = indicator_medians.to_numpy(dtype=float)[indicator_codes]
    baseline_levels = shared_levels[np.arange(len(shared_levels)), baseline_indices[indicator_codes]]
    shared_change = shared_levels - baseline_levels[:, None]
    shared_change[~has_baseline[indicator_codes], :] = np.nan
    adjusted = log_values - shared_change
    observed_count = np.isfinite(adjusted).sum(axis=1)
    baseline = np.nanmedian(adjusted, axis=1)
    residuals = adjusted - baseline[:, None]
    mad = np.nanmedian(np.abs(residuals - np.nanmedian(residuals, axis=1)[:, None]), axis=1)
    scale = 1.4826 * mad
    fallback = np.nanstd(residuals, axis=1)
    scale = np.where((scale > 0) & np.isfinite(scale), scale, np.maximum(fallback, 0.05))
    target_index = years.index(target_year)
    score = np.abs(residuals[:, target_index]) / scale
    absolute_change = np.abs(numeric.to_numpy(dtype=float)[:, target_index] - np.expm1(baseline + shared_change[:, target_index]))
    selected = (observed_count >= min_observations) & np.isfinite(numeric.to_numpy(dtype=float)[:, target_index]) & (score >= threshold) & (absolute_change >= min_absolute_change)
    result = values.loc[selected, group_columns].copy()
    result["Year"] = int(target_year)
    result["Value"] = numeric.loc[selected, target_year].astype(float)
    result["Expected from Global Trend"] = np.expm1(baseline[selected] + shared_change[selected, target_index])
    result["Global baseline year"] = np.asarray(years, dtype=object)[baseline_indices[indicator_codes[selected]]]
    result["Global reporting coverage"] = indicator_coverage.to_numpy(dtype=float)[indicator_codes[selected], target_index]
    result["Absolute change"] = absolute_change[selected]
    result["Global Indicator Change"] = shared_change[selected, target_index]
    result["Score"] = score[selected]
    result["Detector"] = "global_trend_adjusted"
    result["Explanation"] = result["Score"].map(lambda value: f"The selected value departs from its facility history after adjusting for the indicator's shared global trend; adjusted score {value:.1f}.")
    return result.reset_index(drop=True)


def method_catalog(observation_count: int, frequency: str = "annual") -> pd.DataFrame:
    """Describe detector availability without exposing model parameters to users."""
    seasonal = frequency in {"quarterly", "monthly"}
    methods = [
        ("Robust historical level", 4, False, "Compares the selected value with the facility's historical median."),
        ("Robust trend", 4, False, "Compares the selected value with a robust Theil-Sen trend."),
        ("Global-trend adjusted", 4, False, "Removes shared indicator-wide movement before scoring facility departures."),
        ("EWMA", 8, False, "Detects recent movement with more weight on recent observations."),
        ("CUSUM", 8, False, "Detects persistent shifts in the level of a series."),
        ("Seasonal baseline", 12, True, "Compares each period with the same period in earlier cycles."),
        ("STL residual", 16, True, "Removes trend and seasonality before scoring residuals."),
        ("State-space", 12, False, "Produces an evolving level estimate and prediction interval."),
        ("ARIMA/ETS", 12, False, "Fits a forecasting model and scores out-of-range observations."),
    ]
    rows = []
    for name, minimum, needs_seasonality, explanation in methods:
        eligible = observation_count >= minimum and (not needs_seasonality or seasonal)
        reason = "Available" if eligible else (f"Needs at least {minimum} observations" if observation_count < minimum else "Needs quarterly or monthly observations")
        rows.append({"Method": name, "Available": eligible, "Minimum observations": minimum, "Reason": reason, "Description": explanation})
    return pd.DataFrame(rows)


def _mahalanobis_year(tidy: pd.DataFrame, year: str, indicators: list[str], min_observed_indicators: int, min_indicator_prevalence: float) -> pd.DataFrame:
    wide = tidy.pivot_table(index=HIERARCHY_COLUMNS + ["Facility Type"], columns="Data Element", values=year, aggfunc="first")
    wide = wide.reindex(columns=indicators)
    if wide.empty:
        return pd.DataFrame()
    observed = wide.notna()
    prevalence = observed.mean(axis=0)
    retained = [indicator for indicator in indicators if prevalence[indicator] >= min_indicator_prevalence]
    wide = wide[retained]
    observed = wide.notna()
    observed_counts = observed.sum(axis=1)
    wide = wide.loc[observed_counts >= min_observed_indicators]
    observed = observed.loc[wide.index]
    if wide.empty or wide.shape[1] == 0:
        return pd.DataFrame()

    medians = wide.median(axis=0)
    values = wide.fillna(medians).to_numpy(dtype=float)
    centered = values - medians.to_numpy(dtype=float)
    covariance = np.cov(centered, rowvar=False) if len(wide) > 1 else np.eye(wide.shape[1])
    covariance = np.atleast_2d(np.nan_to_num(covariance, nan=0.0))
    variances = np.var(centered, axis=0)
    for index, variance in enumerate(variances):
        covariance[index, index] = variance if np.isfinite(variance) and variance > 0 else 1.0
    diagonal = np.diag(covariance)
    regularisation = max(float(np.nanmedian(diagonal)) * 0.05, 1e-6)
    covariance += np.eye(len(retained)) * regularisation
    inverse = np.linalg.pinv(covariance)
    precision_centered = centered @ inverse
    signed_contributions = centered * precision_centered
    distances = np.sqrt(np.maximum(signed_contributions.sum(axis=1), 0.0))
    records = wide.reset_index()
    records["Year"] = int(year)
    records["Value"] = wide.sum(axis=1).to_numpy()
    records["Raw Mahalanobis Distance"] = distances
    records["Observed Indicators"] = observed.sum(axis=1).to_numpy()
    records["Total Indicators"] = len(retained)
    records["Score"] = records["Raw Mahalanobis Distance"] / np.sqrt(records["Total Indicators"])
    top_contributors = []
    top_contribution_details = []
    for row_index in range(len(records)):
        magnitudes = np.abs(signed_contributions[row_index])
        order = np.argsort(magnitudes)[::-1][:3]
        total_magnitude = magnitudes[order].sum()
        details = [f"{retained[index]} ({magnitudes[index] / total_magnitude:.2%})" for index in order if magnitudes[index] > 0 and total_magnitude > 0 and magnitudes[index] / total_magnitude >= 0.0001]
        top_contributors.append("\n".join(details))
    records["Leading indicators"] = top_contributors
    metadata = wide.index.names + ["Year", "Raw Mahalanobis Distance", "Observed Indicators", "Total Indicators", "Score", "Leading indicators"]
    return records[metadata]


def profile_anomalies(tidy: pd.DataFrame, year: str, threshold: float = 1.5, min_observed_indicators: int = 10, min_indicator_prevalence: float = 0.2, min_peer_facilities: int = 30, include_other: bool = False) -> pd.DataFrame:
    """Score one year with vectorized, dimension-normalized facility-type distances."""
    scoring_data = tidy if include_other else tidy[tidy["Facility Type"].ne("other/unclassified")]
    indicators = sorted(scoring_data["Data Element"].dropna().unique())
    frames = []
    for _, peer_group in scoring_data.groupby(["Facility Type"], dropna=False, sort=False):
        if peer_group["Facility"].nunique() < min_peer_facilities:
            continue
        frames.append(_mahalanobis_year(peer_group, year, indicators, min_observed_indicators, min_indicator_prevalence))
    result = pd.concat([frame for frame in frames if not frame.empty], ignore_index=True) if frames else pd.DataFrame()
    return result[result["Score"] >= threshold].reset_index(drop=True) if not result.empty else result


def rank_profile_anomalies(profiles: pd.DataFrame, mode: str = "top_percent", amount: float = 5) -> pd.DataFrame:
    """Return a user-sized review list without requiring score interpretation."""
    if profiles.empty:
        return profiles.copy()
    ranked = profiles.sort_values(["Score", "Raw Mahalanobis Distance"], ascending=False).reset_index(drop=True).copy()
    if mode == "top_n":
        count = min(max(int(amount), 1), len(ranked))
    else:
        count = min(max(int(np.ceil(len(ranked) * float(amount) / 100)), 1), len(ranked))
    ranked = ranked.head(count).copy()
    ranked.insert(0, "Rank", np.arange(1, len(ranked) + 1))
    return ranked


def impossible_values(tidy: pd.DataFrame, year: str) -> pd.DataFrame:
    columns = HIERARCHY_COLUMNS + ["Data Element", "Facility Type"]
    result = tidy.loc[tidy[year].lt(0), columns].copy()
    result["Year"] = int(year)
    result["Value"] = tidy.loc[result.index, year].astype(float)
    result["Score"] = np.inf
    result["Detector"] = "impossible_value"
    result["Explanation"] = "The reported count is negative."
    return result.reset_index(drop=True)


def analyze_routine_tidy(tidy: pd.DataFrame, year: str, threshold: float = 3.5, min_observed_indicators: int = 10, min_indicator_prevalence: float = 0.2, profile_threshold: float = 1.5, min_peer_facilities: int = 30, include_other: bool = False, min_absolute_change: float = 0.0, treat_leading_zeros_missing: bool = True, progress_callback=None) -> dict[str, pd.DataFrame | list[str]]:
    if progress_callback:
        progress_callback(0.15, "Workbook loaded and prepared")
    years = sorted([column for column in tidy.columns if re.fullmatch(r"20\d{2}", str(column)) and column != "2026"])
    if year not in years:
        raise ValueError(f"Selected year {year} is not available. Choose one of: {', '.join(years)}")
    time_series_data = mask_leading_zeros_as_missing(tidy, years) if treat_leading_zeros_missing else tidy
    impossible = impossible_values(tidy, year)
    if progress_callback:
        progress_callback(0.30, "Checked impossible values")
    temporal = temporal_anomalies(time_series_data, year, years, threshold, min_absolute_change)
    if progress_callback:
        progress_callback(0.45, "Scored historical-level anomalies")
    trend = robust_trend_anomalies(time_series_data, year, years, threshold, min_absolute_change=min_absolute_change)
    if progress_callback:
        progress_callback(0.60, "Scored robust trends")
    global_trend = global_trend_adjusted_anomalies(time_series_data, year, years, threshold, min_absolute_change=min_absolute_change, min_indicator_prevalence=min_indicator_prevalence)
    if progress_callback:
        progress_callback(0.75, "Adjusted for shared indicator trends")
    profile = profile_anomalies(tidy, year, profile_threshold, min_observed_indicators, min_indicator_prevalence, min_peer_facilities, include_other)
    if progress_callback:
        progress_callback(1.0, "Completed facility-profile scoring")
    return {"tidy": tidy, "years": years, "selected_year": year,
            "impossible": impossible, "temporal": temporal, "trend": trend,
            "global_trend": global_trend, "methods": method_catalog(len(years), "annual"), "profile": profile}


def run_routine_analysis(source: str | Path | BinaryIO, year: str, threshold: float = 3.5, min_observed_indicators: int = 10, min_indicator_prevalence: float = 0.2, profile_threshold: float = 1.5, min_peer_facilities: int = 30, include_other: bool = False, progress_callback=None) -> dict[str, pd.DataFrame | list[str]]:
    return analyze_routine_tidy(load_routine_export(source), year, threshold, min_observed_indicators, min_indicator_prevalence, profile_threshold, min_peer_facilities, include_other, progress_callback)

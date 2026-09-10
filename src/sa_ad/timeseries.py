"""Generic time-series anomaly methods for future quarterly/monthly inputs."""

from __future__ import annotations

import numpy as np
import pandas as pd


def _scale(values: pd.Series) -> float:
    observed = values.dropna().astype(float)
    if len(observed) < 2:
        return 1.0
    median = observed.median()
    mad = np.median(np.abs(observed - median))
    return float(max(1.4826 * mad, observed.std(ddof=0), 1.0))


def ewma_scores(values: pd.Series, span: int = 4) -> pd.DataFrame:
    """Score observations by deviation from an exponentially weighted level."""
    series = pd.to_numeric(values, errors="coerce")
    baseline = series.shift(1).ewm(span=span, min_periods=2, adjust=False).mean()
    scale = _scale(series)
    result = pd.DataFrame({"Value": series, "Expected": baseline})
    result["Score"] = (result["Value"] - result["Expected"]).abs() / scale
    result["Detector"] = "ewma"
    result["Explanation"] = result["Score"].map(lambda score: f"The observation is {score:.1f} robust scale units from the prior EWMA level." if pd.notna(score) else "Insufficient prior observations for EWMA scoring.")
    return result


def cusum_scores(values: pd.Series, drift: float = 0.5) -> pd.DataFrame:
    """Accumulate standardized evidence of a sustained upward or downward shift."""
    series = pd.to_numeric(values, errors="coerce")
    center = series.expanding(min_periods=2).median().shift(1)
    scale = _scale(series)
    residual = ((series - center) / scale).fillna(0.0)
    positive = np.zeros(len(series))
    negative = np.zeros(len(series))
    for index in range(1, len(series)):
        positive[index] = max(0.0, positive[index - 1] + residual.iloc[index] - drift)
        negative[index] = min(0.0, negative[index - 1] + residual.iloc[index] + drift)
    result = pd.DataFrame({"Value": series, "Positive CUSUM": positive, "Negative CUSUM": negative})
    result["Score"] = np.maximum(positive, -negative)
    result["Detector"] = "cusum"
    result["Explanation"] = result["Score"].map(lambda score: f"CUSUM accumulated {score:.1f} standardized units of sustained shift.")
    return result


def seasonal_baseline_scores(values: pd.Series, phase: pd.Series, min_cycles: int = 3) -> pd.DataFrame:
    """Compare each observation with the median for its seasonal phase."""
    series = pd.to_numeric(values, errors="coerce")
    phases = pd.Series(phase, index=series.index)
    counts = phases.groupby(phases).size()
    baseline = phases.map(series.groupby(phases).median())
    scale = _scale(series - baseline)
    result = pd.DataFrame({"Value": series, "Expected": baseline, "Cycles": phases.map(counts)})
    result["Score"] = (result["Value"] - result["Expected"]).abs() / scale
    result.loc[result["Cycles"] < min_cycles, "Score"] = np.nan
    result["Detector"] = "seasonal_baseline"
    result["Explanation"] = result.apply(lambda row: f"The observation is {row['Score']:.1f} robust scale units from its seasonal-phase median." if pd.notna(row["Score"]) else "Fewer than the required seasonal cycles are available.", axis=1)
    return result

"""
Statistical metrics used across all experiments.

All metrics operate on plain Python lists/floats -- no numpy required.
numpy is used only when available for AUROC (degrades gracefully otherwise).
"""
from __future__ import annotations

import math
import statistics
import sys

# Force UTF-8 on Windows so hypothesis strings with >= symbols print correctly
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
from typing import Sequence

from examples.experiments.shared.config import ExperimentResult


# ── Core metrics ──────────────────────────────────────────────────────────────

def improvement_pct(baseline: float, improved: float) -> float:
    """Percentage improvement from baseline to improved.

    Positive = improved > baseline (e.g. accuracy went up).
    Returns 0.0 when baseline is zero to avoid division errors.
    """
    if baseline == 0.0:
        return 0.0
    return (improved - baseline) / abs(baseline) * 100.0


def silent_failure_rate(
    n_bad_outputs: int,
    n_total_outputs: int,
) -> float:
    """Fraction of bad outputs that were NOT caught (passed through silently).

    Args:
        n_bad_outputs: Number of bad outputs that slipped through.
        n_total_outputs: Total outputs evaluated.
    """
    if n_total_outputs == 0:
        return 0.0
    return n_bad_outputs / n_total_outputs


def cohen_kappa(
    y_true: Sequence[int],
    y_pred: Sequence[int],
) -> float:
    """Cohen's κ for two binary classifiers.

    Values: 1.0 = perfect, 0.0 = chance, <0 = worse than chance.
    """
    assert len(y_true) == len(y_pred), "y_true and y_pred must have equal length"
    n = len(y_true)
    if n == 0:
        return 0.0

    # Agreement matrix counts
    tp = sum(1 for a, b in zip(y_true, y_pred) if a == 1 and b == 1)
    tn = sum(1 for a, b in zip(y_true, y_pred) if a == 0 and b == 0)
    fp = sum(1 for a, b in zip(y_true, y_pred) if a == 0 and b == 1)
    fn = sum(1 for a, b in zip(y_true, y_pred) if a == 1 and b == 0)

    observed_agreement = (tp + tn) / n

    # Expected agreement under independence
    p_actual_pos = (tp + fn) / n
    p_pred_pos = (tp + fp) / n
    p_actual_neg = 1.0 - p_actual_pos
    p_pred_neg = 1.0 - p_pred_pos
    expected_agreement = p_actual_pos * p_pred_pos + p_actual_neg * p_pred_neg

    if expected_agreement == 1.0:
        return 1.0
    return (observed_agreement - expected_agreement) / (1.0 - expected_agreement)


def auroc(
    y_true: Sequence[int],
    scores: Sequence[float],
) -> float:
    """Area under the ROC curve (pure-Python trapezoidal implementation).

    Args:
        y_true: Ground truth binary labels (0 or 1).
        scores: Continuous prediction scores (higher = more likely positive).
    """
    assert len(y_true) == len(scores)
    n = len(y_true)
    if n == 0:
        return 0.5

    # Sort by score descending
    paired = sorted(zip(scores, y_true), key=lambda x: -x[0])

    n_pos = sum(y_true)
    n_neg = n - n_pos
    if n_pos == 0 or n_neg == 0:
        return 0.5

    tp, fp = 0, 0
    prev_fpr, prev_tpr = 0.0, 0.0
    auc = 0.0

    for _, label in paired:
        if label == 1:
            tp += 1
        else:
            fp += 1
        tpr = tp / n_pos
        fpr = fp / n_neg
        auc += (fpr - prev_fpr) * (tpr + prev_tpr) / 2.0
        prev_fpr, prev_tpr = fpr, tpr

    return round(auc, 4)


def f1(
    y_true: Sequence[int],
    y_pred: Sequence[int],
) -> float:
    """Binary F1 score."""
    assert len(y_true) == len(y_pred)
    tp = sum(1 for a, b in zip(y_true, y_pred) if a == 1 and b == 1)
    fp = sum(1 for a, b in zip(y_true, y_pred) if a == 0 and b == 1)
    fn = sum(1 for a, b in zip(y_true, y_pred) if a == 1 and b == 0)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0

    if precision + recall == 0:
        return 0.0
    return 2.0 * precision * recall / (precision + recall)


def recall_score(
    y_true: Sequence[int],
    y_pred: Sequence[int],
) -> float:
    """Binary recall."""
    tp = sum(1 for a, b in zip(y_true, y_pred) if a == 1 and b == 1)
    fn = sum(1 for a, b in zip(y_true, y_pred) if a == 1 and b == 0)
    return tp / (tp + fn) if (tp + fn) > 0 else 0.0


def r_squared(x: Sequence[float], y: Sequence[float]) -> float:
    """Coefficient of determination (R²) for a simple linear regression."""
    n = len(x)
    if n < 2:
        return 0.0
    mean_x = statistics.mean(x)
    mean_y = statistics.mean(y)

    ss_tot = sum((yi - mean_y) ** 2 for yi in y)
    if ss_tot == 0:
        return 1.0  # perfect constant fit

    # Slope and intercept via least squares
    denom = sum((xi - mean_x) ** 2 for xi in x)
    if denom == 0:
        return 0.0
    slope = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y)) / denom
    intercept = mean_y - slope * mean_x

    ss_res = sum((yi - (slope * xi + intercept)) ** 2 for xi, yi in zip(x, y))
    return round(1.0 - ss_res / ss_tot, 4)


def is_statistically_significant(
    n_success: int,
    n_total: int,
    null_p: float,
    alpha: float = 0.05,
) -> bool:
    """One-sided binomial test: is observed success rate > null_p at level alpha?

    Uses a normal approximation (valid for n >= 30).
    """
    if n_total == 0:
        return False
    p_hat = n_success / n_total
    se = math.sqrt(null_p * (1 - null_p) / n_total)
    if se == 0:
        return p_hat > null_p
    z = (p_hat - null_p) / se
    # One-sided p-value using error function approximation
    # P(Z > z) = 0.5 * erfc(z / sqrt(2))
    p_val = 0.5 * math.erfc(z / math.sqrt(2))
    return p_val < alpha


# ── Reporting ─────────────────────────────────────────────────────────────────

def print_result(result: ExperimentResult) -> None:
    """Print a formatted experiment result to stdout."""
    status = "[PASS]" if result.passed else "[FAIL]"
    bar = "=" * 60
    print(f"\n{bar}")
    print(f"  {result.experiment_id}  |  {status}")
    print(f"  {result.hypothesis}")
    print(f"  {result.primary_metric}: {result.primary_value:.4f}  "
          f"(threshold: {result.threshold:.4f})")
    if result.secondary_metrics:
        for k, v in result.secondary_metrics.items():
            val_str = f"{v:.4f}" if isinstance(v, float) else str(v)
            print(f"    {k}: {val_str}")
    if result.notes:
        print(f"  note: {result.notes}")
    print(bar)

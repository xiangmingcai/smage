"""Feature Down aggregation helpers.

No Qt and no project state: rules and a DataFrame go in, a DataFrame comes
out.
"""
from __future__ import annotations


def _feature_auto_name(rule) -> str:
    """Default new-column name for a rule: '<source>_<method>' (or 'count')."""
    if rule.get("method") == "count":
        return "count"
    source = rule.get("source") or "value"
    method = rule.get("method") or "mean"
    if method == "percentile":
        perc = rule.get("perc")
        suffix = f"p{int(perc)}" if perc else "p"
    else:
        suffix = method
    return f"{source}_{suffix}"


def _feature_safe_stem(text: str) -> str:
    """Make a filesystem-safe file stem (no path separators or reserved chars)."""
    import re

    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", str(text)).strip("._")
    return cleaned or "feature"


def _feature_count_name(params) -> str:
    """The Count column name: its stored name, made unique on a clash.

    Defaults to 'count'; when that would collide with the feature column or
    another rule's new name it becomes 'count_1', 'count_2', …
    """
    rules = params.get("rules") or []
    stored = (rules[0].get("name") or "").strip() if rules else ""
    base = stored or "count"
    used = {
        (r.get("name") or "").strip()
        for r in rules
        if r.get("method") != "count"
    }
    used.add(params.get("feature") or "")
    if base not in used:
        return base
    n = 1
    while f"{base}_{n}" in used:
        n += 1
    return f"{base}_{n}"


def _feature_aggregate_frame(df, feature, rules, count_name):
    """Aggregate `df` by the `feature` column.

    Every distinct feature value becomes one output row. The first rule is the
    fixed Count; the others aggregate one source variable each, optionally
    restricted to an in-class percentile range (sorted by that row's own
    source variable). Returns ``(out, columns, warnings)``.
    """
    import pandas as pd

    grouped = df.groupby(feature, sort=True)
    sizes = grouped.size()
    out = pd.DataFrame(index=sizes.index)
    out.index.name = feature
    out[count_name] = sizes.astype("int64")
    warnings = []

    for rule in rules:
        if rule.get("method") == "count":
            continue
        source = rule["source"]
        method = rule.get("method") or "mean"
        name = (rule.get("name") or _feature_auto_name(rule)).strip()
        series = df[source]
        lo = float(rule.get("lo", 0.0))
        hi = float(rule.get("hi", 100.0))
        if lo <= 0.0 and hi >= 100.0:
            values_in_range = series
        else:
            # Per-class thresholds; mapping the group quantiles back onto the
            # rows is much faster than a per-group transform.
            q_lo_by = grouped[source].quantile(lo / 100.0)
            q_hi_by = grouped[source].quantile(hi / 100.0)
            q_lo = df[feature].map(q_lo_by)
            q_hi = df[feature].map(q_hi_by)
            keep = (series >= q_lo) & (series <= q_hi)
            values_in_range = series.where(keep)
        sub = values_in_range.groupby(df[feature], sort=True)
        if method == "percentile":
            agg = sub.quantile(float(rule.get("perc") or 50.0) / 100.0)
        elif method == "sum":
            agg = sub.sum(min_count=1)
        else:
            agg = sub.agg(method)
        out[name] = agg.reindex(out.index)
        if lo > 0.0 or hi < 100.0:
            empty = int(agg.isna().sum())
            if empty:
                warnings.append(
                    f"{empty} class(es) had no points in {lo:g}–{hi:g}% for '{name}'"
                )

    out = out.reset_index()
    columns = [feature, count_name] + [
        (r.get("name") or _feature_auto_name(r)).strip()
        for r in rules
        if r.get("method") != "count"
    ]
    return out.loc[:, columns], columns, warnings

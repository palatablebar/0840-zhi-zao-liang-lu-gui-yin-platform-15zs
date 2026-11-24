import hashlib
from decimal import Decimal
from pathlib import Path

from app.catalog import SPEC
from app.domain.calculate import calculate_rows
from app.domain.decision import decide_rows
from app.domain.prepare import prepare_rows
from app.intake.service import load
from app.reconcile.ledger import build_ledger


# Result fields that are frozen verbatim when a work order must keep its
# previously accepted numbers (交接量对不上).
RESULT_FIELDS = ("label", "rolling", "stage_count", "stage_seqs", "input_total", "lost", "problem",
                 "handover_ok", "first_pass_good", "rework_good_total", "metric", "limit",
                 "verdict", "reason")
ROW_FIELDS = ("label", "verdict", "reason", "metric", "limit", "status", "state", "breakdown",
              "update_state", "supplement_reason", "first_pass_good", "rework_good_total", "lost",
              "input_total", "rolling")


def jsonable(value):
    """Convert internal values to a JSON-safe view, dropping ``_internal`` keys."""
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items() if not str(key).startswith("_")}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    return value


def baseline_of(item):
    """The numbers that define a work order's accepted result."""
    return {field: item[field] for field in RESULT_FIELDS if field in item}


def _stage(name):
    return SPEC["stages"].index(name) + 1


def result_summary(results):
    summary = {"PASS": 0, "REVIEW": 0, "REJECT": 0}
    for item in results:
        verdict = item.get("verdict", "REVIEW")
        summary[verdict] = summary.get(verdict, 0) + 1
    return summary


def result_chart(results):
    chart_field = {"0840": "rolling", "0849": "mean", "0850": "settlement_cents"}.get(SPEC["code"], "metric")
    return [{"label": item.get("label", item.get(SPEC["key"], "")),
             "value": float(item.get(chart_field) or 0),
             "limit": float(item.get("limit") or 0)}
            for item in results[:12]]


def result_rows(results):
    rows = []
    for item in results[:18]:
        rows.append({key: value for key, value in item.items()
                     if key in ROW_FIELDS or key in SPEC["columns"] or key in SPEC["result_columns"]})
    return rows


def _apply_retention(results, retained, states):
    """Keep the accepted result for work orders whose handover does not reconcile."""
    final, computed = [], set()
    for item in results:
        label = item["label"]
        computed.add(label)
        state = states.get(label) or {}
        missing_stage = label in retained and bool(
            set(retained[label].get("stage_seqs") or []) - set(item.get("stage_seqs") or []))
        keep = label in retained and (not item.get("handover_ok", True) or missing_stage)
        if keep:
            kept = dict(retained[label])
            kept["update_state"] = "RETAINED"
            reason = state.get("reason")
            if not reason:
                reason = "DATA_INVALID" if missing_stage else "STAGE_DISCONTINUITY"
            kept["supplement_reason"] = reason
            final.append(kept)
            continue
        item = dict(item)
        if state:
            item["update_state"] = state.get("state")
        if not item.get("handover_ok", True):
            item["supplement_reason"] = "STAGE_DISCONTINUITY"
        elif state.get("reason"):
            item["supplement_reason"] = state.get("reason")
        final.append(item)
    # A retained work order may stop being computable after a supplement merges;
    # its accepted result still has to stay in the batch.
    for label, kept in retained.items():
        if label not in computed:
            item = dict(kept)
            item["update_state"] = "RETAINED"
            item["supplement_reason"] = (states.get(label) or {}).get("reason") or "DATA_INVALID"
            final.append(item)
    return final


def refresh(payload):
    """Rebuild derived view fields from ``_results`` after a supplement."""
    results = payload.get("_results") or []
    payload["rows"] = result_rows(results)
    payload["chart"] = result_chart(results)
    payload["summary"] = result_summary(results)
    if payload.get("ledger"):
        payload["ledger"]["counts"]["computed"] = len(results)
        payload["ledger"]["metric_total"] = str(
            sum((item.get("metric") or 0 for item in results), Decimal("0")))
        payload["ledger"]["updated_orders"] = sum(1 for item in results if item.get("update_state") == "UPDATED")
        payload["ledger"]["retained_orders"] = sum(1 for item in results if item.get("update_state") == "RETAINED")
        payload["ledger"]["checks"] = [
            {"id": "source", "label": "来源行守恒", "ok": payload["ledger"].get("balanced", False)},
            {"id": "computed", "label": "计算条目完整", "ok": payload["ledger"]["counts"]["computed"] == len(results)},
        ]
        if payload["ledger"]["counts"].get("supplements"):
            payload["ledger"]["checks"].append(
                {"id": "supplement", "label": "补报工单已归类",
                 "ok": payload["ledger"]["updated_orders"] + payload["ledger"]["retained_orders"] <= len(results)})
    return payload


def compose(profile, source, base_counts, intake_issues, rows, upto="证据交付",
            retained=None, states=None, supplements=None, merge_issues=(), supplement_count=0):
    """Build a run payload from (possibly supplemented) source rows.

    ``retained`` maps work orders to their accepted result, kept verbatim when
    the handover quantity does not reconcile. ``states`` records the supplement
    outcome per work order. The returned payload keeps ``Decimal`` values and
    ``_``-prefixed internals; call :func:`jsonable` for the API view.
    """
    retained = retained or {}
    states = states or {}
    prepared, prepare_issues, results = [], [], []
    if _stage(upto) >= 2:
        prepared, prepare_issues = prepare_rows(rows)
    if _stage(upto) >= 3:
        results = calculate_rows(prepared)
    if _stage(upto) >= 4:
        results = decide_rows(results)
    if retained:
        results = _apply_retention(results, retained, states)

    counts = dict(base_counts)
    counts["prepared"] = len(prepared)
    counts["computed"] = len(results)
    issues = list(intake_issues) + list(merge_issues) + list(prepare_issues)
    run_key = hashlib.sha256(
        (SPEC["code"] + profile + source["sha256"] + SPEC["rule"]).encode("utf-8")).hexdigest()[:24]
    payload = {
        "project": SPEC["code"] + " " + SPEC["name"],
        "rule": SPEC["rule_short"],
        "profile": profile,
        "source": source,
        "counts": counts,
        "issues": issues[:60],
        "rows": [],
        "row_kind": "result",
        "chart": [],
        "summary": {"PASS": 0, "REVIEW": 0, "REJECT": 0},
        "ledger": None,
        "stages": [{"id": "intake", "name": SPEC["stages"][0]},
                   {"id": "prepare", "name": SPEC["stages"][1]},
                   {"id": "calculate", "name": SPEC["stages"][2]},
                   {"id": "decision", "name": SPEC["stages"][3]},
                   {"id": "reconcile", "name": SPEC["stages"][4]}],
        "run_key": run_key,
        "supplements": list(supplements or []),
        "_source_rows": [dict(row) for row in rows],
        "_base_counts": dict(base_counts),
        "_intake_issues": list(intake_issues),
        "_merge_issues": list(merge_issues),
        "_results": results,
        "_effective": {item["label"]: baseline_of(item) for item in results},
        "_states": dict(states),
    }

    if _stage(upto) == 1:
        payload["rows"] = [{key: item.get(key) for key in SPEC["columns"]} for item in rows[:18]]
        payload["row_kind"] = "source"
        payload["summary"] = {"PASS": 0, "REVIEW": 0, "REJECT": 0}
        return payload
    if _stage(upto) == 2:
        payload["rows"] = [{key: item.get(key) for key in SPEC["columns"]} for item in prepared[:18]]
        payload["row_kind"] = "source"
        payload["summary"] = {"PASS": 0, "REVIEW": 0, "REJECT": 0}
        return payload

    payload["rows"] = result_rows(results)
    payload["chart"] = result_chart(results)
    payload["summary"] = result_summary(results)
    if _stage(upto) >= 5:
        payload["ledger"] = build_ledger(counts, prepared, prepare_issues, results,
                                         supplement_count=supplement_count)
    return payload


def pipeline(profile="dirty", path=None, upto="证据交付"):
    intake = load(profile, path)
    source = {"profile": profile, "filename": Path(intake["path"]).name, "sha256": intake["sha256"]}
    payload = compose(profile, source, intake["counts"], intake["issues"], intake["rows"], upto=upto)
    return jsonable(payload)


def build_run(profile="dirty", path=None):
    """Raw payload for storage: keeps Decimal precision and internals."""
    intake = load(profile, path)
    source = {"profile": profile, "filename": Path(intake["path"]).name, "sha256": intake["sha256"]}
    return compose(profile, source, intake["counts"], intake["issues"], intake["rows"])

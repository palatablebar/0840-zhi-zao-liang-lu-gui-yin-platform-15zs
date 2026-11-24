from decimal import Decimal

from app.catalog import SPEC
from app.reconcile.checks import all_checks


def build_ledger(counts, prepared, prepare_issues, results, supplement_count=0):
    counts = dict(counts)
    counts["prepared"] = len(prepared)
    counts["prepare_issues"] = len(prepare_issues)
    counts["computed"] = len(results)
    counts["supplements"] = supplement_count
    balanced = (counts["input"] ==
                counts["accepted"] + counts["isolated"] + counts["duplicates"] + counts["ignored"])
    metric = sum((item.get("metric") for item in results if item.get("metric") is not None), Decimal("0"))
    updated = sum(1 for item in results if item.get("update_state") == "UPDATED")
    retained = sum(1 for item in results if item.get("update_state") == "RETAINED")
    checks = [
        {"id": "source", "label": "来源行守恒", "ok": balanced},
        {"id": "computed", "label": "计算条目完整", "ok": counts["computed"] == len(results)},
    ]
    if supplement_count:
        # 每个工单要么更新、要么沿用，不能两者都不是或被重复归类。
        checks.append({"id": "supplement", "label": "补报工单已归类",
                       "ok": updated + retained <= len(results)})
    return {"counts": counts, "balanced": balanced,
            "updated_orders": updated, "retained_orders": retained,
            "metric_field": SPEC.get("metric_field", "metric"),
            "metric_total": str(metric), "checks": checks, "all_checks": all_checks(checks)}

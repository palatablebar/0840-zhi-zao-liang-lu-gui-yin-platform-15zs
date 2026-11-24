from app.catalog import SPEC
from app.domain.helpers import D


def calculate_rows(items):
    out = []
    orders = {}
    for item in items:
        orders.setdefault(item["work_order"], []).append(item)
    for work_order, stages in orders.items():
        stages.sort(key=lambda row: (row["stage_seq"], row.get("_line", 0)))
        rolling = D("1", "CALC")
        problem = None
        handover_ok = True
        input_total = 0
        lost = 0
        first_pass_good = 0
        rework_good_total = 0
        for index, stage in enumerate(stages):
            if index:
                previous = stages[index - 1]
                # 交接量：下道实收应等于上道首检良品 + 返工良品。
                if stage["input_qty"] != previous["good_qty"] + previous["rework_good"]:
                    problem = "STAGE_DISCONTINUITY"
                    handover_ok = False
            # 首检良率只看首检良品，返工良品不计入一次通过。
            rolling *= D(str(stage["good_qty"]), "CALC") / D(str(stage["input_qty"]), "CALC")
            input_total += stage["input_qty"]
            lost += stage["scrap_qty"] + stage["rework_qty"] - stage["rework_good"]
            first_pass_good += stage["good_qty"]
            rework_good_total += stage["rework_good"]
        out.append({"label": work_order, "rolling": rolling, "stage_count": len(stages),
                    "stage_seqs": [stage["stage_seq"] for stage in stages],
                    "input_total": input_total, "lost": lost, "problem": problem,
                    "handover_ok": handover_ok, "first_pass_good": first_pass_good,
                    "rework_good_total": rework_good_total,
                    "metric": D(str(input_total), "CALC"), "limit": D("0.90", "LIMIT")})
    for item in out:
        item.setdefault("label", str(item.get(SPEC["key"], "")))
    return out

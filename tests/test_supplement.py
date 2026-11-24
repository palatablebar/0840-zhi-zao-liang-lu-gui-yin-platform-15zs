import csv
import io


COLUMNS = ["row_id", "work_order", "stage_seq", "input_qty", "good_qty", "scrap_qty", "rework_qty", "rework_good"]


def report(rows):
    return {"columns": COLUMNS, "rows": rows}


def create(client, profile="clean"):
    response = client.post("/api/runs", json={"profile": profile})
    assert response.status_code == 200
    return response.get_json()


def approve(client, run):
    response = client.post(f"/api/runs/{run['run_id']}/review",
                           json={"decision": "APPROVED", "comment": "已核验", "expected_revision": run["revision"]})
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def result(run, label):
    return next(item for item in run["rows"] if item["label"] == label)


# WO1 的交接量：冲压 good 950 + rework_good 10 = 960，焊接实收 960，原本对得上。
# WO2：冲压 good 490 + rework_good 4 = 494，焊接实收 494，原本对得上。


def test_clean_scenario_defaults_to_no_update_state(client):
    run = create(client)
    assert all(result(run, label).get("update_state") is None for label in ("WO1", "WO2"))


def test_rework_good_is_not_counted_as_first_pass(client):
    run = create(client)
    wo1 = result(run, "WO1")
    # 首检良品只累加 good_qty；返工良品单独统计。
    assert wo1["first_pass_good"] == 950 + 912
    # 滚动良率 = (950/1000) * (912/960)，返工良品未计入分子。
    assert abs(wo1["rolling"] - (950 / 1000) * (912 / 960)) < 1e-9


def test_reconciled_work_order_updates_loss_after_signoff(client):
    run = approve(client, create(client))
    before = result(run, "WO1")["lost"]
    response = client.post(f"/api/runs/{run['run_id']}/supplements", json=report([
        {"row_id": "R002", "work_order": "WO1", "stage_seq": "2", "input_qty": "960", "good_qty": "920",
         "scrap_qty": "25", "rework_qty": "15", "rework_good": "5"},
    ]))
    assert response.status_code == 200
    updated = response.get_json()
    wo1 = result(updated, "WO1")
    assert wo1["update_state"] == "UPDATED"
    assert wo1["lost"] != before
    assert updated["revision"] == run["revision"] + 1


def test_handover_mismatch_keeps_signed_result_but_other_orders_update(client):
    run = approve(client, create(client))
    signed_wo1 = dict(result(run, "WO1"))
    signed_wo2 = dict(result(run, "WO2"))
    response = client.post(f"/api/runs/{run['run_id']}/supplements", json=report([
        # WO1 焊接实收 955 ≠ 冲压 good 950 + rework_good 10；交接量对不上。
        {"row_id": "R002", "work_order": "WO1", "stage_seq": "2", "input_qty": "955", "good_qty": "910",
         "scrap_qty": "30", "rework_qty": "15", "rework_good": "5"},
        # WO2 仍对得上，损耗照常更新。
        {"row_id": "R004", "work_order": "WO2", "stage_seq": "2", "input_qty": "494", "good_qty": "460",
         "scrap_qty": "24", "rework_qty": "10", "rework_good": "8"},
    ]))
    assert response.status_code == 200
    updated = response.get_json()
    wo1, wo2 = result(updated, "WO1"), result(updated, "WO2")
    assert wo1["update_state"] == "RETAINED"
    assert wo1["supplement_reason"] == "STAGE_DISCONTINUITY"
    for field in ("rolling", "lost", "input_total", "verdict"):
        assert wo1[field] == signed_wo1[field]
    assert wo2["update_state"] == "UPDATED"
    assert wo2["lost"] != signed_wo2["lost"]
    assert set(updated["supplements"][-1]["retained"]) == {"WO1"}
    assert set(updated["supplements"][-1]["updated"]) == {"WO2"}


def test_late_actual_receipt_updates_previously_retained_order(client):
    run = approve(client, create(client))
    first = client.post(f"/api/runs/{run['run_id']}/supplements", json=report([
        {"row_id": "R002", "work_order": "WO1", "stage_seq": "2", "input_qty": "955", "good_qty": "910",
         "scrap_qty": "30", "rework_qty": "15", "rework_good": "5"},
    ])).get_json()
    assert result(first, "WO1")["update_state"] == "RETAINED"
    signed_lost = result(first, "WO1")["lost"]
    # 实收补齐：下道实收回到 960 = 冲压 good 950 + rework_good 10，交接量重新对上。
    second = client.post(f"/api/runs/{run['run_id']}/supplements", json=report([
        {"row_id": "R002", "work_order": "WO1", "stage_seq": "2", "input_qty": "960", "good_qty": "925",
         "scrap_qty": "25", "rework_qty": "10", "rework_good": "5"},
    ])).get_json()
    wo1 = result(second, "WO1")
    assert wo1["update_state"] == "UPDATED"
    assert wo1["lost"] != signed_lost


def test_retained_order_updates_when_prior_stage_supplement_reconciles(client):
    run = approve(client, create(client))
    # 先改上道冲压的返工良品，使焊接实收 960 不再对得上。
    first = client.post(f"/api/runs/{run['run_id']}/supplements", json=report([
        {"row_id": "R001", "work_order": "WO1", "stage_seq": "1", "input_qty": "1000", "good_qty": "950",
         "scrap_qty": "30", "rework_qty": "20", "rework_good": "12"},
    ])).get_json()
    assert result(first, "WO1")["update_state"] == "RETAINED"
    # 下道实收补到 962 后重新对上，工单更新。
    second = client.post(f"/api/runs/{run['run_id']}/supplements", json=report([
        {"row_id": "R002", "work_order": "WO1", "stage_seq": "2", "input_qty": "962", "good_qty": "912",
         "scrap_qty": "30", "rework_qty": "20", "rework_good": "10"},
    ])).get_json()
    assert result(second, "WO1")["update_state"] == "UPDATED"


def test_order_is_retained_when_supplement_makes_a_stage_row_invalid(client):
    run = approve(client, create(client))
    signed = result(run, "WO1")
    # 焊接行质量不守恒会被隔离；已签发的两工序结果不能被悄悄少算。
    updated = client.post(f"/api/runs/{run['run_id']}/supplements", json=report([
        {"row_id": "R002", "work_order": "WO1", "stage_seq": "2", "input_qty": "962", "good_qty": "912",
         "scrap_qty": "28", "rework_qty": "20", "rework_good": "10"},
    ])).get_json()
    wo1 = result(updated, "WO1")
    assert wo1["update_state"] == "RETAINED"
    assert wo1["stage_count"] == signed["stage_count"] == 2
    assert wo1["lost"] == signed["lost"]
    assert wo1["supplement_reason"] == "DATA_INVALID"


def test_replaying_same_supplement_does_not_double_count_loss(client):
    run = approve(client, create(client))
    payload = report([
        {"row_id": "R002", "work_order": "WO1", "stage_seq": "2", "input_qty": "960", "good_qty": "920",
         "scrap_qty": "25", "rework_qty": "15", "rework_good": "5"},
    ])
    first = client.post(f"/api/runs/{run['run_id']}/supplements", json=payload).get_json()
    second = client.post(f"/api/runs/{run['run_id']}/supplements", json=payload).get_json()
    assert first["revision"] == second["revision"]
    assert len(first["supplements"]) == len(second["supplements"]) == 1
    assert result(first, "WO1")["lost"] == result(second, "WO1")["lost"]


def test_replay_with_stale_revision_still_deduplicates(client):
    run = approve(client, create(client))
    payload = report([
        {"row_id": "R002", "work_order": "WO1", "stage_seq": "2", "input_qty": "960", "good_qty": "920",
         "scrap_qty": "25", "rework_qty": "15", "rework_good": "5"},
    ])
    first = client.post(f"/api/runs/{run['run_id']}/supplements", json=payload).get_json()
    # 客户端重试带着旧版本号，仍视为同一份补报而不重复计损。
    replay = client.post(f"/api/runs/{run['run_id']}/supplements?expected_revision={run['revision']}",
                         json=payload).get_json()
    assert replay["revision"] == first["revision"]
    assert replay["ledger"]["counts"]["supplements"] == 1


def test_same_content_in_different_record_order_deduplicates(client):
    run = approve(client, create(client))
    rows = [
        {"row_id": "R002", "work_order": "WO1", "stage_seq": "2", "input_qty": "960", "good_qty": "920",
         "scrap_qty": "25", "rework_qty": "15", "rework_good": "5"},
        {"row_id": "R004", "work_order": "WO2", "stage_seq": "2", "input_qty": "494", "good_qty": "460",
         "scrap_qty": "24", "rework_qty": "10", "rework_good": "8"},
    ]
    first = client.post(f"/api/runs/{run['run_id']}/supplements", json=report(rows)).get_json()
    replay = client.post(f"/api/runs/{run['run_id']}/supplements", json=report(list(reversed(rows)))).get_json()
    assert replay["revision"] == first["revision"]
    assert len(replay["supplements"]) == 1


def test_partial_columns_only_touch_present_fields(client):
    run = approve(client, create(client))
    before = result(run, "WO2")
    updated = client.post(f"/api/runs/{run['run_id']}/supplements", json={
        "rows": [{"row_id": "R004", "rework_good": "6"}],
    }).get_json()
    wo2 = result(updated, "WO2")
    assert wo2["rework_good_total"] == before["rework_good_total"] - 2
    assert wo2["input_total"] == before["input_total"]


def test_supplement_never_counts_rework_good_as_first_pass(client):
    run = approve(client, create(client))
    updated = client.post(f"/api/runs/{run['run_id']}/supplements", json=report([
        {"row_id": "R002", "work_order": "WO1", "stage_seq": "2", "input_qty": "960", "good_qty": "920",
         "scrap_qty": "25", "rework_qty": "15", "rework_good": "15"},
    ])).get_json()
    wo1 = result(updated, "WO1")
    # 首检良品仅来自 good_qty。
    assert wo1["first_pass_good"] == 950 + 920
    assert abs(wo1["rolling"] - (950 / 1000) * (920 / 960)) < 1e-12



def test_csv_supplement_body_is_accepted(client):
    run = approve(client, create(client))
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(COLUMNS)
    writer.writerow(["R004", "WO2", "2", "494", "460", "24", "10", "8"])
    response = client.post(f"/api/runs/{run['run_id']}/supplements", data=stream.getvalue().encode("utf-8"),
                           headers={"Content-Type": "text/csv"})
    assert response.status_code == 200
    assert result(response.get_json(), "WO2")["update_state"] == "UPDATED"


def test_supplement_requires_signed_run(client):
    run = create(client)  # DRAFT
    response = client.post(f"/api/runs/{run['run_id']}/supplements", json=report([
        {"row_id": "R004", "work_order": "WO2", "stage_seq": "2", "input_qty": "494", "good_qty": "460",
         "scrap_qty": "24", "rework_qty": "10", "rework_good": "8"},
    ]))
    assert response.status_code == 409
    assert response.get_json()["code"] == "NOT_APPROVED"


def test_stale_supplement_revision_is_rejected(client):
    run = approve(client, create(client))
    client.post(f"/api/runs/{run['run_id']}/supplements", json=report([
        {"row_id": "R004", "work_order": "WO2", "stage_seq": "2", "input_qty": "494", "good_qty": "460",
         "scrap_qty": "24", "rework_qty": "10", "rework_good": "8"},
    ]))
    response = client.post(f"/api/runs/{run['run_id']}/supplements?expected_revision={run['revision']}",
                           json=report([
                               {"row_id": "R004", "work_order": "WO2", "stage_seq": "2", "input_qty": "494",
                                "good_qty": "455", "scrap_qty": "29", "rework_qty": "10", "rework_good": "8"},
                           ]))
    assert response.status_code == 409
    assert response.get_json()["code"] == "STALE_REVIEW"


def test_original_signed_evidence_remains_available(client):
    run = approve(client, create(client))
    original = client.get(f"/api/runs/{run['run_id']}/evidence?revision={run['revision']}").get_json()
    client.post(f"/api/runs/{run['run_id']}/supplements", json=report([
        {"row_id": "R004", "work_order": "WO2", "stage_seq": "2", "input_qty": "494", "good_qty": "460",
         "scrap_qty": "24", "rework_qty": "10", "rework_good": "8"},
    ]))
    after = client.get(f"/api/runs/{run['run_id']}/evidence?revision={run['revision']}").get_json()
    assert after["artifact_sha256"] == original["artifact_sha256"]
    assert after["review"]["revision"] == run["revision"]
    assert after["supplements"] == []


def test_evidence_history_lists_every_version(client):
    run = approve(client, create(client))
    client.post(f"/api/runs/{run['run_id']}/supplements", json=report([
        {"row_id": "R004", "work_order": "WO2", "stage_seq": "2", "input_qty": "494", "good_qty": "460",
         "scrap_qty": "24", "rework_qty": "10", "rework_good": "8"},
    ]))
    history = client.get(f"/api/runs/{run['run_id']}/evidence/history").get_json()
    assert [item["revision"] for item in history["versions"]] == [run["revision"], run["revision"] + 1]


def test_supplement_ledger_counts_applied_reports(client):
    run = approve(client, create(client))
    client.post(f"/api/runs/{run['run_id']}/supplements", json=report([
        {"row_id": "R004", "work_order": "WO2", "stage_seq": "2", "input_qty": "494", "good_qty": "460",
         "scrap_qty": "24", "rework_qty": "10", "rework_good": "8"},
    ]))
    updated = client.get(f"/api/runs/{run['run_id']}").get_json()
    assert updated["ledger"]["counts"]["supplements"] == 1
    assert all(item["ok"] for item in updated["ledger"]["checks"])


def test_late_downstream_stage_is_appended_by_supplement(client):
    run = approve(client, create(client))
    # clean 样例 WO2 只有两道工序；补报可带来晚到的下道实收行。
    response = client.post(f"/api/runs/{run['run_id']}/supplements", json=report([
        {"row_id": "R005", "work_order": "WO2", "stage_seq": "3", "input_qty": "470", "good_qty": "450",
         "scrap_qty": "12", "rework_qty": "8", "rework_good": "4"},
    ]))
    updated = response.get_json()
    wo2 = result(updated, "WO2")
    # 470 = 上道 good 470 + rework_good 0（clean WO2 焊接 rework_good 8 → 478 ≠ 470）
    assert wo2["update_state"] == "RETAINED"
    assert wo2["stage_count"] == 2  # 保留已签发的两工序结果

"""返工补报的解析、合并与逐工单保留。

补报在工单签发后到达，带来最新的返工/损耗数量或晚到的下道实收数。
合并后按工单重新计算：交接量对得上的工单更新损耗，对不上的沿用此前已签发的
结果；实收补齐并对上后，之前没更新的工单也会更新。返工良品始终不计入首检通过数。
"""

import csv
import hashlib
import io

from app.catalog import SPEC
from app.errors import DomainIssue
from app.intake.normalize import canonical
from app.intake.reader import decode
from app.service import baseline_of, compose, refresh
from app.evidence.render import render


IDENTITY = ("row_id", "work_order", "stage_seq")


def read_report(raw: bytes):
    """Parse a supplement report; the header may be a subset of the schema."""
    if not raw:
        raise DomainIssue("EMPTY_FILE")
    text = decode(raw)
    if not text.strip():
        raise DomainIssue("BLANK_FILE")
    try:
        parsed = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error:
        raise DomainIssue("UNCLOSED_QUOTE")
    if not parsed:
        raise DomainIssue("EMPTY_FILE")
    header = [canonical(cell) for cell in parsed[0]]
    _check_header(header)
    records = []
    for number, cells in enumerate(parsed[1:], start=2):
        values = [canonical(cell) for cell in cells]
        if not any(values) or values == header:
            continue
        if len(values) != len(header):
            raise DomainIssue("SUPPLEMENT_MALFORMED", 400, f"第 {number} 行列数与表头不一致")
        records.append({"line": number, "values": dict(zip(header, values))})
    if not records:
        raise DomainIssue("HEADER_ONLY")
    return {"header": header, "records": records}


def report_from_rows(header, rows):
    header = [canonical(column) for column in header]
    _check_header(header)
    records = []
    for number, row in enumerate(rows, start=2):
        values = {column: canonical(row.get(column, "")) for column in header}
        if not any(values.values()):
            continue
        records.append({"line": number, "values": values})
    if not records:
        raise DomainIssue("HEADER_ONLY")
    return {"header": header, "records": records}


def _check_header(header):
    if not header or any(column not in SPEC["columns"] for column in header):
        raise DomainIssue("HEADER_MISMATCH")
    if not any(column in IDENTITY for column in header):
        raise DomainIssue("HEADER_MISMATCH", detail="补报需包含 row_id、work_order 或 stage_seq")


def report_digest(report):
    """Content digest: replaying the same report maps to the same digest.

    Record values only, so record order and the report filename do not matter.
    """
    keys = sorted(tuple(sorted(record["values"].items())) for record in report["records"])
    return hashlib.sha256(repr(keys).encode("utf-8")).hexdigest()


def _slot(row):
    return (row.get("work_order"), row.get("stage_seq"))


def merge_rows(base_rows, report):
    """Merge report records into batch rows and report the touched work orders.

    Matching is by ``row_id`` first, then by ``(work_order, stage_seq)``.
    Unmatched records are appended, which is how a late downstream actual
    receipt enters the batch. Returns ``(rows, touched, appended)``.
    """
    header = report["header"]
    merged = [dict(row) for row in base_rows]
    by_id, by_slot = {}, {}
    for index, row in enumerate(merged):
        if row.get("row_id"):
            by_id[row["row_id"]] = index
        by_slot[_slot(row)] = index
    touched, appended = set(), 0
    next_line = max((row.get("_line", 0) for row in merged), default=1) + 1
    for record in report["records"]:
        values = record["values"]
        index = None
        if values.get("row_id") and values["row_id"] in by_id:
            index = by_id[values["row_id"]]
        elif values.get("work_order") and values.get("stage_seq") and _slot(values) in by_slot:
            index = by_slot[_slot(values)]
        if index is None:
            if not (values.get("work_order") and values.get("stage_seq")):
                # 无法定位的新行会导致重复计损，直接拒收整份补报。
                raise DomainIssue("SUPPLEMENT_MALFORMED", 400,
                                  f"第 {record['line']} 行缺少 row_id 或 work_order+stage_seq")
            row = {column: "" for column in SPEC["columns"]}
            row.update(values)
            row["_line"] = next_line
            next_line += 1
            merged.append(row)
            appended += 1
            if row.get("row_id"):
                by_id[row["row_id"]] = len(merged) - 1
            by_slot[_slot(row)] = len(merged) - 1
        else:
            row = merged[index]
            for column in header:
                if column != "row_id":
                    row[column] = values[column]
        order = merged[index].get("work_order") if index is not None else values.get("work_order")
        if order:
            touched.add(order)
    return merged, touched, appended


def apply_report(store, run_id, report, expected_revision=None):
    """Apply a supplement to a signed run.

    Only an ``APPROVED`` batch accepts supplements. The same report content is
    applied at most once. A work order keeps its issued numbers while its
    handover does not reconcile, and a later report that fixes the receipt
    updates it.
    """
    digest = report_digest(report)

    def plan(payload):
        if payload.get("_source_rows") is None:
            # 早期版本只保存了预览行，无法安全合并补报；宁可拒绝也不漏算。
            raise DomainIssue("SUPPLEMENT_UNSUPPORTED", 409,
                              "该批次为旧版存储格式，请重新执行批次后再接收补报")
        merged, touched, appended = merge_rows(payload["_source_rows"], report)
        # 来源行守恒针对原始来源文件；补报带来的新行单独计数，不改变原来源行数。
        base_counts = payload.get("_base_counts") or {}
        base_counts.setdefault("supplement_rows", 0)
        base_counts["supplement_rows"] += appended
        # Every work order retains its last issued numbers unless this report
        # makes its handover reconcile.
        retained = {label: dict(values) for label, values in (payload.get("_effective") or {}).items()}
        new = compose(payload["profile"], payload["source"], base_counts,
                      payload.get("_intake_issues", []), merged,
                      retained=retained, supplements=payload.get("supplements", []),
                      merge_issues=payload.get("_merge_issues", []),
                      supplement_count=len(payload.get("supplements", [])) + 1)
        states = {}
        for item in new["_results"]:
            label = item["label"]
            if label not in touched:
                continue
            if item.get("update_state") == "RETAINED" or not item.get("handover_ok", True):
                states[label] = {"state": "RETAINED",
                                 "reason": item.get("supplement_reason") or "STAGE_DISCONTINUITY"}
            else:
                states[label] = {"state": "UPDATED", "reason": None}
        new["_states"] = states
        for item in new["_results"]:
            state = states.get(item["label"])
            if state:
                item["update_state"] = state["state"]
                if state["reason"]:
                    item["supplement_reason"] = state["reason"]
        # Rebuild the effective baseline so the next report compares against
        # what is accepted now (updated orders move on, retained ones do not).
        new["_effective"] = {item["label"]: baseline_of(item) for item in new["_results"]}
        refresh(new)
        entry = {
            "rows": len(report["records"]),
            "work_orders": sorted(touched),
            "updated": sorted(label for label, state in states.items() if state["state"] == "UPDATED"),
            "retained": sorted(label for label, state in states.items() if state["state"] == "RETAINED"),
        }
        return new, entry

    row, problem = store.apply_supplement(run_id, digest, expected_revision, plan)
    if problem == "missing":
        raise DomainIssue("RUN_NOT_FOUND", 404)
    if problem == "not_approved":
        raise DomainIssue("NOT_APPROVED", 409)
    if problem == "stale":
        raise DomainIssue("STALE_REVIEW", 409)
    from app.review.service import serialize
    run = serialize(row)
    if problem is None:
        # 补报更新也是一次可追溯的版本；原签发版本保持只读可查。
        store.save_evidence(run_id, row["revision"], row["status"], render(run))
    return run, problem

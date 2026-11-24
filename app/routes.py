from flask import Blueprint, abort, current_app, jsonify, request, send_file

from app.catalog import SPEC
from app.config import SAMPLES
from app.errors import DomainIssue
from app.evidence.render import render
from app.review.service import approve, create_run, serialize
from app.service import build_run, pipeline
from app.supplement.service import apply_report, read_report, report_from_rows


api = Blueprint("api", __name__)
STAGE_INDEX = {"intake": 1, "prepare": 2, "calculate": 3, "decision": 4, "reconcile": 5}


def store():
    return current_app.extensions["runs"]


@api.get("/api/catalog")
def catalog():
    return jsonify(code=SPEC["code"], name=SPEC["name"], actor=SPEC["actor"], rule=SPEC["rule"],
                   rule_short=SPEC["rule_short"],
                   stages=[{"id": item, "name": name} for item, name in zip(
                       ["intake", "prepare", "calculate", "decision", "reconcile", "review", "evidence"],
                       SPEC["stages"])],
                   chart=SPEC["chart"], result_columns=SPEC["result_columns"],
                   columns=SPEC["columns"], samples=["dirty", "clean"])


@api.post("/api/stages/<stage_id>")
def stage(stage_id):
    if stage_id not in STAGE_INDEX:
        raise DomainIssue("STAGE_UNKNOWN", 404)
    data = request.get_json(silent=True) or {}
    profile = data.get("profile", "dirty")
    upto = SPEC["stages"][STAGE_INDEX[stage_id] - 1]
    return jsonify(pipeline(profile, upto=upto))


@api.post("/api/runs")
def create():
    data = request.get_json(silent=True) or {}
    profile = data.get("profile", "dirty")
    return jsonify(create_run(store(), build_run(profile)))


@api.get("/api/runs/<run_id>")
def get_run(run_id):
    run = serialize(store().get(run_id))
    if not run:
        raise DomainIssue("RUN_NOT_FOUND", 404)
    return jsonify(run)


@api.post("/api/runs/<run_id>/review")
def review(run_id):
    data = request.get_json(silent=True) or {}
    if "decision" not in data or "expected_revision" not in data:
        raise DomainIssue("BAD_REQUEST")
    return jsonify(approve(store(), run_id, data["decision"], data.get("comment", ""),
                           int(data["expected_revision"])))


@api.post("/api/runs/<run_id>/supplements")
def supplement(run_id):
    """接收返工补报：JSON 行、CSV 文本或 CSV 上传。"""
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        rows = data.get("rows")
        if rows is None:
            raise DomainIssue("BAD_REQUEST")
        header = data.get("columns")
        if not header:
            # 未显式给出列时只更新行里实际出现的字段，避免把其余字段清空。
            ordered = [column for column in SPEC["columns"]
                       if any(column in row for row in rows)]
            header = ordered
        report = report_from_rows(header, rows)
        expected = data.get("expected_revision", request.args.get("expected_revision"))
    else:
        raw = request.get_data()
        if not raw:
            raise DomainIssue("BAD_REQUEST")
        report = read_report(raw)
        expected = request.args.get("expected_revision")
    run, _ = apply_report(store(), run_id, report,
                          expected_revision=int(expected) if expected is not None else None)
    return jsonify(run)


@api.get("/api/runs/<run_id>/evidence")
def evidence(run_id):
    if store().get(run_id) is None:
        raise DomainIssue("RUN_NOT_FOUND", 404)
    revision = request.args.get("revision")
    stored = store().get_evidence(run_id, int(revision) if revision else None)
    if stored is None:
        if revision is not None:
            raise DomainIssue("EVIDENCE_NOT_FOUND", 404)
        # 旧批次或尚未落盘的签发记录，按当前签发状态即时渲染。
        return jsonify(render(serialize(store().get(run_id))))
    # 返回签发时固化的快照，摘要与当时完全一致。
    return jsonify(stored["artifact"])


@api.get("/api/runs/<run_id>/evidence/history")
def evidence_history(run_id):
    if store().get(run_id) is None:
        raise DomainIssue("RUN_NOT_FOUND", 404)
    with store().connect() as db:
        rows = db.execute("SELECT revision, status, created_at FROM evidence WHERE run_id=? ORDER BY revision",
                          (run_id,)).fetchall()
    return jsonify(run_id=run_id, versions=[dict(row) for row in rows])


@api.get("/api/examples/<profile>")
def example(profile):
    if profile not in {"clean", "dirty"}:
        abort(404)
    return send_file(SAMPLES / f"{profile}.csv", as_attachment=True)

from app.errors import DomainIssue
from app.evidence.render import render
from app.review.codec import loads
from app.review.policy import approval_allowed
from app.service import jsonable


def create_run(store, payload):
    row = store.create(payload["run_key"], payload["profile"], payload)
    return serialize(row)


def approve(store, run_id, decision, comment, expected_revision):
    row = store.get(run_id)
    if row is None:
        raise DomainIssue("RUN_NOT_FOUND", 404)
    payload = loads(row["payload"])
    if decision not in {"APPROVED", "HELD"}:
        raise DomainIssue("BAD_REQUEST")
    if decision == "APPROVED" and not approval_allowed(payload):
        raise DomainIssue("GATE_BLOCKED", 409)
    row, problem = store.review(run_id, decision, comment.strip(), expected_revision)
    if problem == "missing":
        raise DomainIssue("RUN_NOT_FOUND", 404)
    if problem == "stale":
        raise DomainIssue("STALE_REVIEW", 409)
    if decision == "APPROVED":
        # 证据在签发时固化，之后无论补报如何更新批次都可追溯原签发版本。
        store.save_evidence(run_id, row["revision"], row["status"], render(serialize(row)))
    return serialize(row)


def serialize(row):
    if row is None:
        return None
    payload = jsonable(loads(row["payload"]))
    payload.update(run_id=row["id"], status=row["status"], revision=row["revision"],
                   comment=row["comment"], created_at=row["created_at"])
    return payload

# 接口

- `GET /`：工作台。
- `GET /api/catalog`：阶段、字段和样例。
- `POST /api/stages/{stage}`：按阶段返回处理结果。
- `POST /api/runs`：执行完整批次并保存草稿。
- `GET /api/runs/{id}`：读取批次。
- `POST /api/runs/{id}/review`：签发或暂缓，提交 `decision`、`comment`、`expected_revision`。
- `POST /api/runs/{id}/supplements`：接收返工补报，仅限已签发批次。
  - JSON：`{"columns": [...], "rows": [{"row_id": "...", ...}], "expected_revision": 1}`；`columns` 可省略，默认整套字段。
  - CSV：请求体为 CSV 文本（`Content-Type: text/csv`），表头可为字段子集；版本号用 `?expected_revision=1` 传入。
  - 同一份补报内容重复提交返回相同版本，不重复计损。
  - 响应中的 `supplements` 记录每份补报的 `updated` / `retained` 工单。
- `GET /api/runs/{id}/evidence`：下载已签发证据；`?revision=N` 取回指定签发/更新版本的固化快照。
- `GET /api/runs/{id}/evidence/history`：列出该批次所有已固化证据版本。
- `GET /api/examples/{profile}`：下载样例。
- `GET /healthz`：健康检查。

## 结果字段

结果行除 `rolling`、`lost` 等指标外，还包括：

- `first_pass_good`：首检良品累计，不含返工良品。
- `update_state`：`UPDATED`（补报后已更新）、`RETAINED`（交接量未对上，沿用已签发结果）或空。
- `supplement_reason`：保留原因，如 `STAGE_DISCONTINUITY`。

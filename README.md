# 单向抓包负载重组工具（pcap-reassembler）

用于网络排障的**单向**抓包负载重组网页：你知道某条数据流每个分片应当落在负载中的什么字节位置，但分片可能乱序到达、重复发送或相互矛盾。把「预期字节数」和每个分片的「起始偏移 + 十六进制内容」填入页面，服务端按字节位置重组，并直观展示覆盖、冲突与缺口。

技术栈：Python 3.11 + FastAPI + 原生 JavaScript（无前端构建、无第三方 JS 依赖）。

## 功能说明

- **按字节位置重组**：分片起始偏移支持十进制或 `0x` 十六进制，内容支持连续 hex、空格或冒号分隔（如 `48656c6c6f`、`48:65:6c`）。
- **重叠处理**
  - 同一字节位置被多次覆盖且**取值相同** → 自动去重，计入「同值去重字节」；
  - 取值**不同** → 该字节标记为**冲突**，页面给出冲突区间，并抽样列出每个冲突偏移收到过的不同值。
- **缺口清单**：列出从 `0` 到预期长度（左闭右开 `[0, N)`）内的**全部未覆盖区间**，逐字节无遗漏。
- **可视化**：按偏移绘制覆盖 / 冲突 / 缺口色带（悬停查看字节范围），并提供 16 字节一行的 hex dump 预览。
- **下载门槛**：只有当范围**完整（无任何缺口）且无冲突**时，才出现「下载还原的二进制」按钮；否则只给出原因，不提供下载。
- 结果只在服务端内存中临时保存（默认 100 份 / 30 分钟，LRU + 过期淘汰），过期后需重新重组。

## 一条命令启动（Docker Compose）

需要已安装 Docker 与 Compose 插件（`docker compose version` 可用）。

```bash
docker compose up --build -d
```

该命令会自动构建镜像、安装全部 Python 依赖并在后台启动服务。

访问：

- 网页界面：<http://localhost:8000/>
- 健康检查 / 当前限制：<http://localhost:8000/api/health>
- 接口文档（Swagger）：<http://localhost:8000/docs>

停止与清理：

```bash
docker compose down          # 停止
docker compose down -v       # 停止并清理（无持久化卷）
```

> 端口固定为容器内 **8000**；如需换宿主机端口，修改 `docker-compose.yml` 中的 `ports`，例如 `"9000:8000"`。

## 必要配置

所有配置均通过 `docker-compose.yml` 的 `environment` 设置，均有默认值，不配也能直接用：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MAX_EXPECTED_LENGTH` | `16777216`（16 MiB） | 单次重组预期负载的最大字节数 |
| `MAX_FRAGMENTS` | `4096` | 单次请求最多分片数 |
| `MAX_TOTAL_INPUT` | `67108864`（64 MiB） | 所有分片原始字节（含重叠）总量上限 |
| `JOB_RETENTION_COUNT` | `100` | 可下载结果在内存中保留的最大份数 |
| `JOB_RETENTION_SECONDS` | `1800`（30 分钟） | 可下载结果的保留时长 |

监听地址与端口通过容器启动命令固定为 `0.0.0.0:8000`，宿主机映射在 Compose 的 `ports` 中调整。

## 本地（非 Docker）运行

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## HTTP 接口

### `POST /api/reassemble`

请求体：

```json
{
  "expected_length": 16,
  "fragments": [
    {"offset": 0,  "data": "48656c6c6f20576f726c64"},
    {"offset": 10, "data": "643020746f21"}
  ]
}
```

上例中 `Hello World` 覆盖 `[0,11)`，`d0 to!` 覆盖 `[10,16)`，偏移 10 处两片同为 `64`（`d`），自动去重；最终完整负载为 `Hello World0 to!`。

响应（节选）：

```json
{
  "expected_length": 16,
  "complete": true,
  "has_conflict": false,
  "downloadable": true,
  "in_order": true,
  "stats": {"fragment_count": 2, "covered_length": 16, "gap_length": 0,
            "conflict_length": 0, "duplicate_bytes": 1, "total_input_bytes": 17},
  "coverage": [[0, 16]],
  "conflicts": [],
  "gaps": [],
  "conflict_samples": [],
  "preview_hex": "…",
  "job_id": "…",
  "download_url": "/api/download/<job_id>"
}
```

- `coverage` / `conflicts` / `gaps` 均为 `[起始偏移, 结束偏移)` 的有序区间数组；
- 分片越界、奇数位 hex、非 hex 字符、预期长度超限等输入错误统一返回 `400 {"detail": "..."}`；
- `downloadable=false` 时 `job_id` 与 `download_url` 为 `null`。

### `GET /api/download/{job_id}`

下载重组完成的原始二进制（`application/octet-stream`）。结果过期或未知返回 404。

## 快速试用

打开页面后可点击：

- **「填充示例：乱序+冲突+缺口」**：验证乱序排序、异值冲突标红和尾部缺口；
- **「填充示例：完整可下载」**：两片在偏移 10 处同值重叠（去重），覆盖全部 16 字节，可直接下载。

## 目录结构

```
.
├── app/
│   ├── __init__.py
│   ├── main.py          # FastAPI 路由、任务缓存、静态托管
│   └── reassembly.py    # 重组核心：去重 / 冲突 / 缺口
├── static/
│   ├── index.html       # 页面结构
│   ├── app.js           # 原生 JS：表单、可视化、下载
│   └── style.css
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## 排障提示

- **冲突是真实矛盾**：同一偏移收到不同字节通常意味着分片不属于同一条流、序列号推算错误或数据被篡改。工具不会替你猜测采用哪个值，预览中保留最先到达的值，冲突字节以红色标出。
- **缺口按字节列出**：即使只丢了 1 个字节也无法还原完整负载，下载按钮保持禁用。
- **大负载**：重组复杂度与「预期长度 + 输入字节总量」成线性关系；默认上限适合一般报文级排障，更大场景请调大对应环境变量并留意容器内存。

# 单向抓包负载重组

网络排障用的小工具:把单向抓包里**乱序、重叠**的 TCP/UDP 负载分片,按**字节偏移**重新拼回原始二进制。

- 输入:预期还原的总字节数,以及若干 `{起始偏移, 十六进制内容}` 分片;
- 服务端逐字节重组:
  - **同值重叠去重** —— 同一偏移多次出现且字节值相同,视为正常覆盖;
  - **异值重叠标出冲突** —— 同一偏移出现不同字节值,该字节及连续冲突区间单独列出,并给出涉及的分片和冲突现场(各值);
  - **缺口区间** —— 列出 `[0, 预期长度)` 内所有未被覆盖的区间;
  - **溢出区间** —— 起始偏移加长度超过预期长度的尾部(便于发现偏移填错);
- 页面按偏移用彩色覆盖图展示正常覆盖 / 冲突 / 缺口 / 溢出,并分段列出区间与十六进制预览;
- **只有范围完整(零缺口)且无冲突时**,才允许下载还原的二进制;下载接口在服务端再次强校验。

技术栈:Python FastAPI + 原生 JavaScript(无前端构建、无 npm 依赖)。

## 一条命令启动

需要 Docker 与 Docker Compose v2:

```bash
docker compose up --build
```

启动后访问:

- **页面:http://localhost:8000**
- 健康检查:http://localhost:8000/api/health
- 区间列表/下载等均为 JSON API(`POST /api/reassemble`、`POST /api/download`)

后台运行加 `-d`:

```bash
docker compose up --build -d
docker compose logs -f
docker compose down
```

### 不用 Docker(本地开发)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 配置(均可选,有默认值)

通过环境变量调整服务端处理上限(见 `docker-compose.yml`):

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MAX_EXPECTED_BYTES` | `16777216`(16 MiB) | 允许提交的预期字节数上限 |
| `MAX_TOTAL_BYTES` | `16777216`(16 MiB) | 所有分片的字节合计上限 |
| `MAX_FRAGMENTS` | `2000` | 单次提交的分片数量上限 |

## 使用方法

1. 在页面顶部填写**预期字节数**;
2. 用「添加分片」逐行填入每个乱序分片的**起始偏移**(从 0 开始)与**十六进制内容**
   (可带空格、冒号、`0x` 前缀,例如 `48:65:6c:6c:6f` 或 `0x48656c6c6f`);分片可填名称便于在冲突列表中定位;
3. 点「重组」,查看覆盖图与四类区间;
4. 顶部提示「范围完整且无冲突」后,点「下载还原二进制」得到 `reassembled.bin`。
   只要还有缺口或冲突,下载按钮禁用,且服务端接口也会以 409 拒绝。

页面首次打开会自动填充一组内置示例(包含同值重叠、异值冲突、缺口与溢出),点「填充示例」可随时恢复。

## HTTP API

### `POST /api/reassemble`

请求:

```json
{
  "expected_length": 16,
  "fragments": [
    {"offset": 0, "hex": "00 01 02 03 04", "label": "A"},
    {"offset": 8, "hex": "aa090a0b", "label": "C"}
  ]
}
```

响应含 `stats`(覆盖/冲突/缺口字节数、覆盖率)、`ranges.covered|conflict|gap|overflow`(区间、长度、十六进制预览、冲突涉及的分片与现场值)、`complete` 与 `can_download`。

### `POST /api/download`

请求体同上。服务端重新重组并校验,通过后返回 `application/octet-stream`(文件名 `reassembled.bin`);
不完整或有冲突时返回 `409 Conflict`。超过处理上限返回 `413`,输入非法返回 `422`。

## 目录结构

```
app/
├── main.py          # FastAPI 路由、参数与上限校验、下载强校验
├── reassembly.py    # 十六进制解析、按偏移重组、区间合并/反转
└── static/
    ├── index.html   # 单页界面
    ├── style.css
    └── app.js       # 原生 JS:分片编辑、覆盖图绘制、区间列表、下载
Dockerfile
docker-compose.yml
requirements.txt
```

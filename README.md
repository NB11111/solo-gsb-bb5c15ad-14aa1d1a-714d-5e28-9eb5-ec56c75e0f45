# 匿名评审分配可辨识性审计 API

基于 **Python · FastAPI · Pydantic · NetworkX** 的二分图匹配审计服务：

- 接收**数量相等且非空**的评审人、稿件及允许配对；
- 当存在完美匹配时，返回**确定性**分配——按评审人 ID 排序后字典序最小的完美匹配；
- 逐对标注该配对是否为**强制配对**（即是否在**所有**完美匹配中都被选中）；
- 每个非强制配对附一组**不含该边**的完整替代完美匹配；
- 不存在完美匹配时返回 **409**，给出一个最大匹配及双方未匹配 ID，**绝不返回部分成功**；
- 重复 ID、重复配对、未知引用、数量不等或空输入以 **422** 精确定位字段位置。

## 单条命令启动

```bash
docker compose up --build
```

服务地址（容器内外一致，监听 8000 端口）：

- 服务根地址：<http://localhost:8000>
- 健康检查：<http://localhost:8000/health>
- 交互式文档（Swagger UI）：<http://localhost:8000/docs>
- OpenAPI Schema：<http://localhost:8000/openapi.json>

> 需安装 Docker 与 Docker Compose v2（`docker compose` 子命令）。
> 停止服务：`docker compose down`。

## 接口

### `POST /audit`

请求体：

```json
{
  "reviewers": ["alice", "bob"],
  "manuscripts": ["m1", "m2"],
  "allowed_pairs": [
    {"reviewer_id": "alice", "manuscript_id": "m1"},
    {"reviewer_id": "alice", "manuscript_id": "m2"},
    {"reviewer_id": "bob", "manuscript_id": "m1"},
    {"reviewer_id": "bob", "manuscript_id": "m2"}
  ]
}
```

示例调用：

```bash
curl -s http://localhost:8000/audit \
  -H 'Content-Type: application/json' \
  -d '{
    "reviewers": ["alice", "bob"],
    "manuscripts": ["m1", "m2"],
    "allowed_pairs": [
      {"reviewer_id": "alice", "manuscript_id": "m1"},
      {"reviewer_id": "alice", "manuscript_id": "m2"},
      {"reviewer_id": "bob", "manuscript_id": "m1"},
      {"reviewer_id": "bob", "manuscript_id": "m2"}
    ]
  }'
```

#### 200 —— 存在完美匹配

```json
{
  "perfect_match": true,
  "assignment": [
    {
      "reviewer_id": "alice",
      "manuscript_id": "m1",
      "forced": false,
      "alternative": [
        {"reviewer_id": "alice", "manuscript_id": "m2"},
        {"reviewer_id": "bob", "manuscript_id": "m1"}
      ]
    },
    {
      "reviewer_id": "bob",
      "manuscript_id": "m2",
      "forced": false,
      "alternative": [
        {"reviewer_id": "alice", "manuscript_id": "m2"},
        {"reviewer_id": "bob", "manuscript_id": "m1"}
      ]
    }
  ]
}
```

字段语义：

| 字段 | 说明 |
| --- | --- |
| `assignment` | 按 `reviewer_id` 升序排列的完整分配，**每次调用逐字节确定** |
| `forced: true` | 该配对出现在**所有**完美匹配中（无法辨识出替代方案），`alternative` 为 `null` |
| `forced: false` | 存在其他完美匹配；`alternative` 给出一组**完整、且不包含当前配对**的替代分配（同样按评审人排序、字典序最小） |

确定性规则：评审人按 ID 字典序依次处理，每人选择在“仍可达到全局最优”前提下最小的稿件 ID；
因此响应与节点遍历顺序无关，相同输入永远得到相同输出。强制判定基于匹配图上交替环的强连通分量（SCC）判据。

#### 409 —— 不存在完美匹配

返回一个**最大基数匹配**（同样按评审人排序、字典序最小）及双方未匹配 ID；HTTP 状态码即冲突信号，不返回 `assignment`，不存在部分成功：

```json
{
  "perfect_match": false,
  "detail": "No perfect matching exists for the given allowed pairs.",
  "maximum_matching": [
    {"reviewer_id": "r1", "manuscript_id": "m1"}
  ],
  "unmatched_reviewer_ids": ["r2"],
  "unmatched_manuscript_ids": ["m2"]
}
```

#### 422 —— 请求语义/结构错误

沿用 FastAPI 标准错误信封 `{"detail": [...]}`，每条错误的 `loc` 精确定位到字段或数组下标：

| 错误类型 `type` | 定位示例 `loc` | 触发条件 |
| --- | --- | --- |
| `missing` / `string_too_short` / `too_short` | `reviewers`、`allowed_pairs` | 字段缺失、空字符串、空列表（三者均须非空） |
| `count_mismatch` | `body` | 评审人与稿件数量不相等 |
| `duplicate_id` | `reviewers.1` / `manuscripts.i` | 评审人或稿件 ID 重复（定位到重复出现的位置） |
| `duplicate_pair` | `allowed_pairs.3` | 允许配对重复（定位到重复出现的位置） |
| `unknown_reference` | `allowed_pairs.i.reviewer_id` / `.manuscript_id` | 配对引用了未声明的评审人或稿件 |

一次请求会返回**全部**可定位错误，便于一次性修正。

## 设计说明

- `app/matching.py`：NetworkX 二分图原语
  - 内部节点使用 `reviewer:` / `manuscript:` 前缀命名，评审人与稿件即使**同名也不会冲突**；
  - `lex_min_matching`：贪心 + 残差图 Hopcroft–Karp 可行性预言机，产出字典序最小的最大匹配；
  - `forced_edges`：围绕一个完美匹配构造有向图（匹配边评审人→稿件，非匹配边稿件→评审人），SCC 判据识别强制边；
  - `perfect_matching_without`：删除指定边后重新求字典序最小完美匹配，生成替代分配。
- `app/schemas.py`：Pydantic v2 请求/响应模型（`min_length=1` 保证非空）。
- `app/main.py`：`POST /audit`、跨字段 422 校验、`GET /health`。
- 典型规模（每方 ≤20 人、任意密度）全量审计耗时远低于 1 秒。

## 本地开发（不使用 Docker）

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000      # 开发服务器
python -m pytest tests/ -v                     # 运行测试
```

测试套件包含针对小图的**暴力枚举交叉验证**（字典序最小性、强制边判定、替代分配合法性）以及 409/422 行为用例。

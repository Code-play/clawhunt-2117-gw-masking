# S5-GW-010 会员空间平台 · 调用前脱敏：占位符替换与请求级加密映射表

本单交付「模型网关调用前脱敏」能力：把 messages 中所有 role 的文本交给识别器，命中值替换为
`[[PHONE_1]]` / `[[IDNO_1]]` / `[[EMAIL_1]]` 形式的占位符；同一请求内相同值复用同一占位符，序号按出现顺序递增；
映射表用 AES-GCM 加密后写入 Redis 键 `gw:mask:{request_id}`（TTL 600 秒），还原完成后立即删除；
在 system 消息末尾追加固定说明；命中数回填到 `x_gw.masked_entity_count`；`mask_enabled` 是功能级开关
（默认开启，关闭需网关管理员操作并写审计）。只有脱敏后的文本会发往厂商，映射表与原值不写入任何日志。

## 目录

```text
app/masking/recognizers.py    识别器：手机号（可带 +86/分隔符）、18 位证件号、邮箱；重叠按优先级排除
app/masking/placeholders.py   占位符语法 [[TYPE_SEQ]]、请求级分配器、固定 system 说明、还原函数
app/masking/crypto.py         AES-256-GCM 封装（密钥来自 GW_MASK_KEY，AAD 绑定 request_id）
app/masking/store.py          Redis 加密存取：gw:mask:{request_id}，TTL 600，还原后删除
app/masking/service.py        MaskingService.mask / restore_text / restore_messages / discard
app/gateway.py                调用编排：脱敏 -> 调厂商（只发脱敏文本）-> 还原 -> 删映射
app/features.py               mask_enabled 功能开关（默认开，站点覆盖 > 组织 > 默认）
app/audit.py                  审计写入 + 「审计明细不得含个人信息」的强制校验
app/models.py                 SQLAlchemy 2 模型：gw_feature_flag、gw_audit_log（统一公共字段）
app/api/                      FastAPI 接口：脱敏、还原、开关查询/修改、网关调用
migrations/                   Alembic 迁移（含 partial unique index 与 downgrade）
tests/                        4 条验收条款的用例 + 反例 + 日志不泄漏断言 + 迁移往返
docs/placeholder-spec.md      占位符规范（语法、序号、加密存储格式、还原与冲突规则）
examples/gateway_demo.py      端到端演示（脱敏 -> 密文 -> 解密 -> mock 厂商 -> 还原 -> 删除）
SOLUTION.md                   交付说明
VERIFICATION.md               验收条款 -> 命令 -> 预期 -> 实测
```

## 运行环境与安装

Python 3.11+（验证用 3.13.14），建议用 [uv](https://docs.astral.sh/uv/)：

```bash
uv venv .venv --python 3.13
# Windows
uv pip install --python .venv/Scripts/python.exe -e ".[test]"
# Linux/macOS
uv pip install --python .venv/bin/python -e ".[test]"
```

不建虚拟环境也可以，直接装依赖：

```bash
uv pip install fastapi sqlalchemy alembic "psycopg[binary]" redis cryptography httpx pytest fakeredis
```

## 环境变量

配置与密钥只从环境变量读取（模板见 `.env.example`）：

| 变量 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `GW_MASK_KEY` | 是 | 无 | AES-GCM 主密钥。base64 编码的 32 字节，或长度 ≥16 的口令（HKDF-SHA256 派生）。缺失即启动失败（失败关闭） |
| `GW_MASK_TTL_SECONDS` | 否 | `600` | 映射表 TTL，单位秒；本单要求 600 |
| `GW_MASK_ENABLED_DEFAULT` | 否 | `true` | 无显式开关行时的默认值 |
| `GW_REDIS_URL` | 否 | `redis://127.0.0.1:6379/0` | 映射表存储 |
| `GW_DATABASE_URL` | 否 | `postgresql+psycopg://gw:gw@127.0.0.1:5432/gw` | 功能开关与审计 |
| `GW_VENDOR_BASE_URL` | 否 | `http://127.0.0.1:9/v1` | 模型厂商 OpenAI 兼容地址 |
| `GW_VENDOR_TIMEOUT_SECONDS` | 否 | `10` | 厂商调用超时 |
| `GW_LOG_LEVEL` | 否 | `INFO` | 日志级别；日志只含元数据，不含消息内容 |

启动（`GW_MASK_KEY` 必须已设置）：

```bash
.venv/Scripts/python.exe -m uvicorn app.api.main:create_app --factory --port 8000
```

## 数据库迁移

```bash
set GW_DATABASE_URL=postgresql+psycopg://gw:gw@127.0.0.1:5432/gw
.venv/Scripts/python.exe -m alembic upgrade head
.venv/Scripts/python.exe -m alembic downgrade base
```

只审阅 DDL 而不连库：`.venv/Scripts/python.exe -m alembic upgrade head --sql`。

迁移建两张表：`gw_feature_flag`（`mask_enabled` 等开关；组织级与站点级各一条 partial unique index，
避免 PostgreSQL 把 NULL 当作互不相等）和 `gw_audit_log`（只追加；`detail` 为元数据）。

## 测试

```bash
# 默认：fakeredis + SQLite，不需要任何外部服务
.venv/Scripts/python.exe -m pytest -q
```

接真实服务复跑同一条命令即可（用于验收证据）：

```bash
set GW_TEST_REDIS_URL=redis://127.0.0.1:6379/0
set GW_TEST_DATABASE_URL=postgresql+psycopg://gw:gw@127.0.0.1:5432/gw
.venv/Scripts/python.exe -m pytest -q
```

端到端演示（需可用的 Redis；厂商用 mock，不访问外网）：

```bash
set GW_MASK_KEY=AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=
set GW_REDIS_URL=redis://127.0.0.1:6379/0
.venv/Scripts/python.exe -m examples.gateway_demo
```

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/v1/gw/mask` | 脱敏 messages，返回占位符清单、命中数、去重数与 system 说明 |
| POST | `/api/v1/gw/mask/restore` | 还原一段文本，随后删除映射；映射不存在返回 410 |
| POST | `/api/v1/gw/mask/restore-messages` | 还原 messages 中所有文本槽位，随后删除映射 |
| GET | `/api/v1/gw/features/mask_enabled` | 查询当前租户生效值（含是否显式设置） |
| PATCH | `/api/v1/gw/features/mask_enabled` | 管理员修改开关，写审计并返回 `audit_id` |
| POST | `/api/v1/gw/chat/completions` | 网关调用路径：按开关决定是否脱敏，回填 `x_gw.masked_entity_count` |
| GET | `/healthz` | 存活探针 |

租户与身份来自网关注入的请求头（鉴权由平台访问控制工单实现，本单只消费这些头）：

| 请求头 | 用途 |
| --- | --- |
| `X-Org-Id` / `X-Site-Id` | 租户与站点；站点级开关优先于组织级 |
| `X-GW-Actor-Id` / `X-GW-Actor-Role` | 操作者与角色；改开关必须是 `gateway_admin` |

示例（虚构数据）：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/gw/mask -H 'content-type: application/json' -d '{
  "messages": [
    {"role": "system", "content": "你是会员空间助理。"},
    {"role": "user", "content": "手机 13812345678，邮箱 zhang.san@example.com"}
  ]
}'
# {"request_id":"...","masked_messages":[{"role":"system","content":"你是会员空间助理。\n\n文中 [[类型_序号]] 为占位符，请原样保留，不要改写或推测"},{"role":"user","content":"手机 [[PHONE_1]]，邮箱 [[EMAIL_1]]"}],
#  "masked_entity_count":2,"distinct_values":2,"placeholders":[{"placeholder":"[[EMAIL_1]]","type":"EMAIL"},{"placeholder":"[[PHONE_1]]","type":"PHONE"}]}

curl -X PATCH http://127.0.0.1:8000/api/v1/gw/features/mask_enabled \
  -H 'content-type: application/json' -H 'X-Org-Id: 0b6f7c1e-1a2b-4c3d-8e9f-0a1b2c3d4e5f' \
  -H 'X-GW-Actor-Id: gateway-admin@example.invalid' -H 'X-GW-Actor-Role: gateway_admin' \
  -d '{"enabled": false, "reason": "vendor incident drill"}'
```

占位符语法、序号规则、加密存储格式、还原与冲突规则的完整说明见
[`docs/placeholder-spec.md`](docs/placeholder-spec.md)。

## 对接点（依赖其他工单时先用的桩）

| 对接点 | 现状 | 集成方式 |
| --- | --- | --- |
| 模型厂商 | `app/gateway.py` 的 `VendorClient`（httpx，OpenAI 兼容 `/chat/completions`）；测试用 `httpx.MockTransport` | 配置 `GW_VENDOR_BASE_URL` 与密钥（密钥走平台密钥管理，本仓库不落盘） |
| 鉴权与租户 | 只读 `X-Org-Id` / `X-Site-Id` / `X-GW-Actor-*` 头，校验角色 `gateway_admin` | 由访问控制工单的中间件注入同名头，本模块无需改动 |
| 识别器 | 正则识别手机号/证件号/邮箱，实现 `Recognizer` 协议即可插入 | 在构造 `MaskingService` 时传入 `recognizers=[...]`，不影响占位符与存储逻辑 |
| 审计表 | 自建 `gw_audit_log`，动作名 `gw.feature_flag.updated` | 若平台统一审计表上线，替换 `app/audit.record` 的实现即可 |
| 请求上下文 | `x_gw.masked_entity_count` 同时回填响应体与 `x-gw-masked-entity-count` 响应头 | 网关层把它写入自己的请求上下文与计费/指标 |

## 安全约定

- 只有脱敏后的 messages 会被发往厂商；`mask_enabled` 关闭时按显式旁路发送原文，并以 WARN 日志记录旁路事实（不含内容）。
- 映射表在 Redis 中始终是密文（AES-256-GCM，随机 nonce，AAD 绑定 `request_id`）；还原成功后立即删除。
- 映射表、原值、消息正文都不进日志；`app/audit.py` 会拒绝包含手机号/证件号/邮箱的审计明细。
- 密钥缺失、解密失败、Redis 不可用时一律失败关闭：宁可不调用厂商，也不把原文发出去。
- 交付物全部使用虚构数据，不含客户名称。

## 已知边界

- 识别器是模式识别：覆盖中国大陆手机号（可带 `+86` 与单个分隔符）、18 位证件号、常规邮箱；非手机号段的座机、
  15 位旧证件号、姓名/地址/公司名等自由文本实体不在本单范围，需通过 `Recognizer` 扩展。
- 输入里已包含 `[[TYPE_SEQ]]` 形式文本时拒绝脱敏（422）：网关产生的 token 与用户手写的 token 无法区分，
  拒绝比还原出错更安全。
- 映射表 TTL 600 秒是单次调用的反转窗口；调用超过 TTL 的请求无法还原（410），需按 600 秒内完成调用的约束设计。

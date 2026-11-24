# 验收对照与实测记录（S5-GW-010 / ClawHunt 工单 #2117）

环境：Windows 11，Python 3.13.14，FastAPI 0.165.0（Starlette 1.10.0，pydantic 2.14.4），
SQLAlchemy 2.1.9，Alembic 1.18.x，cryptography，redis-py 8.2.0，pytest 9.2.0。
真实依赖复跑时使用一次性容器：Redis 7（`redis:7-alpine`）、PostgreSQL 16（`postgres:16-alpine`），
均以随机宿主端口启动，验完即删；不使用客户或本机既有数据库。

以下命令均在仓库根目录执行，依赖安装见 README。

## 一、验收条款逐条对照

| 条款 | 命令 | 预期 | 实测 |
| --- | --- | --- | --- |
| 1. 同一手机号在一条消息中出现 3 次，脱敏后均为同一占位符（单元测试） | `.venv\Scripts\python.exe -m pytest -o addopts= -q tests/test_masking_placeholders.py::test_criterion_1_same_phone_three_times_gets_one_placeholder` | 三次命中都是 `[[PHONE_1]]`，`masked_entity_count=3`、`distinct_values=1` | `1 passed in 0.04s` |
| 2. Redis 中 `gw:mask:{request_id}` 的值无法直接读出原值，用密钥解密后可还原（单元测试） | `.venv\Scripts\python.exe -m pytest -o addopts= -q tests/test_encrypted_store.py::test_criterion_2_redis_value_is_not_readable_but_decrypts` | 存储值中不含手机号/证件号/邮箱与映射表结构；用 `GW_MASK_KEY` 解密后占位符与原值一致 | `1 passed in 0.04s` |
| 3. 发往 mock 厂商的请求体中搜索不到任何原始虚构手机号、证件号、邮箱（集成测试断言） | `.venv\Scripts\python.exe -m pytest -o addopts= -q tests/test_vendor_payload.py::test_criterion_3_vendor_request_contains_no_originals` | 厂商请求原始字节不含任何原值，含统一占位符，映射在调用后删除 | `1 passed in 0.03s` |
| 4. 测试运行产生的应用日志中不含原值与映射表内容（测试断言） | `.venv\Scripts\python.exe -m pytest -o addopts= -q tests/test_logs_no_pii.py::test_criterion_4_full_flow_logs_no_originals_or_mapping` | 捕获 DEBUG 级全部日志记录，不含 4 个虚构原值与映射表结构；审计行只含元数据 | `1 passed in 0.18s` |
| 交付物齐备（脱敏服务、加密存取、开关与审计、单元测试、占位符规范 README） | `.venv\Scripts\python.exe -m pytest -o addopts= -q` | 全部用例通过 | `42 passed, 1 skipped in 3.8s`（默认 fakeredis + SQLite；skip 是需真实数据库的迁移往返） |

同一条命令接真实服务复跑（验收证据）：

```bash
set GW_TEST_REDIS_URL=redis://127.0.0.1:16380/0
set GW_TEST_DATABASE_URL=postgresql+psycopg://gw:gw@127.0.0.1:16381/gw
.venv\Scripts\python.exe -m pytest -o addopts= -q
# 43 passed in 3.84s   （无 skip：迁移往返在真实 PostgreSQL 16 上跑）
```

## 二、迁移往返（真实 PostgreSQL 16）

```bash
set GW_DATABASE_URL=postgresql+psycopg://gw:gw@127.0.0.1:16381/gw
docker exec gw2117-pg psql -U gw -d gw -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe temporal_scripts\inspect-db.py      # 交付仓库外的本地检查脚本
.venv\Scripts\python.exe -m alembic downgrade base
.venv\Scripts\python.exe temporal_scripts\inspect-db.py
```

实测输出（原文）：

```text
-- upgrade head --
INFO  [alembic.runtime.migration] Running upgrade  -> 0001_gw_masking, Gateway masking tables: feature flags and the security audit trail.
-- inspect --
tables: ['alembic_version', 'gw_audit_log', 'gw_feature_flag']
-- gw_audit_log columns: ['action', 'actor_id', 'created_at', 'created_by', 'deleted_at', 'detail', 'entity_id', 'entity_type', 'id', 'org_id', 'site_id', 'updated_at']
-- gw_audit_log indexes: ['ix_gw_audit_log_action_created', 'ix_gw_audit_log_org_created', 'ix_gw_audit_log_org_id', 'ix_gw_audit_log_site_id']
-- gw_audit_log pk: ['id']
-- gw_feature_flag columns: ['created_at', 'created_by', 'deleted_at', 'enabled', 'flag_key', 'id', 'org_id', 'reason', 'site_id', 'updated_at', 'updated_by']
-- gw_feature_flag indexes: ['ix_gw_feature_flag_org_id', 'ix_gw_feature_flag_org_key', 'ix_gw_feature_flag_site_id', 'uq_gw_feature_flag_org_scope', 'uq_gw_feature_flag_site_scope']
-- gw_feature_flag pk: ['id']
-- downgrade base --
INFO  [alembic.runtime.migration] Running downgrade 0001_gw_masking -> , Gateway masking tables: feature flags and the security audit trail.
-- inspect after downgrade --
tables: ['alembic_version']
```

离线 DDL 审阅（不连库）由 `tests/test_migration.py::test_offline_upgrade_renders_tables_indexes_and_partial_where`
与 `::test_offline_downgrade_drops_everything_it_created` 断言：`CREATE TABLE gw_feature_flag` / `gw_audit_log`、
`CREATE UNIQUE INDEX uq_gw_feature_flag_org_scope ... WHERE site_id IS NULL`、
`CREATE UNIQUE INDEX uq_gw_feature_flag_site_scope ... WHERE site_id IS NOT NULL`、`JSONB`，以及 downgrade 的
`DROP TABLE` / `DROP INDEX` 成对出现。

## 三、端到端演示（真实 Redis 7）

```bash
set GW_MASK_KEY=AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=
set GW_REDIS_URL=redis://127.0.0.1:16380/0
.venv\Scripts\python.exe -m examples.gateway_demo
```

实测输出（原文，虚构数据）：

```text
== 1. 脱敏结果 ==
[system] 你是会员空间平台的助理。

文中 [[类型_序号]] 为占位符，请原样保留，不要改写或推测
[user] 联系人手机 [[PHONE_1]]，再次确认 [[PHONE_1]]，邮箱 [[EMAIL_1]]，证件 [[IDNO_1]]。请整理成摘要。
masked_entity_count=4 distinct_values=3
占位符类型: {"[[PHONE_1]]": "PHONE", "[[EMAIL_1]]": "EMAIL", "[[IDNO_1]]": "IDNO"}

== 2. Redis 中的映射表（密文） ==
key=gw:mask:demo-7ab9293af7a74de2 ttl=600 value_prefix=AZnvMNJrgQN2SLVxVF2X-maH...
plaintext_visible= False

== 3. 用密钥解密 ==
decrypted_mapping_matches_expected= True

== 4. mock 厂商调用 ==
vendor_request_contains_originals= False
vendor_request_has_placeholders= True
gateway_headers= {"x-gw-request-id": "demo-1431fd0ae4554107"}
reply_restored= True
masked_entity_count= 4

== 5. 还原完成后映射被删除 ==
mapping_deleted_after_restore= True

RESULT: PASS
```

## 四、关键断言明细

| 断言 | 位置 |
| --- | --- |
| 相同值复用同一占位符、序号按首次出现且按类型独立 | `test_criterion_1_same_phone_three_times_gets_one_placeholder`、`test_sequence_follows_first_appearance_per_type`、`test_repeated_value_across_messages_reuses_token`、`test_sequence_restarts_for_a_new_request` |
| 密文不含原值与映射结构、AAD 绑定 request_id、篡改/错密钥/错请求号均解密失败 | `test_criterion_2_redis_value_is_not_readable_but_decrypts`、`test_ciphertext_is_bound_to_the_request_id`、`test_tampered_ciphertext_is_rejected`、`test_wrong_key_cannot_open_the_mapping` |
| TTL 600 秒 | `test_stored_mapping_expires_after_six_hundred_seconds` |
| 还原后立即删除映射、二次还原 410 | `test_mapping_is_deleted_after_restoration`、`test_restore_endpoint_deletes_mapping` |
| 厂商请求只含脱敏文本、system 的 role 也被脱敏、命中数回填响应头 | `test_criterion_3_vendor_request_contains_no_originals`、`test_vendor_payload_only_has_masked_text_even_for_system_role`、`test_chat_completions_masks_by_default` |
| 厂商失败时丢弃映射 | `test_mapping_is_discarded_when_the_vendor_fails` |
| 日志（DEBUG 级全量）不含原值与映射表 | `test_criterion_4_full_flow_logs_no_originals_or_mapping` |
| 审计只写元数据，含个人信息的明细被拒绝 | `test_audit_log_rows_carry_metadata_only`、`test_audit_detail_with_personal_data_is_refused` |
| 开关默认开启、非管理员 403 且不写审计、管理员关闭写审计 | `test_mask_enabled_defaults_to_true`、`test_feature_flag_change_requires_admin`、`test_admin_can_disable_masking_and_the_change_is_audited` |
| 关闭开关后走显式旁路（原文、`masked=false`、命中数 0） | `test_chat_completions_bypasses_when_flag_disabled`、`test_bypass_sends_original_text_when_masking_is_disabled` |
| system 说明追加/新建、分片文本处理 | `test_system_note_is_appended_to_existing_system_message`、`test_system_message_is_created_when_absent`、`test_text_parts_are_masked_and_other_parts_preserved` |

## 五、反例（边界）

| 反例 | 预期 | 结果 |
| --- | --- | --- |
| 输入已含 `[[PHONE_9]]` | 拒绝脱敏（422），错误信息不回显 token 原文 | `test_input_with_reserved_placeholder_is_rejected`、`test_mask_rejects_reserved_tokens` 通过 |
| 缺 role / 空 messages / 超短 request_id | 422 | `test_malformed_messages_are_rejected`、`test_invalid_request_id_is_rejected` 通过 |
| 未知功能开关 | 404 | `test_unknown_feature_flag_is_404` 通过 |
| 审计明细里粘了手机号 | `UnsafeAuditDetail`，不写行 | `test_audit_detail_with_personal_data_is_refused` 通过 |
| 缺 `X-Org-Id` 调网关接口 | 400 | `test_chat_completions_requires_org_header` 通过 |

## 六、本环境未覆盖的部分（如实说明）

- 真实模型厂商未接入：验收条款只要求 mock 厂商，`VendorClient` 为 OpenAI 兼容 `/chat/completions`，
  厂商地址与密钥由环境变量注入；联调时替换 `GW_VENDOR_BASE_URL` 即可，不需要改脱敏代码。
- 网关鉴权未实现：本单只消费 `X-Org-Id` / `X-Site-Id` / `X-GW-Actor-*` 头并校验 `gateway_admin` 角色，
  真实登录态与签名由平台的访问控制工单提供，README「对接点」已写明。
- 识别器为模式识别：只覆盖中国大陆手机号、18 位证件号、常规邮箱；姓名/地址等自由文本实体需通过
  `Recognizer` 协议扩展（README 已注明边界）。
- 生产 Redis / PostgreSQL 的高可用、连接池与监控不在本单范围；本仓库只按要求使用环境变量配置并给出迁移。

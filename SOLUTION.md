# S5-GW-010 交付说明（Solution）

ClawHunt 工单 #2117 · 会员空间平台 · 调用前脱敏：占位符替换与请求级加密映射表
仓库：`https://github.com/Code-play/clawhunt-2117-gw-masking`（公开）

## 一、范围理解（逐句落到实现）

原单【本单范围】共 7 项要求，逐条对应如下：

| 原单要求 | 实现位置 | 可验证点 |
| --- | --- | --- |
| `MaskingService.mask(messages, request_id)`：对所有 role 的文本调用识别器 | `app/masking/service.py::mask`，逐 message、逐文本槽位（含 content 分片数组） | 单测覆盖 system/user/assistant 三种 role 与分片文本 |
| 命中值替换为 `[[PHONE_1]]`、`[[IDNO_1]]`、`[[EMAIL_1]]` | `app/masking/placeholders.py::PlaceholderAllocator`、`format_token` | `test_criterion_1`、`test_sequence_follows_first_appearance_per_type` |
| 同一请求内相同值复用同一占位符，序号按出现顺序递增 | 请求级分配器：`(type, value) -> token` 缓存 + 每类型独立计数器 | `test_repeated_value_across_messages_reuses_token`、`test_sequence_restarts_for_a_new_request` |
| 映射表 AES-GCM 加密（密钥 `GW_MASK_KEY`），存 `gw:mask:{request_id}`，TTL 600 秒 | `app/masking/crypto.py`（AES-256-GCM + 12 字节 nonce + AAD 绑定 request_id）、`app/masking/store.py`（`SET ... EX 600`） | `test_criterion_2`、`test_ciphertext_is_bound_to_the_request_id`、`test_stored_mapping_expires_after_six_hundred_seconds` |
| 还原完成后立即删除 | `MaskingService.restore_text / restore_messages` 在替换成功后 `delete`；厂商失败时 `discard` | `test_mapping_is_deleted_after_restoration`、`test_mapping_is_discarded_when_the_vendor_fails` |
| system 消息末尾追加固定说明「文中 [[类型_序号]] 为占位符，请原样保留，不要改写或推测」 | `MaskingService._append_system_note`：末条 system 追加（空 content 只留说明；分片 content 追加 text 分片；无 system 则在开头新建） | `test_system_note_is_appended_to_existing_system_message`、`test_system_message_is_created_when_absent` |
| `x_gw.masked_entity_count` 回填命中数 | `MaskResult.masked_entity_count`（出现次数，含重复）；网关响应体 `x_gw.masked_entity_count` 与响应头 `x-gw-masked-entity-count` | `test_chat_completions_masks_by_default` |
| `mask_enabled` 功能级开关，默认开启；关闭需网关管理员操作并写审计 | `app/features.py::FeatureFlagService`（站点 > 组织 > 默认）、`app/api/routes.py` 的 `PATCH`（`gateway_admin` 才允许）、`app/audit.py` 写 `gw_audit_log` | `test_mask_enabled_defaults_to_true`、`test_feature_flag_change_requires_admin`、`test_admin_can_disable_masking_and_the_change_is_audited` |
| 只有脱敏后的文本才发往厂商；映射表与原值不写日志 | `app/gateway.py::ChatGateway.complete` 只把 `result.messages` 交给 `VendorClient`；`app/audit.py` 在写入前用同一套识别器拒绝含个人信息的明细；日志只记 request_id 与计数 | `test_criterion_3`、`test_criterion_4` |

【交付物】脱敏服务代码（`app/masking/`）、加密存取工具（`app/masking/crypto.py` + `store.py`）、
功能级开关与审计（`app/features.py`、`app/audit.py`、`app/models.py`、`migrations/`）、单元测试（`tests/`）、
README（`README.md` + `docs/placeholder-spec.md` 占位符规范）。【依赖】无（厂商、鉴权均为桩/mock，README 写明对接点）。

## 二、调用链

```text
POST /api/v1/gw/chat/completions
  1. 读取租户头 X-Org-Id / X-Site-Id，查 mask_enabled（站点行 > 组织行 > 默认 true）
  2. MaskingService.mask(messages, request_id)
       - 校验 request_id 与 messages 形状；拒绝输入里已有的 [[TYPE_SEQ]]
       - 逐 role、逐文本槽位识别 phone/idno/email，替换为 [[TYPE_SEQ]]
       - 映射表 JSON -> AES-256-GCM(AAD=gw:mask:{request_id}:v1) -> SET gw:mask:{request_id} EX 600
       - 末条 system 追加固定说明（无则新建）
  3. VendorClient.complete(只含脱敏文本, headers{x-gw-request-id})
  4. 用同一 request_id 解密映射，还原厂商响应里的占位符
  5. 删除 gw:mask:{request_id}；响应回填 x_gw.masked_entity_count
异常路径：厂商失败/超时 -> 丢弃映射后抛错（502），不留可还原的悬空数据。
```

开关关闭时走显式旁路：不改写 messages、不写映射、`masked=false`、命中数 0，并以 WARN 记录旁路事实（不含内容）。

## 三、关键设计决策

1. **失败关闭**：`GW_MASK_KEY` 缺失或过短直接报错（503），Redis 不可用或写失败抛错，绝不退回明文发厂商。
2. **AAD 绑定请求**：密文附加数据为 `gw:mask:{request_id}:v1`；把 A 请求的密文挪到 B 请求下解密会失败，
   防止映射在键空间里被错误复用。
3. **映射表结构不可读**：密文之外没有明文字段；即使拿到 Redis 值，也只能看到 base64 密文。
4. **识别器隐私优先**：18 位证件号按形状脱敏、不按校验位判断，避免虚构数据校验位不合法时漏脱敏；
   邮箱优先于手机号匹配，避免邮箱本地部分的数字被错误拆分。
5. **占位符冲突显式拒绝**：输入已含 `[[TYPE_SEQ]]` 时返回 422 而不做猜测，避免还原时篡改用户原文；
   这是本单「不掩盖风险」的处理方式，README 已写明。
6. **审计防泄漏**：`audit.record` 在写库前对 detail 做识别器扫描，命中即拒绝——把「不落日志/不落审计」
   从评审约定变成代码强制。
7. **开关的唯一真相在数据库**：`gw_feature_flag` 用两条 partial unique index（`site_id IS NULL` 与
   `IS NOT NULL`）保证组织级与站点级各只有一行，避开 PostgreSQL 把 NULL 视为互不相等的坑。
8. **厂商协议最小假设**：OpenAI 兼容 `/chat/completions`；响应正文取 `choices[0].message.content`，
   文本分片拼接后还原，其他字段原样透传。

## 四、运行与验证

```bash
uv venv .venv --python 3.13
uv pip install --python .venv/Scripts/python.exe -e ".[test]"
.venv/Scripts/python.exe -m pytest -q            # 42 passed, 1 skipped（fakeredis + SQLite）
# 接真实服务：见 README 的 GW_TEST_REDIS_URL / GW_TEST_DATABASE_URL，43 passed
.venv/Scripts/python.exe -m alembic upgrade head # 需要 GW_DATABASE_URL
.venv/Scripts/python.exe -m examples.gateway_demo  # 端到端演示，RESULT: PASS
```

逐条验收的命令、预期与实测输出见 `VERIFICATION.md`；占位符语法、序号规则、加密格式、还原与冲突规则见
`docs/placeholder-spec.md`。

## 五、对接点

| 对接点 | 现状 | 集成方式 |
| --- | --- | --- |
| 模型厂商 | `VendorClient`（httpx，OpenAI 兼容），测试用 MockTransport | 配置 `GW_VENDOR_BASE_URL`；厂商密钥走平台密钥管理，本仓库不落盘 |
| 鉴权与租户 | 消费 `X-Org-Id` / `X-Site-Id` / `X-GW-Actor-Id` / `X-GW-Actor-Role` | 由访问控制工单注入同名头；改开关要求 `gateway_admin` |
| 识别器 | 正则识别 phone/idno/email，`Recognizer` 协议可插入 | `MaskingService(store, recognizers=[...])`，不影响占位符与存储 |
| 统一审计表 | 自建 `gw_audit_log`（`action= gw.feature_flag.updated`） | 平台统一审计上线后替换 `app/audit.record` 实现 |
| 计费/指标 | `x_gw.masked_entity_count` 同时出现在响应体与响应头 | 网关层写入请求上下文与计费通道 |

## 六、边界与不在本单范围

- 识别器只覆盖中国大陆手机号、18 位证件号、常规邮箱；姓名/地址/公司名等自由文本实体需扩展识别器。
- 映射表 TTL 600 秒是反转窗口：调用需在 600 秒内完成，超时后还原返回 410（符合本单「还原完成后立即删除」的假设）。
- 真实登录态、签名、限流、厂商密钥托管、生产 Redis/PostgreSQL 高可用不在本单范围。
- 所有数据均为虚构，交付物不含客户名称。

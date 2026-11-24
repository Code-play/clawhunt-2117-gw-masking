"""端到端演示：脱敏 -> 加密存 Redis -> mock 厂商调用 -> 还原 -> 删映射。

运行（需要可用的 Redis；厂商用 httpx MockTransport 模拟，不发起真实外网请求）：

    set GW_MASK_KEY=...      # base64 32 字节 或 >=16 字符的口令
    set GW_REDIS_URL=redis://127.0.0.1:6379/0
    .venv/Scripts/python.exe -m examples.gateway_demo

输出只包含占位符、命中数与布尔判定，不打印任何原值或映射表明文。
"""

from __future__ import annotations

import json
import sys
import uuid

import httpx

from app.config import load_settings
from app.gateway import ChatGateway, VendorClient
from app.masking import (
    MaskingService,
    RedisMappingStore,
    decrypt_mapping,
    mapping_key,
    resolve_key,
)

PHONE = "13812345678"
PHONE_REPEAT = PHONE
EMAIL = "zhang.san@example.com"
IDNO = "11010519491231002X"
ORIGINALS = [PHONE, EMAIL, IDNO]

MESSAGES = [
    {"role": "system", "content": "你是会员空间平台的助理。"},
    {
        "role": "user",
        "content": f"联系人手机 {PHONE}，再次确认 {PHONE_REPEAT}，邮箱 {EMAIL}，证件 {IDNO}。请整理成摘要。",
    },
]


def main() -> int:
    settings = load_settings()
    store = RedisMappingStore.from_url(
        settings.redis_url,
        mask_key=settings.mask_key,
        ttl_seconds=settings.mask_ttl_seconds,
    )
    masking = MaskingService(store)

    request_id = "demo-" + uuid.uuid4().hex[:16]
    result = masking.mask(MESSAGES, request_id)

    print("== 1. 脱敏结果 ==")
    for message in result.messages:
        print(f"[{message['role']}] {message['content']}")
    print(f"masked_entity_count={result.masked_entity_count} distinct_values={result.distinct_values}")
    print("占位符类型:", json.dumps(result.placeholder_kinds, ensure_ascii=False))

    print("\n== 2. Redis 中的映射表（密文） ==")
    key = mapping_key(request_id)
    blob = store.raw_value(request_id) or ""
    print(f"key={key} ttl={store.ttl(request_id)} value_prefix={blob[:24]}...")
    print("plaintext_visible=", any(secret in blob for secret in ORIGINALS))

    print("\n== 3. 用密钥解密 ==")
    mapping = decrypt_mapping(resolve_key(settings.mask_key), request_id, blob)
    placeholders = mapping["placeholders"]
    expected = {"[[PHONE_1]]": PHONE, "[[EMAIL_1]]": EMAIL, "[[IDNO_1]]": IDNO}
    print("decrypted_mapping_matches_expected=", placeholders == expected)

    print("\n== 4. mock 厂商调用 ==")
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode("utf-8")
        captured["headers"] = {k: v for k, v in request.headers.items() if k.startswith("x-gw")}
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "已记录，稍后回拨 [[PHONE_1]]。"}}]},
        )

    vendor = VendorClient(settings.vendor_base_url, transport=httpx.MockTransport(handler))
    gateway = ChatGateway(masking, vendor)
    chat_request_id = "demo-" + uuid.uuid4().hex[:16]
    try:
        reply = gateway.complete(MESSAGES, chat_request_id)
    finally:
        vendor.close()

    sent = str(captured["body"])
    print("vendor_request_contains_originals=", any(secret in sent for secret in ORIGINALS))
    print("vendor_request_has_placeholders=", "[[PHONE_1]]" in sent)
    print("gateway_headers=", json.dumps(captured["headers"], ensure_ascii=False))
    print("reply_restored=", PHONE in reply.content and "[[PHONE_1]]" not in reply.content)
    print("masked_entity_count=", reply.masked_entity_count)

    print("\n== 5. 还原完成后映射被删除 ==")
    masking.restore_text(request_id, "回拨 [[PHONE_1]]")
    print("mapping_deleted_after_restore=", store.raw_value(request_id) is None)

    ok = (
        placeholders == expected
        and not any(secret in blob for secret in ORIGINALS)
        and not any(secret in sent for secret in ORIGINALS)
        and "[[PHONE_1]]" in sent
        and PHONE in reply.content
    )
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

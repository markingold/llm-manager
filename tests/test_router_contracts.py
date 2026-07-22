import pytest
from pydantic import ValidationError

from api.router.contracts import RouterChatRequest


def test_raw_json_schema_is_wrapped_for_provider_response_format():
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
    request = RouterChatRequest(json_schema=schema)
    assert request.provider_json_schema() == {
        "name": "structured_response",
        "strict": True,
        "schema": schema,
    }


def test_pre_wrapped_json_schema_preserves_name_and_strictness():
    schema = {"type": "object"}
    request = RouterChatRequest(json_schema={"name": "answer", "strict": False, "schema": schema})
    assert request.provider_json_schema() == {"name": "answer", "strict": False, "schema": schema}


def test_multimodal_message_parts_are_rejected_by_text_only_contract():
    with pytest.raises(ValidationError):
        RouterChatRequest(
            messages=[{
                "role": "user",
                "content": [{"type": "image_url", "image_url": {"url": "https://example.invalid/a.png"}}],
            }]
        )

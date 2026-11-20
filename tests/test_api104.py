from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services import api104


def test_rich_method_metadata() -> None:
    assert api104.SendRichMessage.__api_method__ == "sendRichMessage"
    assert api104.SendRichMessageDraft.__api_method__ == "sendRichMessageDraft"


def test_rich_message_requires_chat_or_callback_reply() -> None:
    with pytest.raises(ValidationError):
        api104.SendRichMessage(rich_message={"html": "<b>x</b>"})
    assert api104.SendRichMessage(
        rich_message={"html": "<b>x</b>"},
        ephemeral_message_parameters={"callback_query_id": "cb"},
    )


def test_rich_draft_rejects_zero_id() -> None:
    with pytest.raises(ValidationError):
        api104.SendRichMessageDraft(
            chat_id=1, draft_id=0, rich_message={"html": "<b>x</b>"}
        )

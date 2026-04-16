"""Unit tests for generate_reply_draft() in classifier.py."""

from unittest.mock import MagicMock, patch

from reply_monitor.classifier import _ACTION_DRAFT_TAGS, generate_reply_draft


class TestGenerateReplyDraft:
    def test_returns_empty_without_api_key(self):
        result = generate_reply_draft(
            "body text", ["WRONG_CHANNEL"], "Acme Corp", api_key=None
        )
        assert result == ""

    def test_returns_empty_for_non_action_tags(self):
        result = generate_reply_draft(
            "body text",
            ["AUTO_ACKNOWLEDGE", "REQUEST_ACCEPTED"],
            "Acme Corp",
            api_key="sk-test",
        )
        assert result == ""

    def test_returns_empty_on_api_error(self):
        with patch("anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.side_effect = Exception("API error")
            result = generate_reply_draft(
                "body text", ["WRONG_CHANNEL"], "Acme Corp", api_key="sk-test"
            )
        assert result == ""

    def test_returns_draft_on_success(self):
        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(text="  Please specify the correct channel.\n  ")
        ]
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 50

        with patch("anthropic.Anthropic") as mock_cls, patch(
            "contact_resolver.cost_tracker.record_llm_call"
        ) as mock_record:
            mock_cls.return_value.messages.create.return_value = mock_response
            result = generate_reply_draft(
                "We cannot process over chat, use appropriate legal channels.",
                ["WRONG_CHANNEL"],
                "Whop",
                api_key="sk-test",
            )

        assert result == "Please specify the correct channel."
        mock_record.assert_called_once()
        call_kwargs = mock_record.call_args[1]
        assert call_kwargs["source"] == "reply_draft"

    def test_all_action_draft_tags_covered(self):
        expected = {
            "WRONG_CHANNEL",
            "MORE_INFO_REQUIRED",
            "CONFIRMATION_REQUIRED",
            "IDENTITY_REQUIRED",
            "HUMAN_REVIEW",
            "PORTAL_VERIFICATION",
        }
        assert _ACTION_DRAFT_TAGS == expected

    def test_draft_generated_for_each_action_tag(self):
        """Each action tag independently triggers draft generation."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="draft text")]
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 40

        for tag in _ACTION_DRAFT_TAGS:
            with patch("anthropic.Anthropic") as mock_cls, patch(
                "contact_resolver.cost_tracker.record_llm_call"
            ):
                mock_cls.return_value.messages.create.return_value = mock_response
                result = generate_reply_draft(
                    "body", [tag], "TestCo", api_key="sk-test"
                )
            assert result == "draft text", f"Tag {tag} should trigger draft generation"

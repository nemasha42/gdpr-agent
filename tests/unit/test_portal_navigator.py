"""Unit tests for portal_submitter/portal_navigator.py — multi-step portal navigation."""

import re
from unittest.mock import MagicMock, patch, call

import pytest

from portal_submitter.portal_navigator import navigate_to_form, page_has_form


class TestPageHasForm:
    def test_page_with_inputs(self):
        page = MagicMock()
        page.locator.return_value.count.return_value = 3
        assert page_has_form(page) is True
        page.locator.assert_called_once_with("input:not([type=hidden]), textarea, select")

    def test_page_without_inputs(self):
        page = MagicMock()
        page.locator.return_value.count.return_value = 0
        assert page_has_form(page) is False


class TestHintNavigation:
    def test_ketch_hints_find_form(self):
        """Ketch hints: click 'privacy request' then 'access data', form found after second click."""
        page = MagicMock()
        locator_counts = iter([0, 0, 3])
        page.locator.return_value.count.side_effect = lambda: next(locator_counts)

        link_locator = MagicMock()
        page.get_by_role.return_value = link_locator
        link_locator.count.return_value = 1

        result = navigate_to_form(page, "ketch")
        assert result is True
        assert link_locator.first.click.call_count == 2

    def test_unknown_platform_no_hints_no_llm(self):
        """Unknown platform with no LLM key — returns False immediately."""
        page = MagicMock()
        page.locator.return_value.count.return_value = 0
        result = navigate_to_form(page, "unknown")
        assert result is False

    def test_hints_exhausted_falls_to_llm(self):
        """When hints don't find form, falls back to LLM navigator."""
        page = MagicMock()
        page.locator.return_value.count.return_value = 0

        link_locator = MagicMock()
        page.get_by_role.return_value = link_locator
        link_locator.count.return_value = 1

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Submit Request")]
        mock_response.usage.input_tokens = 500
        mock_response.usage.output_tokens = 10
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        page.accessibility = MagicMock()
        page.accessibility.snapshot.return_value = {"role": "WebArea", "children": []}

        with patch("portal_submitter.portal_navigator._get_anthropic_client", return_value=mock_client):
            result = navigate_to_form(page, "ketch", api_key="test-key")

        assert result is False
        assert mock_client.messages.create.called


class TestLLMNavigation:
    def test_llm_finds_form_in_one_step(self):
        """LLM suggests a button, clicking it reveals form fields."""
        page = MagicMock()
        locator_counts = iter([0, 2])
        page.locator.return_value.count.side_effect = lambda: next(locator_counts)

        page.accessibility = MagicMock()
        page.accessibility.snapshot.return_value = {
            "role": "WebArea",
            "children": [{"role": "link", "name": "Access your data"}],
        }

        link_locator = MagicMock()
        link_locator.count.return_value = 1
        page.get_by_role.return_value = link_locator

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Access your data")]
        mock_response.usage.input_tokens = 500
        mock_response.usage.output_tokens = 10
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("portal_submitter.portal_navigator._get_anthropic_client", return_value=mock_client):
            result = navigate_to_form(page, "unknown", api_key="test-key")

        assert result is True

    def test_llm_max_steps_exceeded(self):
        """LLM navigator gives up after 3 steps without finding form."""
        page = MagicMock()
        page.locator.return_value.count.return_value = 0

        page.accessibility = MagicMock()
        page.accessibility.snapshot.return_value = {
            "role": "WebArea",
            "children": [{"role": "button", "name": "Next"}],
        }

        link_locator = MagicMock()
        link_locator.count.return_value = 1
        page.get_by_role.return_value = link_locator

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Next")]
        mock_response.usage.input_tokens = 500
        mock_response.usage.output_tokens = 10
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("portal_submitter.portal_navigator._get_anthropic_client", return_value=mock_client):
            result = navigate_to_form(page, "unknown", api_key="test-key")

        assert result is False
        assert mock_client.messages.create.call_count == 3


class TestNoApiKey:
    def test_no_api_key_skips_llm(self):
        """Without API key, LLM fallback is skipped."""
        page = MagicMock()
        page.locator.return_value.count.return_value = 0

        link_locator = MagicMock()
        page.get_by_role.return_value = link_locator
        link_locator.count.return_value = 1

        result = navigate_to_form(page, "ketch")
        assert result is False

"""Unit tests for portal_submitter/portal_navigator.py — multi-step portal navigation."""

from unittest.mock import MagicMock, patch


from portal_submitter.portal_navigator import navigate_to_form, page_has_form


def _mock_page_with_visible_inputs(count):
    """Create a mock page where page_has_form returns True if count > 0."""
    page = MagicMock()
    if count > 0:
        elements = [
            MagicMock(is_visible=MagicMock(return_value=True)) for _ in range(count)
        ]
    else:
        elements = []
    page.locator.return_value.all.return_value = elements
    return page


class TestPageHasForm:
    def test_page_with_inputs(self):
        page = _mock_page_with_visible_inputs(3)
        assert page_has_form(page) is True
        page.locator.assert_called_once_with(
            "input:not([type=hidden]), textarea, select"
        )

    def test_page_without_inputs(self):
        page = _mock_page_with_visible_inputs(0)
        assert page_has_form(page) is False

    def test_page_with_hidden_inputs_only(self):
        """Hidden inputs (e.g. cookie consent) should not count as form fields."""
        page = MagicMock()
        elements = [
            MagicMock(is_visible=MagicMock(return_value=False)) for _ in range(5)
        ]
        page.locator.return_value.all.return_value = elements
        assert page_has_form(page) is False


class TestHintNavigation:
    def test_ketch_hints_find_form(self):
        """Ketch hints: click 'privacy request' then 'access data', form found after second click."""
        page = MagicMock()

        # page_has_form returns False, False, then True (after second hint click)
        visible_results = iter(
            [[], [], [MagicMock(is_visible=MagicMock(return_value=True))]]
        )
        page.locator.return_value.all.side_effect = lambda: next(visible_results)

        link_locator = MagicMock()
        page.get_by_role.return_value = link_locator
        link_locator.count.return_value = 1

        result = navigate_to_form(page, "ketch")
        assert result is True
        assert link_locator.first.click.call_count == 2

    def test_unknown_platform_no_hints_no_llm(self):
        """Unknown platform with no LLM key — returns False immediately."""
        page = _mock_page_with_visible_inputs(0)
        result = navigate_to_form(page, "unknown")
        assert result is False

    def test_hints_exhausted_falls_to_llm(self):
        """When hints don't find form, falls back to LLM navigator."""
        page = _mock_page_with_visible_inputs(0)

        link_locator = MagicMock()
        page.get_by_role.return_value = link_locator
        link_locator.count.return_value = 1

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Submit Request")]
        mock_response.usage.input_tokens = 500
        mock_response.usage.output_tokens = 10
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        page.locator.return_value.aria_snapshot.return_value = (
            '- heading "Page" [level=1]'
        )

        with patch(
            "portal_submitter.portal_navigator._get_anthropic_client",
            return_value=mock_client,
        ):
            result = navigate_to_form(page, "ketch", api_key="test-key")

        assert result is False
        assert mock_client.messages.create.called


class TestLLMNavigation:
    def test_llm_finds_form_in_one_step(self):
        """LLM suggests a button, clicking it reveals form fields."""
        page = MagicMock()

        # First call: no form. Second call: form found (after LLM-guided click)
        visible_results = iter(
            [[], [MagicMock(is_visible=MagicMock(return_value=True))]]
        )
        page.locator.return_value.all.side_effect = lambda: next(visible_results)

        page.locator.return_value.aria_snapshot.return_value = (
            '- link "Access your data"'
        )

        link_locator = MagicMock()
        link_locator.count.return_value = 1
        page.get_by_role.return_value = link_locator

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Access your data")]
        mock_response.usage.input_tokens = 500
        mock_response.usage.output_tokens = 10
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch(
            "portal_submitter.portal_navigator._get_anthropic_client",
            return_value=mock_client,
        ):
            result = navigate_to_form(page, "unknown", api_key="test-key")

        assert result is True

    def test_llm_max_steps_exceeded(self):
        """LLM navigator gives up after 3 steps without finding form."""
        page = _mock_page_with_visible_inputs(0)

        page.locator.return_value.aria_snapshot.return_value = '- button "Next"'

        link_locator = MagicMock()
        link_locator.count.return_value = 1
        page.get_by_role.return_value = link_locator

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Next")]
        mock_response.usage.input_tokens = 500
        mock_response.usage.output_tokens = 10
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch(
            "portal_submitter.portal_navigator._get_anthropic_client",
            return_value=mock_client,
        ):
            result = navigate_to_form(page, "unknown", api_key="test-key")

        assert result is False
        assert mock_client.messages.create.call_count == 3


class TestNoApiKey:
    def test_no_api_key_skips_llm(self):
        """Without API key, LLM fallback is skipped."""
        page = _mock_page_with_visible_inputs(0)

        link_locator = MagicMock()
        page.get_by_role.return_value = link_locator
        link_locator.count.return_value = 1

        result = navigate_to_form(page, "ketch")
        assert result is False

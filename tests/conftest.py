"""
Shared pytest fixtures for the FinancialGenie test suite.

No env-vars are stripped here. Tests that trigger real Anthropic
(Claude) API calls must opt-in via the ``@pytest.mark.live_api`` marker,
which is excluded from the default run via ``pytest.ini``.
"""

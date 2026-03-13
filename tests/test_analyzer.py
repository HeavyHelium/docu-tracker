import pytest
from unittest.mock import MagicMock, patch
from docu_tracker.analyzer import analyze_document


def _mock_tool_use_response(title, authors, summary, topics):
    """Build a mock Anthropic API response with tool_use."""
    mock_block = MagicMock()
    mock_block.type = "tool_use"
    mock_block.input = {
        "title": title,
        "authors": authors,
        "summary": summary,
        "topics": topics,
    }
    mock_response = MagicMock()
    mock_response.content = [mock_block]
    return mock_response


@patch("docu_tracker.analyzer.anthropic")
def test_analyze_document_returns_metadata(mock_anthropic_module):
    """Should parse tool_use response into metadata dict."""
    mock_client = MagicMock()
    mock_anthropic_module.Anthropic.return_value = mock_client
    mock_client.messages.create.return_value = _mock_tool_use_response(
        title="Attention Is All You Need",
        authors=["Vaswani", "Shazeer"],
        summary="Introduces the Transformer architecture.",
        topics=["AI Safety", "Academic"],
    )
    result = analyze_document(
        text="Some paper text...",
        topic_names=["AI Safety", "Academic", "Other"],
        api_key="sk-test",
    )
    assert result["title"] == "Attention Is All You Need"
    assert result["authors"] == ["Vaswani", "Shazeer"]
    assert result["summary"] == "Introduces the Transformer architecture."
    assert result["topics"] == ["AI Safety", "Academic"]


@patch("docu_tracker.analyzer.anthropic")
def test_analyze_maps_unknown_topic_to_other(mock_anthropic_module):
    """Topics not in the provided list should be mapped to Other."""
    mock_client = MagicMock()
    mock_anthropic_module.Anthropic.return_value = mock_client
    mock_client.messages.create.return_value = _mock_tool_use_response(
        title="Paper",
        authors=["Author"],
        summary="Summary.",
        topics=["Quantum Computing"],
    )
    result = analyze_document(
        text="Some text",
        topic_names=["AI Safety", "Other"],
        api_key="sk-test",
    )
    assert result["topics"] == ["Other"]


@patch("docu_tracker.analyzer.anthropic")
def test_analyze_handles_api_error(mock_anthropic_module):
    """Should return None on API errors."""
    mock_client = MagicMock()
    mock_anthropic_module.Anthropic.return_value = mock_client
    mock_client.messages.create.side_effect = Exception("API error")
    result = analyze_document(
        text="Some text",
        topic_names=["Other"],
        api_key="sk-test",
    )
    assert result is None


def test_analyze_without_api_key():
    """Should return None when no API key is provided."""
    result = analyze_document(
        text="Some text",
        topic_names=["Other"],
        api_key=None,
    )
    assert result is None

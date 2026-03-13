import anthropic

DEFAULT_MODEL = "claude-haiku-4-5-20251001"

TOOL_DEFINITION = {
    "name": "extract_document_metadata",
    "description": "Extract metadata from a document",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "The document title"},
            "authors": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of author names",
            },
            "summary": {
                "type": "string",
                "description": "A 2-3 sentence summary of the document",
            },
            "topics": {
                "type": "array",
                "items": {"type": "string"},
                "description": "One or more topics from the provided list",
            },
        },
        "required": ["title", "authors", "summary", "topics"],
    },
}


def _format_topic_list(topics_with_descriptions):
    """Format topics for the LLM prompt, including descriptions when available."""
    lines = []
    for name, desc in topics_with_descriptions:
        if desc:
            lines.append(f"- {name}: {desc}")
        else:
            lines.append(f"- {name}")
    return "\n".join(lines)


def analyze_document(text: str, topic_names: list[str], api_key: str | None,
                     topics_with_descriptions: list[tuple[str, str]] | None = None,
                     model: str | None = None) -> dict | None:
    if not api_key:
        return None

    if topics_with_descriptions:
        topic_block = _format_topic_list(topics_with_descriptions)
    else:
        topic_block = "\n".join(f"- {name}" for name in topic_names)

    system_prompt = (
        "You are a document metadata extractor. Given the text from a document, "
        "extract its title, authors, a brief summary, and classify it into one or "
        "more of the following topics:\n\n"
        f"{topic_block}\n\n"
        "Only use topics from the provided list. If none fit well, use \"Other\".\n"
        "Use the extract_document_metadata tool to return your results."
    )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model or DEFAULT_MODEL,
            max_tokens=1024,
            system=system_prompt,
            tools=[TOOL_DEFINITION],
            tool_choice={"type": "tool", "name": "extract_document_metadata"},
            messages=[{"role": "user", "content": text}],
        )

        for block in response.content:
            if block.type == "tool_use":
                result = block.input
                # Map unknown topics to Other
                valid_topics = set(topic_names)
                mapped_topics = []
                for t in result.get("topics", []):
                    if t in valid_topics:
                        mapped_topics.append(t)
                    else:
                        mapped_topics.append("Other")
                # Deduplicate while preserving order
                seen = set()
                unique_topics = []
                for t in mapped_topics:
                    if t not in seen:
                        seen.add(t)
                        unique_topics.append(t)
                result["topics"] = unique_topics
                return result

        return None
    except Exception:
        return None

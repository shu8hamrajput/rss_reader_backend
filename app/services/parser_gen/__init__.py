"""One-click content-extractor generator/improviser.

    python -m app.services.parser_gen generate <feed_or_article_url> [--llm] [--samples N]
    python -m app.services.parser_gen improvise <slug> [--llm] [--feedback "TEXT"] [--url URL]...
    python -m app.services.parser_gen approve <slug>

See app/services/fetchers/generated/ for where output lands.
"""

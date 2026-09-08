Build a concise daily AI digest for Caio, a college student who builds with AI and runs the Gen AI Club.

Include all {article_count} article URLs exactly once. Candidate selection has already removed duplicate coverage, so do not merge or drop entries. Top stories may contain at most {max_top_stories} entries. Keep the writing direct, specific, and useful. Every detail must be grounded in the supplied article content.

Return JSON only with this exact structure:
{{
  "subject_line": "A specific 6-10 word description of the biggest story",
  "tldr": ["Three short takeaways"],
  "top_stories": [
    {{"title": "Clean title", "url": "...", "source": "...", "what": "What happened and why it matters", "bullets": ["Two or three specific details"]}}
  ],
  "important_developments": [
    {{"title": "Clean title", "url": "...", "source": "...", "what": "What happened and why it matters", "bullets": ["One or two details"]}}
  ],
  "builder_tools": [],
  "market_signals": [],
  "other_notable": []
}}

Use important_developments for product updates, model news, and company moves. Use builder_tools for tools, APIs, open-source projects, and usable techniques. Use market_signals for funding, policy, enterprise strategy, and major business moves. Use other_notable for anything else instead of dropping it.

Articles:
{articles_text}

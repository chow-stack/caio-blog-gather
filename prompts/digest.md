Build a highly concise, easy-to-scan daily AI digest for Caio, a college student who builds with AI and runs the Gen AI Club.

Include all {article_count} article URLs exactly once. Candidate selection has already removed duplicate coverage, so do not merge or drop entries. Top stories may contain at most {max_top_stories} entries. Every detail must be grounded in the supplied article content.

Writing rules:
- TLDR: exactly 3 one-sentence takeaways, each no more than 18 words
- what: one sentence, no more than 28 words; say what changed and why it matters
- top-story bullets: at most 2, each no more than 16 words
- every other story: at most 1 bullet, only when it adds a concrete fact not already in what
- other_notable: no bullets
- remove throat-clearing, hype, repeated context, and generic claims
- prefer concrete nouns, active voice, and exact numbers

Return JSON only with this exact structure:
{{
  "subject_line": "A specific 6-10 word description of the biggest story",
  "tldr": ["Three short one-sentence takeaways"],
  "top_stories": [
    {{"title": "Clean title", "url": "...", "source": "...", "what": "What happened and why it matters", "bullets": ["Up to two specific details"]}}
  ],
  "important_developments": [
    {{"title": "Clean title", "url": "...", "source": "...", "what": "What happened and why it matters", "bullets": ["At most one useful detail"]}}
  ],
  "builder_tools": [],
  "market_signals": [],
  "other_notable": []
}}

Use important_developments for product updates, model news, and company moves. Use builder_tools for tools, APIs, open-source projects, and usable techniques. Use market_signals for funding, policy, enterprise strategy, and major business moves. Use other_notable for anything else instead of dropping it.

Articles:
{articles_text}

"""
Review analyzer: uses Claude API to extract structured intelligence from
Booking.com guest reviews.

The analysis is designed for a property owner who wants to:
  1. Understand what guests love/dislike
  2. Learn what operational practices work well vs. poorly
  3. Identify requests for more / less of something
  4. Evaluate satisfaction with additional services
"""

import json
import os
from typing import Optional

import anthropic


# ──────────────────────────────────────────────────────────────────────────────
# Prompt templates
# ──────────────────────────────────────────────────────────────────────────────

ANALYSIS_SYSTEM_PROMPT = """You are an expert hospitality consultant helping a
property owner understand what makes their accommodation succeed or fail,
based on guest reviews scraped from Booking.com.

You will be given a batch of guest reviews. Your job is to produce a structured
analysis in JSON that will help the owner improve their own operations.

Return ONLY valid JSON — no markdown fences, no prose outside the JSON.
"""

ANALYSIS_USER_PROMPT = """Below are {n_reviews} guest reviews for "{property_name}".

REVIEWS:
{reviews_text}

Produce a JSON object with exactly these keys:

{{
  "property_name": "<name>",
  "total_reviews_analyzed": <int>,
  "overall_sentiment": "<Very Positive | Positive | Mixed | Negative | Very Negative>",
  "summary": "<2-3 sentence executive summary for a property owner>",

  "what_guests_love": [
    {{"theme": "<short label>", "detail": "<explanation>", "frequency": "<High | Medium | Low>", "example_quotes": ["<quote 1>", ...]}}
  ],

  "what_guests_dislike": [
    {{"theme": "<short label>", "detail": "<explanation>", "frequency": "<High | Medium | Low>", "example_quotes": ["<quote 1>", ...]}}
  ],

  "what_works_well": [
    {{"area": "<Checkin | Cleanliness | Communication | Location | Amenities | Decor | Kitchen | Bathroom | etc.>",
       "finding": "<specific operational insight>",
       "quotes": ["<quote>"]}}
  ],

  "what_does_not_work": [
    {{"area": "<same categories as above>",
       "finding": "<specific operational issue>",
       "severity": "<Critical | Moderate | Minor>",
       "quotes": ["<quote>"]}}
  ],

  "guests_want_more_of": [
    {{"item": "<what they want more of>", "detail": "<context>", "frequency": "<High | Medium | Low>"}}
  ],

  "guests_want_less_of": [
    {{"item": "<what they want less of>", "detail": "<context>", "frequency": "<High | Medium | Low>"}}
  ],

  "additional_services": {{
    "praised": [
      {{"service": "<name>", "why_appreciated": "<reason>", "quotes": ["<quote>"]}}
    ],
    "criticised": [
      {{"service": "<name>", "issue": "<description>", "quotes": ["<quote>"]}}
    ]
  }},

  "top_action_items": [
    "<Specific, actionable recommendation for a property owner #1>",
    "<...>",
    "<...>"
  ]
}}
"""


# ──────────────────────────────────────────────────────────────────────────────
# Analyzer class
# ──────────────────────────────────────────────────────────────────────────────

class ReviewAnalyzer:
    """Analyzes Booking.com reviews using Claude."""

    MODEL = "claude-sonnet-4-6"
    MAX_TOKENS = 4096

    # Booking.com reviews are typically short; we can fit many in one call.
    # At ~200 chars/review, 200 reviews ≈ 40 k chars — well within context.
    BATCH_SIZE = 200

    def __init__(self, api_key: Optional[str] = None):
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise ValueError(
                "ANTHROPIC_API_KEY is not set. "
                "Export it or put it in a .env file."
            )
        self.client = anthropic.Anthropic(api_key=key)

    # ── formatting helpers ────────────────────────────────────────────────────

    @staticmethod
    def _format_reviews(reviews: list[dict]) -> str:
        """Turn review dicts into readable numbered text for the prompt."""
        lines = []
        for i, r in enumerate(reviews, 1):
            parts = [f"--- Review #{i} ---"]
            if r.get("date"):
                parts.append(f"Date: {r['date']}")
            if r.get("score"):
                parts.append(f"Score: {r['score']}")
            if r.get("reviewer_country"):
                parts.append(f"Country: {r['reviewer_country']}")
            if r.get("stay_type"):
                parts.append(f"Stay type: {r['stay_type']}")
            if r.get("room_type"):
                parts.append(f"Room: {r['room_type']}")
            if r.get("title"):
                parts.append(f"Title: {r['title']}")
            if r.get("positive"):
                parts.append(f"Liked: {r['positive']}")
            if r.get("negative"):
                parts.append(f"Disliked: {r['negative']}")
            lines.append("\n".join(parts))
        return "\n\n".join(lines)

    # ── core analysis ─────────────────────────────────────────────────────────

    def analyze(self, property_data: dict) -> dict:
        """
        Run full analysis on a scraped PropertyData dict.
        Returns a structured analysis dict.
        """
        prop = property_data.get("property", {})
        reviews = property_data.get("reviews", [])

        if not reviews:
            return {
                "error": "No reviews available for analysis.",
                "property_name": prop.get("name", "Unknown"),
                "total_reviews_analyzed": 0,
            }

        property_name = prop.get("name", "Unknown property")
        print(f"\nAnalyzing {len(reviews)} reviews for '{property_name}'...")

        # Split into batches if very large
        batches = [
            reviews[i : i + self.BATCH_SIZE]
            for i in range(0, len(reviews), self.BATCH_SIZE)
        ]

        if len(batches) == 1:
            result = self._analyze_batch(property_name, batches[0])
        else:
            # Multi-batch: analyze each then synthesize
            print(f"  Reviews split into {len(batches)} batches for analysis...")
            batch_results = []
            for idx, batch in enumerate(batches, 1):
                print(f"  Batch {idx}/{len(batches)} ({len(batch)} reviews)...")
                batch_results.append(self._analyze_batch(property_name, batch))
            result = self._synthesize(property_name, batch_results, len(reviews))

        return result

    def _analyze_batch(self, property_name: str, reviews: list[dict]) -> dict:
        reviews_text = self._format_reviews(reviews)
        user_msg = ANALYSIS_USER_PROMPT.format(
            n_reviews=len(reviews),
            property_name=property_name,
            reviews_text=reviews_text,
        )

        message = self.client.messages.create(
            model=self.MODEL,
            max_tokens=self.MAX_TOKENS,
            system=ANALYSIS_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )

        raw = message.content[0].text.strip()
        # Strip accidental markdown fences
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Return raw text wrapped in a dict for graceful degradation
            return {"raw_analysis": raw, "parse_error": True}

    def _synthesize(
        self, property_name: str, batch_results: list[dict], total: int
    ) -> dict:
        """
        Merge multiple batch analyses into one coherent report.
        """
        combined_json = json.dumps(batch_results, ensure_ascii=False, indent=2)
        synth_prompt = f"""You have {len(batch_results)} partial analyses of reviews for
"{property_name}" (total {total} reviews).

PARTIAL ANALYSES:
{combined_json}

Produce ONE final analysis in the same JSON schema, merging all partial results.
Deduplicate themes. Keep the most frequent / highest-severity items.
Combine all quotes. Re-rank top_action_items by importance.
Return ONLY valid JSON."""

        message = self.client.messages.create(
            model=self.MODEL,
            max_tokens=self.MAX_TOKENS,
            system=ANALYSIS_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": synth_prompt}],
        )

        raw = message.content[0].text.strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"raw_analysis": raw, "parse_error": True}

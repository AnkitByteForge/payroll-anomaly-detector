"""
services/llm_client.py

Reusable async Groq LLM client.

Design decisions:
  - AsyncGroq client from the official groq SDK
  - Two public methods: ask() for plain text, ask_json() for structured output
  - ask_json() strips markdown code fences before parsing (Groq sometimes wraps JSON)
  - Retry logic: one automatic retry on transient errors
  - temperature=0.1 for deterministic, consistent categorization output
  - Module-level singleton (llm) — import this everywhere, never instantiate directly

Usage:
    from services.llm_client import llm

    text = await llm.ask("Summarize this data", system_prompt="Be concise")
    data = await llm.ask_json("Classify this record: ...", system_prompt="...")
"""

import json
import logging
import re

from groq import AsyncGroq

from config import settings

logger = logging.getLogger(__name__)


class LLMClient:
    """
    Async wrapper around the Groq chat completions API.
    """

    def __init__(self):
        self._client = AsyncGroq(api_key=settings.groq_api_key)
        self.model = settings.groq_model

    async def ask(
        self,
        user_prompt: str,
        system_prompt: str = "You are a helpful assistant.",
        max_tokens: int = 600,
        temperature: float = 0.1,
    ) -> str:
        """
        Send a prompt to the LLM and return the text response.

        Args:
            user_prompt:   The user-turn message.
            system_prompt: The system-turn message (sets role/behavior).
            max_tokens:    Maximum tokens in the response.
            temperature:   Sampling temperature (low = more deterministic).

        Returns:
            The LLM's response as a plain string.

        Raises:
            Exception: if the API call fails after one retry.
        """
        logger.debug("[LLMClient] ask() | model=%s | tokens=%d", self.model, max_tokens)

        for attempt in range(2):  # one retry on transient failure
            try:
                response = await self._client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                content = response.choices[0].message.content or ""
                logger.debug("[LLMClient] Response received (%d chars)", len(content))
                return content.strip()

            except Exception as e:
                if attempt == 0:
                    logger.warning("[LLMClient] Transient error, retrying: %s", e)
                else:
                    logger.error("[LLMClient] Failed after retry: %s", e)
                    raise

        return ""  # unreachable but satisfies type checker

    async def ask_json(
        self,
        user_prompt: str,
        system_prompt: str = (
            "You are a helpful assistant. "
            "Always respond with valid JSON only. "
            "Do not include markdown, code fences, or any text outside the JSON object."
        ),
        max_tokens: int = 600,
    ) -> dict:
        """
        Send a prompt expecting a JSON response.

        Handles common LLM quirks:
          - Strips ```json ... ``` code fences
          - Strips leading/trailing whitespace
          - Falls back gracefully if JSON is malformed

        Args:
            user_prompt:   The user-turn message (should describe the JSON format wanted).
            system_prompt: System prompt instructing JSON-only output.
            max_tokens:    Max tokens in the response.

        Returns:
            Parsed dict from the LLM response.

        Raises:
            ValueError: if the response cannot be parsed as JSON after cleanup.
        """
        raw = await self.ask(user_prompt, system_prompt, max_tokens)

        cleaned = self._strip_code_fences(raw)

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.error(
                "[LLMClient] JSON parse failed. Raw response:\n%s\nError: %s",
                raw,
                e,
            )
            raise ValueError(
                f"LLM response was not valid JSON. Raw: {raw[:300]}"
            ) from e

    @staticmethod
    def _strip_code_fences(text: str) -> str:
        """
        Remove markdown code fences that some LLMs add around JSON:
          ```json { ... } ```  →  { ... }
          ``` { ... } ```      →  { ... }
        """
        # Remove ```json ... ``` or ``` ... ``` blocks
        text = re.sub(r"```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```", "", text)
        return text.strip()


# ---------------------------------------------------------------------------
# Module-level singleton — import `llm` everywhere
# ---------------------------------------------------------------------------
llm = LLMClient()
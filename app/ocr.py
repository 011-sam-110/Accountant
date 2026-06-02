"""Vision-LLM OCR — one call returns {date, amount, vendor, category, confidence}.

Provider-swappable behind `extract()`. The default `stub` provider returns
plausible data so the whole scan->confirm->save loop works offline. Real
providers (Gemini 2.5 Flash primary; OpenAI / Anthropic fallbacks) use
structured output with the category constrained to the fixed SA103 enum.

Golden rule: extract() NEVER raises. On any failure it returns an all-"missing"
result so the gentle-failure path on the confirm screen still works.
"""
from __future__ import annotations

import base64
import json
import random
from dataclasses import dataclass, field
from datetime import date, timedelta

import httpx

from .config import settings
from .sa103 import OCR_CATEGORY_GUIDANCE, OCR_EXPENSE_CODES
from .util import parse_amount_to_minor, parse_uk_date

_SYSTEM = (
    "You read a photo of a UK receipt for a self-employed driving instructor and "
    "extract structured data for an SA103 self-assessment record. Rules: dates as "
    "ISO YYYY-MM-DD; amount as the final GBP TOTAL paid (not subtotal/VAT) as a "
    "number; choose exactly one category from the allowed list; NEVER invent a "
    "value — if a field is unreadable return null and mark its confidence "
    f"\"missing\". Category guidance: {OCR_CATEGORY_GUIDANCE}. "
    f"Allowed categories: {', '.join(OCR_EXPENSE_CODES)}."
)

_SCHEMA_HINT = (
    '{"date": "YYYY-MM-DD|null", "amount": number|null, "vendor": "string|null", '
    '"suggested_category": "one of allowed", '
    '"confidence": {"date":"high|low|missing","amount":"high|low|missing",'
    '"vendor":"high|low|missing","category":"high|low|missing"}}'
)


@dataclass
class OCRResult:
    amount_minor: int | None = None
    date: date | None = None
    vendor: str | None = None
    suggested_category: str = "other"
    confidence: dict = field(default_factory=lambda: {
        "amount": "missing", "date": "missing",
        "vendor": "missing", "category": "missing"})
    ok: bool = True
    error: str | None = None

    @property
    def empty(self) -> bool:
        return self.amount_minor is None and self.date is None and not self.vendor


# --------------------------------------------------------------- stub ----
_STUB_VENDORS = [
    ("Shell", "car_van_travel", (4200, 7300)),
    ("BP", "car_van_travel", (3800, 6900)),
    ("Tesco Petrol", "car_van_travel", (4000, 7000)),
    ("Halfords", "vehicle_costs", (1500, 8900)),
    ("Kwik Fit", "vehicle_costs", (4500, 19900)),
    ("Vodafone", "phone_internet", (1500, 4500)),
    ("EE", "phone_internet", (2000, 5000)),
    ("Vistaprint", "advertising_marketing", (1900, 6500)),
    ("DVSA", "professional_franchise_fees", (6200, 6200)),
    ("WHSmith", "accountancy_admin_bank", (300, 2500)),
]


class StubOCR:
    """Deterministic-ish plausible extraction for offline dev/demo."""

    def extract(self, image_bytes: bytes, content_type: str = "image/jpeg") -> OCRResult:
        seed = len(image_bytes) if image_bytes else random.randint(0, 9999)
        rnd = random.Random(seed)
        vendor, cat, (lo, hi) = rnd.choice(_STUB_VENDORS)
        amount = rnd.randint(lo, hi)
        d = date.today() - timedelta(days=rnd.randint(0, 9))
        # Occasionally flag the amount low-confidence to exercise the "please check" UI.
        amt_conf = "low" if rnd.random() < 0.15 else "high"
        return OCRResult(
            amount_minor=amount, date=d, vendor=vendor, suggested_category=cat,
            confidence={"amount": amt_conf, "date": "high",
                        "vendor": "high", "category": "high"},
            ok=True)


# --------------------------------------------------- normalise helper ----
def _normalise(data: dict) -> OCRResult:
    conf = data.get("confidence") or {}
    amount_minor = None
    if data.get("amount") is not None:
        try:
            amount_minor = parse_amount_to_minor(data["amount"])
        except ValueError:
            conf["amount"] = "missing"
    d = None
    if data.get("date"):
        try:
            d = parse_uk_date(data["date"], default=None)
        except Exception:
            conf["date"] = "missing"
    cat = (data.get("suggested_category") or "other").strip()
    if cat not in OCR_EXPENSE_CODES:
        cat = "other"
        conf["category"] = conf.get("category", "low")
    return OCRResult(
        amount_minor=amount_minor, date=d, vendor=(data.get("vendor") or None),
        suggested_category=cat,
        confidence={
            "amount": conf.get("amount", "high" if amount_minor is not None else "missing"),
            "date": conf.get("date", "high" if d is not None else "missing"),
            "vendor": conf.get("vendor", "high" if data.get("vendor") else "missing"),
            "category": conf.get("category", "high")},
        ok=True)


# ----------------------------------------------------- real providers ----
class GeminiOCR:
    MODEL = "gemini-2.5-flash"

    def extract(self, image_bytes: bytes, content_type="image/jpeg") -> OCRResult:
        b64 = base64.b64encode(image_bytes).decode()
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{self.MODEL}:generateContent?key={settings.gemini_api_key}")
        body = {
            "system_instruction": {"parts": [{"text": _SYSTEM}]},
            "contents": [{"parts": [
                {"text": f"Return ONLY JSON matching: {_SCHEMA_HINT}"},
                {"inline_data": {"mime_type": content_type, "data": b64}}]}],
            "generationConfig": {"response_mime_type": "application/json",
                                 "temperature": 0},
        }
        r = httpx.post(url, json=body, timeout=30)
        r.raise_for_status()
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        return _normalise(json.loads(text))


class OpenAIOCR:
    MODEL = "gpt-4o-mini"

    def extract(self, image_bytes: bytes, content_type="image/jpeg") -> OCRResult:
        b64 = base64.b64encode(image_bytes).decode()
        r = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json={"model": self.MODEL, "temperature": 0,
                  "response_format": {"type": "json_object"},
                  "messages": [
                      {"role": "system", "content": _SYSTEM},
                      {"role": "user", "content": [
                          {"type": "text", "text": f"Return ONLY JSON: {_SCHEMA_HINT}"},
                          {"type": "image_url", "image_url": {
                              "url": f"data:{content_type};base64,{b64}"}}]}]},
            timeout=30)
        r.raise_for_status()
        return _normalise(json.loads(r.json()["choices"][0]["message"]["content"]))


class AnthropicOCR:
    MODEL = "claude-haiku-4-5-20251001"

    def extract(self, image_bytes: bytes, content_type="image/jpeg") -> OCRResult:
        b64 = base64.b64encode(image_bytes).decode()
        r = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": settings.anthropic_api_key,
                     "anthropic-version": "2023-06-01"},
            json={"model": self.MODEL, "max_tokens": 512, "system": _SYSTEM,
                  "messages": [{"role": "user", "content": [
                      {"type": "image", "source": {"type": "base64",
                       "media_type": content_type, "data": b64}},
                      {"type": "text",
                       "text": f"Return ONLY JSON: {_SCHEMA_HINT}"}]}]},
            timeout=30)
        r.raise_for_status()
        return _normalise(json.loads(r.json()["content"][0]["text"]))


_PROVIDERS = {"stub": StubOCR, "gemini": GeminiOCR,
              "openai": OpenAIOCR, "anthropic": AnthropicOCR}


def extract(image_bytes: bytes, content_type: str = "image/jpeg") -> OCRResult:
    """Run the configured provider. Never raises — failure => all-missing result."""
    provider_name = settings.effective_ocr
    provider = _PROVIDERS.get(provider_name, StubOCR)()
    try:
        return provider.extract(image_bytes, content_type)
    except Exception as exc:  # noqa: BLE001 — gentle-failure path needs this
        return OCRResult(ok=False, error=str(exc),
                         confidence={k: "missing" for k in
                                     ("amount", "date", "vendor", "category")})

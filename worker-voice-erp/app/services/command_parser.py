"""Worker utterance -> intent + quantity (Person A).

Pure, deterministic text processing: no I/O, no state, no access to inventory
or the database. The parser only classifies what the worker said. The dialogue
manager decides what to do with it and the backend validates every action.

Matching works on whole normalized tokens and whole phrases, never raw
substrings, so "assembled" is not "assemble" and "yes" inside an unrelated
sentence is not a confirmation.

Coverage is a fixed vocabulary (see the *_PHRASES sets). It handles the team
pack's test phrases and common variants; it is not general Hindi/Marathi/
Hinglish understanding. Extend it by adding normalized phrases to the sets and
a test case to tests/test_command_parser.py.

Intents follow the shared API contract v1.0.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

PRODUCT_ID = "bicycle-standard"
UNIT = "piece"

INTENTS = (
    "create_production_order",
    "report_assembly_finished",
    "provide_completed_quantity",
    "confirm",
    "correct_previous_answer",
    "cancel",
    "unknown",
)
LANGUAGES = ("en", "hi", "mr", "mixed", "unknown")

# Below this speech-recognition confidence nothing is treated as an intent.
MIN_CONFIDENCE = 0.45


def normalize(text: str) -> str:
    """Lowercase, unify Devanagari variants, drop punctuation, collapse spaces.

    Decimal points between digits and a minus sign before digits are kept so
    fractional and negative quantities can be rejected rather than misread.
    """
    text = unicodedata.normalize("NFC", text or "").lower()
    text = text.replace("αñ╝", "")  # nukta: αñûαñ╝ -> αñû
    text = text.replace("αñü", "αñé")  # chandrabindu -> anusvara: αñ╣αñ╛αñü -> αñ╣αñ╛αñé
    text = text.translate(str.maketrans("αÑªαÑºαÑ¿αÑ⌐αÑ¬αÑ½αÑ¼αÑ¡αÑ«αÑ»", "0123456789"))
    text = re.sub(r"(?<=\d)[.,](?=\d)", "\x00", text)
    text = re.sub(r"(?<![\w])-(?=\d)", "\x01", text)
    text = text.replace("'", "")  # "it's" -> "its"
    # Python's \w excludes Devanagari vowel signs, so keep the whole block
    # except danda, double danda and the abbreviation sign.
    text = re.sub(r"[αÑñαÑÑαÑ░]", " ", text)
    text = re.sub(r"[^\w\sαñÇ-αÑ┐\x00\x01]", " ", text)
    text = text.replace("\x00", ".").replace("\x01", "-")
    return " ".join(text.split())


def _norm_set(words) -> frozenset:
    return frozenset(normalize(w) for w in words)


# --- vocabulary --------------------------------------------------------------

EN_ONES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
EN_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}

INDIC_NUMBERS = {
    # Hindi (romanized + Devanagari)
    "shunya": 0, "αñ╢αÑéαñ¿αÑìαñ»": 0, "ek": 1, "αñÅαñò": 1, "do": 2, "αñªαÑï": 2, "teen": 3, "αññαÑÇαñ¿": 3,
    "char": 4, "chaar": 4, "αñÜαñ╛αñ░": 4, "paanch": 5, "panch": 5, "αñ¬αñ╛αñéαñÜ": 5,
    "chhe": 6, "chhah": 6, "αñ¢αñ╣": 6, "saat": 7, "αñ╕αñ╛αññ": 7, "aath": 8, "αñåαñá": 8,
    "nau": 9, "αñ¿αÑî": 9, "das": 10, "αñªαñ╕": 10, "gyarah": 11, "αñùαÑìαñ»αñ╛αñ░αñ╣": 11,
    "barah": 12, "αñ¼αñ╛αñ░αñ╣": 12, "terah": 13, "αññαÑçαñ░αñ╣": 13, "chaudah": 14, "αñÜαÑîαñªαñ╣": 14,
    "pandrah": 15, "αñ¬αñéαñªαÑìαñ░αñ╣": 15, "solah": 16, "αñ╕αÑïαñ▓αñ╣": 16, "satrah": 17, "αñ╕αññαÑìαñ░αñ╣": 17,
    "atharah": 18, "athara": 18, "αñàαñáαñ╛αñ░αñ╣": 18, "unnis": 19, "αñëαñ¿αÑìαñ¿αÑÇαñ╕": 19,
    "bees": 20, "αñ¼αÑÇαñ╕": 20, "ikkis": 21, "αñçαñòαÑìαñòαÑÇαñ╕": 21, "bais": 22, "αñ¼αñ╛αñêαñ╕": 22,
    "teis": 23, "αññαÑçαñêαñ╕": 23, "chaubis": 24, "αñÜαÑîαñ¼αÑÇαñ╕": 24, "pachchis": 25, "pachis": 25,
    "αñ¬αñÜαÑìαñÜαÑÇαñ╕": 25, "tees": 30, "αññαÑÇαñ╕": 30, "chalis": 40, "αñÜαñ╛αñ▓αÑÇαñ╕": 40,
    "pachas": 50, "αñ¬αñÜαñ╛αñ╕": 50, "sau": 100, "αñ╕αÑî": 100,
    # Marathi
    "don": 2, "αñªαÑïαñ¿": 2, "pach": 5, "αñ¬αñ╛αñÜ": 5, "saha": 6, "αñ╕αñ╣αñ╛": 6, "αñ¿αñè": 9,
    "daha": 10, "αñªαñ╣αñ╛": 10, "akra": 11, "αñàαñòαñ░αñ╛": 11, "bara": 12, "αñ¼αñ╛αñ░αñ╛": 12,
    "tera": 13, "αññαÑçαñ░αñ╛": 13, "chauda": 14, "αñÜαÑîαñªαñ╛": 14, "pandhra": 15, "αñ¬αñéαñºαñ░αñ╛": 15,
    "sola": 16, "αñ╕αÑïαñ│αñ╛": 16, "satra": 17, "αñ╕αññαñ░αñ╛": 17, "athra": 18, "αñàαñáαñ░αñ╛": 18,
    "ekonis": 19, "αñÅαñòαÑïαñúαÑÇαñ╕": 19, "vees": 20, "αñ╡αÑÇαñ╕": 20, "panchvis": 25, "αñ¬αñéαñÜαñ╡αÑÇαñ╕": 25,
    "αñÜαñ╛αñ│αÑÇαñ╕": 40, "pannas": 50, "αñ¬αñ¿αÑìαñ¿αñ╛αñ╕": 50,
}
INDIC_NUMBERS = {normalize(k): v for k, v in INDIC_NUMBERS.items()}

# Words that are numbers only next to a bicycle word or when said alone
# ("do" = Hindi 2 but also English "do"; "tera" = Marathi 13 but Hindi "your").
CONTEXT_ONLY_NUMBERS = _norm_set(["do", "tera", "sau", "bara"])

FRACTION_WORDS = _norm_set([
    "half", "aadha", "adha", "αñåαñºαñ╛", "dhai", "αñóαñ╛αñê", "dedh", "αñíαÑçαñóαñ╝", "sadhe", "αñ╕αñ╛αñóαñ╝αÑç",
    "adich", "αñàαñíαÑÇαñÜ", "didh", "αñªαÑÇαñí", "quarter", "pauna", "αñ¬αÑîαñ¿αÑç",
])

BICYCLE_WORDS = _norm_set([
    "cycle", "cycles", "bicycle", "bicycles", "bike", "bikes", "saikil",
    "αñ╕αñ╛αñçαñòαñ┐αñ▓", "αñ╕αñ╛αñçαñòαñ┐αñ▓αÑçαñé", "αñ╕αñ╛αñêαñòαñ┐αñ▓", "αñ╕αñ╛αñ»αñòαñ▓", "αñ╕αñ╛αñ»αñòαñ▓αÑÇ", "αñ╕αñ╛αñ»αñòαñ▓αÑçαñé",
    "piece", "pieces", "nag", "αñ¿αñù",
])

CANCEL_PHRASES = _norm_set([
    "cancel", "cancelled", "abort", "stop", "rad karo", "radd karo", "radd",
    "αñ░αñªαÑìαñª", "αñ░αñªαÑìαñª αñòαñ░αÑï", "αñ░αñªαÑìαñª αñòαñ░αñ╛", "band karo", "αñ¼αñéαñª αñòαñ░αÑï", "αñ¼αñéαñª αñòαñ░αñ╛",
    "mat karo", "αñ«αññ αñòαñ░αÑï", "rehne do", "αñ░αñ╣αñ¿αÑç αñªαÑï", "nako", "αñ¿αñòαÑï",
])
NEGATION_PHRASES = _norm_set([
    "no", "nope", "not", "nahi", "nahin", "nai", "αñ¿αñ╣αÑÇαñé", "αñ¿αñ╣αÑÇ", "αñ¿αñ╛αñ╣αÑÇ",
    "wrong", "galat", "αñùαñ▓αññ", "chuk", "αñÜαÑéαñò", "αñÜαÑüαñòαÑÇαñÜαÑç",
])
CORRECTION_PHRASES = _norm_set([
    "correction", "actually", "sorry", "instead", "change", "badlo", "αñ¼αñªαñ▓αÑï", "αñ¼αñªαñ▓αñ╛",
    "matlab", "αñ«αññαñ▓αñ¼", "i mean",
])
CONFIRM_PHRASES = _norm_set([
    "yes", "yeah", "yep", "haan", "haa", "han", "ha", "haanji", "haan ji", "ji haan",
    "αñ╣αñ╛αñü", "αñ╣αñ╛αñé", "αñ╣αñ╛", "αñ╣αÑïαñ»", "hoy", "theek hai", "thik hai", "αñáαÑÇαñò αñ╣αÑê",
    "sahi hai", "αñ╕αñ╣αÑÇ αñ╣αÑê", "save karo", "save kara", "save it", "save",
    "confirm", "confirmed", "ok", "okay", "αñ¼αñ░αÑïαñ¼αñ░", "barobar", "go ahead",
])
# Whole-utterance-only confirmations (too short / overloaded inside sentences).
CONFIRM_EXACT = _norm_set(["ho", "αñ╣αÑï", "ji", "αñ£αÑÇ"])
# Words allowed next to a confirmation word. Anything else makes it unclear.
CONFIRM_FILLERS = _norm_set([
    "it", "please", "pls", "ji", "jee", "now", "this", "that", "do", "karo", "kara",
    "kar", "αñòαñ░αÑï", "αñòαñ░αñ╛", "αñòαñ░", "αñªαÑï", "sure", "correct", "right", "bilkul", "αñ¼αñ┐αñ▓αñòαÑüαñ▓",
    "hai", "αñ╣αÑê", "aahe", "αñåαñ╣αÑç", "sir", "go", "ahead", "αñ╕αÑçαñ╡",
])

FINISHED_PHRASES = _norm_set([
    "ho gaya", "ho gayi", "ho gaye", "ho gai", "αñ╣αÑï αñùαñ»αñ╛", "αñ╣αÑï αñùαñê", "αñ╣αÑï αñùαñÅ", "αñ╣αÑï αñùαñ»αÑç",
    "finished", "finish", "done", "complete", "completed", "khatam", "αñûαññαÑìαñ«",
    "assembled", "built", "made", "produced", "ban gaye", "ban gayi", "ban gaya",
    "αñ¼αñ¿ αñùαñÅ", "αñ¼αñ¿ αñùαñê", "αñ¼αñ¿ αñùαñ»αñ╛", "kar liya", "kar liye", "αñòαñ░ αñ▓αñ┐αñ»αñ╛", "αñòαñ░ αñ▓αñ┐αñÅ",
    "pura", "αñ¬αÑéαñ░αñ╛", "αñ¬αÑéαñ░αÑìαñú", "zala", "jhala", "zale", "jhale", "αñ¥αñ╛αñ▓αñ╛", "αñ¥αñ╛αñ▓αÑç", "αñ¥αñ╛αñ▓αÑÇ",
    "sampla", "αñ╕αñéαñ¬αñ▓αÑç", "αñ╕αñéαñ¬αñ▓αñ╛", "kele", "αñòαÑçαñ▓αÑç", "αñòαÑçαñ▓αÑìαñ»αñ╛", "tayyar hai", "αññαÑêαñ»αñ╛αñ░ αñ╣αÑê",
])
ASSEMBLE_PHRASES = _norm_set([
    "assemble", "assembly", "αñàαñ╕αÑçαñéαñ¼αñ▓", "αñàαñ╕αÑçαñéαñ¼αñ▓αÑÇ", "αñàαñ╕αÑçαñ«αÑìαñ¼αñ▓", "banao", "αñ¼αñ¿αñ╛αñô",
    "banana", "αñ¼αñ¿αñ╛αñ¿αñ╛", "banva", "αñ¼αñ¿αñ╡αñ╛", "build", "make", "produce", "manufacture",
    "start", "shuru", "αñ╢αÑüαñ░αÑé", "αñ╕αÑüαñ░αÑé", "tayar kara", "αññαñ»αñ╛αñ░ αñòαñ░αñ╛", "αññαÑêαñ»αñ╛αñ░ αñòαñ░αÑï",
])

HINDI_MARKERS = _norm_set([
    "karo", "kar", "αñòαñ░αÑï", "gaya", "αñùαñ»αñ╛", "gaye", "αñùαñÅ", "nahi", "αñ¿αñ╣αÑÇαñé", "haan", "αñ╣αñ╛αñé",
    "banao", "αñ¼αñ¿αñ╛αñô", "theek", "αñáαÑÇαñò", "hai", "αñ╣αÑê", "αñ╕αñ╛αñçαñòαñ┐αñ▓", "αñ¼αÑÇαñ╕", "athara", "bees",
    "mat", "khatam", "ban",
])
MARATHI_MARKERS = _norm_set([
    "kara", "αñòαñ░αñ╛", "zala", "jhala", "αñ¥αñ╛αñ▓αñ╛", "αñ¥αñ╛αñ▓αÑç", "nako", "αñ¿αñòαÑï", "hoy", "αñ╣αÑïαñ»",
    "αñ╕αñ╛αñ»αñòαñ▓", "αñ╕αñ╛αñ»αñòαñ▓αÑÇ", "αñ╡αÑÇαñ╕", "vees", "αñªαÑïαñ¿", "αñ¬αñ╛αñÜ", "aahe", "αñåαñ╣αÑç", "αñ¿αñ╛αñ╣αÑÇ",
    "αñ¼αñ░αÑïαñ¼αñ░", "barobar", "αñ¬αÑéαñ░αÑìαñú", "athra", "αñàαñáαñ░αñ╛", "banva", "αñ¼αñ¿αñ╡αñ╛",
])


@dataclass
class ParsedCommand:
    """Result of parsing one utterance.

    ``quantity_role`` says what ``quantity`` means so the target and the
    actual completed quantity can never be mixed up:
    ``"target"`` for create_production_order, ``"completed"`` for
    provide_completed_quantity / correct_previous_answer / confirm.
    """

    intent: str
    original_text: str
    normalized_text: str = ""
    language: str = "unknown"
    quantity: Optional[int] = None
    quantity_role: Optional[str] = None  # "target" | "completed"
    quantity_invalid: Optional[str] = None  # "fractional" | "negative"
    finished_mentioned: bool = False
    cancel_scope: Optional[str] = None  # "order" (explicit cancel) | "reject" (plain "no")
    confidence: Optional[float] = None  # from speech recognition; None = unavailable
    reason: Optional[str] = None  # why the intent is unknown

    @property
    def confidence_available(self) -> bool:
        return self.confidence is not None

    def contract_slots(self) -> dict:
        """Slots in the shape the API contract uses for each intent."""
        if self.intent == "create_production_order":
            slots = {"product_id": PRODUCT_ID, "target_quantity": self.quantity, "unit": UNIT}
        elif self.intent in ("provide_completed_quantity", "correct_previous_answer"):
            slots = {"actual_completed_quantity": self.quantity, "unit": UNIT}
        elif self.intent == "confirm" and self.quantity is not None:
            slots = {"actual_completed_quantity": self.quantity, "unit": UNIT}
        elif self.intent == "cancel":
            slots = {"scope": self.cancel_scope}
        else:
            slots = {}
        if self.quantity_invalid:
            slots["quantity_invalid"] = self.quantity_invalid
        if self.intent == "unknown" and self.reason:
            slots["reason"] = self.reason
        return slots

    def to_dict(self) -> dict:
        return {
            "intent": self.intent,
            "original_text": self.original_text,
            "normalized_text": self.normalized_text,
            "language": self.language,
            "quantity": self.quantity,
            "quantity_role": self.quantity_role,
            "quantity_invalid": self.quantity_invalid,
            "confidence": self.confidence,
            "confidence_available": self.confidence_available,
            "reason": self.reason,
            "slots": self.contract_slots(),
        }


def _has_phrase(padded: str, phrases) -> bool:
    return any(f" {p} " in padded for p in phrases)


def _remove_phrases(padded: str, phrases) -> str:
    for p in sorted(phrases, key=len, reverse=True):
        while f" {p} " in padded:
            padded = padded.replace(f" {p} ", " ")
    return padded


def _is_number_token(tok: str) -> bool:
    return bool(re.fullmatch(r"-?\d+(\.\d+)?", tok)) or tok in EN_ONES or tok in EN_TENS \
        or tok in INDIC_NUMBERS or tok in FRACTION_WORDS or tok in ("and", "hundred")


def extract_numbers(tokens: list[str]) -> tuple[list[int], Optional[str]]:
    """Return (whole numbers found, invalid kind) from normalized tokens."""
    numbers: list[int] = []
    invalid: Optional[str] = None
    single = len(tokens) == 1
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if re.fullmatch(r"-?\d+(\.\d+)?", tok):
            if tok.startswith("-"):
                invalid = "negative"
            elif "." in tok:
                invalid = "fractional"
            else:
                numbers.append(int(tok))
            i += 1
            continue
        if tok in FRACTION_WORDS:
            invalid = "fractional"
            i += 1
            continue
        if tok in EN_ONES or tok in EN_TENS:
            current = 0
            consumed = False
            while i < len(tokens):
                t = tokens[i]
                if t in EN_TENS:
                    current += EN_TENS[t]
                elif t in EN_ONES:
                    current += EN_ONES[t]
                elif t == "hundred" and consumed:
                    current = max(current, 1) * 100
                elif t == "and" and consumed and i + 1 < len(tokens) and (
                    tokens[i + 1] in EN_ONES or tokens[i + 1] in EN_TENS
                ):
                    pass
                else:
                    break
                consumed = True
                i += 1
            numbers.append(current)
            continue
        if tok in INDIC_NUMBERS:
            if tok in CONTEXT_ONLY_NUMBERS and not single:
                neighbours = tokens[max(0, i - 1):i] + tokens[i + 1:i + 2]
                if not any(n in BICYCLE_WORDS for n in neighbours):
                    i += 1
                    continue
            numbers.append(INDIC_NUMBERS[tok])
        i += 1
    return numbers, invalid


def detect_language(text: str) -> str:
    """Heuristic language label (en/hi/mr/mixed/unknown) from script and marker words."""
    norm = normalize(text)
    if not norm:
        return "unknown"
    padded = f" {norm} "
    has_deva = bool(re.search(r"[αñÇ-αÑ┐]", norm))
    has_latin = bool(re.search(r"[a-z]", norm))
    hi = _has_phrase(padded, HINDI_MARKERS)
    mr = _has_phrase(padded, MARATHI_MARKERS)
    if has_deva and has_latin:
        return "mixed"
    if has_deva:
        if mr and not hi:
            return "mr"
        if hi and mr:
            return "mixed"
        return "hi"
    if has_latin:
        return "mixed" if (hi or mr) else "en"
    return "unknown"  # digits only: no language evidence


def parse(text: str, confidence: Optional[float] = None) -> ParsedCommand:
    """Classify one worker utterance. Never raises for string input."""
    norm = normalize(text)
    result = ParsedCommand(
        intent="unknown",
        original_text=text or "",
        normalized_text=norm,
        language=detect_language(text),
        confidence=confidence,
    )

    def unknown(reason: str) -> ParsedCommand:
        result.intent = "unknown"
        result.reason = reason
        result.quantity = None
        result.quantity_role = None
        return result

    def intent(name: str, role: Optional[str] = None) -> ParsedCommand:
        result.intent = name
        result.quantity_role = role if result.quantity is not None else None
        return result

    if not norm:
        return unknown("empty")
    if confidence is not None and confidence < MIN_CONFIDENCE:
        return unknown("low_confidence")

    padded = f" {norm} "
    tokens = norm.split()
    numbers, invalid = extract_numbers(tokens)
    distinct = sorted(set(numbers))
    result.quantity_invalid = invalid
    if len(distinct) == 1 and not invalid:
        result.quantity = distinct[0]

    is_cancel = _has_phrase(padded, CANCEL_PHRASES)
    is_negation = _has_phrase(padded, NEGATION_PHRASES)
    is_correction = _has_phrase(padded, CORRECTION_PHRASES)
    is_confirm = _has_phrase(padded, CONFIRM_PHRASES) or norm in CONFIRM_EXACT
    is_finished = _has_phrase(padded, FINISHED_PHRASES)
    is_assemble = _has_phrase(padded, ASSEMBLE_PHRASES)
    result.finished_mentioned = is_finished

    # Two different numbers, or a valid and an invalid one: never guess.
    if len(distinct) > 1 or (distinct and invalid):
        return unknown("ambiguous_quantity")

    if is_cancel:
        if is_confirm and not is_negation:  # "yes cancel"
            return unknown("conflicting_confirm_and_cancel")
        result.cancel_scope = "order"
        result.quantity = None
        return intent("cancel")

    if is_finished:
        if is_negation:  # "not finished", "abhi nahi hua"
            return unknown("negated_completion")
        if numbers or invalid:
            return intent("provide_completed_quantity", "completed")
        return intent("report_assembly_finished")

    if is_confirm:
        if is_negation:  # "haan nahi"
            return unknown("conflicting_confirm_and_negation")
        if invalid:
            return unknown("invalid_quantity_with_confirm")
        if norm in CONFIRM_EXACT:
            return intent("confirm")
        # A confirmation must be only confirmation words (plus an optional
        # number). "yes, I want to assemble 20" is not a confirmation.
        rest = _remove_phrases(padded, CONFIRM_PHRASES | CONFIRM_FILLERS | BICYCLE_WORDS)
        if any(not _is_number_token(t) for t in rest.split()):
            return unknown("confirmation_with_other_content")
        return intent("confirm", "completed")

    if is_negation or is_correction:
        if numbers or invalid:
            return intent("correct_previous_answer", "completed")
        if is_negation:
            result.cancel_scope = "reject"
            return intent("cancel")
        return unknown("correction_without_quantity")

    if is_assemble:
        if not numbers and not invalid:
            return unknown("missing_quantity")
        return intent("create_production_order", "target")

    # Bare quantity: "18", "athara", "18 cycle".
    leftover = [t for t in tokens if not (
        _is_number_token(t) or t in BICYCLE_WORDS or t in {"only", "sirf", "bas", "αñ½αñòαÑìαññ", "αñ╕αñ┐αñ░αÑìαñ½"}
    )]
    if (numbers or invalid) and not leftover:
        return intent("provide_completed_quantity", "completed")

    return unknown("unrecognized")

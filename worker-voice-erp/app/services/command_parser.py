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
    text = text.replace("़", "")  # nukta: ख़ -> ख
    text = text.replace("ँ", "ं")  # chandrabindu -> anusvara: हाँ -> हां
    text = text.translate(str.maketrans("०१२३४५६७८९", "0123456789"))
    text = re.sub(r"(?<=\d)[.,](?=\d)", "\x00", text)
    text = re.sub(r"(?<![\w])-(?=\d)", "\x01", text)
    text = text.replace("'", "")  # "it's" -> "its"
    # Python's \w excludes Devanagari vowel signs, so keep the whole block
    # except danda, double danda and the abbreviation sign.
    text = re.sub(r"[।॥॰]", " ", text)
    text = re.sub(r"[^\w\sऀ-ॿ\x00\x01]", " ", text)
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
    "shunya": 0, "शून्य": 0, "ek": 1, "एक": 1, "do": 2, "दो": 2, "teen": 3, "तीन": 3,
    "char": 4, "chaar": 4, "चार": 4, "paanch": 5, "panch": 5, "पांच": 5,
    "chhe": 6, "chhah": 6, "छह": 6, "saat": 7, "सात": 7, "aath": 8, "आठ": 8,
    "nau": 9, "नौ": 9, "das": 10, "दस": 10, "gyarah": 11, "ग्यारह": 11,
    "barah": 12, "बारह": 12, "terah": 13, "तेरह": 13, "chaudah": 14, "चौदह": 14,
    "pandrah": 15, "पंद्रह": 15, "solah": 16, "सोलह": 16, "satrah": 17, "सत्रह": 17,
    "atharah": 18, "athara": 18, "अठारह": 18, "unnis": 19, "उन्नीस": 19,
    "bees": 20, "बीस": 20, "ikkis": 21, "इक्कीस": 21, "bais": 22, "बाईस": 22,
    "teis": 23, "तेईस": 23, "chaubis": 24, "चौबीस": 24, "pachchis": 25, "pachis": 25,
    "पच्चीस": 25, "tees": 30, "तीस": 30, "chalis": 40, "चालीस": 40,
    "pachas": 50, "पचास": 50, "sau": 100, "सौ": 100,
    # Marathi
    "don": 2, "दोन": 2, "pach": 5, "पाच": 5, "saha": 6, "सहा": 6, "नऊ": 9,
    "daha": 10, "दहा": 10, "akra": 11, "अकरा": 11, "bara": 12, "बारा": 12,
    "tera": 13, "तेरा": 13, "chauda": 14, "चौदा": 14, "pandhra": 15, "पंधरा": 15,
    "sola": 16, "सोळा": 16, "satra": 17, "सतरा": 17, "athra": 18, "अठरा": 18,
    "ekonis": 19, "एकोणीस": 19, "vees": 20, "वीस": 20, "panchvis": 25, "पंचवीस": 25,
    "चाळीस": 40, "pannas": 50, "पन्नास": 50,
}
INDIC_NUMBERS = {normalize(k): v for k, v in INDIC_NUMBERS.items()}

# Words that are numbers only next to a bicycle word or when said alone
# ("do" = Hindi 2 but also English "do"; "tera" = Marathi 13 but Hindi "your").
CONTEXT_ONLY_NUMBERS = _norm_set(["do", "tera", "sau", "bara"])

FRACTION_WORDS = _norm_set([
    "half", "aadha", "adha", "आधा", "dhai", "ढाई", "dedh", "डेढ़", "sadhe", "साढ़े",
    "adich", "अडीच", "didh", "दीड", "quarter", "pauna", "पौने",
])

BICYCLE_WORDS = _norm_set([
    "cycle", "cycles", "bicycle", "bicycles", "bike", "bikes", "saikil",
    "साइकिल", "साइकिलें", "साईकिल", "सायकल", "सायकली", "सायकलें",
    "piece", "pieces", "nag", "नग",
])

CANCEL_PHRASES = _norm_set([
    "cancel", "cancelled", "abort", "stop", "rad karo", "radd karo", "radd",
    "रद्द", "रद्द करो", "रद्द करा", "band karo", "बंद करो", "बंद करा",
    "mat karo", "मत करो", "rehne do", "रहने दो", "nako", "नको",
])
NEGATION_PHRASES = _norm_set([
    "no", "nope", "not", "nahi", "nahin", "nai", "नहीं", "नही", "नाही",
    "wrong", "galat", "गलत", "chuk", "चूक", "चुकीचे",
])
CORRECTION_PHRASES = _norm_set([
    "correction", "actually", "sorry", "instead", "change", "badlo", "बदलो", "बदला",
    "matlab", "मतलब", "i mean",
])
CONFIRM_PHRASES = _norm_set([
    "yes", "yeah", "yep", "haan", "haa", "han", "ha", "haanji", "haan ji", "ji haan",
    "हाँ", "हां", "हा", "होय", "hoy", "theek hai", "thik hai", "ठीक है",
    "sahi hai", "सही है", "save karo", "save kara", "save it", "save",
    "confirm", "confirmed", "ok", "okay", "बरोबर", "barobar", "go ahead",
    "ho", "हो", "सेव करो", "सेव करा", "सेव्ह करा", "सेव्ह करो",
])
# Whole-utterance-only confirmations (too short / overloaded inside sentences).
CONFIRM_EXACT = _norm_set(["ho", "हो", "ji", "जी"])
# Words allowed next to a confirmation word. Anything else makes it unclear.
CONFIRM_FILLERS = _norm_set([
    "it", "please", "pls", "ji", "jee", "now", "this", "that", "do", "karo", "kara",
    "kar", "करो", "करा", "कर", "दो", "sure", "correct", "right", "bilkul", "बिलकुल",
    "hai", "है", "aahe", "आहे", "sir", "go", "ahead", "सेव", "सेव्ह", "save",
])

FINISHED_PHRASES = _norm_set([
    "ho gaya", "ho gayi", "ho gaye", "ho gai", "हो गया", "हो गई", "हो गए", "हो गये",
    "finished", "finish", "done", "complete", "completed", "khatam", "खत्म",
    "assembled", "built", "made", "produced", "ban gaye", "ban gayi", "ban gaya",
    "बन गए", "बन गई", "बन गया", "kar liya", "kar liye", "कर लिया", "कर लिए",
    "pura", "पूरा", "पूर्ण", "zala", "jhala", "zale", "jhale", "झाला", "झाले", "झाली",
    "sampla", "संपले", "संपला", "kele", "केले", "केल्या", "tayyar hai", "तैयार है",
    "झाल्या", "jhalya", "zalya", "तयार झाल्या", "तयार झाले", "बन गई हैं", "हो गई हैं",
])
ASSEMBLE_PHRASES = _norm_set([
    "assemble", "assembly", "असेंबल", "असेंबली", "असेम्बल", "banao", "बनाओ",
    "banana", "बनाना", "banva", "बनवा", "build", "make", "produce", "manufacture",
    "start", "shuru", "शुरू", "सुरू", "tayar kara", "तयार करा", "तैयार करो",
])

HINDI_MARKERS = _norm_set([
    "karo", "kar", "करो", "gaya", "गया", "gaye", "गए", "nahi", "नहीं", "haan", "हां",
    "banao", "बनाओ", "theek", "ठीक", "hai", "है", "साइकिल", "बीस", "athara", "bees",
    "mat", "khatam", "ban", "हैं", "गई", "बन", "कीं", "किया", "करें", "अठारह",
    "nahin", "gayi", "gai", "rahe",
])
MARATHI_MARKERS = _norm_set([
    "kara", "करा", "zala", "jhala", "झाला", "झाले", "nako", "नको", "hoy", "होय",
    "सायकल", "सायकली", "वीस", "vees", "दोन", "पाच", "aahe", "आहे", "नाही",
    "बरोबर", "barobar", "पूर्ण", "athra", "अठरा", "banva", "बनवा",
    "झाल्या", "झाली", "आहेत", "तयार", "केल्या", "सेव्ह", "jhalya", "zalya", "aahet", "naahi",
])
# English words that only appear in English sentences of this domain. Hindi or
# Marathi grammar words win over these, so "20 cycle assemble karo" is Hindi.
ENGLISH_MARKERS = _norm_set([
    "i", "we", "it", "the", "to", "want", "please", "yes", "no", "cancel", "save",
    "assemble", "assembled", "bicycle", "bicycles", "bikes", "done", "finished",
    "completed", "how", "many", "okay", "ok", "stop", "wrong", "correct", "actually",
    "made", "built", "have", "has", "is", "are", "was",
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


# Marathi verb/grammar words that Hindi does not use. If speech recognised with
# an English/Hindi model contains one, the worker was probably speaking Marathi.
MARATHI_GRAMMAR = _norm_set([
    "करा", "झाले", "झाला", "झाली", "झाल्या", "आहे", "आहेत", "नाही", "नको", "होय",
    "kara", "zala", "jhala", "zale", "jhale", "aahe", "aahet", "nako", "hoy",
])


def has_marathi_grammar(text: str) -> bool:
    norm = normalize(text)
    return bool(norm) and _has_phrase(f" {norm} ", MARATHI_GRAMMAR)


def _count_phrases(padded: str, phrases) -> int:
    return sum(1 for p in phrases if f" {p} " in padded)


def dominant_language(text: str) -> Optional[str]:
    """The language whose grammar words dominate the sentence: en, hi, mr or None.

    Counts distinct marker words, never a single look-alike noun: Hindi/Marathi
    grammar words (karo/करो, kara/करा, gaye/गए, झाले/झाल्या, ...) decide a
    code-switched sentence; English counts only when no Hindi/Marathi marker is
    present. A tie, or Devanagari without markers, returns None (undecided).
    """
    norm = normalize(text)
    if not norm:
        return None
    padded = f" {norm} "
    hi = _count_phrases(padded, HINDI_MARKERS)
    mr = _count_phrases(padded, MARATHI_MARKERS)
    if hi != mr:
        return "hi" if hi > mr else "mr"
    if hi:  # tie between Hindi and Marathi evidence
        return None
    if re.search(r"[ऀ-ॿ]", norm):
        return None
    return "en" if _count_phrases(padded, ENGLISH_MARKERS) else None


def detect_language(text: str) -> str:
    """Heuristic language label (en/hi/mr/mixed/unknown) from script and marker words."""
    norm = normalize(text)
    if not norm:
        return "unknown"
    padded = f" {norm} "
    has_deva = bool(re.search(r"[ऀ-ॿ]", norm))
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
        _is_number_token(t) or t in BICYCLE_WORDS or t in {"only", "sirf", "bas", "फक्त", "सिर्फ"}
    )]
    if (numbers or invalid) and not leftover:
        return intent("provide_completed_quantity", "completed")

    return unknown("unrecognized")

import pytest

from app.services.command_parser import parse


# --- order requests: quantity is the TARGET --------------------------------

@pytest.mark.parametrize(
    "text,quantity,language",
    [
        ("Assemble 20 bicycles.", 20, "en"),
        ("I want to assemble 10 bicycles.", 10, "en"),
        ("assemble twenty five bicycles", 25, "en"),
        ("बीस साइकिल असेंबल करो।", 20, "hi"),
        ("वीस सायकली असेंबल करा।", 20, "mr"),
        ("20 cycle assemble karo.", 20, "mixed"),
        ("२० सायकली असेंबल करा", 20, "mr"),
        ("do cycle banao", 2, "mixed"),
    ],
)
def test_order_requests(text, quantity, language):
    p = parse(text)
    assert p.intent == "create_production_order"
    assert p.quantity == quantity
    assert p.quantity_role == "target"
    assert p.language == language
    assert p.contract_slots() == {"product_id": "bicycle-standard", "target_quantity": quantity, "unit": "piece"}


# --- completion reports: quantity is the ACTUAL COMPLETED count -------------

@pytest.mark.parametrize(
    "text,quantity",
    [
        ("I assembled 18 bicycles.", 18),
        ("18 cycle ban gaye.", 18),
        ("18", 18),
        ("athara", 18),
        ("अठरा", 18),
        ("eighteen", 18),
        ("१८ सायकली", 18),
    ],
)
def test_completed_quantity(text, quantity):
    p = parse(text)
    assert p.intent == "provide_completed_quantity"
    assert p.quantity == quantity
    assert p.quantity_role == "completed"
    assert p.contract_slots() == {"actual_completed_quantity": quantity, "unit": "piece"}


def test_target_and_completed_quantities_are_never_confused():
    order = parse("Assemble 20 bicycles.")
    done = parse("I assembled 18 bicycles.")
    assert (order.quantity_role, done.quantity_role) == ("target", "completed")
    assert "actual_completed_quantity" not in order.contract_slots()
    assert "target_quantity" not in done.contract_slots()


def test_assembled_is_not_assemble():
    # Whole-token matching: past tense is a completion report, not a new order.
    assert parse("I assembled 18 bicycles").intent != "create_production_order"


@pytest.mark.parametrize("text", ["Assembly ho gaya", "assembly done", "assembly finished",
                                  "असेंबली हो गई", "पूर्ण झाले", "kaam khatam"])
def test_assembly_finished_without_quantity(text):
    p = parse(text)
    assert p.intent == "report_assembly_finished"
    assert p.quantity is None


# --- confirmation, cancellation, correction ---------------------------------

@pytest.mark.parametrize("text", ["Yes, save it.", "Haan, save karo.", "haan", "yes", "होय", "हाँ",
                                  "ठीक है", "theek hai", "ok", "ho", "haan ji theek hai"])
def test_confirm(text):
    assert parse(text).intent == "confirm"


@pytest.mark.parametrize("text", [
    "yes I want to assemble 20 bicycles",
    "yes but wait",
    "yesterday",
    "haan nahi",
    "yes cancel",
    "the answer is not yes",
])
def test_yes_inside_other_content_is_not_confirmation(text):
    assert parse(text).intent != "confirm"


@pytest.mark.parametrize("text", ["No, cancel.", "cancel", "radd karo", "रद्द करो", "stop", "नको"])
def test_explicit_cancel(text):
    p = parse(text)
    assert p.intent == "cancel"
    assert p.cancel_scope == "order"


@pytest.mark.parametrize("text", ["no", "nahi", "नहीं", "नाही"])
def test_plain_no_is_reject(text):
    p = parse(text)
    assert p.intent == "cancel"
    assert p.cancel_scope == "reject"


@pytest.mark.parametrize("text", ["nahi 17", "no, 17", "galat 17", "नहीं, सत्रह", "actually 17"])
def test_correction(text):
    p = parse(text)
    assert p.intent == "correct_previous_answer"
    assert p.quantity == 17
    assert p.quantity_role == "completed"


# --- unknown / ambiguous / invalid ------------------------------------------

@pytest.mark.parametrize(
    "text,reason",
    [
        ("", "empty"),
        ("   ", "empty"),
        ("hmm what", "unrecognized"),
        ("20 ya 25 cycle", "ambiguous_quantity"),
        ("18 nahi 17", "ambiguous_quantity"),
        ("assemble cycles", "missing_quantity"),
        ("I have not assembled", "negated_completion"),
        ("haan nahi", "conflicting_confirm_and_negation"),
    ],
)
def test_unknown_and_ambiguous(text, reason):
    p = parse(text)
    assert p.intent == "unknown"
    assert p.reason == reason
    assert p.quantity is None


@pytest.mark.parametrize("text,kind", [("2.5 cycle", "fractional"), ("dhai cycle", "fractional"),
                                       ("-3", "negative"), ("आधा", "fractional")])
def test_invalid_quantities_are_flagged_not_rounded(text, kind):
    p = parse(text)
    assert p.quantity is None
    assert p.quantity_invalid == kind


def test_zero_is_extracted_for_the_dialogue_to_reject():
    assert parse("0").quantity == 0


def test_low_confidence_is_never_an_intent():
    p = parse("haan", confidence=0.2)
    assert p.intent == "unknown" and p.reason == "low_confidence"
    assert parse("haan", confidence=0.9).intent == "confirm"


def test_result_structure_keeps_original_text_and_confidence():
    p = parse("  Assemble 20 Bicycles!! ")
    d = p.to_dict()
    assert d["original_text"] == "  Assemble 20 Bicycles!! "
    assert d["normalized_text"] == "assemble 20 bicycles"
    assert d["confidence"] is None and d["confidence_available"] is False
    assert parse("haan", confidence=0.8).to_dict()["confidence_available"] is True


def test_hindi_softeners_do_not_flip_intent():
    assert parse("20 cycle assemble karo na").intent == "create_production_order"
    assert parse("20 cycle assemble kar do").intent == "create_production_order"


def test_parser_never_raises_on_garbage():
    for junk in ["!!!", "\x00", "🚲🚲", "a" * 600, "१२३४५६७८९०", "-", "...", "न", None]:
        parse(junk)

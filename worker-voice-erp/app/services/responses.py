"""What the assistant says, in English, Hindi and Marathi (Person A).

Two jobs, both pure (no I/O):

* ``say(key, language, **values)``: every user-facing sentence lives in
  ``MESSAGES`` in all three languages. Numbers stay as digits so quantities
  never change meaning; order ids and HTTP codes are passed through untouched.
* ``resolve_response_language(...)``: decides which language to answer in,
  from the words the worker used, the language Google heard, and the language
  of the conversation so far.

Intent names, API fields and status values are never translated.
"""

from __future__ import annotations

import re
import string
from typing import Optional

from app.services.command_parser import ParsedCommand, dominant_language, normalize

SUPPORTED = ("en", "hi", "mr")
DEFAULT_LANGUAGE = "en"
SPEECH_TAGS = {"en": "en-IN", "hi": "hi-IN", "mr": "mr-IN"}
LANGUAGE_NAMES = {"en": "English", "hi": "हिन्दी", "mr": "मराठी"}

# Utterances with this many words or fewer (numbers not counted) are too short
# to judge a language from; they keep the conversation's language.
SHORT_UTTERANCE_WORDS = 2

MESSAGES: dict[str, dict[str, str]] = {
    # --- prompts for each dialogue state ---------------------------------------
    "prompt_idle": {
        "en": "Tell me how many bicycles to assemble.",
        "hi": "बताइए, कितनी साइकिल असेंबल करनी हैं।",
        "mr": "किती सायकली असेंबल करायच्या ते सांगा.",
    },
    "prompt_completed": {
        "en": "Tell me how many bicycles to assemble next.",
        "hi": "बताइए, अब कितनी साइकिल असेंबल करनी हैं।",
        "mr": "आता किती सायकली असेंबल करायच्या ते सांगा.",
    },
    "prompt_order_requested": {
        "en": "Please wait, I am checking parts.",
        "hi": "कृपया रुकिए, मैं पुर्ज़े जाँच रहा हूँ।",
        "mr": "कृपया थांबा, मी पार्ट्स तपासत आहे.",
    },
    "prompt_waiting": {
        "en": "Say assembly finished when you are done.",
        "hi": "काम पूरा होने पर बोलिए: असेंबली हो गई।",
        "mr": "काम पूर्ण झाल्यावर म्हणा: असेंबली झाली.",
    },
    "ask_completed": {
        "en": "How many bicycles did you assemble?",
        "hi": "आपने कितनी साइकिल असेंबल कीं?",
        "mr": "तुम्ही किती सायकली असेंबल केल्या?",
    },
    "prompt_draft_requested": {
        "en": "Please wait, I am recording the count.",
        "hi": "कृपया रुकिए, मैं संख्या दर्ज कर रहा हूँ।",
        "mr": "कृपया थांबा, मी संख्या नोंदवत आहे.",
    },
    "prompt_finalizing": {
        "en": "Please wait, I am saving.",
        "hi": "कृपया रुकिए, मैं सेव कर रहा हूँ।",
        "mr": "कृपया थांबा, मी सेव्ह करत आहे.",
    },
    "prompt_uncertain": {
        "en": "The last save could not be confirmed. Say yes to retry safely.",
        "hi": "पिछले सेव की पुष्टि नहीं हो सकी। सुरक्षित तरीके से दोबारा कोशिश करने के लिए 'हाँ' बोलिए।",
        "mr": "मागच्या सेव्हची खात्री झाली नाही. सुरक्षितपणे पुन्हा प्रयत्न करण्यासाठी 'हो' म्हणा.",
    },
    # --- order step -------------------------------------------------------------------
    "wait_server": {
        "en": "Please wait, I am still waiting for the server.",
        "hi": "कृपया रुकिए, सर्वर के जवाब का इंतज़ार है।",
        "mr": "कृपया थांबा, सर्व्हरच्या उत्तराची वाट पाहत आहे.",
    },
    "order_already_active": {
        "en": "An order for {target} bicycles is already active. {prompt} Say cancel to stop it.",
        "hi": "{target} साइकिल का ऑर्डर पहले से चल रहा है। {prompt} रोकने के लिए 'रद्द करो' बोलिए।",
        "mr": "{target} सायकलींची ऑर्डर आधीच सुरू आहे. {prompt} थांबवण्यासाठी 'रद्द करा' म्हणा.",
    },
    "checking_parts": {
        "en": "Checking parts for {n} {bicycles}.",
        "hi": "{n} साइकिल के लिए पुर्ज़े जाँच रहा हूँ।",
        "mr": "{n} सायकलींसाठी पार्ट्स तपासत आहे.",
    },
    "order_created_available": {
        "en": "Assembling {n} {bicycles}. All parts are available. You can start.",
        "hi": "{n} साइकिल असेंबल करनी हैं। सभी पुर्ज़े उपलब्ध हैं। आप शुरू कर सकते हैं।",
        "mr": "{n} सायकली असेंबल करायच्या आहेत. सर्व पार्ट्स उपलब्ध आहेत. तुम्ही सुरू करू शकता.",
    },
    "order_created_unconfirmed": {
        "en": "Order created for {n} bicycles, but the server did not confirm that all parts are available. "
              "Check parts before starting.",
        "hi": "{n} साइकिल का ऑर्डर बन गया, लेकिन सर्वर ने सभी पुर्ज़े उपलब्ध होने की पुष्टि नहीं की। "
              "शुरू करने से पहले पुर्ज़े जाँच लीजिए।",
        "mr": "{n} सायकलींची ऑर्डर तयार झाली, पण सर्व पार्ट्स उपलब्ध असल्याची खात्री सर्व्हरने दिली नाही. "
              "सुरू करण्यापूर्वी पार्ट्स तपासा.",
    },
    "insufficient_stock": {
        "en": "Not enough parts for {requested} bicycles. You can assemble up to {max_q}. Say a new quantity to start.",
        "hi": "{requested} साइकिल के लिए पूरे पुर्ज़े नहीं हैं। आप ज़्यादा से ज़्यादा {max_q} साइकिल असेंबल कर सकते हैं। "
              "शुरू करने के लिए नई संख्या बोलिए।",
        "mr": "{requested} सायकलींसाठी पुरेसे पार्ट्स नाहीत. तुम्ही जास्तीत जास्त {max_q} सायकली असेंबल करू शकता. "
              "सुरू करण्यासाठी नवीन संख्या सांगा.",
    },
    "insufficient_none": {
        "en": "Not enough parts to assemble any bicycles.",
        "hi": "एक भी साइकिल असेंबल करने के लिए पुर्ज़े नहीं हैं।",
        "mr": "एकही सायकल असेंबल करण्यासाठी पुरेसे पार्ट्स नाहीत.",
    },
    "order_unreachable": {
        "en": "I could not reach the production server, so I cannot confirm the order was created. Please try again.",
        "hi": "प्रोडक्शन सर्वर से संपर्क नहीं हो सका, इसलिए मैं ऑर्डर बनने की पुष्टि नहीं कर सकता। कृपया दोबारा कोशिश करें।",
        "mr": "प्रोडक्शन सर्व्हरशी संपर्क झाला नाही, त्यामुळे ऑर्डर तयार झाल्याची खात्री देता येत नाही. "
              "कृपया पुन्हा प्रयत्न करा.",
    },
    "unexpected_order": {
        "en": "The server sent an unexpected order response. Order status unknown.",
        "hi": "सर्वर से ऑर्डर का अनपेक्षित जवाब आया। ऑर्डर की स्थिति पता नहीं है।",
        "mr": "सर्व्हरकडून ऑर्डरचे अनपेक्षित उत्तर आले. ऑर्डरची स्थिती माहीत नाही.",
    },
    # --- completed count / draft ----------------------------------------------------------
    "no_active_order": {
        "en": "There is no active order. To start, say for example: assemble 20 bicycles.",
        "hi": "अभी कोई ऑर्डर नहीं चल रहा है। शुरू करने के लिए बोलिए, जैसे: बीस साइकिल असेंबल करो।",
        "mr": "सध्या कोणतीही ऑर्डर सुरू नाही. सुरू करण्यासाठी म्हणा, उदाहरणार्थ: वीस सायकली असेंबल करा.",
    },
    "is_assembly_finished": {
        "en": "Is the assembly finished? Say: assembly finished.",
        "hi": "क्या असेंबली पूरी हो गई? बोलिए: असेंबली हो गई।",
        "mr": "असेंबली पूर्ण झाली का? म्हणा: असेंबली झाली.",
    },
    "more_than_target": {
        "en": "{q} is more than the target of {target}. How many bicycles did you assemble?",
        "hi": "{q} टारगेट {target} से ज़्यादा है। आपने कितनी साइकिल असेंबल कीं?",
        "mr": "{q} हे लक्ष्य {target} पेक्षा जास्त आहे. तुम्ही किती सायकली असेंबल केल्या?",
    },
    "recording": {
        "en": "Recording {q} {bicycles}.",
        "hi": "{q} साइकिल दर्ज कर रहा हूँ।",
        "mr": "{q} सायकली नोंदवत आहे.",
    },
    "read_back": {
        "en": "{d} {bicycles} assembled against {target}. Shall I save?",
        "hi": "{target} में से {d} साइकिल असेंबल हुईं। क्या मैं सेव करूँ?",
        "mr": "{target} पैकी {d} सायकली असेंबल झाल्या. सेव्ह करू का?",
    },
    "unexpected_response": {
        "en": "The server sent an unexpected response.",
        "hi": "सर्वर से अनपेक्षित जवाब आया।",
        "mr": "सर्व्हरकडून अनपेक्षित उत्तर आले.",
    },
    "count_unreachable": {
        "en": "I could not record the count.",
        "hi": "मैं संख्या दर्ज नहीं कर सका।",
        "mr": "मला संख्या नोंदवता आली नाही.",
    },
    # --- confirmation / save -----------------------------------------------------------------
    "nothing_to_confirm": {
        "en": "There is nothing to confirm yet. {prompt}",
        "hi": "अभी पुष्टि करने के लिए कुछ नहीं है। {prompt}",
        "mr": "अजून पुष्टी करण्यासारखे काही नाही. {prompt}",
    },
    "confirm_mismatch": {
        "en": "You said {q}, but the recorded count is {d}. Say the correct number, or say yes to save {d}.",
        "hi": "आपने {q} कहा, लेकिन दर्ज संख्या {d} है। सही संख्या बोलिए, या {d} सेव करने के लिए 'हाँ' बोलिए।",
        "mr": "तुम्ही {q} म्हणालात, पण नोंदवलेली संख्या {d} आहे. योग्य संख्या सांगा, किंवा {d} सेव्ह करण्यासाठी 'हो' म्हणा.",
    },
    "saving": {"en": "Saving.", "hi": "सेव कर रहा हूँ।", "mr": "सेव्ह करत आहे."},
    "retrying_save": {
        "en": "Retrying the save.",
        "hi": "सेव दोबारा कोशिश कर रहा हूँ।",
        "mr": "पुन्हा सेव्ह करण्याचा प्रयत्न करत आहे.",
    },
    "saved": {
        "en": "Saved. {n} {bicycles} added to stock.",
        "hi": "सेव हो गया। {n} साइकिल स्टॉक में जोड़ दी गईं।",
        "mr": "सेव्ह झाले. {n} सायकली स्टॉकमध्ये जोडल्या.",
    },
    "confirm_uncertain": {
        "en": "I could not confirm the save. It is not shown as saved. Say yes to retry safely.",
        "hi": "सेव की पुष्टि नहीं हो सकी, इसलिए इसे सेव नहीं माना गया है। सुरक्षित तरीके से दोबारा कोशिश करने के लिए 'हाँ' बोलिए।",
        "mr": "सेव्ह झाल्याची खात्री झाली नाही, म्हणून ते सेव्ह झाले असे धरलेले नाही. "
              "सुरक्षितपणे पुन्हा प्रयत्न करण्यासाठी 'हो' म्हणा.",
    },
    "inventory_unconfirmed": {
        "en": "The server did not confirm the inventory update. It is not shown as saved. Say yes to retry safely.",
        "hi": "सर्वर ने स्टॉक अपडेट की पुष्टि नहीं की, इसलिए इसे सेव नहीं माना गया है। "
              "सुरक्षित तरीके से दोबारा कोशिश करने के लिए 'हाँ' बोलिए।",
        "mr": "सर्व्हरने स्टॉक अपडेटची खात्री दिली नाही, म्हणून ते सेव्ह झाले असे धरलेले नाही. "
              "सुरक्षितपणे पुन्हा प्रयत्न करण्यासाठी 'हो' म्हणा.",
    },
    "login_expired": {
        "en": "Not saved: your login has expired. Please log in again.",
        "hi": "सेव नहीं हुआ: आपका लॉग इन खत्म हो गया है। कृपया दोबारा लॉग इन करें।",
        "mr": "सेव्ह झाले नाही: तुमचे लॉग इन संपले आहे. कृपया पुन्हा लॉग इन करा.",
    },
    "or_cancel": {
        "en": "Or say cancel.",
        "hi": "या 'रद्द करो' बोलिए।",
        "mr": "किंवा 'रद्द करा' म्हणा.",
    },
    # --- cancel / reject ---------------------------------------------------------------------
    "cannot_cancel_uncertain": {
        "en": "I cannot cancel yet: the last save may have reached the server. Say yes to retry and check the result.",
        "hi": "अभी रद्द नहीं कर सकता: पिछला सेव शायद सर्वर तक पहुँच गया हो। नतीजा जाँचने के लिए 'हाँ' बोलिए।",
        "mr": "आत्ता रद्द करता येणार नाही: मागचे सेव्ह कदाचित सर्व्हरपर्यंत पोहोचले असेल. "
              "निकाल तपासण्यासाठी 'हो' म्हणा.",
    },
    "not_saved_ask_again": {
        "en": "Okay, not saved. How many bicycles did you assemble? Or say cancel.",
        "hi": "ठीक है, सेव नहीं किया। आपने कितनी साइकिल असेंबल कीं? या 'रद्द करो' बोलिए।",
        "mr": "ठीक आहे, सेव्ह केले नाही. तुम्ही किती सायकली असेंबल केल्या? किंवा 'रद्द करा' म्हणा.",
    },
    "okay_then": {"en": "Okay. {prompt}", "hi": "ठीक है। {prompt}", "mr": "ठीक आहे. {prompt}"},
    "cancelled_order": {
        "en": "Cancelled. Order {order_id} was not completed and nothing was saved.",
        "hi": "रद्द कर दिया। ऑर्डर {order_id} पूरा नहीं हुआ और कुछ भी सेव नहीं हुआ।",
        "mr": "रद्द केले. ऑर्डर {order_id} पूर्ण झाली नाही आणि काहीही सेव्ह झाले नाही.",
    },
    "cancelled": {
        "en": "Cancelled. Nothing was saved.",
        "hi": "रद्द कर दिया। कुछ भी सेव नहीं हुआ।",
        "mr": "रद्द केले. काहीही सेव्ह झाले नाही.",
    },
    # --- backend failures ---------------------------------------------------------------------
    "prefix_order_not_created": {"en": "Order not created", "hi": "ऑर्डर नहीं बना", "mr": "ऑर्डर तयार झाली नाही"},
    "prefix_count_not_recorded": {"en": "Count not recorded", "hi": "संख्या दर्ज नहीं हुई",
                                  "mr": "संख्या नोंदवली गेली नाही"},
    "prefix_not_saved": {"en": "Not saved", "hi": "सेव नहीं हुआ", "mr": "सेव्ह झाले नाही"},
    "fail_not_allowed": {
        "en": "{prefix}: you are not allowed to do this. Please log in again.",
        "hi": "{prefix}: आपको इसकी अनुमति नहीं है। कृपया दोबारा लॉग इन करें।",
        "mr": "{prefix}: तुम्हाला याची परवानगी नाही. कृपया पुन्हा लॉग इन करा.",
    },
    "fail_not_available": {
        "en": "{prefix}: this step is not available on the production server yet (HTTP {code}).",
        "hi": "{prefix}: यह सुविधा प्रोडक्शन सर्वर पर अभी उपलब्ध नहीं है (HTTP {code})।",
        "mr": "{prefix}: ही सुविधा प्रोडक्शन सर्व्हरवर अजून उपलब्ध नाही (HTTP {code}).",
    },
    # The backend's own message is English; hi/mr give the reason class and the UI
    # still shows the backend's full response next to the reply.
    "fail_backend_message": {
        "en": "{prefix}: {message}",
        "hi": "{prefix}: सर्वर ने अनुरोध स्वीकार नहीं किया (HTTP {code})।",
        "mr": "{prefix}: सर्व्हरने विनंती स्वीकारली नाही (HTTP {code}).",
    },
    "fail_server_error": {
        "en": "{prefix} (server error {code}).",
        "hi": "{prefix} (सर्वर त्रुटि {code})।",
        "mr": "{prefix} (सर्व्हर त्रुटी {code}).",
    },
    # --- quantity validation -------------------------------------------------------------------
    "what_target": {"en": "number of bicycles to assemble", "hi": "असेंबल करने वाली साइकिलों की संख्या",
                    "mr": "असेंबल करायच्या सायकलींची संख्या"},
    "what_completed": {"en": "number of bicycles completed", "hi": "पूरी हुई साइकिलों की संख्या",
                       "mr": "पूर्ण झालेल्या सायकलींची संख्या"},
    "qty_fractional": {
        "en": "Please say a whole {what}.",
        "hi": "कृपया {what} पूरी संख्या में बोलिए।",
        "mr": "कृपया {what} पूर्ण आकड्यात सांगा.",
    },
    "qty_negative": {
        "en": "The {what} cannot be negative. Please say it again.",
        "hi": "{what} ऋणात्मक नहीं हो सकती। कृपया दोबारा बोलिए।",
        "mr": "{what} ऋण असू शकत नाही. कृपया पुन्हा सांगा.",
    },
    "qty_missing": {
        "en": "I did not catch the {what}. Please say it again.",
        "hi": "{what} समझ नहीं आई। कृपया दोबारा बोलिए।",
        "mr": "{what} समजली नाही. कृपया पुन्हा सांगा.",
    },
    "qty_zero": {
        "en": "Zero cannot be used. Please say the {what}, or say cancel.",
        "hi": "शून्य नहीं चलेगा। कृपया {what} बोलिए, या 'रद्द करो' बोलिए।",
        "mr": "शून्य चालणार नाही. कृपया {what} सांगा, किंवा 'रद्द करा' म्हणा.",
    },
    # --- clarification -------------------------------------------------------------------------
    "clarify_missing_quantity": {
        "en": "How many bicycles should I assemble? For example: assemble 20 bicycles.",
        "hi": "कितनी साइकिल असेंबल करनी हैं? जैसे: बीस साइकिल असेंबल करो।",
        "mr": "किती सायकली असेंबल करायच्या? उदाहरणार्थ: वीस सायकली असेंबल करा.",
    },
    "clarify": {"en": "{lead} {prompt}", "hi": "{lead} {prompt}", "mr": "{lead} {prompt}"},
    "lead_empty": {"en": "I did not hear anything.", "hi": "मुझे कुछ सुनाई नहीं दिया।",
                   "mr": "मला काहीच ऐकू आले नाही."},
    "lead_low_confidence": {"en": "I am not sure I heard that correctly.", "hi": "मुझे ठीक से समझ नहीं आया।",
                            "mr": "मला नीट समजले नाही."},
    "lead_ambiguous_quantity": {
        "en": "I heard more than one number. Please say just one number.",
        "hi": "मैंने एक से ज़्यादा संख्या सुनी। कृपया सिर्फ़ एक संख्या बोलिए।",
        "mr": "मी एकापेक्षा जास्त संख्या ऐकल्या. कृपया फक्त एकच संख्या सांगा.",
    },
    "lead_negated_completion": {"en": "Okay, the assembly is not finished yet.",
                                "hi": "ठीक है, असेंबली अभी पूरी नहीं हुई।",
                                "mr": "ठीक आहे, असेंबली अजून पूर्ण झाली नाही."},
    "lead_confirmation_with_other_content": {"en": "Please answer only yes or no.",
                                             "hi": "कृपया सिर्फ़ 'हाँ' या 'नहीं' बोलिए।",
                                             "mr": "कृपया फक्त 'हो' किंवा 'नाही' म्हणा."},
    "lead_conflicting_confirm_and_negation": {"en": "I heard both yes and no. Please say only one.",
                                              "hi": "मैंने 'हाँ' और 'नहीं' दोनों सुने। कृपया एक ही बोलिए।",
                                              "mr": "मी 'हो' आणि 'नाही' दोन्ही ऐकले. कृपया एकच सांगा."},
    "lead_conflicting_confirm_and_cancel": {"en": "I heard both yes and cancel. Please say only one.",
                                            "hi": "मैंने 'हाँ' और 'रद्द' दोनों सुने। कृपया एक ही बोलिए।",
                                            "mr": "मी 'हो' आणि 'रद्द' दोन्ही ऐकले. कृपया एकच सांगा."},
    "lead_unrecognized": {"en": "Sorry, I did not understand.", "hi": "माफ़ कीजिए, मैं समझ नहीं पाया।",
                          "mr": "माफ करा, मला समजले नाही."},
}


def bicycles(n: Optional[int], language: str) -> str:
    """Noun form that goes with a number (only English inflects in our templates)."""
    if language == "hi":
        return "साइकिल"
    if language == "mr":
        return "सायकल" if n == 1 else "सायकली"
    return "bicycle" if n == 1 else "bicycles"


def say(key: str, language: Optional[str], **values) -> str:
    """Render one catalogue message. Unknown languages fall back to English."""
    language = language if language in SUPPORTED else DEFAULT_LANGUAGE
    templates = MESSAGES[key]
    template = templates.get(language) or templates[DEFAULT_LANGUAGE]
    if "{bicycles}" in template:
        values.setdefault("bicycles", bicycles(values.get("n", values.get("q", values.get("d"))), language))
    return template.format(**values)


def placeholders(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


# --------------------------------------------------------------- language choice

def _word_count(text: str) -> int:
    return len([t for t in normalize(text).split() if not re.fullmatch(r"-?\d+(\.\d+)?", t)])


def resolve_response_language(
    text: str,
    parsed: ParsedCommand,
    recognized_language: Optional[str],
    previous: Optional[str],
) -> tuple[str, str]:
    """Pick the reply language. Returns (language, reason).

    Order of evidence:
    1. Short replies ("haan", "हो", "yes", "18", "no") keep the conversation's
       language: one word is not enough to judge a language.
    2. Grammar words in the text (karo/करो -> Hindi, kara/करा/झाले -> Marathi,
       English-only sentence -> English). Code-switched sentences follow their
       Hindi/Marathi grammar, not the English nouns inside them.
    3. The language the speech recogniser reported (Google's language id).
    4. The conversation's language; then Devanagari -> Hindi; else English.
    """
    short = _word_count(text) <= SHORT_UTTERANCE_WORDS or parsed.intent in ("confirm",) or (
        parsed.intent == "cancel" and parsed.cancel_scope == "reject"
    )
    if short and previous in SUPPORTED:
        return previous, "conversation"
    evidence = dominant_language(text)
    if evidence in SUPPORTED:
        return evidence, "words"
    if recognized_language in SUPPORTED:
        return recognized_language, "speech_recognition"
    if previous in SUPPORTED:
        return previous, "conversation"
    if re.search(r"[ऀ-ॿ]", text or ""):
        return "hi", "script"
    return DEFAULT_LANGUAGE, "default"

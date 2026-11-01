"""What the Telegram bot says besides answers: commands, confirmations and alert headers, in English, Hindi and Marathi.
NOTE: the Hindi and Marathi wording is meant to be read through by a native speaker."""
from __future__ import annotations

TEXT = {
    "en": {
        "welcome": "Namaste! I am KisaanMitra. I read live weather, rain and mandi prices for your farm and tell you what matters, in your language.\n\n1) Tell me your village and crop, for example: Nashik, Maharashtra, Onion\n2) Ask anything, by text or by voice note: \"should I sell my onions?\"\n3) Send /watch on and I will message you when something important changes, even when you are not chatting.\n\nCommands: /plan today's plan, /place, /crop, /qty, /lang en|hi|mr, /watch on|off, /status, /help",
        "help": "Ask in text or by voice note. Commands: /plan (today's actions), /place <village, state>, /crop <name>, /lang en|hi|mr, /watch on|off (alerts when something changes), /status, /stop (delete my data).",
        "need_place": "Please send your village and state first, for example: /place Nashik, Maharashtra",
        "place_set": "Place saved: {place}.", "crop_set": "Crop saved: {crop}.", "lang_set": "Language saved.", "lang_bad": "Use /lang en, /lang hi or /lang mr.",
        "qty_set": "Quantity saved: {qty} quintals. Market answers will show the value of your lot.", "qty_bad": "Send a number of quintals, for example: /qty 20",
        "watch_on": "Watching {place}. I will message you when something important changes (checked about every {mins} minutes).",
        "watch_off": "Alerts are off. Send /watch on to turn them on again.", "watch_need": "Set your place first with /place, then send /watch on.",
        "status": "Place: {place}\nCrop: {crop}\nLanguage: {language}\nAlerts: {watch}", "on": "on", "off": "off", "unset": "not set",
        "alert_head": "🔔 KisaanMitra: what changed for {place}", "alert_foot": "Send /plan for today's actions.",
        "plan_head": "Today's plan for {place}", "plan_none": "Nothing needs action today based on the data that could be read.",
        "plan_skipped": "Not checked: {what}", "plan_fail": "Today's plan is not available right now: the live sources could not be reached. Please try again in a few minutes.",
        "heard": "🎤 I heard: {text}", "no_voice": "I could not hear a question in that voice note. Please speak closer to the phone, or type it.",
        "voice_off": "Voice notes need the speech service, which is not available right now. Please type your question.",
        "busy": "Something went wrong on my side. Please try again in a moment.", "stopped": "Done. Your data is deleted. Send /start to begin again.",
        "sources": "Live data", "photo": "Photos are not read here yet. Please describe what you see, or use the web app for photo diagnosis.",
    },
    "hi": {
        "welcome": "नमस्ते! मैं किसानमित्र हूँ। मैं आपके खेत का ताज़ा मौसम, बारिश और मंडी भाव देखकर बताता हूँ कि क्या ज़रूरी है, आपकी भाषा में।\n\n1) अपना गाँव और फसल बताएँ, जैसे: Nashik, Maharashtra, प्याज\n2) कुछ भी पूछें, लिखकर या वॉइस नोट से: \"क्या मैं अपना प्याज बेचूँ?\"\n3) /watch on भेजें, तो कोई ज़रूरी बदलाव होने पर मैं खुद संदेश भेजूँगा।\n\nकमांड: /plan आज की योजना, /place, /crop, /qty, /lang en|hi|mr, /watch on|off, /status, /help",
        "help": "लिखकर या वॉइस नोट से पूछें। कमांड: /plan (आज के काम), /place <गाँव, राज्य>, /crop <नाम>, /lang en|hi|mr, /watch on|off (बदलाव पर अलर्ट), /status, /stop (मेरा डेटा मिटाएँ)।",
        "need_place": "पहले अपना गाँव और राज्य भेजें, जैसे: /place Nashik, Maharashtra",
        "place_set": "जगह सेव हुई: {place}।", "crop_set": "फसल सेव हुई: {crop}।", "lang_set": "भाषा सेव हुई।", "lang_bad": "/lang en, /lang hi या /lang mr भेजें।",
        "qty_set": "मात्रा सेव हुई: {qty} क्विंटल। मंडी के जवाब में आपके माल की कीमत दिखेगी।", "qty_bad": "क्विंटल में संख्या भेजें, जैसे: /qty 20",
        "watch_on": "{place} पर नज़र रख रहा हूँ। कोई ज़रूरी बदलाव होने पर संदेश भेजूँगा (लगभग हर {mins} मिनट में जाँच)।",
        "watch_off": "अलर्ट बंद। फिर चालू करने के लिए /watch on भेजें।", "watch_need": "पहले /place से अपनी जगह बताएँ, फिर /watch on भेजें।",
        "status": "जगह: {place}\nफसल: {crop}\nभाषा: {language}\nअलर्ट: {watch}", "on": "चालू", "off": "बंद", "unset": "तय नहीं",
        "alert_head": "🔔 किसानमित्र: {place} में क्या बदला", "alert_foot": "आज के काम देखने के लिए /plan भेजें।",
        "plan_head": "{place} के लिए आज की योजना", "plan_none": "पढ़े जा सके आंकड़ों के हिसाब से आज कोई काम ज़रूरी नहीं।",
        "plan_skipped": "जाँचा नहीं गया: {what}", "plan_fail": "आज की योजना अभी उपलब्ध नहीं: लाइव स्रोत नहीं मिल सके। कुछ मिनट बाद फिर कोशिश करें।",
        "heard": "🎤 मैंने सुना: {text}", "no_voice": "इस वॉइस नोट में सवाल सुनाई नहीं दिया। फ़ोन के पास बोलें, या लिखकर पूछें।",
        "voice_off": "वॉइस नोट के लिए स्पीच सेवा चाहिए, जो अभी उपलब्ध नहीं। कृपया लिखकर पूछें।",
        "busy": "मेरी तरफ़ से कुछ गड़बड़ हुई। कृपया थोड़ी देर में फिर कोशिश करें।", "stopped": "ठीक है। आपका डेटा मिटा दिया गया। फिर शुरू करने के लिए /start भेजें।",
        "sources": "लाइव डेटा", "photo": "यहाँ फोटो अभी नहीं पढ़ी जाती। जो दिख रहा है वह लिखकर बताएँ, या फोटो जाँच के लिए वेब ऐप इस्तेमाल करें।",
    },
    "mr": {
        "welcome": "नमस्कार! मी किसानमित्र. मी तुमच्या शेताचे ताजे हवामान, पाऊस आणि बाजारभाव पाहून काय महत्त्वाचे आहे ते तुमच्या भाषेत सांगतो.\n\n1) तुमचे गाव आणि पीक सांगा, उदा: Nashik, Maharashtra, कांदा\n2) काहीही विचारा, लिहून किंवा व्हॉइस नोटने: \"मी माझा कांदा विकू का?\"\n3) /watch on पाठवा, म्हणजे काही महत्त्वाचे बदलले की मी स्वतः संदेश पाठवेन.\n\nकमांड: /plan आजची योजना, /place, /crop, /qty, /lang en|hi|mr, /watch on|off, /status, /help",
        "help": "लिहून किंवा व्हॉइस नोटने विचारा. कमांड: /plan (आजची कामे), /place <गाव, राज्य>, /crop <नाव>, /lang en|hi|mr, /watch on|off (बदल झाल्यास अलर्ट), /status, /stop (माझा डेटा पुसा).",
        "need_place": "आधी तुमचे गाव आणि राज्य पाठवा, उदा: /place Nashik, Maharashtra",
        "place_set": "जागा सेव्ह झाली: {place}.", "crop_set": "पीक सेव्ह झाले: {crop}.", "lang_set": "भाषा सेव्ह झाली.", "lang_bad": "/lang en, /lang hi किंवा /lang mr पाठवा.",
        "qty_set": "प्रमाण सेव्ह झाले: {qty} क्विंटल. बाजारभावाच्या उत्तरात तुमच्या मालाची किंमत दिसेल.", "qty_bad": "क्विंटलमध्ये संख्या पाठवा, उदा: /qty 20",
        "watch_on": "{place} वर लक्ष ठेवत आहे. काही महत्त्वाचे बदलले की संदेश पाठवेन (सुमारे दर {mins} मिनिटांनी तपासणी).",
        "watch_off": "अलर्ट बंद. पुन्हा चालू करण्यासाठी /watch on पाठवा.", "watch_need": "आधी /place ने तुमची जागा सांगा, मग /watch on पाठवा.",
        "status": "जागा: {place}\nपीक: {crop}\nभाषा: {language}\nअलर्ट: {watch}", "on": "चालू", "off": "बंद", "unset": "ठरवलेले नाही",
        "alert_head": "🔔 किसानमित्र: {place} मध्ये काय बदलले", "alert_foot": "आजची कामे पाहण्यासाठी /plan पाठवा.",
        "plan_head": "{place} साठी आजची योजना", "plan_none": "वाचता आलेल्या माहितीनुसार आज कोणतेही काम आवश्यक नाही.",
        "plan_skipped": "तपासले नाही: {what}", "plan_fail": "आजची योजना आत्ता उपलब्ध नाही: लाइव्ह स्रोत मिळाले नाहीत. काही मिनिटांनी पुन्हा प्रयत्न करा.",
        "heard": "🎤 मी ऐकले: {text}", "no_voice": "या व्हॉइस नोटमध्ये प्रश्न ऐकू आला नाही. फोनच्या जवळ बोला, किंवा लिहून विचारा.",
        "voice_off": "व्हॉइस नोटसाठी स्पीच सेवा लागते, ती आत्ता उपलब्ध नाही. कृपया लिहून विचारा.",
        "busy": "माझ्या बाजूने काहीतरी चुकले. कृपया थोड्या वेळाने पुन्हा प्रयत्न करा.", "stopped": "ठीक आहे. तुमचा डेटा पुसला. पुन्हा सुरू करण्यासाठी /start पाठवा.",
        "sources": "लाइव्ह डेटा", "photo": "इथे फोटो अजून वाचले जात नाहीत. जे दिसते ते लिहून सांगा, किंवा फोटो तपासणीसाठी वेब अॅप वापरा.",
    },
}


def t(lang: str, key: str, **kw) -> str:
    table = TEXT.get(lang) or TEXT["en"]
    return (table.get(key) or TEXT["en"][key]).format(**kw)

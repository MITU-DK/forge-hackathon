"""Rule labels in the farmer's own language (a label never reaches the farmer as CAPITAL_WORDS_WITH_UNDERSCORES)."""

LABEL_TEXT = {
    "hi": {
        "ABOVE_STATE_MEDIAN": "राज्य के औसत भाव से ऊपर", "BELOW_STATE_MEDIAN": "राज्य के औसत भाव से नीचे", "NEAR_STATE_MEDIAN": "राज्य के औसत भाव के आसपास",
        "IRRIGATE_IF_SOIL_DRY": "मिट्टी सूखी लगे तो सिंचाई करें", "SKIP_IRRIGATION": "सिंचाई की ज़रूरत नहीं, बारिश आ रही है",
        "RAIN_COVERS_DEMAND": "बारिश पानी की मांग पूरी करती है", "RAIN_SHORT_OF_DEMAND": "बारिश पानी की मांग से कम है",
        "SELL_NOW": "अभी बेचने का संकेत, भाव गिर रहे हैं", "SELL_BEFORE_RAIN": "बारिश से पहले बेचने पर विचार करें", "STORAGE_RISK": "भंडारण का जोखिम",
        "WATERLOGGING_RISK": "जलभराव का खतरा", "DRY_SPELL_ALERT": "सूखे दौर की चेतावनी", "IRRIGATION_URGENT": "सिंचाई जल्दी ज़रूरी",
        "NEEDS_IRRIGATION": "सिंचाई चाहिए", "NEEDS_IRRIGATION_RAIN_FAR_BELOW_NEED": "बारिश बहुत कम है, सिंचाई ज़रूरी", "RAIN_COVERS_NEED": "बारिश से ज़रूरत पूरी होती है",
        "WETTER_THAN_OPTIMUM": "ज़रूरत से ज़्यादा बारिश", "FAR_WETTER_THAN_CROP_TOLERATES": "फसल की सहनसीमा से बहुत ज़्यादा बारिश", "NOT_SUITABLE": "उपयुक्त नहीं",
        "BEFORE_EARLIEST_HARVEST": "कटाई का समय अभी नहीं आया", "IN_HARVEST_WINDOW": "कटाई का समय चल रहा है", "PAST_LATEST_CYCLE": "सामान्य अवधि से आगे निकल चुकी", "NOT_SOWN_YET": "अभी बोई नहीं गई",
        "OUTSIDE_TOLERANCE": "सहनसीमा से बाहर", "NOT_TOLERATED": "सहन नहीं करती", "TOLERATED": "सहन कर लेती है", "ACCEPTABLE": "स्वीकार्य",
        "HEAVY_RAIN": "भारी बारिश", "HEAT_STRESS": "गर्मी का तनाव", "COLD_STRESS": "ठंड का तनाव", "INSUFFICIENT_DATA": "पर्याप्त आंकड़े नहीं",
    },
    "mr": {
        "ABOVE_STATE_MEDIAN": "राज्याच्या सरासरी भावापेक्षा जास्त", "BELOW_STATE_MEDIAN": "राज्याच्या सरासरी भावापेक्षा कमी", "NEAR_STATE_MEDIAN": "राज्याच्या सरासरी भावाच्या जवळ",
        "IRRIGATE_IF_SOIL_DRY": "माती कोरडी वाटल्यास सिंचन करा", "SKIP_IRRIGATION": "सिंचनाची गरज नाही, पाऊस येत आहे",
        "RAIN_COVERS_DEMAND": "पाऊस पाण्याची गरज भागवतो", "RAIN_SHORT_OF_DEMAND": "पाऊस पाण्याच्या गरजेपेक्षा कमी आहे",
        "SELL_NOW": "आता विकण्याचा संकेत, भाव घसरत आहेत", "SELL_BEFORE_RAIN": "पावसापूर्वी विकण्याचा विचार करा", "STORAGE_RISK": "साठवणुकीचा धोका",
        "WATERLOGGING_RISK": "पाणी साचण्याचा धोका", "DRY_SPELL_ALERT": "कोरड्या काळाचा इशारा", "IRRIGATION_URGENT": "सिंचन लवकर आवश्यक",
        "NEEDS_IRRIGATION": "सिंचन लागेल", "NEEDS_IRRIGATION_RAIN_FAR_BELOW_NEED": "पाऊस खूप कमी, सिंचन आवश्यक", "RAIN_COVERS_NEED": "पावसाने गरज भागते",
        "WETTER_THAN_OPTIMUM": "गरजेपेक्षा जास्त पाऊस", "FAR_WETTER_THAN_CROP_TOLERATES": "पिकाच्या सहनशक्तीपेक्षा खूप जास्त पाऊस", "NOT_SUITABLE": "योग्य नाही",
        "BEFORE_EARLIEST_HARVEST": "काढणीची वेळ अजून आलेली नाही", "IN_HARVEST_WINDOW": "काढणीचा काळ सुरू आहे", "PAST_LATEST_CYCLE": "नेहमीच्या कालावधीपेक्षा पुढे गेले", "NOT_SOWN_YET": "अजून पेरलेले नाही",
        "OUTSIDE_TOLERANCE": "सहनशक्तीच्या बाहेर", "NOT_TOLERATED": "सहन करत नाही", "TOLERATED": "सहन करते", "ACCEPTABLE": "स्वीकारार्ह",
        "HEAVY_RAIN": "मुसळधार पाऊस", "HEAT_STRESS": "उष्णतेचा ताण", "COLD_STRESS": "थंडीचा ताण", "INSUFFICIENT_DATA": "पुरेशी माहिती नाही",
    },
}

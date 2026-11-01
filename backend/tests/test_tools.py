"""Tools with the network replaced by response-SHAPE fixtures (tests only; never used by the app)."""
import datetime as dt

import app.tools.location as location
import app.tools.mandi as mandi_mod
import app.tools.weather as weather_mod
from app.core.config import Settings
from app.tools import NeedsInput, PlaceNotFound, SourceUnavailable
from app.tools.http import Fetched

GEO = {"results": [{"name": "Nashik", "latitude": 19.99, "longitude": 73.78, "admin1": "Maharashtra", "admin2": "Nashik", "country_code": "IN"}]}


def forecast(n=7, rain=None):
    rain = rain or [0.0] * n
    return {
        "current": {"temperature_2m": 28.0, "relative_humidity_2m": 60, "wind_speed_10m": 9.0},
        "daily": {
            "time": [f"2026-11-{i + 1:02d}" for i in range(n)],
            "temperature_2m_max": [33.0] * n, "temperature_2m_min": [20.0] * n, "precipitation_sum": rain,
            "precipitation_probability_max": [10] * n, "wind_speed_10m_max": [9.0] * n, "et0_fao_evapotranspiration": [4.0] * n,
        },
        "hourly": {"soil_moisture_3_to_9cm": [0.21] * 48, "soil_moisture_9_to_27cm": [0.30] * 48, "soil_moisture_27_to_81cm": [None] * 48, "soil_temperature_6cm": [29.94] * 48},
    }


def mandi_records(day_price):
    out = []
    for date, prices in day_price.items():
        for i, p in enumerate(prices):
            out.append({"State": "Maharashtra", "District": "Nashik" if i == 0 else "Pune", "Market": f"M{i}", "Commodity": "Onion", "Arrival_Date": date, "Min_Price": p - 100, "Max_Price": p + 100, "Modal_Price": p})
    return out


def install(monkey_routes, key="k"):
    """monkey_routes: fn(url, params) -> data. Patches get_json in every tool module + settings."""
    def fake(url, params=None, ttl=None, timeout=None):
        return Fetched(monkey_routes(url, params or {}), url, "2026-11-01T10:00:00Z", False, 0)

    for m in (location, weather_mod, mandi_mod):
        m.get_json = fake
    mandi_mod.get_settings = lambda: Settings(data_gov_api_key=key)


def routes_ok(url, params):
    if "geocoding" in url:
        return GEO
    if "open-meteo" in url:
        return forecast(rain=[0, 70.0, 0, 0, 0, 0, 0])
    if "data.gov.in" in url:  # behaves like the live API: lower-case filters work, Title-case match nothing, date filter ignored
        if "filters[state]" not in params:
            return {"records": []}
        today = dt.date.today()
        recs = []
        for off, prices in {0: [2500] * 6, 7: [2000] * 6}.items():  # six markets today and a week ago (the shape a trend needs)
            recs += mandi_records({(today - dt.timedelta(days=off)).strftime("%d/%m/%Y"): prices})
        return {"records": recs}
    raise AssertionError(url)


def test_weather_tool_builds_facts_cards_sources():
    install(routes_ok)
    r = weather_mod.weather("", {"place": "Nashik, Maharashtra"})
    assert r.ok and "RISK FLAG HEAVY_RAIN on 2026-11-02: 70.0 mm" in r.text
    assert "SOIL MOISTURE" in r.text and r.cards[0]["type"] == "forecast"
    assert "WATER BALANCE next 7 days: forecast rain 70.0 mm vs reference crop water demand" in r.text and "RAIN_COVERS_DEMAND" in r.text
    assert r.facts["location"]["state"] == "Maharashtra" and any(s["name"] == "Open-Meteo forecast" for s in r.sources)


def test_tools_ask_for_missing_input():
    install(routes_ok)
    for fn in (weather_mod.weather, mandi_mod.mandi):
        try:
            fn("", {})
            raise AssertionError("expected NeedsInput")
        except NeedsInput as e:
            assert e.field == "place"
    try:
        mandi_mod.mandi("", {"place": "Nashik"})
        raise AssertionError("expected NeedsInput")
    except NeedsInput as e:
        assert e.field == "crop"


def test_mandi_without_key_is_explicit_failure_not_fake_data():
    install(routes_ok, key="")
    r = mandi_mod.mandi("", {"place": "Nashik", "crop": "onion"})
    assert not r.ok and "SOURCE UNAVAILABLE" in r.text and "DATA_GOV_API_KEY" in r.text and "Do NOT guess" in r.text


def test_mandi_tool_signal_and_markets_with_lower_case_filters():
    install(routes_ok)
    r = mandi_mod.mandi("", {"place": "Nashik", "crop": "onion"})
    assert r.ok and "SIGNAL (computed in code): HOLD - prices are RISING" in r.text and "MARKETS in Nashik" in r.text
    assert r.facts["commodity"] == "Onion" and r.facts["price"]["signal"] in {"SELL_NOW", "NEUTRAL", "HOLD", "INSUFFICIENT_DATA"}
    assert r.cards[0]["type"] == "prices" and r.cards[0]["unit"] == "Rs/quintal"


def test_mandi_no_records_says_so():
    install(lambda url, p: GEO if "geocoding" in url else {"records": []})
    r = mandi_mod.mandi("", {"place": "Nashik", "crop": "onion"})
    assert r.ok and "no published records" in r.text and "do not estimate" in r.text
    assert "never say it is unreachable" in r.text  # the source answered: only the crop has no rows


def test_cross_check_tool():
    install(routes_ok)
    r = mandi_mod.cross("", {"place": "Nashik", "crop": "onion"})
    assert r.ok and r.text.startswith("DECISION (computed in code)") and r.facts["decision"] in {"SELL_NOW", "HOLD", "NEUTRAL", "INSUFFICIENT_DATA"}


def test_place_not_found_is_graceful():
    install(lambda url, p: {"results": []})
    r = weather_mod.weather("", {"place": "Nowhereville"})
    assert not r.ok and "place not found" in r.text


def test_mandi_district_without_markets_falls_back_to_state_and_says_so():
    # Records exist for Maharashtra but none in the farmer's district (Satara): state-level markets + an explicit note.
    def routes(url, params):
        if "geocoding" in url:
            return {"results": [{"name": "Satara", "latitude": 17.7, "longitude": 74.0, "admin1": "Maharashtra", "admin2": "Satara", "country_code": "IN"}]}
        if "filters[state]" not in params:
            return {"records": []}
        return {"records": mandi_records({dt.date.today().strftime("%d/%m/%Y"): [2000, 2100, 2200]})}

    install(routes)
    r = mandi_mod.mandi("", {"place": "Satara", "crop": "onion"})
    assert r.ok and "NOTE: no markets reporting Onion in Satara" in r.text and "HIGHEST MARKETS in Maharashtra" in r.text
    assert "MARKETS in Satara" not in r.text and r.cards[0]["markets"]


def test_mandi_single_day_source_gives_market_position_and_honest_no_trend():
    def routes(url, params):
        if "geocoding" in url:
            return GEO
        if "filters[state]" not in params:
            return {"records": []}
        d = dt.date.today().strftime("%d/%m/%Y")
        return {"records": mandi_records({d: [3000, 2000, 2200, 2400]})}  # i==0 is Nashik (3000); others Pune

    install(routes)
    r = mandi_mod.mandi("", {"place": "Nashik", "crop": "onion"})
    assert r.ok and r.facts["price"]["signal"] == "INSUFFICIENT_DATA" and "trend is not available" in r.text
    pos = r.facts["position"]
    assert pos["state_median"] == 2300 and pos["local_median"] == 3000 and pos["label"] == "ABOVE_STATE_MEDIAN" and pos["best_price"] == 3000
    assert "MARKET POSITION" in r.text and "ABOVE_STATE_MEDIAN" in r.text and r.cards[0]["position"] == pos


def test_mandi_fetches_once_not_per_day():
    calls = []

    def routes(url, params):
        if "geocoding" in url:
            return GEO
        calls.append(params)
        return {"records": mandi_records({dt.date.today().strftime("%d/%m/%Y"): [2000, 2100, 2200]})}

    install(routes)
    mandi_mod.mandi("", {"place": "Nashik", "crop": "onion"})
    today_calls = [c for c in calls if "sort[Arrival_Date]" not in c]
    assert len(today_calls) == 1 and "filters[state]" in today_calls[0] and "filters[commodity]" in today_calls[0]
    assert len(calls) == 2  # one sorted history call, never one per day: the API rate limits bursts


def history_routes(prices_by_days_ago, today_prices=(2500,) * 6, fail=False):
    """The two real resources: today's (lower-case filters, no date) and history (Title-case filters, date honoured)."""
    def routes(url, params):
        if "geocoding" in url:
            return GEO
        if "open-meteo" in url:
            return forecast(rain=[0, 0, 0, 0, 0, 0, 0])
        if "sort[Arrival_Date]" not in params:
            return {"records": mandi_records({dt.date.today().strftime("%d/%m/%Y"): list(today_prices)})}
        if fail:
            raise SourceUnavailable("history down")
        return {"records": [r for off, prices in sorted(prices_by_days_ago.items()) for r in mandi_records({(dt.date.today() - dt.timedelta(days=off)).strftime("%d/%m/%Y"): prices})]}
    return routes


def test_history_resource_gives_a_real_trend_and_its_own_source():
    install(history_routes({1: [2000] * 6, 7: [2000] * 6}))
    r = mandi_mod.mandi("", {"place": "Nashik", "crop": "onion"})
    p = r.facts["price"]
    assert p["signal"] == "HOLD" and p["points"] == 3 and p["change_pct"] == 25.0 and p["markets_compared"] == 6
    assert p["compared_with"] == (dt.date.today() - dt.timedelta(days=7)).isoformat()
    assert any(s["name"] == "data.gov.in Agmarknet history" for s in r.sources)
    assert r.facts["position"] and "SIGNAL (computed in code): HOLD - prices are RISING" in r.text and "not a forecast" in r.text
    assert "same 6 markets moved 25.0 %" in r.text


def test_a_falling_week_is_sell_now_and_says_falling():
    install(history_routes({7: [3000] * 6}))
    r = mandi_mod.mandi("", {"place": "Nashik", "crop": "onion"})
    assert r.facts["price"]["signal"] == "SELL_NOW" and "prices are FALLING" in r.text


def test_history_failure_degrades_to_today_with_honest_no_trend():
    install(history_routes({}, fail=True))
    r = mandi_mod.mandi("", {"place": "Nashik", "crop": "onion"})
    assert r.ok and r.facts["price"]["signal"] == "INSUFFICIENT_DATA" and "trend is not available" in r.text
    assert not any("history" in s["name"] for s in r.sources)


def test_history_failure_never_claims_no_records_it_could_not_check():
    install(history_routes({}, today_prices=(), fail=True))
    r = mandi_mod.mandi("", {"place": "Nashik", "crop": "onion"})
    assert "could not be checked" in r.text


def test_history_rows_outside_the_window_or_another_commodity_are_not_used():
    def routes(url, params):
        if "sort[Arrival_Date]" in params:
            day = lambda n: (dt.date.today() - dt.timedelta(days=n)).strftime("%d/%m/%Y")
            return {"records": [{**mandi_records({day(1): [9000]})[0], "Commodity": "Onion Green"}, *mandi_records({day(40): [9000]})]}
        return history_routes({})(url, params)

    install(routes)
    r = mandi_mod.mandi("", {"place": "Nashik", "crop": "onion"})
    assert r.facts["price"]["signal"] == "INSUFFICIENT_DATA" and "9000" not in r.text


def test_cross_check_uses_the_trend_when_history_exists():
    install(history_routes({7: [2000] * 6}))
    r = mandi_mod.cross("", {"place": "Nashik", "crop": "onion"})
    assert r.facts["decision"] == "HOLD" and "NO PRICE TREND" not in r.text


def test_title_case_filter_fallback_still_works_if_lower_matches_nothing():
    def routes(url, params):
        if "geocoding" in url:
            return GEO
        if "filters[State]" not in params:
            return {"records": []}
        return {"records": mandi_records({dt.date.today().strftime("%d/%m/%Y"): [2000, 2100, 2200]})}

    install(routes)
    assert mandi_mod.mandi("", {"place": "Nashik", "crop": "onion"}).ok


def test_crop_is_found_as_a_whole_word_only():
    # "price" contains "rice" and "turmeric" contains "tur": neither may pick a crop by accident.
    for text in ("what is the price today", "any turmeric rates", "saturday market"):
        pick = None
        try:
            pick = mandi_mod.commodity_name({}, text)
        except NeedsInput:
            pick = "ASK"
        assert pick in ("ASK", "Turmeric"), (text, pick)
    try:
        mandi_mod.commodity_name({}, "what is the price today")
        raise AssertionError("expected NeedsInput")
    except NeedsInput as e:
        assert e.field == "crop"
    assert mandi_mod.commodity_name({}, "onion rates in nashik") == "Onion" and mandi_mod.commodity_name({}, "tur dal rate") == "Red gram/Arhar/Tur(whole)"


def test_aliases_use_names_verified_in_the_real_dataset():
    a = mandi_mod.ALIASES
    assert a["paddy"] == "Paddy(Common)" and a["onion"] == "Onion" and a["moong"] == "Green Gram(Moong)(Whole)" and a["chilli"] == "Green Chilli"
    assert "sugarcane" not in a  # no such commodity in the dataset


def test_loose_commodity_filter_results_are_cut_to_exact_matches():
    # Real behaviour (30 Oct 2026): filters[commodity]=Onion also returns 'Onion Green'; 'Potato' returns 'Sweet Potato'.
    today = dt.date.today().strftime("%d/%m/%Y")
    base = {"State": "Maharashtra", "District": "Pune", "Variety": "Other", "Grade": "Local", "Arrival_Date": today}
    loose = [
        {**base, "Market": "A", "Commodity": "Onion", "Min_Price": 3500, "Max_Price": 4000, "Modal_Price": 3800},
        {**base, "Market": "B", "Commodity": "Onion ", "Min_Price": 3500, "Max_Price": 4000, "Modal_Price": 3900},
        {**base, "Market": "C", "Commodity": "Onion Green", "Min_Price": 8, "Max_Price": 10, "Modal_Price": 9},
        {**base, "Market": "D", "Commodity": "onion", "Min_Price": 3600, "Max_Price": 4100, "Modal_Price": 3700},
    ]

    def routes(url, params):
        if "geocoding" in url:
            return GEO
        return {"records": loose, "total": len(loose)} if "filters[state]" in params else {"records": []}

    install(routes)
    r = mandi_mod.mandi("", {"place": "Nashik", "crop": "onion"})
    assert r.ok and "Onion Green" not in r.text
    assert r.facts["position"]["markets"] == 3 and r.facts["position"]["state_median"] == 3800  # Rs 9 never enters the median


def test_nothing_exact_means_honest_no_records():
    today = dt.date.today().strftime("%d/%m/%Y")
    only_loose = [{"State": "Maharashtra", "District": "Pune", "Market": "C", "Commodity": "Sweet Potato", "Variety": "x", "Grade": "y", "Arrival_Date": today, "Min_Price": 1, "Max_Price": 2, "Modal_Price": 2000}]

    def routes(url, params):
        return GEO if "geocoding" in url else {"records": only_loose, "total": 1}

    install(routes)
    r = mandi_mod.mandi("", {"place": "Nashik", "crop": "potato"})
    assert r.ok and "no published records" in r.text and "do not estimate" in r.text


def test_results_are_paged_until_total_is_reached():
    today = dt.date.today().strftime("%d/%m/%Y")
    rec = lambda i: {"State": "Maharashtra", "District": "Pune", "Market": f"M{i}", "Commodity": "Onion", "Variety": "x", "Grade": "y", "Arrival_Date": today, "Min_Price": 1000, "Max_Price": 3000, "Modal_Price": 2000 + i}
    seen_offsets = []

    def routes(url, params):
        if "geocoding" in url:
            return GEO
        off = int(params["offset"])
        if "sort[Arrival_Date]" in params:
            return {"records": []}
        seen_offsets.append(off)
        return {"records": [rec(i) for i in range(off, min(off + 500, 700))], "total": 700}

    install(routes)
    r = mandi_mod.mandi("", {"place": "Nashik", "crop": "onion"})
    assert seen_offsets == [0, 500] and r.facts["position"]["markets"] == 700


def test_state_names_are_translated_to_the_dataset_spelling():
    for geo, data in [("Kerala", "Keralam"), ("Chhattisgarh", "Chattisgarh"), ("National Capital Territory of Delhi", "NCT of Delhi"), ("Puducherry", "Pondicherry"), ("Maharashtra", "Maharashtra"), ("Sikkim", "Sikkim")]:
        assert mandi_mod.agmarknet_state(geo) == data


def test_kerala_farmer_queries_keralam_and_gets_records():
    asked = []

    def routes(url, params):
        if "geocoding" in url:
            return {"results": [{"name": "Kochi", "latitude": 9.9, "longitude": 76.2, "admin1": "Kerala", "admin2": "Ernakulam District", "country_code": "IN"}]}
        asked.append(params.get("filters[state]"))
        rec = lambda i: {"State": "Keralam", "District": "Ernakulam", "Market": f"M{i}", "Commodity": "Banana", "Variety": "x", "Grade": "y", "Arrival_Date": dt.date.today().strftime("%d/%m/%Y"), "Min_Price": 3000, "Max_Price": 4000, "Modal_Price": 3500 + i}
        return {"records": [rec(i) for i in range(4)], "total": 4} if params.get("filters[state]") == "Keralam" else {"records": []}

    install(routes)
    r = mandi_mod.mandi("", {"place": "Kochi", "crop": "banana"})
    assert asked[0] == "Keralam" and r.ok and r.facts["position"]["markets"] == 4
    assert r.facts["position"]["label"] in {"NEAR_STATE_MEDIAN", "ABOVE_STATE_MEDIAN", "BELOW_STATE_MEDIAN"}  # 'Ernakulam District' matched 'Ernakulam'


def test_geocoder_picks_the_most_populous_match_when_no_state_is_given():
    import app.tools.location as loc_mod

    results = [
        {"name": "Panaji", "latitude": 22.0, "longitude": 73.0, "admin1": "Gujarat", "admin2": "Kheda district", "population": 0},
        {"name": "Panaji", "latitude": 15.5, "longitude": 73.8, "admin1": "Goa", "admin2": "North Goa", "population": 114759},
    ]
    install(lambda url, p: {"results": results})
    assert loc_mod.resolve_location({"place": "Panaji"})["state"] == "Goa"
    assert loc_mod.resolve_location({"place": "Panaji, Gujarat"})["state"] == "Gujarat"  # an explicit state still wins


def test_a_state_the_farmer_typed_is_never_silently_replaced_by_another_state():
    import app.tools.location as loc_mod

    only_gujarat = [{"name": "Himatnagar", "latitude": 23.6, "longitude": 72.9, "admin1": "Gujarat", "admin2": "Sabar Kantha", "population": 81137}]
    install(lambda url, p: {"results": only_gujarat})
    try:
        loc_mod.resolve_location({"place": "Ahmednagar, Maharashtra"})
        raise AssertionError("expected PlaceNotFound")
    except PlaceNotFound as e:
        assert "maharashtra" in str(e).lower()
    assert loc_mod.resolve_location({"place": "Ahmednagar"})["state"] == "Gujarat"  # no state typed: first/most populous match
    assert loc_mod.resolve_location({"place": "Himatnagar, India"})["state"] == "Gujarat"  # 'India' is not a state: no rejection
    r = weather_mod.weather("", {"place": "Ahmednagar, Maharashtra"})
    assert not r.ok and "place not found" in r.text


def test_a_district_the_farmer_typed_is_never_silently_replaced_by_another_district():
    import app.tools.location as loc_mod

    latur = [{"name": "Sawantwadi", "latitude": 18.4, "longitude": 76.8, "admin1": "Maharashtra", "admin2": "Latur District", "population": 0}]
    install(lambda url, p: {"results": latur})
    try:
        loc_mod.resolve_location({"place": "Sawantwadi, Sindhudurg, Maharashtra"})
        raise AssertionError("expected PlaceNotFound")
    except PlaceNotFound as e:
        assert "sindhudurg" in str(e).lower()
    assert loc_mod.resolve_location({"place": "Sawantwadi, Latur, Maharashtra"})["district"] == "Latur District"
    assert loc_mod.resolve_location({"place": "Sawantwadi, Latur district"})["district"] == "Latur District"  # filler words ignored
    assert loc_mod.resolve_location({"place": "Sawantwadi"})["district"] == "Latur District"  # no district typed: unchanged
    assert loc_mod.resolve_location({"place": "Sawantwadi, India"})["state"] == "Maharashtra"
    r = mandi_mod.mandi("", {"place": "Sawantwadi, Sindhudurg, Maharashtra", "crop": "Onion"})
    assert not r.ok and "place not found" in r.text


def test_old_and_alternate_place_names_are_looked_up_under_the_name_the_geocoder_knows():
    import app.tools.location as loc_mod

    asked = []

    def routes(url, p):
        asked.append(p["name"])
        state = "Chhattisgarh" if p["name"] == "Raipur" else "Maharashtra"
        return {"results": [{"name": p["name"], "latitude": 19.1, "longitude": 74.7, "admin1": state, "admin2": "Ahmednagar", "population": 367140}]}

    install(routes)
    assert loc_mod.resolve_location({"place": "Ahmednagar, Maharashtra"})["district"] == "Ahmednagar"
    assert loc_mod.resolve_location({"place": "Raipur district, Chhattisgarh"})["name"] == "Raipur"
    assert asked == ["Ahilyanagar", "Raipur"]


def test_history_window_reaches_the_days_the_trend_compares_with():
    # The trend compares with a day 5-9 days back, so the history fetch must cover at least that far.
    assert Settings().mandi_days >= 10
    for back in (5, 6, 7, 8, 9):
        install(history_routes({back: [2000] * 6}))
        r = mandi_mod.mandi("", {"place": "Nashik", "crop": "onion"})
        assert r.facts["price"]["signal"] == "HOLD" and r.facts["price"]["window_days"] == back, back
    install(history_routes({10: [2000] * 6}))  # 10 days back is out of range: honest "no trend", never a guess
    assert mandi_mod.mandi("", {"place": "Nashik", "crop": "onion"}).facts["price"]["signal"] == "INSUFFICIENT_DATA"


def test_signal_text_carries_the_per_crop_evidence_and_the_decision_explains_itself():
    install(history_routes({7: [2000] * 6}))
    r = mandi_mod.mandi("", {"place": "Nashik", "crop": "onion"})
    assert "not a forecast" in r.text and "two times out of three" in r.text
    d = mandi_mod.cross("", {"place": "Nashik", "crop": "onion"})
    assert "DECISION (computed in code): HOLD (prices rising, 25.0 % in 7 days; a recent trend, not a forecast)" in d.text


def test_a_devanagari_place_is_searched_in_its_language_and_returned_with_english_names():
    import app.tools.location as loc_mod

    asked = []

    def routes(url, p):
        asked.append((url.rsplit("/", 1)[-1], p.get("language")))
        if url.endswith("/search"):  # like the live geocoder: native script only matches when language=mr
            return {"results": [{"id": 7, "name": "नाशिक", "latitude": 20.0, "longitude": 73.8, "admin1": "महाराष्ट्र", "admin2": "नाशिक", "population": 1486053}]} if p["language"] == "mr" else {}
        return {"id": 7, "name": "Nashik", "latitude": 20.0, "longitude": 73.8, "admin1": "Maharashtra", "admin2": "Nashik"}

    install(routes)
    for typed in ("नाशिक", "नाशिक, महाराष्ट्र"):
        loc = loc_mod.resolve_location({"place": typed})
        assert (loc["name"], loc["state"], loc["district"]) == ("Nashik", "Maharashtra", "Nashik")
    assert ("search", "mr") in asked and ("get", "en") in asked


def test_a_devanagari_place_the_geocoder_does_not_know_is_reported_not_guessed():
    import app.tools.location as loc_mod

    install(lambda url, p: {})
    try:
        loc_mod.resolve_location({"place": "लासलगाव"})
    except PlaceNotFound:
        return
    raise AssertionError("expected PlaceNotFound")


def test_crop_names_in_hindi_and_marathi_map_to_the_agmarknet_commodity():
    from app.tools.mandi import ALIASES, commodity_name

    assert commodity_name({"crop": "कांदा"}, "") == "Onion"
    assert commodity_name({"crop": "प्याज"}, "") == "Onion"
    assert commodity_name({}, "मी माझा टोमॅटो विकू का?") == "Tomato"
    assert commodity_name({"crop": "Onion"}, "") == "Onion"
    assert not any(ord(ch) > 127 for k in ALIASES for ch in k)  # the UI dropdown is built from ALIASES: stays English


def test_a_half_filled_today_is_left_out_and_the_last_complete_day_is_used():
    today = dt.date.today()
    fmt = lambda off: (today - dt.timedelta(days=off)).strftime("%d/%m/%Y")

    def routes(url, params):
        if "geocoding" in url:
            return GEO
        if "open-meteo" in url:
            return forecast()
        if "filters[state]" not in params:
            return {"records": []}
        recs = mandi_records({fmt(0): [2900], fmt(1): [2500] * 12, fmt(8): [2000] * 12})  # one market so far today, twelve yesterday
        return {"records": recs}

    install(routes)
    r = mandi_mod.mandi("", {"place": "Nashik, Maharashtra", "crop": "Onion"})
    assert r.ok and "only 1 price report(s) have come in" in r.text and "latest COMPLETE day" in r.text
    assert f"{(today).isoformat()}: Rs 2900" not in r.text  # the one-market average is not shown as a price
    assert r.facts["price"]["latest_date"] == (today - dt.timedelta(days=1)).isoformat()
    assert "Rs 2900" not in r.text.split("NOTE:")[-1]  # nor used for the highest-market or position lines


def test_a_crop_that_always_reports_from_few_markets_is_not_treated_as_partial():
    from app.decision.price_rules import without_partial_day

    norm = lambda d: [mandi_mod._norm(r) for r in mandi_records(d)]  # the tool normalises raw API keys before this rule runs
    recs = norm({"01/11/2026": [2500, 2600], "02/11/2026": [2500, 2550], "03/11/2026": [2550, 2560]})
    kept, note = without_partial_day(recs)
    assert note is None and kept == recs
    kept, note = without_partial_day(norm({"02/11/2026": [2500] * 10, "03/11/2026": [2500] * 4}))
    assert note == {"date": "2026-11-03", "markets": 4, "used": "2026-11-02"} and len(kept) == 10
    assert without_partial_day(norm({"03/11/2026": [2500]}))[1] is None  # a single day is all there is


def test_district_names_the_geocoder_lacks_are_searched_under_a_name_it_knows():
    import app.tools.location as loc_mod

    asked = []

    def routes(url, p):
        asked.append(p["name"])
        return {"results": [{"name": p["name"], "latitude": 1.0, "longitude": 2.0, "admin1": "Maharashtra", "admin2": "X", "population": 1}]}

    install(routes)
    for typed, searched in [("Raigad", "Alibag"), ("Buldhana, Maharashtra", "Buldana"), ("Chhatrapati Sambhajinagar", "Aurangabad")]:
        assert loc_mod.resolve_location({"place": typed})["state"] == "Maharashtra"
        assert asked[-1] == searched


def test_devanagari_district_names_the_geocoder_lacks_are_mapped_to_english_names():
    import app.tools.location as loc_mod

    asked = []

    def routes(url, p):
        asked.append((p["name"], p["language"]))
        return {"results": [{"name": p["name"], "latitude": 1.0, "longitude": 2.0, "admin1": "Maharashtra", "admin2": "X", "population": 1}]}

    install(routes)
    for typed, searched in [("सातारा", "Satara"), ("सांगली, महाराष्ट्र", "Sangli"), ("इंदौर", "Indore")]:
        loc_mod.resolve_location({"place": typed})
        assert asked[-1] == (searched, "en")  # searched under the English name, in English


def test_own_markets_are_the_markets_whose_name_matches_the_farmers_place():
    rows = [
        {"market": "Pune(Moshi)", "district": "Pune", "modal": 600.0, "min": 500.0, "max": 700.0},
        {"market": "Pune(Manjri)", "district": "Pune", "modal": 1000.0, "min": 800.0, "max": 1200.0},
        {"market": "APMC Khed", "district": "Pune", "modal": 1000.0, "min": 900.0, "max": 1100.0},
    ]
    assert [m["market"] for m in mandi_mod.own_markets(rows, ["Pune", "Pune"])] == ["Pune(Manjri)", "Pune(Moshi)"]  # both Pune markets, not Khed
    assert mandi_mod.own_markets(rows, ["Maharashtra"]) == []
    assert mandi_mod.own_markets(rows, ["Pen", ""]) == []  # too short to match safely


def test_a_dropped_history_request_is_retried_once_so_the_trend_is_not_lost(monkeypatch):
    calls = {"hist": 0}
    monkeypatch.setattr(mandi_mod, "HISTORY_RETRY_DELAY_S", 0)

    def flaky(url, params):
        if "filters[State]" in params:  # the history resource uses Title-case filters
            calls["hist"] += 1
            if calls["hist"] == 1:
                raise SourceUnavailable("dropped")
            today = dt.date.today()
            return {"records": mandi_records({(today - dt.timedelta(days=7)).strftime("%d/%m/%Y"): [2000] * 6}), "total": 6}
        return routes_ok(url, params)

    install(flaky)
    r = mandi_mod.mandi("", {"place": "Nashik, Maharashtra", "crop": "Onion"})
    assert calls["hist"] >= 2 and r.ok and "SIGNAL (computed in code)" in r.text


def test_soil_profile_reports_each_depth_the_model_returned_and_never_invents_one():
    install(routes_ok)
    r = weather_mod.weather("", {"place": "Nashik, Maharashtra"})
    assert "SOIL MOISTURE 3-9 cm" in r.text and "SOIL MOISTURE DEEPER (root zone, model estimate, next-24h average): 9-27 cm 0.3 m3/m3." in r.text
    assert "27-81" not in r.text and "SOIL TEMPERATURE (model estimate, next-24h average): 6 cm 29.9 C" in r.text and "18 cm" not in r.text  # missing layers are left out
    assert r.facts["soil_profile"] == {"moisture_3_9cm": 0.21, "moisture_9_27cm": 0.3, "temp_6cm_c": 29.9}


def test_an_empty_answer_is_asked_again_uncached_before_saying_there_is_no_data(monkeypatch):
    monkeypatch.setattr(mandi_mod, "HISTORY_RETRY_DELAY_S", 0)
    calls = {"n": 0}

    def blip(url, params):
        if "data.gov.in" in url and "filters[state]" in params:
            calls["n"] += 1
            return {"records": []} if calls["n"] == 1 else routes_ok(url, params)
        return routes_ok(url, params) if "filters[State]" not in params else {"records": []}

    install(blip)
    r = mandi_mod.mandi("", {"place": "Nashik, Maharashtra", "crop": "Onion"})
    assert calls["n"] == 2 and "no published records" not in r.text and "MANDI PRICES for Onion" in r.text


def test_empty_record_lists_are_cached_only_briefly_but_real_data_keeps_the_normal_ttl(monkeypatch):
    from app.tools import http

    http._cache.clear()
    class R:
        def __init__(self, data): self.data = data
        def raise_for_status(self): pass
        def json(self): return self.data
    class C:
        def __init__(self): self.data = {"records": []}
        def get(self, *a, **k): return R(self.data)
    c = C()
    monkeypatch.setattr(http, "_client", c)
    http.get_json("https://api.data.gov.in/resource/x", {"a": 1}, ttl=1800)
    assert next(iter(http._cache.values()))[2] == http.EMPTY_TTL_S
    http._cache.clear(); c.data = {"records": [{"x": 1}]}
    http.get_json("https://api.data.gov.in/resource/x", {"a": 1}, ttl=1800)
    assert next(iter(http._cache.values()))[2] is None


def test_raw_source_view_returns_the_clipped_real_answer_and_only_for_fetched_urls():
    import httpx
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.tools import http
    from tests.test_resilience import use

    use(lambda req: httpx.Response(200, json={"records": [{"x": i} for i in range(2000)]}))
    f = http.get_json("https://api.data.gov.in/resource/abc", {"api-key": "SECRET123", "limit": 5})
    assert "SECRET123" not in f.url
    c = TestClient(create_app())
    r = c.get("/api/raw", params={"url": f.url})
    assert r.status_code == 200 and r.json()["clipped"] and "SECRET123" not in r.text and r.json()["fetched_at"] == f.fetched_at
    assert c.get("/api/raw", params={"url": "https://evil.example/x"}).status_code == 404


def test_mandi_proxy_url_is_used_only_when_set(monkeypatch):
    import app.tools.mandi as mandi
    from app.core.config import get_settings

    monkeypatch.setenv("MANDI_BASE_URL", "")
    get_settings.cache_clear()
    assert mandi._api("abc").startswith("https://api.data.gov.in/resource/")
    monkeypatch.setenv("MANDI_BASE_URL", "https://x.example.workers.dev/")
    get_settings.cache_clear()
    assert mandi._api("abc") == "https://x.example.workers.dev/resource/abc"
    get_settings.cache_clear()

from app.decision import price_rules as pr
from app.decision import weather_rules as wr
from app.guardrails.validators import forbid_patterns, grounded_numbers, max_words, numbers_in, numeric_limit, run_all


def daily(n=7, **over):
    base = {
        "time": [f"2026-11-{i + 1:02d}" for i in range(n)],
        "temperature_2m_max": [32.0] * n, "temperature_2m_min": [20.0] * n,
        "precipitation_sum": [0.0] * n, "precipitation_probability_max": [10] * n,
        "wind_speed_10m_max": [9.0] * n, "et0_fao_evapotranspiration": [4.0] * n,
    }
    base.update(over)
    return base


def test_weather_flags_and_windows():
    d = daily(precipitation_sum=[0, 70.0, 0, 0, 0, 0, 0], temperature_2m_max=[32, 32, 41, 32, 32, 32, 32], wind_speed_10m_max=[9, 9, 9, 22, 9, 9, 9])
    a = wr.analyze(wr.days_from_daily(d))
    kinds = {(f["flag"], f["day"]) for f in a["flags"]}
    assert ("HEAVY_RAIN", "2026-11-02") in kinds and ("HEAT_STRESS", "2026-11-03") in kinds
    assert "2026-11-04" not in a["spray_ok_days"]  # wind 22 km/h
    assert a["irrigation"] == "SKIP_IRRIGATION" and a["rain_next_48h_mm"] == 70.0


def test_dry_spell_and_missing_values():
    a = wr.analyze(wr.days_from_daily(daily()))
    assert a["dry_spell"] and a["irrigation"] == "IRRIGATE_IF_SOIL_DRY" and a["flags"] == []
    a2 = wr.analyze(wr.days_from_daily(daily(precipitation_sum=[None] * 7)))  # None must not crash
    assert isinstance(a2["flags"], list)


def rec(date, modal):
    return {"arrival_date": date, "modal_price": modal}


def test_price_series_and_signal():
    recs = [rec("01/11/2026", 2000), rec("01/11/2026", 2200), rec("02/11/2026", 2100), rec("03/11/2026", "2,150"), rec("04/11/2026", 2600), rec("bad", 5), rec("05/11/2026", None)]
    s = pr.build_series(recs)
    assert [p["date"] for p in s] == ["2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04"]
    assert s[0]["modal"] == 2100 and s[0]["n"] == 2


def test_build_series_zero_and_blank_prices():
    recs = [rec("01/11/2026", v) for v in (0, "0", "", None, "abc", -5)] + [rec("02/11/2026", 2000)]
    assert pr.build_series(recs) == [{"date": "2026-11-02", "modal": 2000, "n": 1}]


def test_build_series_outliers_dropped():
    recs = [rec("01/11/2026", v) for v in (100, 200, 150, 50000)]
    s = pr.build_series(recs)
    assert s[0]["n"] == 3 and s[0]["modal"] == 150
    low = pr.build_series([rec("01/11/2026", v) for v in (2000, 2100, 2200, 5)])
    assert low[0]["n"] == 3


def test_build_series_duplicates_average_out():
    s = pr.build_series([rec("01/11/2026", 2000)] * 3)
    assert s == [{"date": "2026-11-01", "modal": 2000, "n": 3}]


def mrec(date, market, modal):
    return {"arrival_date": date, "market": market, "modal_price": modal}


def day_records(date, prices, names=None):
    names = names or [f"M{i}" for i in range(len(prices))]
    return [mrec(date, n, p) for n, p in zip(names, prices)]


def test_two_days_of_data_is_insufficient():
    recs = day_records("01/11/2026", [2000] * 6) + day_records("02/11/2026", [2500] * 6)
    assert pr.trend_signal(recs)["signal"] == "INSUFFICIENT_DATA"
    assert pr.trend_signal([])["signal"] == "INSUFFICIENT_DATA" and pr.trend_signal([])["points"] == 0


def test_trend_direction_rising_is_hold_falling_is_sell_steady_is_neutral():
    # Real prices show momentum: a rise tends to continue, so rising -> HOLD and falling -> SELL_NOW (the old rule was the reverse).
    then = day_records("01/11/2026", [2000] * 6)
    up = pr.trend_signal(then + day_records("08/11/2026", [2200] * 6))
    down = pr.trend_signal(then + day_records("08/11/2026", [1800] * 6))
    flat = pr.trend_signal(then + day_records("08/11/2026", [2040] * 6))
    assert (up["signal"], up["change_pct"]) == ("HOLD", 10.0) and (down["signal"], down["change_pct"]) == ("SELL_NOW", -10.0) and flat["signal"] == "NEUTRAL"
    assert up["compared_with"] == "2026-11-01" and up["latest_date"] == "2026-11-08" and up["window_days"] == 7 and up["markets_compared"] == 6
    assert up["pct_vs_baseline"] == up["change_pct"] and up["baseline_mean"] == 2000 and up["latest"] == 2200 and up["trend_per_day"] > 0
    edge = pr.trend_signal(then + day_records("08/11/2026", [2100] * 6))  # exactly +5 %: the band edge counts as a move
    assert edge["signal"] == "HOLD" and pr.trend_signal(then + day_records("08/11/2026", [1900] * 6))["signal"] == "SELL_NOW"


def test_a_changing_set_of_reporting_markets_cannot_fake_a_price_move():
    # Same six markets at an unchanged Rs 2000, but on the second day four dear markets also report. The old daily
    # average jumped by 40 %; the same-market comparison correctly sees no change.
    then = day_records("01/11/2026", [2000] * 6)
    now = day_records("08/11/2026", [2000] * 6) + day_records("08/11/2026", [4000] * 4, ["X1", "X2", "X3", "X4"])
    old_style = pr.build_series(then + [{"arrival_date": r["arrival_date"], "modal_price": r["modal_price"]} for r in now])
    assert (old_style[-1]["modal"] - old_style[0]["modal"]) / old_style[0]["modal"] > 0.15
    got = pr.trend_signal(then + now)
    assert got["signal"] == "NEUTRAL" and got["change_pct"] == 0.0 and got["markets_compared"] == 6


def test_trend_needs_enough_shared_markets_and_a_day_about_a_week_back():
    then = day_records("01/11/2026", [2000] * 6)
    assert pr.trend_signal(then + day_records("08/11/2026", [2400] * 4 + [0, 0], ["M0", "M1", "M2", "M3", "X1", "X2"]))["signal"] == "INSUFFICIENT_DATA"  # only 4 shared
    assert pr.trend_signal(then + day_records("04/11/2026", [2400] * 6))["signal"] == "INSUFFICIENT_DATA"  # 3 days back: not comparable
    assert pr.trend_signal(then + day_records("15/11/2026", [2400] * 6))["signal"] == "INSUFFICIENT_DATA"  # 14 days back
    assert pr.trend_signal(then + day_records("10/11/2026", [2400] * 6))["signal"] == "HOLD"  # 9 days back: inside the +/-2 day slack
    assert pr.trend_signal(then + day_records("06/11/2026", [2400] * 6))["signal"] == "HOLD"  # 5 days back: inside the slack


def test_trend_compares_with_the_day_closest_to_a_week_back():
    recs = day_records("29/10/2026", [1000] * 6) + day_records("01/11/2026", [2000] * 6) + day_records("08/11/2026", [2200] * 6)
    assert pr.trend_signal(recs)["compared_with"] == "2026-11-01"


def test_trend_cleans_real_world_mess_strings_duplicate_rows_outliers_and_zeros():
    then = day_records("01/11/2026", ["2,000", "2000", 2000, "2000", 2000, 2000, 5, 0, None, "abc"], [f"M{i}" for i in range(6)] + ["M6", "M7", "M8", "M9"])  # Rs 5 = typo, others unusable
    now = day_records("08/11/2026", [2200] * 6) + [mrec("08/11/2026", "M0", 2400), mrec("08/11/2026", "M0", 2000)]  # two variety rows for M0: median 2200
    got = pr.trend_signal(then + now)
    assert got["signal"] == "HOLD" and got["change_pct"] == 10.0 and got["markets_compared"] == 6


def test_trend_market_names_match_across_spacing_and_case():
    then = day_records("01/11/2026", [2000] * 6, [f"APMC Lasalgaon {i} " for i in range(6)])
    now = day_records("08/11/2026", [2200] * 6, [f"apmc  lasalgaon {i}" for i in range(6)])
    assert pr.trend_signal(then + now)["markets_compared"] == 6


def test_cross_check():
    heavy = {"flags": [{"flag": "HEAVY_RAIN", "day": "2026-11-02", "day_index": 1, "value": 70.0}]}
    assert pr.cross_check({"signal": "SELL_NOW"}, heavy)[0].startswith("SELL_BEFORE_RAIN")  # falling prices + heavy rain
    assert pr.cross_check({"signal": "HOLD"}, heavy)[0].startswith("STORAGE_RISK")  # rising prices + heavy rain: protect or sell part
    assert pr.cross_check({"signal": "HOLD"}, {"flags": []}) == []
    assert "too short" in pr.cross_check({"signal": "INSUFFICIENT_DATA"}, {"flags": []})[0]


def test_numbers_in_handles_commas_and_indic_digits():
    assert numbers_in("Rs 2,450 and 33.0 C") == {"2450", "33"}
    assert "25" in numbers_in("तापमान २५ डिग्री")


def test_grounded_numbers():
    v = grounded_numbers()
    ctx = "max 33.1 C, rain 70.2 mm, Rs 2,450 on 2026-11-02"
    assert v("Rs 2450, about 33 C, rain 70.2 mm on 2 Nov", ctx) == []
    bad = v("Price will reach Rs 3100 and rain 85 mm", ctx)
    assert bad and "3100" in bad[0] and "85" in bad[0]
    assert v("wait 3 days", ctx) == []  # small integers are not checked


def test_run_all_mixes_context_and_plain_validators():
    vs = [grounded_numbers(), max_words(4), forbid_patterns({"guarantee": "no guarantee"}), numeric_limit("dose", r"([\d.]+) ml per litre", hi=0.0)]
    assert run_all(vs, "ok fine", "") == []
    out = run_all(vs, "I guarantee 99 and 5 ml per litre today please now", "")
    assert len(out) == 4


def test_market_position_labels_and_edges():
    rows = lambda *ps: [{"market": f"M{i}", "district": "Nashik" if i == 0 else "Pune", "modal": p} for i, p in enumerate(ps)]
    hi = pr.market_position(rows(3000, 2000, 2200, 2400), "Nashik")
    assert hi["label"] == "ABOVE_STATE_MEDIAN" and hi["state_median"] == 2300 and hi["pct_vs_state_median"] == 30.4 and hi["best_price"] == 3000
    assert pr.market_position(rows(1500, 2000, 2200, 2400), "nashik")["label"] == "BELOW_STATE_MEDIAN"  # case-insensitive district
    assert pr.market_position(rows(2300, 2000, 2200, 2400), "Nashik")["label"] == "NEAR_STATE_MEDIAN"
    nolocal = pr.market_position(rows(3000, 2000, 2200), "Satara")
    assert "label" not in nolocal and nolocal["best_price"] == 3000
    assert pr.market_position(rows(3000, 2000), "Nashik") is None and pr.market_position([], "x") is None
    assert pr.market_position(rows(0, None, 2200, 2000, 2100), "Nashik")["markets"] == 3  # zero/None prices ignored


def test_clean_rows_drops_entries_ten_times_away_from_the_median():
    # Generic guard (typos / unit slips): prices of 9, 14 and 30 among values near 3700.
    rows = [{"market": m, "modal": p} for m, p in [("A", 3700), ("B", 4000), ("C", 3600), ("D", 9), ("E", 14), ("F", 30), ("G", 5500)]]
    kept = [r["market"] for r in pr.clean_rows(rows)]
    assert kept == ["A", "B", "C", "G"]
    assert pr.clean_rows([{"market": "x", "modal": 100}, {"market": "y", "modal": 5000}]) == [{"market": "x", "modal": 100}, {"market": "y", "modal": 5000}]  # <3 values: nothing to compare against
    assert pr.clean_rows([]) == []


def test_norm_name_matches_across_sources():
    n = pr.norm_name
    assert n("Srīnagar District") == n("Srinagar") == "srinagar"
    assert n("Raipur district") == n("RAIPUR ") == "raipur" and n(None) == ""
    rows = [{"market": "A", "district": "Ernakulam", "modal": 3500}, {"market": "B", "district": "Thrissur", "modal": 3000}, {"market": "C", "district": "Idukki", "modal": 3200}]
    assert pr.market_position(rows, "Ernakulam District")["local_median"] == 3500


def test_cross_check_uses_market_position_when_there_is_no_trend():
    heavy = {"flags": [{"flag": "HEAVY_RAIN", "day": "2026-11-02", "day_index": 1, "value": 70.0}]}
    above = {"label": "ABOVE_STATE_MEDIAN", "pct_vs_state_median": 5.3, "best_price": 5500, "best_market": "Ganjwad", "best_district": "Chandrapur"}
    below = {**above, "label": "BELOW_STATE_MEDIAN", "pct_vs_state_median": -7.9}
    near = {**above, "label": "NEAR_STATE_MEDIAN", "pct_vs_state_median": 1.0}
    none = {"signal": "INSUFFICIENT_DATA"}
    n = pr.cross_check(none, heavy, above)[0]
    assert n.startswith("SELL_BEFORE_RAIN") and "no clear price trend" in n and "5.3 %" in n
    n = pr.cross_check(none, heavy, below)[0]
    assert n.startswith("STORAGE_RISK") and "7.9 %" in n and "Rs 5500 at Ganjwad (Chandrapur)" in n
    assert pr.cross_check(none, heavy, near)[0].startswith("STORAGE_RISK") and "Rs 5500" not in pr.cross_check(none, heavy, near)[0]
    assert pr.cross_check(none, heavy)[0].startswith("STORAGE_RISK")  # no position at all
    # a real trend always wins over position
    falling = {"signal": "SELL_NOW", "change_pct": -8.2, "window_days": 7}
    rising = {"signal": "HOLD", "change_pct": 9.4, "window_days": 7}
    n = pr.cross_check(falling, heavy, below)[0]
    assert n.startswith("SELL_BEFORE_RAIN") and "fell 8.2 %" in n and "not a price forecast" in n
    n = pr.cross_check(rising, heavy, above)[0]
    assert n.startswith("STORAGE_RISK") and "rising (9.4 %" in n and "not a price forecast" in n
    steady = {"signal": "NEUTRAL", "change_pct": 1.0, "window_days": 7}
    assert pr.cross_check(steady, heavy, above)[0].startswith("SELL_BEFORE_RAIN") and "no clear price trend" in pr.cross_check(steady, heavy, above)[0]
    # no heavy rain: position alone creates no sell/storage note
    assert [x for x in pr.cross_check(none, {"flags": []}, above) if x.startswith(("SELL", "STORAGE"))] == []


def test_plain_text_strips_markdown_symbols_but_keeps_numbers_and_dates():
    from app.guardrails.validators import plain_text

    raw = "## Advice\n- **Sell** now at Rs 3,717\n* use `2026-11-05` to spray\n> note: 5-10 mm"
    out = plain_text(raw)
    assert "*" not in out and "`" not in out and "#" not in out and not out.startswith("-")
    assert "Rs 3,717" in out and "2026-11-05" in out and "5-10 mm" in out
    assert plain_text("") == "" and plain_text(None) == ""


def test_negated_guarantee_is_accepted_with_typographic_apostrophes():
    from app.guardrails.validators import forbid_unless_negated

    v = forbid_unless_negated({r"\bguarantee[d]?\b": "Makes a guarantee."})
    assert v("I can’t guarantee 100% profit on onions.") == []
    assert v("I can't guarantee profit.") == [] and v("I am unable to guarantee profit.") == []
    assert v("I guarantee a profit.") == ["Makes a guarantee."]


def test_trend_evidence_is_per_crop_and_says_when_a_crop_was_not_tested():
    assert "two times out of three" in pr.trend_evidence("Onion") and "NOT reliable" in pr.trend_evidence("tomato")
    assert "barely moved" in pr.trend_evidence(" Potato ")
    for crop in ("Wheat", "", None):
        assert pr.trend_evidence(crop) == pr.TREND_UNTESTED and "not been checked" in pr.trend_evidence(crop)


def test_plain_text_writes_internal_labels_as_words_but_keeps_the_decision():
    from app.guardrails.validators import plain_text
    assert plain_text("Price is ABOVE_STATE_MEDIAN. Signal: SELL_NOW. NEUTRAL, HOLD.") == "Price is above state median. Signal: SELL NOW. NEUTRAL, HOLD."


def test_trend_evidence_only_goes_with_a_signal_it_applies_to():
    assert pr.trend_evidence("onion", "NEUTRAL") == "" and pr.trend_evidence("onion", "SELL_NOW") == ""  # about a rising week
    assert "two times out of three" in pr.trend_evidence("onion", "HOLD")
    assert pr.trend_evidence("tomato", "NEUTRAL") == "" and "NOT reliable" in pr.trend_evidence("tomato", "SELL_NOW")


def test_water_balance_compares_forecast_rain_with_reference_demand():
    wb = wr.analyze(wr.days_from_daily(daily(precipitation_sum=[0, 10.0, 0, 0, 2.0, 0, 0])))["water_balance"]
    assert wb == {"days": 7, "rain_mm": 12.0, "et0_mm": 28.0, "balance_mm": -16.0, "label": "RAIN_SHORT_OF_DEMAND"}
    wet = wr.analyze(wr.days_from_daily(daily(precipitation_sum=[30.0] * 7)))["water_balance"]
    assert wet["label"] == "RAIN_COVERS_DEMAND" and wet["balance_mm"] == 182.0


def test_water_balance_is_absent_not_guessed_when_evapotranspiration_is_missing():
    assert wr.analyze(wr.days_from_daily(daily(et0_fao_evapotranspiration=[None] * 7)))["water_balance"] is None
    assert wr.analyze(wr.days_from_daily(daily(n=4)))["water_balance"] is None  # too few days to judge a week


CTX = "Nashik median Rs 4000, 5.3 % above the state median Rs 3800. 7-day change 3.7 %. 2026-11-02: rain 70.0 mm, max 32.0 C. Price Rs 3,717 per quintal."


def test_units_accept_each_figure_with_its_own_unit_and_rounding():
    from app.guardrails.validators import grounded_units

    ok = "Median is Rs 4000 and ₹3717. It is 5.3 % above, up 3.7 percent. Rain 70 mm. ४००० रुपये. 3800 rupees."
    assert grounded_units()(ok, CTX) == []


def test_units_reject_a_real_number_used_with_the_wrong_unit():
    from app.guardrails.validators import grounded_units

    assert grounded_units()("The price is Rs 3.7 per quintal.", CTX)  # 3.7 is a percent change
    assert grounded_units()("Expect 3800 mm of rain.", CTX)  # 3800 is a price
    assert grounded_units()("Prices rose 4000 %.", CTX)
    assert grounded_units()("मध्य भाव 5.3 रुपये आहे.", CTX)  # Marathi unit words are understood too


def test_units_leave_unknown_numbers_to_grounded_numbers_and_ignore_small_integers():
    from app.guardrails.validators import grounded_units

    assert grounded_units()("Price is Rs 9999.", CTX) == []  # not in the data at all: grounded_numbers reports it
    assert grounded_units()("A rise of 5 % would mean hold.", CTX) == []  # small integer rule threshold


def test_thousands_written_with_a_space_are_one_number_not_two():
    from app.guardrails.validators import grounded_units, numbers_in

    ctx = "Nashik median Rs 4000; state median Rs 3800; best price Rs 5500 at Chandrapur; 2026-11-02: rain 100.0 mm"
    for sp in (" ", " ", " ", " "):
        ans = f"Best Rs 5{sp}500, state median Rs 3{sp}800, Nashik 4{sp}000 रु."
        assert grounded_numbers()(ans, ctx) == [] and grounded_units()(ans, ctx) == []
    assert numbers_in("5 500") == {"5500"} and numbers_in("2026-11-02 100 mm") == {"2026", "11", "2", "100"}  # dates are never merged
    assert grounded_numbers()("Best Rs 5 600", ctx)  # a grouped number that is not in the data is still caught


def test_per_market_gives_one_row_per_market_not_one_per_variety():
    rows = [{"market": "A", "district": "Nashik", "modal": m, "min": lo, "max": hi} for m, lo, hi in ((3000, 2500, 3400), (3200, 2800, 3600), (3100, 2600, 3500))]
    rows += [{"market": "B", "district": "Nashik", "modal": 4000, "min": 3500, "max": 4400}, {"market": "A", "district": "Pune", "modal": 9000, "min": None, "max": None}]
    out = {(r["market"], r["district"]): r for r in pr.per_market(rows)}
    assert len(out) == 3 and out[("A", "Nashik")]["modal"] == 3100 and out[("A", "Nashik")]["min"] == 2500 and out[("A", "Nashik")]["max"] == 3600 and out[("A", "Nashik")]["varieties"] == 3
    assert pr.market_position(pr.per_market(rows * 1), "Nashik")["markets"] == 3  # counts markets, not rows

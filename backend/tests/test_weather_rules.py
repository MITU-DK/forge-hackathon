

# ---- hourly spray windows and the irrigation plan
def _hourly(day="2026-11-04", bad_hours=(), wind=8.0, prob=10, rain=0.0):
    times = [f"{day}T{h:02d}:00" for h in range(24)]
    return {"time": times, "wind_speed_10m": [30.0 if h in bad_hours else wind for h in range(24)],
            "precipitation_probability": [prob] * 24, "precipitation": [1.0 if h in bad_hours else rain for h in range(24)]}


def test_spray_window_is_the_longest_dry_calm_stretch_in_daylight():
    from app.decision.weather_rules import spray_windows

    w = spray_windows(_hourly(bad_hours=(10, 11)))
    assert w == [{"date": "2026-11-04", "start": "12:00", "end": "19:00", "hours": 7}]  # 06-09 is 4 h, 12-18 is 7 h


def test_no_window_when_windy_all_day_or_data_missing_or_a_gap_breaks_it():
    from app.decision.weather_rules import spray_windows

    assert spray_windows(_hourly(wind=30.0)) == [] and spray_windows({"time": ["2026-11-04T06:00"]}) == []
    h = _hourly()
    h["precipitation"][8] = None  # an unknown hour is never assumed dry
    assert spray_windows(h)[0]["start"] == "09:00"


def test_irrigation_plan_skips_wet_day_and_the_day_after():
    from app.decision.weather_rules import irrigation_plan

    days = [{"date": f"d{i}", "rain_mm": r, "et0_mm": 4.0} for i, r in enumerate([0.0, 12.0, 0.0, 0.0])]
    assert [d["action"] for d in irrigation_plan(days)] == ["CHECK_SOIL", "SKIP", "SKIP", "CHECK_SOIL"]


def test_hours_already_gone_today_are_not_offered():
    from app.decision.weather_rules import spray_windows

    assert spray_windows(_hourly(), now_local="2026-11-04T10:00")[0]["start"] == "10:00"
    assert spray_windows(_hourly(), now_local="2026-11-04T18:00") == []  # 18:00-19:00 is only one hour

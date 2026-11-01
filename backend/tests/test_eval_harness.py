

def test_guarantee_check_accepts_a_refusal_but_catches_a_promise():
    from tests.eval.run_eval import promises_guarantee

    assert not promises_guarantee("Because the trend is neutral, I cannot guarantee any profit. No profit guarantee can be given.")
    assert promises_guarantee("Yes, I guarantee you will profit.") and promises_guarantee("This is a sure profit.")

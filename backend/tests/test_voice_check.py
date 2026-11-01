from scripts.voice_check import wer


def test_word_error_rate_counts_substitutions_insertions_and_deletions():
    assert wer("मी माझा कांदा विकू की थांबू", "मी माझा कांदा विकू की थांबू") == 0.0
    assert wer("one two three four", "one two three five") == 0.25  # one substitution in four words
    assert wer("one two three four", "one three four") == 0.25  # one deletion
    assert wer("one two", "one two three four") == 1.0  # two insertions in two words
    assert wer("", "") == 0.0 and wer("", "x") == 1.0
    assert wer("Should I sell my onions now?", "should i sell my onions now") == 0.0  # case and punctuation do not count

from services.estimative_language import icd203


def test_internal_labels_map_to_icd203_bands():
    assert icd203("moderate_high")["text"] == "likely (55-80%)"
    assert icd203("low")["term"] == "very unlikely"
    assert icd203("likely")["midpoint"] == 0.675
    assert icd203("not_applicable") is None

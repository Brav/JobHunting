from jobhunt.dedup import match_key


def test_match_key():
    assert match_key("Data Engineer – Cloud & Big Data", "Grid Dynamics") == "data engineer cloud big data|grid dynamics"
    assert match_key("  Senior PHP  Developer ", "ACME d.o.o.") == match_key("senior php developer", "Acme D.O.O")
    assert match_key("Програмер (PHP)", "Нордеус") == "програмер php|нордеус"
    assert match_key("PHP Developer", "Acme") != match_key("PHP Developer", "Other")

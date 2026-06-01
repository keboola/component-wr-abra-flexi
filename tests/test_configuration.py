import pytest

from configuration import Configuration

BASE = {
    "base_url": "https://demo.flexibee.eu",
    "company": "demo",
    "username": "winstrom",
    "#password": "winstrom",
    "evidence": "adresar",
    "id_column": "kod",
}


def test_minimal_config_valid():
    cfg = Configuration(**BASE)
    assert cfg.base_url == "https://demo.flexibee.eu"
    assert cfg.company == "demo"
    assert cfg.password == "winstrom"
    assert cfg.evidence == "adresar"
    assert cfg.id_column == "kod"
    assert cfg.id_type == "ext"
    assert cfg.batch_size == 100
    assert cfg.fail_on_error is False
    assert cfg.ssl_verify is True


def test_password_alias_mapping():
    cfg = Configuration(**BASE)
    assert cfg.password == "winstrom"


def test_missing_required_field_raises_user_exception():
    from keboola.component import UserException

    data = dict(BASE)
    del data["company"]
    with pytest.raises(UserException):
        Configuration(**data)


def test_invalid_id_type_raises_user_exception():
    from keboola.component import UserException

    data = dict(BASE, id_type="bogus")
    with pytest.raises(UserException):
        Configuration(**data)


def test_batch_size_out_of_range_raises_user_exception():
    from keboola.component import UserException

    with pytest.raises(UserException):
        Configuration(**dict(BASE, batch_size=0))
    with pytest.raises(UserException):
        Configuration(**dict(BASE, batch_size=1000))


def test_id_type_internal_accepted():
    cfg = Configuration(**dict(BASE, id_type="internal"))
    assert cfg.id_type == "internal"

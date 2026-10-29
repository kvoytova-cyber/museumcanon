import pandas as pd
import pytest

from museumcanon import (Artist, Artwork, Collection, ConfigError, Exhibition, Grouping,
                         Selector, load_collection, normalize_country)
from museumcanon.parsers import born_country, split_list, year


def test_parsers():
    assert year("c. 1915–20") == 1915
    assert year(0) is None and year("1966.0") == 1966
    assert split_list("12, 34;56") == ["12", "34", "56"]
    assert born_country("American, born U.S.S.R. 1950") == "USSR"
    assert born_country("French, born Russia. 1866–1944") == "Russia"
    assert born_country("Born 1866, Russian Empire (now Russia); died 1944, France") == "Russia"
    assert born_country("Born Białystok, Russian Empire (now Poland), 1882; died Paris, 1949") == "Poland"
    assert born_country("Born Japan, 1933") == "Japan"
    assert born_country("German, born 1952") is None


def test_artist_equality_across_museums():
    a = Artist("MoMA:1", "Natalia Goncharova", birth_year=1881)
    b = Artist("Tate:9", "Goncharova, Natalia", birth_year=1881)
    assert a == b and len({a, b}) == 1


def test_collection_dunders():
    artist = Artist("A:1", "X")
    w1, w2 = Artwork("1", ["A:1"], acquisition_year=1990, museum="A"), Artwork("2", ["A:1"], acquisition_year=1950, museum="A")
    a = Collection("A", [artist], [w1, w2], [Exhibition("A:e", artist_ids={"A:1"})])
    b = Collection("B", [Artist("B:1", "Y")], [Artwork("1", ["B:1"], museum="B")])
    both = a + b
    assert len(both) == 3 and artist in both and w1 in both
    assert sorted(a)[0] is w2
    assert artist in a.exhibitions[0] and len(a.exhibitions[0]) == 1


def test_selector_and_emigre():
    russia = Selector("Россия", {"Rossiya"}, {"Russian"})
    kandinsky = Artist("1", "Kandinsky", nationality="French", birth_country="Russia")
    assert kandinsky in russia and russia.is_emigre(kandinsky)
    assert normalize_country("Ukrayina") == "Ukraine"
    union = russia | Selector("Украина", {"Ukraine"})
    assert Artist("2", "Archipenko", birth_country="Ukraine") in union
    assert Grouping([russia]).group_of(Artist("3", "Z", nationality="Japanese")) is None


def test_mapping_from_any_table(tmp_path):
    pd.DataFrame({"Код": [1, 2], "ФИО": ["Иванов", "Петров"], "Родился": ["1901", "c. 1920"],
                  "Страна": ["Россия", "Rossiya"]}).to_excel(tmp_path / "a.xlsx", index=False)
    config = {"name": "M", "tables": {"artists": {"path": str(tmp_path / "a.xlsx"), "fields": {
        "artist_id": "Код", "name": "ФИО", "birth_year": {"column": "Родился", "parser": "year"},
        "birth_country": "Страна"}}}}
    collection = load_collection(config)
    assert collection.artists["M:2"].birth_year == 1920


def test_mapping_errors_are_readable(tmp_path):
    pd.DataFrame({"name": ["x"], "id": [1]}).to_csv(tmp_path / "a.csv", index=False)
    config = {"name": "M", "tables": {"artists": {"path": str(tmp_path / "a.csv"),
                                                  "fields": {"artist_id": "ID", "name": "name"}}}}
    with pytest.raises(ConfigError, match="Похожие: id"):
        load_collection(config)
    with pytest.raises(ConfigError, match="обязательные поля"):
        load_collection({"name": "M", "tables": {"artists": {"path": "x.csv", "fields": {"name": "n"}}}})

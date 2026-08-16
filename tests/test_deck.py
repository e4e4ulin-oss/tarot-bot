from collections import Counter

from bot.tarot import load_deck
from bot.tarot.deck import card_title


def test_deck_has_78_unique_cards():
    deck = load_deck()
    assert len(deck) == 78
    assert len({card.id for card in deck}) == 78


def test_arcana_split():
    deck = load_deck()
    majors = [c for c in deck if c.is_major]
    minors = [c for c in deck if not c.is_major]
    assert len(majors) == 22
    assert len(minors) == 56
    assert Counter(c.suit for c in minors) == {
        "wands": 14,
        "cups": 14,
        "swords": 14,
        "pentacles": 14,
    }


def test_every_card_has_both_meanings_and_keywords():
    for card in load_deck():
        assert card.meaning_upright.strip(), card.id
        assert card.meaning_reversed.strip(), card.id
        assert len(card.keywords_upright) >= 2, card.id
        assert len(card.keywords_reversed) >= 2, card.id
        assert card.meaning(False) == card.meaning_upright
        assert card.meaning(True) == card.meaning_reversed


def test_majors_have_advice_and_roman_titles():
    majors = sorted((c for c in load_deck() if c.is_major), key=lambda c: c.number)
    assert [c.number for c in majors] == list(range(22))
    assert all(c.advice for c in majors)
    assert card_title(majors[13]).endswith("XIII. Смерть")


def test_minor_names_are_localized():
    names = {card.id: card.name for card in load_deck() if not card.is_major}
    assert names["cups_01"] == "Туз Кубков"
    assert names["swords_12"] == "Рыцарь Мечей"
    assert names["pentacles_10"] == "Десятка Пентаклей"


def test_every_card_maps_to_a_commons_file():
    """У каждой карты своя картинка на Викискладе, без пересечений."""
    from bot.tarot.images import commons_filename, commons_url

    deck = load_deck()
    names = {commons_filename(card) for card in deck}
    assert len(names) == 78

    fool = next(card for card in deck if card.id == "major_00")
    assert commons_filename(fool) == "RWS_Tarot_00_Fool.jpg"
    # Викисклад раскладывает файлы по md5 имени — путь должен совпадать
    assert commons_url(fool) == (
        "https://upload.wikimedia.org/wikipedia/commons/9/90/RWS_Tarot_00_Fool.jpg"
    )

    ace_of_cups = next(card for card in deck if card.id == "cups_01")
    assert commons_filename(ace_of_cups) == "Cups01.jpg"

    king_of_pentacles = next(card for card in deck if card.id == "pentacles_14")
    assert commons_filename(king_of_pentacles) == "Pents14.jpg"


def test_local_image_takes_priority_when_present(tmp_path, monkeypatch):
    """Своя фотография карты должна перекрывать скан с Викисклада."""
    from bot.tarot import images

    monkeypatch.setattr(images, "LOCAL_DIR", tmp_path)
    card = next(c for c in load_deck() if c.id == "major_00")
    assert images.local_path(card) is None

    (tmp_path / "major_00.jpg").write_bytes("фото".encode())
    assert images.local_path(card) == tmp_path / "major_00.jpg"

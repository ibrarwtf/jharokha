from PIL import Image, ImageDraw

from jharokha.core.bubbles import Bubble, boxes, bubbles
from jharokha.core.models import Frame


def drawn() -> Frame:
    """A white pane with a grey bubble, a tinted one, a bordered picture and some text."""
    img = Image.new("RGB", (400, 320), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle((20, 20, 169, 59), fill=(245, 245, 245))
    d.text((30, 30), "a grey bubble", fill=(30, 30, 30))
    d.rectangle((200, 80, 379, 119), fill=(232, 235, 250))
    d.text((210, 92), "a tinted one", fill=(30, 30, 30))
    d.rectangle((40, 150, 239, 249), outline=(200, 200, 200), width=2)
    d.text((60, 190), "inside a picture", fill=(30, 30, 30))
    d.text((20, 280), "(text on the pane)", fill=(30, 30, 30))
    return Frame(img.width, img.height, img.convert("RGBA").tobytes("raw", "BGRA"), 1, 0.0)


def test_boxes_are_the_filled_and_bordered_rectangles():
    assert boxes(drawn(), (0, 0, 400, 320), line=16) == [
        (20, 20, 150, 40),
        (200, 80, 180, 40),
        (40, 150, 200, 100),
    ]


def test_bubbles_tell_a_fill_from_a_border():
    assert bubbles(drawn(), (0, 0, 400, 320), line=16) == [
        Bubble((20, 20, 150, 40), True),
        Bubble((200, 80, 180, 40), True),
        Bubble((40, 150, 200, 100), False),
    ]


def test_boxes_inside_a_region_only():
    assert boxes(drawn(), (180, 60, 220, 80), line=16) == [(200, 80, 180, 40)]

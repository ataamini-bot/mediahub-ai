"""Lossless text pages for Telegram's message limit."""
from html.parser import HTMLParser


class ReadableHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.links = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.links.append(dict(attrs).get('href', ''))
        if tag == 'br':
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag == 'a' and self.links:
            self.parts.append(' (' + self.links.pop() + ')')


def text_pages(text, *, html=False, limit=3200):
    if html:
        parser = ReadableHTML(); parser.feed(text); parser.close()
        text = ''.join(parser.parts)
    pages = []; current = []; size = 0
    for character in text:
        width = 2 if ord(character) > 0xffff else 1
        if current and size + width > limit:
            pages.append(''.join(current)); current = []; size = 0
        current.append(character); size += width
    if current: pages.append(''.join(current))
    return pages or ['—']

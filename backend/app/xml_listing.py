from urllib.parse import urljoin
from xml.etree import ElementTree

XML_BASE = "{http://www.w3.org/XML/1998/namespace}base"


class FeedElement(ElementTree.Element):
    base: str


class FeedBuilder(ElementTree.TreeBuilder):
    def __init__(self, base: str):
        super().__init__(element_factory=FeedElement)
        self.bases = [base]

    def start(self, tag, attrs):
        element = super().start(tag, attrs)
        base = self.bases[-1]
        element.base = urljoin(base, attrs[XML_BASE]) if XML_BASE in attrs else base
        self.bases.append(element.base)
        return element

    def end(self, tag):
        self.bases.pop()
        return super().end(tag)

    def doctype(self, name, pubid, system):
        raise ValueError("XML feeds with a document type declaration are unsupported")


def parse_feed(content: bytes, base: str) -> FeedElement:
    parser = ElementTree.XMLParser(target=FeedBuilder(base))
    try:
        return ElementTree.fromstring(content, parser)
    except ElementTree.ParseError as exc:
        raise ValueError(f"invalid XML listing: {exc}") from None


def nodes(root: FeedElement, path: str) -> list[FeedElement]:
    if path in ("$", "."):
        return [root]
    try:
        return root.findall(path, {"": "*"})
    except SyntaxError as exc:
        raise ValueError(f"invalid XML path {path!r} ({exc}). Use a relative element path.") from None

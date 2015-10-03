
from cheshire3.selector import (
    SimpleSelector,
    MetadataSelector,
    TransformerSelector,
    SpanXPathSelector
)
from cheshire3.record import LxmlRecord, SaxRecord
from cheshire3.utils import elementType, getFirstData


class XPathProcessor(SimpleSelector):
    """An XPathProcessor is a simple wrapper around an XPath.  It is used
    to evaluate the XPath expression according to a given record in
    workflows"""


class SimpleXPathProcessor(XPathProcessor):
    sources = []

    def __init__(self, session, config, parent):
        self.sources = []
        XPathProcessor.__init__(self, session, config, parent)

    def process_record(self, session, record):
        # Extract XPath and return values
        vals = []
        for src in self.sources:
            # list of {}s
            for xp in src:
                if isinstance(record, LxmlRecord):
                    vals.append(
                        record.process_xpath(session,
                                             xp['string'],
                                             xp['maps']
                                             )
                    )
                else:
                    raise ValueError("Only LXML")
        return vals

# DEPRECATED:  Should use selectors
MetadataXPathProcessor = MetadataSelector
TransformerXPathProcessor = TransformerSelector
SpanXPathProcessor = SpanXPathSelector
MetadataXPath = MetadataSelector
TransformerXPath = TransformerSelector
SpanXPath = SpanXPathSelector

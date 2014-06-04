import threading
try:
    import cStringIO as StringIO
except ImportError:
    import StringIO
import cgi
import codecs
import gzip
import os
import re
import urllib2
import urlparse

import time

import cssutils
from lxml import etree
from lxml.cssselect import CSSSelector


__all__ = ['ClaSS2Style']


grouping_regex = re.compile('([:\-\w]*){([^}]+)}')


def merge_styles(old, new, class_=''):
    """
    if ::
      old = 'font-size:1px; color: red'
    and ::
      new = 'font-size:2px; font-weight: bold'
    then ::
      return 'color: red; font-size:2px; font-weight: bold'

    In other words, the new style bits replace the old ones.

    The @class_ parameter can be something like ':hover' and if that
    is there, you split up the style with '{...} :hover{...}'
    Note: old could be something like '{...} ::first-letter{...}'

    """
    
    def csstext_to_pairs(csstext):
        parsed = cssutils.css.CSSVariablesDeclaration(csstext)
        for key in parsed:
            yield (key, parsed.getVariableValue(key))

    new_keys = set()
    news = []

    # The code below is wrapped in a critical section implemented via ``RLock``-class lock.
    # The lock is required to avoid ``cssutils`` concurrency issues documented in issue #65
    with merge_styles._lock:
        for k, v in csstext_to_pairs(new):
            news.append((k.strip(), v.strip()))
            new_keys.add(k.strip())

        groups = {}
        grouped_split = grouping_regex.findall(old)
        if grouped_split:
            for old_class, old_content in grouped_split:
                olds = []
                for k, v in csstext_to_pairs(old_content):
                    olds.append((k.strip(), v.strip()))
                groups[old_class] = olds
        else:
            olds = []
            for k, v in csstext_to_pairs(old):
                olds.append((k.strip(), v.strip()))
            groups[''] = olds

    # Perform the merge
    relevant_olds = groups.get(class_, {})
    merged = [style for style in relevant_olds if style[0] not in new_keys] + news
    groups[class_] = merged

    if len(groups) == 1:
        return '; '.join('%s:%s' % (k, v) for
                          (k, v) in sorted(groups.values()[0]))
    else:
        all = []
        for class_, mergeable in sorted(groups.items(),
                                        lambda x, y: cmp(x[0].count(':'),
                                                         y[0].count(':'))):
            all.append('%s{%s}' % (class_,
                                   '; '.join('%s:%s' % (k, v)
                                              for (k, v)
                                              in mergeable)))
        return ' '.join(x for x in all if x != '{}')

# The lock is used in merge_styles function to work around threading concurrency bug of cssutils library.
# The bug is documented in issue #65. The bug's reproduction test in test_premailer.test_multithreading.
merge_styles._lock = threading.RLock()


_cdata_regex = re.compile(r'\<\!\[CDATA\[(.*?)\]\]\>', re.DOTALL)
_importants = re.compile('\s*!important')

class ClaSS2Style(object):

    def __init__(self, html, base_url=None,
                 keep_style_tags=False,
                 remove_classes=True,
                 strip_important=True,
                 external_styles=None,
                 method="html",
                 base_path=None,
                 disable_validation=False):
        self.html = html
        self.base_url = base_url
        # whether to delete the <style> tag once it's been processed
        self.keep_style_tags = keep_style_tags
        self.remove_classes = remove_classes
        self.strip_important = strip_important
        if isinstance(external_styles, basestring):
            external_styles = [external_styles]
        self.external_styles = external_styles
        self.method = method
        self.base_path = base_path
        self.disable_validation = disable_validation
        self.rules = {}

    def _parse_style_rules(self, css_body):
        # empty string
        if not css_body:
            return
        sheet = cssutils.parseString(css_body, validate=not self.disable_validation)
        for rule in sheet:
            # ignore comments, font-face, media and unknown rules
            if rule.type in (rule.COMMENT, rule.UNKNOWN_RULE, rule.FONT_FACE_RULE, rule.MEDIA_RULE):
                continue
            bulk = ';'.join(
                u'{0}:{1}'.format(key, rule.style[key])
                for key in rule.style.keys()
            )
            selectors = (
                x.strip()
                for x in rule.selectorText.split(',')
                if x.strip() and x.strip().startswith('.')
            )
            for selector in selectors:
                if not re.match('\.[A-Z_-]', selector, re.I):
                    continue
                self.rules[selector] = bulk

    def transform(self, pretty_print=True, **kwargs):
        """change the self.html and return it with CSS turned into style
        attributes.
        """
        if etree is None:
            return self.html
        
        if self.method == 'xml':
            parser = etree.XMLParser(ns_clean=False, resolve_entities=False)
        else:
            parser = etree.HTMLParser()
        stripped = self.html.strip()
        tree = etree.fromstring(stripped, parser).getroottree()
        page = tree.getroot()
        # lxml inserts a doctype if none exists, so only include it in
        # the root if it was in the original html.
        root = tree if stripped.startswith(tree.docinfo.doctype) else page

        if page is None:
            print repr(self.html)
            raise ValueError("Could not parse the html")
        assert page is not None

        ## style tags
        for element in CSSSelector('style,link[rel~=stylesheet]')(page):
            # If we have a media attribute whose value is anything other than
            # 'screen', ignore the ruleset.
            media = element.attrib.get('media')
            if media and media != 'screen':
                continue

            is_style = element.tag == 'style'
            if is_style:
                css_body = element.text
            else:
                href = element.attrib.get('href')
                if not href:
                    continue
                css_body = self._load_external(href)

            self._parse_style_rules(css_body)

            parent_of_element = element.getparent()
            if not self.keep_style_tags or not is_style:
                parent_of_element.remove(element)

        ## explicitly defined external style file
        if self.external_styles:
            for stylefile in self.external_styles:
                css_body = self._load_external(stylefile)
                self._parse_style_rules(css_body)
        
        for tag_classes in page.xpath('//@class'):
            tag = tag_classes.getparent()
            tag_classes = ['.'+c.strip() for c in tag_classes.split(' ') if c.strip()]
            for tag_class in tag_classes:
                if tag_class in self.rules:
                    old_style = tag.attrib.get('style', '')
                    new_style = self.rules[tag_class]
                    if old_style:
                        new_style = '; '.join([old_style, new_style])
                    tag.attrib['style'] = new_style

        if self.remove_classes:
            # now we can delete all 'class' attributes
            for item in page.xpath('//@class'):
                parent = item.getparent()
                del parent.attrib['class']

        kwargs.setdefault('method', self.method)
        kwargs.setdefault('pretty_print', pretty_print)
        out = etree.tostring(root, **kwargs)
        if self.method == 'xml':
            out = _cdata_regex.sub(lambda m: '/*<![CDATA[*/%s/*]]>*/' % m.group(1), out)
        if self.strip_important:
            out = _importants.sub('', out)
        return out

    def _load_external_url(self, url):
        r = urllib2.urlopen(url)
        _, params = cgi.parse_header(r.headers.get('Content-Type', ''))
        encoding = params.get('charset', 'utf-8')
        if 'gzip' in r.info().get('Content-Encoding', ''):
            buf = StringIO.StringIO(r.read())
            f = gzip.GzipFile(fileobj=buf)
            out = f.read().decode(encoding)
        else:
            out = r.read().decode(encoding)
        return out

    def _load_external(self, url):
        """loads an external stylesheet from a remote url or local path
        """
        if url.startswith('//'):
            # then we have to rely on the base_url
            if self.base_url and 'https://' in self.base_url:
                url = 'https:' + url
            else:
                url = 'http:' + url

        if url.startswith('http://') or url.startswith('https://'):
            css_body = self._load_external_url(url)
        else:
            stylefile = url
            if not os.path.isabs(stylefile):
                stylefile = os.path.abspath(
                    os.path.join(self.base_path or '', stylefile)
                )
            if os.path.exists(stylefile):
                with codecs.open(stylefile, encoding='utf-8') as f:
                    css_body = f.read()
            elif self.base_url:
                url = urlparse.urljoin(self.base_url, url)
                return self._load_external(url)
            else:
                raise ValueError(u"Could not find external style: %s" %
                                 stylefile)
        return css_body

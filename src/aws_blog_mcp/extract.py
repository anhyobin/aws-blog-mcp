"""Extract metadata and a clean markdown body from an AWS blog post page.

All AWS blogs share one server-rendered WordPress template (verified on posts from 2014 to 2026)::

    body.aws-blogs-page-single
      article.blog-post
        h1.blog-post-title
        footer.blog-post-meta     byline, time[property=datePublished], .blog-post-categories a
        section.blog-post-content <- the body (may contain extra .blog-author-box co-authors)
        footer > .blog-author-box byline authors with bios

The body is cleaned in HTML first (WordPress artefacts, widgets, captions, code blocks, headings,
tables), then converted with :class:`~aws_blog_mcp.markdown.BlogConverter` and tidied.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from urllib.parse import quote, urljoin, urlsplit

from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag

from aws_blog_mcp.blogs import blog_for_url
from aws_blog_mcp.errors import InvalidInputError, NotABlogPostError
from aws_blog_mcp.markdown import BlogConverter, tidy
from aws_blog_mcp.text import clean_text, dedupe_names, split_authors
from aws_blog_mcp.urls import normalize_post_url

ImageMode = Literal["markdown", "alt", "none"]

_INLINE_MERGE = ("em", "strong", "b", "i", "u", "code")
_IMAGE_EXT_RE = re.compile(r"\.(?:png|jpe?g|gif|webp|svg|bmp)(?:\?.*)?$", re.I)
_LANG_CLASS_RE = re.compile(r"^(?:lang|language)-([\w+#.-]+)$")
_YOUTUBE_RE = re.compile(r"youtube(?:-nocookie)?\.com/embed/([\w-]{6,})")
_HEADING_RE = re.compile(r"^h[1-6]$")
_ZERO_WIDTH = dict.fromkeys(map(ord, "\x00​‌‍⁠﻿"))
_SPACE_RUN_RE = re.compile(r"[ \t]{2,}")
_SMALL_INT_RE = re.compile(r"^[0-9]{1,4}$")
_BLOCK_TAGS = frozenset({"ul", "ol", "p", "div", "pre", "table", "blockquote"})
_INLINE_WRAPPERS = ("strong", "b", "em", "i", "u", "span", "a", "code")
_SPACES = {0x00A0: " ", 0x202F: " ", 0x2007: " "}
_REMOVE_SELECTOR = ", ".join(
    (
        "script", "style", "noscript", "template", "form", "input", "button", "select",
        "textarea", "svg", "canvas", "audio", "video", "object", "embed", ".blog-share-dialog",
        "#amazon-polly-audio-table", "[id^=amazon-polly]", "[id^=amazon-ai-player]",
        ".js-mbox",
    )
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class PostPage:
    """Metadata of a post page plus its (uncleaned) body HTML."""

    url: str
    """Canonical URL."""
    title: str
    authors: tuple[str, ...]
    published: datetime | None
    modified: datetime | None
    categories: tuple[str, ...]
    tags: tuple[str, ...]
    language: str | None
    body_html: str


def parse_post_page(html: str, url: str) -> PostPage:
    """Parse a post page.

    Raises:
        NotABlogPostError: the page is not a single blog post (listing page, error page, or the
            template changed).
    """
    soup = BeautifulSoup(html, "lxml")
    body_classes = _classes(soup.body) if soup.body else []
    sections = soup.select("article.blog-post section.blog-post-content")
    article = soup.select_one("article.blog-post")
    if "aws-blogs-page-single" not in body_classes or len(sections) != 1 or article is None:
        raise NotABlogPostError(
            f"{url} is not a single blog post page. Pass the URL of an individual post "
            "(for example one returned by search_posts)."
        )
    canonical = _trusted_canonical(_attr(soup.select_one('link[rel="canonical"]'), "href"), url)

    title_el = article.select_one("h1.blog-post-title") or article.find("h1")
    title = clean_text(title_el.get_text(" ")) if isinstance(title_el, Tag) else ""
    if not title:
        title = re.sub(r"\s*\|\s*Amazon Web Services\s*$", "", _meta(soup, "og:title") or "")

    meta_footer = article.select_one("footer.blog-post-meta")
    byline = ""
    if meta_footer is not None:
        first_span = meta_footer.find("span")
        if isinstance(first_span, Tag):
            match = re.match(r"by\b\s*(.*)", clean_text(first_span.get_text(" ")), re.I)
            byline = match.group(1) if match else ""  # e.g. the date span when there is no byline
    box_names: list[str] = []
    for box in article.select(".blog-author-box"):
        name_el = box.find(["h3", "h4"]) or box.select_one('[property="name"]')
        if isinstance(name_el, Tag):
            name = clean_text(name_el.get_text(" "))
            if name:
                box_names.append(name)

    time_el = article.select_one('time[property="datePublished"]')
    published = _parse_datetime(_attr(time_el, "datetime") or _meta(soup, "article:published_time"))
    modified = _parse_datetime(_meta(soup, "article:modified_time"))
    categories = tuple(
        dict.fromkeys(
            clean_text(a.get_text(" "))
            for a in (meta_footer.select(".blog-post-categories a") if meta_footer else [])
        )
    )
    tags = tuple(clean_text(a.get_text(" ")) for a in article.select('.blog-tag-list a[rel="tag"]'))
    language = _attr(article.select_one('meta[property="inLanguage"]'), "content")

    return PostPage(
        url=canonical,
        title=title,
        authors=dedupe_names([*split_authors(byline), *box_names]),
        published=published,
        modified=modified,
        categories=tuple(c for c in categories if c),
        tags=tuple(t for t in tags if t),
        language=language,
        body_html=str(sections[0]),
    )


def _trusted_canonical(canonical: str | None, fetched_url: str) -> str:
    """Use the page's canonical URL only if it is a valid post URL of the same blog."""
    if not canonical:
        return fetched_url
    try:
        blog, normalized = normalize_post_url(canonical)
    except InvalidInputError:
        return fetched_url
    return normalized if blog is blog_for_url(fetched_url) else fetched_url


def render_body(body_html: str, base_url: str, *, images: ImageMode = "markdown") -> str:
    """Convert a post body (``section.blog-post-content`` HTML) to tidy markdown."""
    soup = BeautifulSoup(body_html, "lxml")
    content = soup.select_one("section.blog-post-content")
    if content is None:  # pragma: no cover - parse_post_page guarantees the section
        return ""
    _clean(soup, content, base_url, images)
    return tidy(BlogConverter().convert_soup(content))


def _clean(soup: BeautifulSoup, content: Tag, base: str, images: ImageMode) -> None:
    # lxml parses a bare <embed src=...> as a container that swallows the following content.
    for el in content.find_all("embed"):
        el.unwrap()
    for el in content.select(_REMOVE_SELECTOR):
        el.decompose()
    _sanitise_numeric_attributes(content)
    for box in content.select(".blog-author-box"):
        parent = box.parent
        box.decompose()
        if isinstance(parent, Tag) and parent.name == "footer" and not parent.get_text(strip=True):
            parent.decompose()
    for el in content.find_all("footer"):
        el.unwrap()

    _merge_continuation_items(content)
    for iframe in content.find_all("iframe"):
        iframe.replace_with(_iframe_to_link(soup, iframe))
    _fix_images_and_links(content, base)
    _flatten_captions(soup, content)
    _flatten_code(soup, content)
    _fix_headings(content)
    _fix_tables(soup, content)
    _merge_adjacent_inline(content)
    _hoist_breaks_and_nesting(content)
    _drop_redundant_inline(content)
    _apply_image_mode(content, images)
    _normalise_text(content)


def _sanitise_numeric_attributes(content: Tag) -> None:
    """Drop span/start attributes that are not small ASCII integers (markdownify would crash)."""
    for cell in content.find_all(["td", "th"]):
        for name in ("colspan", "rowspan"):
            value = _attr(cell, name)
            if value is None:
                continue
            if _SMALL_INT_RE.match(value.strip()):
                cell[name] = value.strip()
            else:
                del cell[name]
    for ol in content.find_all("ol"):
        value = _attr(ol, "start")
        if value is None:
            continue
        if _SMALL_INT_RE.match(value.strip()):
            ol["start"] = value.strip()
        else:
            del ol["start"]


def _merge_continuation_items(content: Tag) -> None:
    """WordPress ``<li style="list-style-type: none">`` continues the previous item."""
    for li in content.find_all("li", style=re.compile(r"list-style-type:\s*none")):
        prev = li.find_previous_sibling("li")
        if isinstance(prev, Tag):
            for child in list(li.contents):
                prev.append(child.extract())
            li.decompose()
        elif all(
            (isinstance(c, NavigableString) and not c.strip())
            or (isinstance(c, Tag) and c.name in _BLOCK_TAGS)
            for c in li.contents
        ):
            li.unwrap()  # only block content (nested list, paragraph...): hoist it
        else:
            del li["style"]  # leading item with its own content: keep it as a normal item


def _iframe_to_link(soup: BeautifulSoup, iframe: Tag) -> Tag:
    src = _attr(iframe, "src") or _attr(iframe, "data-src") or ""
    title = clean_text(_attr(iframe, "title") or "")
    match = _YOUTUBE_RE.search(src)
    if match:
        href = f"https://www.youtube.com/watch?v={match.group(1)}"
        label = f"Video: {title or 'YouTube'}"
    else:
        href = _safe_url("https://aws.amazon.com/", src)
        label = f"Embedded content: {title or urlsplit(href).netloc}"
    p = soup.new_tag("p")
    a = soup.new_tag("a", href=href)
    a.string = label
    p.append(a)
    return p


def _fix_images_and_links(content: Tag, base: str) -> None:
    for img in content.find_all("img"):
        src = (_attr(img, "src") or _attr(img, "data-src") or "").strip()
        # A bare file name ("Picture1.png") is an authoring bug; it is broken on the site too.
        broken = not src or not re.match(r"^(?:https?:)?//|^/|^data:", src, re.I)
        alt = clean_text(_attr(img, "alt") or "")
        img.attrs = {"src": _safe_url(base, src), "alt": alt}
        if broken:
            img["data-broken"] = "1"
    for a in content.find_all("a"):
        href = (_attr(a, "href") or "").strip()
        if not href or href.lower().startswith("javascript:"):
            a.unwrap()
            continue
        if not href.startswith("#"):
            href = _safe_url(base, href)
        imgs = a.find_all("img")
        if (
            len(imgs) == 1
            and not a.get_text(strip=True)
            and (href == _attr(imgs[0], "src") or _IMAGE_EXT_RE.search(href))
        ):
            a.unwrap()  # a link to the image itself adds nothing
            continue
        a.attrs = {"href": href}
    for img in content.find_all("img", attrs={"data-broken": "1"}):
        parent = img.parent
        img.decompose()
        if isinstance(parent, Tag) and parent.name in ("p", "a") and _is_empty(parent):
            parent.decompose()


def _flatten_captions(soup: BeautifulSoup, content: Tag) -> None:
    for cap in content.select("div.wp-caption, figure"):
        if cap.parent is None:
            continue  # already handled as part of an enclosing figure
        img = cap.find("img")
        # Only the container's own caption: nested figures (galleries) keep theirs.
        text_el = cap.find("figcaption", recursive=False) or cap.find(
            class_="wp-caption-text", recursive=False
        )
        caption = clean_text(text_el.get_text(" ")) if isinstance(text_el, Tag) else ""
        if not _is_image_figure(cap, text_el if isinstance(text_el, Tag) else None):
            # Callout boxes, tables, embeds, quotes...: keep everything, caption as a paragraph.
            if isinstance(text_el, Tag):
                p = soup.new_tag("p")
                em = soup.new_tag("em")
                em.string = caption
                p.append(em)
                text_el.replace_with(p) if caption else text_el.decompose()
            if "callout" in _classes(cap):
                cap.name = "blockquote"  # Notion-style callout box
                cap.attrs = {}
            else:
                cap.unwrap()
            continue
        wrapper = soup.new_tag("div")
        if isinstance(img, Tag):
            holder = img.parent if isinstance(img.parent, Tag) and img.parent.name == "a" else img
            p = soup.new_tag("p")
            p.append(holder.extract())
            wrapper.append(p)
        if caption:
            p = soup.new_tag("p")
            em = soup.new_tag("em")
            em.string = caption
            p.append(em)
            wrapper.append(p)
        cap.replace_with(wrapper)


def _is_image_figure(cap: Tag, caption_el: Tag | None) -> bool:
    """True if ``cap`` holds exactly one image (optionally linked) plus an optional caption."""
    if len(cap.find_all("img")) != 1:
        return False
    for child in cap.contents:
        if isinstance(child, NavigableString):
            if child.strip():
                return False
        elif child is caption_el or not isinstance(child, Tag) or child.name == "img":
            continue
        elif child.name in ("a", "p", "div", "span") and child.find("img") is not None:
            if child.get_text(strip=True) and child is not caption_el:
                return False
        else:
            return False
    return True


def _flatten_code(soup: BeautifulSoup, content: Tag) -> None:
    for pre in content.find_all("pre"):
        _lift_out_of_inline(soup, pre)
        lang = _code_language(pre)
        for br in pre.find_all("br"):
            br.replace_with(NavigableString("\n"))
        text = pre.get_text()  # drops <strong> etc. that some posts put inside code
        pre.clear()
        pre.append(NavigableString(text))
        pre.attrs = {"data-lang": lang} if lang else {}
        wrapper = pre.parent
        if (
            isinstance(wrapper, Tag)
            and wrapper.name == "div"
            and "hide-language" in _classes(wrapper)
        ):
            wrapper.unwrap()


def _code_language(pre: Tag) -> str:
    for el in (pre, pre.find("code")):
        if isinstance(el, Tag):
            for cls in _classes(el):
                match = _LANG_CLASS_RE.match(cls)
                if match:
                    return match.group(1).lower()
    return ""


def _fix_headings(content: Tag) -> None:
    for h in content.find_all(_HEADING_RE):
        if not h.get_text().translate(_ZERO_WIDTH).strip():
            if h.find("img"):
                h.name = "p"
            else:
                h.decompose()
    # The post title is the only H1; demote body headings if the author used H1.
    if content.find("h1"):
        for h in content.find_all(_HEADING_RE):
            h.name = f"h{min(6, int(h.name[1]) + 1)}"
    for h in content.find_all(_HEADING_RE):
        for el in h.find_all(["strong", "b"]):
            el.unwrap()


def _fix_tables(soup: BeautifulSoup, content: Tag) -> None:
    for table in content.find_all("table"):
        _expand_rowspans(soup, table)
        cells = table.find_all(["td", "th"])
        if len(table.find_all("tr")) == 1 and len(cells) == 1:
            # 1x1 tables are used as callout boxes.
            quote_el = soup.new_tag("blockquote")
            for child in list(cells[0].contents):
                quote_el.append(child.extract())
            table.replace_with(quote_el)
            continue
        for cell in cells:
            for br in cell.find_all("br"):
                br.replace_with(NavigableString(" <br> "))
            for i, block in enumerate(cell.find_all(["p", "li", "div"])):
                if i:
                    block.insert_before(NavigableString(" <br> "))
                block.unwrap()


def _expand_rowspans(soup: BeautifulSoup, table: Tag) -> None:
    """Repeat row-spanning cells in the rows they cover, so markdown columns stay aligned.

    Short spanned values are copied into each covered row; long ones leave the cells empty.
    """
    rows = [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]
    pending: dict[int, list[Any]] = {}  # column -> [rows left, colspan, text, tag name]
    for index, tr in enumerate(rows):
        cells = tr.find_all(["td", "th"], recursive=False)
        col = 0
        next_cell = 0
        while next_cell < len(cells) or any(c >= col for c in pending):
            if col in pending:
                left, span, text, name = pending[col]
                filler = soup.new_tag(name)
                if span > 1:
                    filler["colspan"] = str(span)
                filler.string = text
                if next_cell < len(cells):
                    cells[next_cell].insert_before(filler)
                else:
                    tr.append(filler)
                if left <= 1:
                    del pending[col]
                else:
                    pending[col][0] = left - 1
                col += span
                continue
            if next_cell >= len(cells):
                tr.append(soup.new_tag("td"))  # short row: keep later spanned cells in place
                col += 1
                continue
            cell = cells[next_cell]
            next_cell += 1
            span = int(_attr(cell, "colspan") or 1)
            rowspan = int(_attr(cell, "rowspan") or 1)
            if "rowspan" in cell.attrs:
                del cell["rowspan"]
            covered = min(rowspan, 1000, len(rows) - index) - 1
            if covered > 0:
                text = clean_text(cell.get_text(" "))
                pending[col] = [covered, max(1, span), text if len(text) <= 80 else "", cell.name]
            col += max(1, span)


def _merge_adjacent_inline(content: Tag) -> None:
    """``<em>a</em><em>b</em>`` -> ``<em>ab</em>`` (fragmentation from pasted documents)."""
    changed = True
    while changed:
        changed = False
        for el in content.find_all(_INLINE_MERGE):
            if el.parent is None or el.find_parent("pre"):
                continue
            nxt = el.next_sibling
            gap = ""
            if isinstance(nxt, NavigableString) and not nxt.strip():
                gap = str(nxt)
                nxt = nxt.next_sibling
            if isinstance(nxt, Tag) and nxt.name == el.name and _classes(nxt) == _classes(el):
                if gap:
                    gap_node = el.next_sibling
                    if isinstance(gap_node, NavigableString):
                        gap_node.extract()
                    el.append(" ")
                for child in list(nxt.contents):
                    el.append(child.extract())
                nxt.decompose()
                changed = True


def _lift_out_of_inline(soup: BeautifulSoup, pre: Tag) -> None:
    """Move a code block out of inline formatting (``<strong>Tip<pre>..</pre></strong>``).

    Markdown cannot put a fence inside ``**...**``; the formatting is split around the block.
    """
    while isinstance(pre.parent, Tag) and pre.parent.name in _INLINE_WRAPPERS:
        parent = pre.parent
        if _only_child(parent, pre):
            parent.unwrap()
            continue
        tail = soup.new_tag(parent.name)
        tail.attrs = dict(parent.attrs)
        for node in list(pre.next_siblings):
            tail.append(node.extract())
        parent.insert_after(pre.extract())
        if tail.get_text(strip=True) or tail.find(True):
            pre.insert_after(tail)


def _only_child(parent: Tag, child: Tag) -> bool:
    return all(
        c is child or (isinstance(c, NavigableString) and not c.strip()) for c in parent.contents
    )


def _hoist_breaks_and_nesting(content: Tag) -> None:
    r"""Fix inline markup that markdown cannot express.

    * ``<b><b>x</b></b>`` -> ``<b>x</b>`` (otherwise ``****x****``);
    * a ``<br>`` at the start or end of emphasis moves outside it (``**Note: **`` does not close);
    * a ``<br>`` right before a nested list or other block is dropped (it renders as ``\\``).
    """
    for el in content.find_all(_INLINE_WRAPPERS):
        parent = el.parent
        if isinstance(parent, Tag) and parent.name == el.name and _only_child(parent, el):
            el.unwrap()
    for el in content.find_all(_INLINE_WRAPPERS):
        if el.parent is None or el.find_parent("pre"):
            continue
        while True:
            kids = [
                k for k in el.contents if not (isinstance(k, NavigableString) and not k.strip())
            ]
            if kids and isinstance(kids[-1], Tag) and kids[-1].name == "br":
                el.insert_after(kids[-1].extract())
            elif kids and isinstance(kids[0], Tag) and kids[0].name == "br":
                el.insert_before(kids[0].extract())
            else:
                break
    for br in content.find_all("br"):
        nxt = br.next_sibling
        while isinstance(nxt, NavigableString) and not nxt.strip():
            nxt = nxt.next_sibling
        if isinstance(nxt, Tag) and nxt.name in _BLOCK_TAGS:
            br.decompose()


def _drop_redundant_inline(content: Tag) -> None:
    for el in content.find_all(["em", "strong", "b", "i", "u"]):
        if el.parent is None or el.find("img"):
            continue
        text = el.get_text().strip()
        if text and all(unicodedata.category(ch)[0] in "PSZ" for ch in text):
            el.unwrap()  # emphasis around punctuation only, e.g. <em>/</em>
    for block in content.find_all(
        ["p", "li", "td", "th", "blockquote", "div", "h1", "h2", "h3", "h4", "h5", "h6"]
    ):
        kids = [k for k in block.contents if not (isinstance(k, NavigableString) and not k.strip())]
        is_br = [isinstance(k, Tag) and k.name == "br" for k in kids]
        lo, hi = 0, len(kids)
        while lo < hi and is_br[lo]:
            lo += 1
        while hi > lo and is_br[hi - 1]:
            hi -= 1
        for kid in kids[:lo] + kids[hi:]:
            kid.decompose()
    for el in content.find_all(["em", "strong", "b", "i", "u", "span", "code"]):
        if el.parent is None or el.find("img") or el.find_parent("pre"):
            continue
        if not el.get_text().strip():
            el.replace_with(NavigableString(" " if el.get_text() else ""))
    for span in content.find_all("span"):
        span.unwrap()


def _apply_image_mode(content: Tag, images: ImageMode) -> None:
    if images == "markdown":
        return
    for img in content.find_all("img"):
        alt = _attr(img, "alt") or ""
        if images == "alt" and alt:
            img.replace_with(NavigableString(f"[Image: {alt}]"))
        else:
            parent = img.parent
            img.decompose()
            if isinstance(parent, Tag) and parent.name in ("p", "a") and _is_empty(parent):
                parent.decompose()


def _normalise_text(content: Tag) -> None:
    for node in content.find_all(string=True):
        text = str(node).translate(_SPACES).translate(_ZERO_WIDTH)
        if not node.find_parent("pre"):
            # Long space runs make markdownify's whitespace regex quadratic; they render as one.
            text = _SPACE_RUN_RE.sub(" ", unicodedata.normalize("NFC", text))
        if text != str(node):
            node.replace_with(NavigableString(text))


def _is_empty(tag: Tag) -> bool:
    return not tag.get_text(strip=True) and tag.find("img") is None


def _safe_url(base: str, raw: str) -> str:
    """Resolve against ``base`` and percent-encode spaces, parentheses and non-ASCII characters.

    Parentheses are encoded so URLs cannot terminate a markdown ``[text](url)`` early.
    """
    return quote(urljoin(base, raw.strip()), safe=":/?#[]@!$&'*+,;=%~")


def _classes(tag: Tag) -> list[str]:
    value = tag.get("class")
    if value is None:
        return []
    if isinstance(value, str):
        return value.split()
    return [str(v) for v in value]


def _attr(tag: object, name: str) -> str | None:
    if not isinstance(tag, Tag):
        return None
    value = tag.get(name)
    if isinstance(value, str):
        return value
    if value is None:
        return None
    return " ".join(str(v) for v in value)


def _meta(soup: BeautifulSoup, prop: str) -> str | None:
    return _attr(soup.select_one(f'meta[property="{prop}"]'), "content")


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError:
        return None

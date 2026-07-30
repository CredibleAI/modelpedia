import html


def escape(value):
    return html.escape(str(value), quote=True)


def anchor(url, text, extra=""):
    return '<a href="%s"%s>%s</a>' % (escape(url), extra, text)


def paragraph(text, css_class=None):
    opening = '<p class="%s">' % css_class if css_class else "<p>"
    return "%s%s</p>" % (opening, text)


def definition_list(pairs):
    body = "".join("<dt>%s</dt><dd>%s</dd>" % (escape(term), value)
                   for term, value in pairs if value)
    if not body:
        return ""
    return "<dl>%s</dl>" % body


def entry(tag, body, count=""):
    return ('<li><span class="tag">%s</span><span class="entry-body">%s</span>'
            '<span class="count">%s</span></li>' % (tag, body, count))


def entry_list(items):
    if not items:
        return ""
    return '<ul class="entries">%s</ul>' % "".join(items)


def heading(title):
    return "<h2>%s</h2>" % escape(title)

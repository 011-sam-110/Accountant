"""Branded HTML for the emails we send (sign-in code + reminders).

`notify.py` is pure transport; this module owns what the messages *look like*.
The markup is deliberately old-school — nested tables, inline styles, a narrow
fixed width — because real email clients (Gmail, Outlook, Apple Mail) strip
`<head>` CSS and ignore modern layout. A small `<style>` block is included for
progressive niceties (button hover, a mobile padding tweak), but every style
that matters is *also* inlined so the message looks right with the `<head>`
thrown away.

Colours mirror ``public/app.css`` so an email feels like the same product as
the app. Each builder returns a complete HTML document; plain-text alternatives
are passed alongside (see callers) so every message is proper multipart.
"""
from __future__ import annotations

import html as _html
from string import Template

from .config import settings

# Brand palette — kept in step with public/app.css :root.
_INK = "#14171a"
_INK2 = "#3a424c"
_MUTED = "#5b6470"
_GREEN = "#1a7a3c"
_GREEN_D = "#14642f"
_GREEN_BG = "#e8f3ec"
_GREEN_LINE = "#cde6d4"
_LINE = "#e2e6ea"
_PAGE = "#f4f6f8"
_CARD = "#ffffff"
_FONT = ("-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,"
         "sans-serif")

# $-placeholders (not {}) so the CSS braces in <style> need no escaping.
_SHELL = Template("""\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light only">
<meta name="supported-color-schemes" content="light">
<title>EasyBooks</title>
<style>
  body { margin:0 !important; padding:0 !important; background:$page; }
  a { color:$green; }
  .tb-btn:hover { background:$green_d !important; }
  @media (max-width:600px) {
    .tb-card { padding:26px 22px !important; }
  }
</style>
</head>
<body style="margin:0;padding:0;background:$page;-webkit-text-size-adjust:100%;">
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;opacity:0;line-height:0;font-size:1px;color:$page;">
$preheader&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;
</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:$page;">
  <tr><td align="center" style="padding:30px 16px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:480px;width:100%;">
      <tr><td class="tb-card" style="background:$card;border:1px solid $line;border-radius:16px;padding:34px 32px;font-family:$font;">
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 24px;">
          <tr>
            <td style="background:$green;border-radius:9px;width:34px;height:34px;text-align:center;vertical-align:middle;color:#ffffff;font-size:19px;font-weight:700;font-family:$font;line-height:34px;">&#10003;</td>
            <td style="padding-left:11px;font-family:$font;font-size:18px;font-weight:700;color:$ink;letter-spacing:-.2px;">EasyBooks</td>
          </tr>
        </table>
        $content
      </td></tr>
      <tr><td style="padding:20px 28px 0;text-align:center;font-family:$font;">
        <p style="margin:0;font-size:12px;line-height:1.5;color:$muted;">Simple record-keeping for self-employed driving instructors.</p>
        $note
      </td></tr>
    </table>
  </td></tr>
</table>
</body>
</html>""")


def _shell(*, preheader: str, content: str, footer_note: str = "") -> str:
    """Wrap inner card content in the full branded email document."""
    note = ""
    if footer_note:
        note = (f'<p style="margin:14px 0 0;font-size:12px;line-height:1.5;'
                f'color:{_MUTED};">{_html.escape(footer_note)}</p>')
    return _SHELL.substitute(
        page=_PAGE, card=_CARD, line=_LINE, ink=_INK, green=_GREEN,
        green_d=_GREEN_D, muted=_MUTED, font=_FONT,
        preheader=_html.escape(preheader), content=content, note=note,
    )


def _h1(text: str) -> str:
    return (f'<h1 style="margin:0 0 16px;font-size:22px;line-height:1.3;'
            f'font-weight:700;color:{_INK};font-family:{_FONT};">{text}</h1>')


def _p(text: str) -> str:
    return (f'<p style="margin:0 0 15px;font-size:16px;line-height:1.6;'
            f'color:{_INK2};font-family:{_FONT};">{text}</p>')


def _button(href: str, label: str) -> str:
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        f'style="margin:10px 0 4px;"><tr>'
        f'<td style="border-radius:10px;background:{_GREEN};">'
        f'<a class="tb-btn" href="{_html.escape(href, quote=True)}" '
        f'style="display:inline-block;padding:14px 28px;font-family:{_FONT};'
        f'font-size:16px;font-weight:700;color:#ffffff;text-decoration:none;'
        f'border-radius:10px;">{_html.escape(label)}</a></td></tr></table>'
    )


def _code_block(code: str) -> str:
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0" style="margin:8px 0 20px;"><tr>'
        f'<td align="center" style="background:{_GREEN_BG};border:1px solid '
        f'{_GREEN_LINE};border-radius:12px;padding:20px 12px;">'
        f'<div style="font-family:\'SF Mono\',SFMono-Regular,Menlo,Consolas,monospace;'
        f'font-size:34px;font-weight:700;letter-spacing:8px;color:{_GREEN_D};'
        f'line-height:1;">{_html.escape(code)}</div></td></tr></table>'
    )


def signin_email(code: str) -> tuple[str, str]:
    """(html, text) for the one-time sign-in code email."""
    code = str(code)
    content = (
        _h1("Here&rsquo;s your sign-in code")
        + _p("Pop this code into EasyBooks to finish signing in:")
        + _code_block(code)
        + _p("It expires in 15 minutes.")
        + _p("If you didn&rsquo;t try to sign in, you can ignore this email "
             "&mdash; nothing will happen.")
    )
    html = _shell(
        preheader="Use this code to finish signing in to EasyBooks.",
        content=content,
    )
    text = (
        "Here's your sign-in code\n\n"
        f"Your code is: {code}\n\n"
        "Enter it in EasyBooks to finish signing in. It expires in 15 minutes.\n\n"
        "If you didn't try to sign in, you can ignore this email.\n\n"
        "— EasyBooks"
    )
    return html, text


def reminder_email(text_body: str) -> str:
    """Wrap a plain-text reminder (built in reminders.py) in branded HTML.

    The text is split into paragraphs on blank lines and escaped, then a single
    clear call-to-action button is added. The original plain text is still sent
    as the message's text/plain alternative by the caller.
    """
    paras = [seg.strip() for seg in text_body.split("\n\n") if seg.strip()]
    body_html = "".join(_p(_html.escape(seg).replace("\n", "<br>")) for seg in paras)
    content = body_html + _button(settings.base_url, "Open EasyBooks")
    # Preview text = first real sentence (skip the "Hi <name>," greeting).
    preheader = paras[1] if len(paras) > 1 else (paras[0] if paras else "")
    return _shell(
        preheader=preheader,
        content=content,
        footer_note=("You're getting this because reminders are switched on. "
                     "You can turn them off any time in EasyBooks."),
    )

"""CashTime patch: redirect quoted messages from non-web clients to a new topic.

When a Zulip mobile client (Flutter, legacy ZulipMobile, etc.) sends a
quoted reply, Zulip's web behaviour is to keep the reply in the
original topic. CashTime wants every quote — including those from
clients we can't patch — to land in a fresh topic, mirroring the
behaviour of the Web "Quote to new topic" action.

This module exposes :func:`maybe_topic_from_quote`, which inspects an
outgoing stream message and, if it looks like a Zulip-formatted quoted
reply produced by a non-web client, returns the topic name to use
instead. The topic name is derived from the first ten cleaned words of
the quoted message body and clamped to 60 characters.

Collision handling: if the channel already has a topic with the
derived name (case-insensitive), that existing topic is returned so
sibling quotes pile up in one place.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from zerver.models import Message, Stream

logger = logging.getLogger("zulip.cashtime_quote_to_topic")

TOPIC_WORD_COUNT = 10
TOPIC_MAX_LENGTH = 60

WEB_CLIENT_NAMES = {
    "website",
    "ZulipDesktop",
    "ZulipElectron",
    "internal",
}


def _is_web_client(client_name: str) -> bool:
    return client_name in WEB_CLIENT_NAMES or client_name.startswith("website")


# Matches a Zulip quoted reply header at the start of the message body.
# Tolerates:
#   * Any number of backticks (>= 3) in the quote fence — Zulip widens
#     the fence when the quoted body itself contains a quote block, so
#     we see ```quote, ````quote, `````quote, etc., depending on depth.
#   * Any localised verb in square brackets — Zulip translates "said"
#     for the client's locale (EN [said], UK [сказав], DE [schrieb], …).
# Examples (both raw and HTML-escape-free):
#   @_**Дмитрий Радионов|7** [said](https://chat.cashtimepay.com/#narrow/.../near/123):\n```quote\n
#   @_**Eugene_Art|13** [said](https://example.com/.../near/3126):\n````quote\n
#   @**Александр|3** [сказав](https://example.com/.../near/12345):\n```quote\n
_QUOTE_HEADER_RE = re.compile(
    r"^@_?\*\*[^*|]+(?:\|\d+)?\*\*\s+\[[^\]]+\]\(([^)]+)\):\s*\n`{3,}quote\n",
    re.MULTILINE,
)

# Pulls the quoted message id out of the /near/<id> segment of the URL.
_NEAR_ID_RE = re.compile(r"/near/(\d+)")


def _extract_quoted_message_id(content: str) -> Optional[int]:
    header = _QUOTE_HEADER_RE.match(content)
    if header is None:
        return None
    said_url = header.group(1)
    near = _NEAR_ID_RE.search(said_url)
    if near is None:
        return None
    try:
        return int(near.group(1))
    except ValueError:
        return None


# --- text cleaning (mirrors web/src/quote_to_new_topic.ts) -------------------

# Strip a leading Zulip quote-reply prefix from a message body: the
# "@_**user|id** [said](url):" header and the entire ```quote ... ```
# block that follows (with matched backtick width).
_LEADING_QUOTE_BLOCK_RE = re.compile(
    r"^@_?\*\*[^*|]+(?:\|\d+)?\*\*\s+\[[^\]]+\]\([^)]+\):\s*\n"
    r"(`{3,})quote\n[\s\S]*?\n\1\s*\n?",
    re.MULTILINE,
)

_FENCED_CODE_RE = re.compile(r"```[\s\S]*?```")
_INLINE_CODE_RE = re.compile(r"`[^`]*`")
_BLOCKQUOTE_RE = re.compile(r"^[ \t]*>+[ \t]?", re.MULTILINE)
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_URL_RE = re.compile(r"https?://\S+")
_MENTION_BOLD_RE = re.compile(r"@_?\*\*[^*|]+(?:\|\d+)?\*\*")
_MENTION_GROUP_RE = re.compile(r"@\*[^*]+\*")
_EMOJI_SHORT_RE = re.compile(r":[a-z0-9_+\-]+:", re.IGNORECASE)
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC_UNDERSCORE_BOLD_RE = re.compile(r"__([^_]+)__")
_ITALIC_ASTERISK_RE = re.compile(r"(^|[^\w*])\*([^*\n]+)\*(?!\w)")
_ITALIC_UNDERSCORE_RE = re.compile(r"(^|[^\w_])_([^_\n]+)_(?!\w)")
_STRIKETHROUGH_RE = re.compile(r"~~([^~]+)~~")
_HEADING_RE = re.compile(r"^[ \t]*#+\s*", re.MULTILINE)
_BULLET_RE = re.compile(r"^[ \t]*[*\-+]\s+", re.MULTILINE)
_ORDERED_RE = re.compile(r"^[ \t]*\d+\.\s+", re.MULTILINE)
_UNICODE_EMOJI_RE = re.compile(
    "["
    "\U0001f000-\U0001ffff"
    "☀-➿"
    "\U0001f300-\U0001f9ff"
    "]",
    re.UNICODE,
)
_WHITESPACE_RE = re.compile(r"\s+")


def clean_for_topic_name(raw: str) -> str:
    """Return the first :data:`TOPIC_WORD_COUNT` words of *raw* with
    Zulip/markdown noise stripped, clamped to :data:`TOPIC_MAX_LENGTH`
    characters on a word boundary.

    Mirrors the TypeScript helper :func:`clean_for_topic_name` in
    ``web/src/quote_to_new_topic.ts`` so the web and mobile paths
    derive identical topic names from identical inputs.
    """
    s = raw
    # If the source message itself starts with a quoted-reply block,
    # drop that block first so we name the new topic after the
    # quoter's own words, not after the inner quote's author.
    s = _LEADING_QUOTE_BLOCK_RE.sub("", s, count=1)
    s = _FENCED_CODE_RE.sub(" ", s)
    s = _INLINE_CODE_RE.sub(" ", s)
    s = _BLOCKQUOTE_RE.sub(" ", s)
    s = _IMAGE_RE.sub(r"\1", s)
    s = _LINK_RE.sub(r"\1", s)
    s = _URL_RE.sub(" ", s)
    s = _MENTION_BOLD_RE.sub(" ", s)
    s = _MENTION_GROUP_RE.sub(" ", s)
    s = _EMOJI_SHORT_RE.sub(" ", s)
    s = _BOLD_RE.sub(r"\1", s)
    s = _ITALIC_UNDERSCORE_BOLD_RE.sub(r"\1", s)
    s = _ITALIC_ASTERISK_RE.sub(r"\1\2", s)
    s = _ITALIC_UNDERSCORE_RE.sub(r"\1\2", s)
    s = _STRIKETHROUGH_RE.sub(r"\1", s)
    s = _HEADING_RE.sub(" ", s)
    s = _BULLET_RE.sub(" ", s)
    s = _ORDERED_RE.sub(" ", s)
    s = _UNICODE_EMOJI_RE.sub(" ", s)
    s = _WHITESPACE_RE.sub(" ", s).strip()

    if not s:
        return "untitled"

    words = s.split(" ")[:TOPIC_WORD_COUNT]
    topic = " ".join(words)

    if len(topic) > TOPIC_MAX_LENGTH:
        topic = topic[:TOPIC_MAX_LENGTH]
        last_space = topic.rfind(" ")
        if last_space > 0:
            topic = topic[:last_space]

    return topic


def _strip_inner_quote_block(content: str) -> str:
    """Remove the leading ```quote ... ``` block from the message body so
    only the quoter's own text (if any) remains. Used when we want to
    consider the user's own reply text for topic naming — but in our
    case we always take the *quoted* message body, so this is unused
    and kept for future use."""
    return _FENCED_CODE_RE.sub("", content, count=1)


def _resolve_topic_collision(stream: Stream, candidate: str) -> str:
    """If the channel already has a topic with the same case-insensitive
    name, return that existing topic so messages pile up consistently;
    otherwise return *candidate* unchanged."""
    existing = (
        Message.objects.filter(
            recipient_id=stream.recipient_id,
            subject__iexact=candidate,
        )
        .order_by("-id")
        .values_list("subject", flat=True)
        .first()
    )
    return existing if existing is not None else candidate


def _maybe_topic_from_quote_inner(
    client_name: str,
    stream: Stream,
    current_topic: str,
    content: str,
) -> Optional[str]:
    if _is_web_client(client_name):
        logger.info("cashtime-quote: skip web client=%s", client_name)
        return None

    quoted_id = _extract_quoted_message_id(content)
    if quoted_id is None:
        logger.info(
            "cashtime-quote: no quote header for client=%s content_head=%r",
            client_name,
            content[:200],
        )
        return None

    try:
        quoted = Message.objects.only("content", "recipient_id").get(id=quoted_id)
    except Message.DoesNotExist:
        logger.info(
            "cashtime-quote: quoted message id=%s not found (client=%s)",
            quoted_id,
            client_name,
        )
        return None

    if quoted.recipient_id != stream.recipient_id:
        logger.info(
            "cashtime-quote: quoted message id=%s lives in recipient=%s, not stream=%s",
            quoted_id,
            quoted.recipient_id,
            stream.recipient_id,
        )
        return None

    candidate = clean_for_topic_name(quoted.content)
    resolved = _resolve_topic_collision(stream, candidate)

    if resolved.casefold() == (current_topic or "").casefold():
        logger.info(
            "cashtime-quote: resolved topic %r equals current %r — skip",
            resolved,
            current_topic,
        )
        return None

    logger.info(
        "cashtime-quote: REDIRECT client=%s quoted_id=%s old_topic=%r new_topic=%r",
        client_name,
        quoted_id,
        current_topic,
        resolved,
    )
    return resolved


def maybe_topic_from_quote(
    client_name: str,
    stream: Stream,
    current_topic: str,
    content: str,
) -> Optional[str]:
    """Return a new topic name for the message, or ``None`` to keep
    *current_topic*. Never raises — any internal failure is logged and
    we fall back to the original topic to avoid breaking message send.

    Conditions under which a redirect happens:
    * Client is not a known web/desktop client (mobile, integration, etc.).
    * Message body starts with a Zulip quoted-reply header.
    * The quoted message exists in the same channel.
    * The derived topic name differs (case-insensitively) from the
      current topic — otherwise there is nothing to redirect.
    """
    try:
        return _maybe_topic_from_quote_inner(client_name, stream, current_topic, content)
    except Exception:
        logger.exception(
            "cashtime-quote: unexpected failure (client=%s) — keeping original topic",
            client_name,
        )
        return None

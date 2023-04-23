from typing import Optional
from anki.utils import strip_html

from .types import *
from .fetch import DictionaryFetcher, parse_entry_url


def parse_ref(raw_ref: str) -> Optional[EntryRef]:
    try:
        id_parts = raw_ref.split("#", maxsplit=1)
        defn = None if len(id_parts) < 2 else id_parts[1]
        return EntryRef(int(id_parts[0]), defn)
    except (ValueError, IndexError):
        return None


def ref_to_string(ref: EntryRef) -> str:
    if ref.definition is None:
        return str(ref.id)
    else:
        return f"{ref.id}#{ref.definition}"


def parse_any_ref(fetcher: DictionaryFetcher, raw_ref: str) -> list[EntryRef]:
    raw_ref = strip_html(raw_ref).strip()
    if raw_ref == "":
        return []

    maybe_ref = parse_ref(raw_ref)
    if maybe_ref is not None:
        return [maybe_ref]

    maybe_ref = parse_entry_url(raw_ref)
    if maybe_ref is not None:
        return [maybe_ref]

    # Hack: we replace spaces with hyphens in the formatter, so replace them back here.
    raw_word = raw_ref.replace("-", " ")
    words = fetcher.lookup_word(raw_word)
    if len(words) > 0:
        return words

    translits = fetcher.lookup_pronounciation(raw_word)
    if len(translits) > 0:
        return [EntryRef(translit) for translit in translits]

    return []

from copy import copy
import re
import logging
from itertools import islice
from typing import Any, Generator, Optional
from urllib.parse import urljoin
import hashlib
import json
import sqlite3
import unicodedata
import requests
from bs4 import BeautifulSoup, NavigableString, Tag

from .utils import norecurse
from .types import *


logger = logging.getLogger(__name__)

BASE_URL = "http://www.thai-language.com"


class EntryNotFound(Exception):
    pass


def _find_single(tag: Tag, *args, **kwargs) -> Optional[Tag]:
    rs = tag.find_all( *args, **kwargs)
    if len(rs) == 0:
        return None
    elif len(rs) == 1:
        return rs[0]
    else:
        raise RuntimeError("More than one tag found")


def _get_single(tag: Tag, *args, **kwargs) -> Tag:
    r = _find_single(tag, *args, **kwargs)
    if r is None:
        raise RuntimeError("Failed to find a tag")
    return r


def _get_first(tag: Tag, *args, **kwargs) -> Tag:
    r = tag.find(*args, **kwargs)
    if r is None:
        raise RuntimeError("Failed to find a tag")
    assert isinstance(r, Tag)
    return r


_ENTRY_URL_REGEX = re.compile(r"(?:(?:(?:https?://)?(?:www\.)?thai-language\.com)?/id/(?P<id>[0-9]+))?(?:#def(?P<def>[0-9]+[^?]*))?")

def parse_entry_url(url: str, self_id: Optional[EntryId] = None) -> Optional[EntryRef]:
    m = _ENTRY_URL_REGEX.fullmatch(url)
    if m is None:
        return None
    else:
        if m["id"] is None:
            if self_id is None:
                return None
            id = self_id
        else:
            id = int(m["id"])
        return EntryRef(
            id=id,
            definition=m["def"],
        )

def build_entry_url(ref: EntryRef) -> str:
    url = f"http://thai-language.com/id/{ref.id}"
    if ref.definition is None or ref.definition == DEFAULT_DEFINITION:
        return url
    else:
        return url + f"#def{ref.definition}"


def _parse_entry_header(entry_tag: Tag):
    # Get the first table.
    header = entry_tag.find("table", width="100%", recursive=False)
    if not isinstance(header, Tag):
        raise RuntimeError("Header not found")
    try:
        # Sometimes there might be several spellings. In this case, pick the first one.
        # For example: http://thai-language.com/id/131401
        word_tag =  _get_first(header, "span", class_="th3")
        if word_tag is None:
            raise RuntimeError("Failed to find a tag")
        sound_tag = _find_single(header, "img", src="/img/speaker.gif")
        if sound_tag is None:
            sound_rel = None
        else:
            assert isinstance(sound_tag.parent, Tag)
            sound_rel = sound_tag.parent.attrs["href"]
        return {
            "entry": word_tag.text,
            "sound_url": sound_rel,
        }
    except Exception as e:
        raise RuntimeError(f"Failed to parse entry header") from e


def _is_pronounciation_table(tag: Tag):
    if tag.name != "table":
        return False
    t = tag.find("td")
    if t is None:
        return False
    return t.text == "pronunciation guide"


def _parse_entry_pronounciations(entry_tag: Tag):
    pronounciations_tag = _get_single(entry_tag, _is_pronounciation_table, recursive=False)
    try:
        pronounciations: dict[str, str] = {}
        for pr in islice(pronounciations_tag.children, 1, None):
            if not isinstance(pr, Tag):
                continue
            cols = pr.find_all("td")
            pronounciation_type = cols[0].text
            pronounciation_value =cols[1].text
            pronounciations[pronounciation_type] = pronounciation_value
        return {
            "pronounciations": pronounciations,
        }
    except Exception as e:
        raise RuntimeError(f"Failed to parse pronounciations: {pronounciations_tag}") from e


def _is_definitions_table(tag: Tag):
    if tag.name != "table":
        return False
    # We look for the horizontal line, which is implemented as a row with
    # back background color and a single cell.
    separator_rows = tag.find_all("tr", style=re.compile("background-color: *black"))
    if len(separator_rows) > 1:
        raise RuntimeError("Unexpected several separator rows")
    return len(separator_rows) > 0


def _is_definitions_separator_row(tag: Tag):
    return tag.get_text() == ""


_CLASSES_REGEX = re.compile(r"\[([a-zA-Z0-9-, ]*)\]")


_DEFINITION_ID_REGEX = re.compile(r"def([0-9]+.*)")


def _parse_definition_header(tag: Tag, id: EntryId, entry_word: str):
    cells = tag.find_all("td", recursive=False)
    if len(cells) != 1:
        # No header found.
        return None
    cell = cells[0]

    id_tag = _find_single(cell, "a", attrs={"class": "ord", "name": _DEFINITION_ID_REGEX})
    def_id: DefinitionId
    if id_tag is None:
        def_id = DEFAULT_DEFINITION
    else:
        def_id_match = _DEFINITION_ID_REGEX.fullmatch(id_tag.attrs["name"])
        assert def_id_match is not None
        def_id = def_id_match[1]

    classes_tag = _find_single(cell, "span", style="font-size:x-small")
    if classes_tag is None:
        classes: list[str] = []
    else:
        m = _CLASSES_REGEX.fullmatch(classes_tag.text)
        if m is None:
            raise RuntimeError(f"Invalid definition header text: {classes_tag.text}")
        classes = m[1].split(", ")
    common_tag = _find_single(cell, "img", alt="common Thai word")

    # Sometimes there might be several spellings. In this case, pick the first one.
    # For example: http://thai-language.com/id/131401
    super_entry_tag = cell.find("span", class_="th2")
    super_entry = None
    if super_entry_tag is not None:
        super_text = super_entry_tag.text
        if super_text != entry_word:
            super_entry = super_text

    ret = {
        "id": def_id,
        "classes": classes,
        "super_entry": super_entry,
        "is_common": common_tag is not None,
    }
    if super_entry_tag is not None:
        components: ComponentsList = []
        for child in super_entry_tag.children:
            if isinstance(child, NavigableString) and child.text == entry_word:
                components.append(SELF_REFERENCE)
            elif isinstance(child, Tag) and child.name == "a" and (ref := parse_entry_url(child.attrs["href"], id)) is not None:
                components.append(ref)
            else:
                raise RuntimeError(f"Unknown super entry child: {child}")
        ret["components"] = components

    return ret


def _parse_definition_field(rows: list[list[Tag]]):
    if len(rows) != 1:
        raise RuntimeError(f"Invalid rows count in a definition: {len(rows)}")
    if len(rows[0]) != 2:
        raise RuntimeError(f"Invalid cells count in a definition: {len(rows[0])}")
    definition = rows[0][1].text
    return {
        "definition": definition,
    }


def _parse_image_field(rows: list[list[Tag]]):
    if len(rows) != 1:
        raise RuntimeError(f"Invalid rows count in an image: {len(rows)}")
    if len(rows[0]) != 2:
        raise RuntimeError(f"Invalid cells count in an image: {len(rows[0])}")
    img_rel = _get_single(rows[0][1], "img").attrs["src"]
    return {
        "image_url": img_rel,
    }


def _parse_categories_field(rows: list[list[Tag]]):
    cats: list[list[str]] = []
    for row in rows:
        categories = _get_single(row[-1], "a", class_="hy").text.split(" » ")
        cats.append(categories)
    return {
        "categories": cats,
    }


def _is_entry_link(tag: Tag):
    # "ttid" hints at a subcomponent; ignore these.
    if tag.name != "a" or "href" not in tag.attrs or "ttid" in tag.attrs:
        return False
    m = _ENTRY_URL_REGEX.fullmatch(tag.attrs["href"])
    return m is not None


def _parse_entry_list_field(rows: list[list[Tag]], id: EntryId) -> list[EntryRef]:
    components: list[EntryRef] = []
    for row in rows:
        link_tag = None
        for cell in row:
            link_tag = _find_single(cell, _is_entry_link)
            if link_tag is not None:
                break
        if link_tag is None:
            raise RuntimeError("Failed to fink an entry link in rows")
        ref = parse_entry_url(link_tag.attrs["href"], id)
        if ref is None:
            raise RuntimeError("Failed to parse an entry URL: {link}")
        components.append(ref)

    return components


def _parse_definitions_table(table: Tag, id: EntryId, entry_word: str) -> Generator[EntryDefinition, None, None]:
    iterator = iter(table.children)

    current: Optional[Tag] = None
    def next_row():
        nonlocal current
        while row := next(iterator, None):
            if isinstance(row, Tag):
                current = row
                return
        current = None
    next_row()

    while current is not None:
        if _is_definitions_separator_row(current):
            next_row()
            continue

        data: dict[str, Any] = {}
        try:
            header_data = _parse_definition_header(current, id, entry_word)
            if header_data is not None:
                data.update(header_data)
                next_row()
            else:
                # Special notes section; skip
                while current is not None and not _is_definitions_separator_row(current):
                    next_row()
                continue
        except Exception as e:
            raise RuntimeError(f"Failed to parse definition header") from e

        while current is not None and not _is_definitions_separator_row(current):
            header_cells = [child for child in current.children if isinstance(child, Tag)]
            field_header = header_cells[0]
            field_name = field_header.text
            field_rows_count = int(field_header.attrs.get("rowspan", 1))
            field_rows = [header_cells]
            next_row()
            for _ in range(field_rows_count - 1):
                if current is None:
                    break
                field_rows.append([child for child in current.children if isinstance(child, Tag)])
                next_row()

            try:
                if field_name == "definition":
                    data.update(_parse_definition_field(field_rows))
                elif field_name == "categories":
                    data.update(_parse_categories_field(field_rows))
                elif field_name == "components":
                    data["components"] = _parse_entry_list_field(field_rows, id)
                elif field_name == "classifier":
                    data["classifiers"] = _parse_entry_list_field(field_rows, id)
                elif field_name == "synonyms":
                    data["synonyms"] = _parse_entry_list_field(field_rows, id)
                elif field_name == "related words":
                    data["related"] = _parse_entry_list_field(field_rows, id)
                elif field_name == "image":
                    data.update(_parse_image_field(field_rows))
                else:
                    logger.debug(f"Unknown field name: '{field_name}'")
            except Exception as e:
                raise RuntimeError(f"Failed to parse field {field_name}") from e

        entry = EntryDefinition(**data)
        yield entry

        next_row()


def _parse_entry_definitions(entry_tag: Tag, id: EntryId, entry_word: str):
    definitions_tag = _get_single(entry_tag, _is_definitions_table, recursive=False)
    try:
        defns = {entry.id: entry for entry in _parse_definitions_table(definitions_tag, id, entry_word)}
        return {
            "definitions": defns,
        }
    except Exception as e:
        raise RuntimeError(f"Failed to parse definitions") from e


REPETITION_CHARACTER = EntryRef(132853)


FETCHER_SETTINGS = {
    "audio": 0,
    "audio_enc": "mp3",
    "streaming": "off",
    "xlitshowmode": 0,
    "xlitsystem": 15, # No default transliteration
    "xs0": "on", # t-i Enhanced
    "xs1": "off", # Phonemic Thai
    "xs2": "off", # IPA
    "xs8": "on", # Paiboon
    "xs3": "off", # RTGS
    "submitted": "save+changes",
    "licensetype": "on",
    "xmp_ena": "on",
    "smp_ena": "on",
    "racycontent": "on",
    "gaycontent": "on",
    "ridcontent": "off", # Royal Institute of Thailand dictionary
}


class DictionaryFetcher:
    _session: requests.Session
    _cache_db: sqlite3.Connection
    _session_initialized = False

    CACHE_VERSION = 4

    def __init__(self, *, cache_database: Optional[str]=None):
        self._session = requests.Session()
        if cache_database is None:
            cache_database = ":memory:"
        self._cache_db = sqlite3.connect(cache_database, isolation_level=None, check_same_thread=False)

        self._cache_db.execute("""
            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY,
                data TEXT NOT NULL
            ) STRICT
        """)
        self._cache_db.execute("""
            CREATE TABLE IF NOT EXISTS redirects (
                id INTEGER PRIMARY KEY,
                entry_id INTEGER NOT NULL,
                FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE
            ) STRICT
        """)
        self._cache_db.execute("""
            CREATE TABLE IF NOT EXISTS media (
                path TEXT PRIMARY KEY,
                data BLOB NOT NULL,
                sha256 TEXT NOT NULL
            ) STRICT
        """)
        self._cache_db.execute("""
            CREATE TABLE IF NOT EXISTS pronounciations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pronounciation TEXT NOT NULL,
                type TEXT NOT NULL,
                entry_id INTEGER NOT NULL,
                definition_id TEXT,
                FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE,
                UNIQUE (entry_id, definition_id, type)
            ) STRICT
        """)
        self._cache_db.execute("""
            CREATE INDEX IF NOT EXISTS pronounciations_idx ON pronounciations (pronounciation)
        """)
        self._cache_db.execute("""
            CREATE TABLE IF NOT EXISTS words (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                word TEXT NOT NULL,
                entry_id INTEGER NOT NULL,
                definition_id TEXT,
                FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE,
                UNIQUE (entry_id, definition_id)
            ) STRICT
        """)

        self._session_initialized = False

    def _ensure_initialized(self):
        if self._session_initialized:
            return
        # Set the settings.
        r = self._session.post(
            urljoin(BASE_URL, "/default.aspx?nav=control"),
            data=FETCHER_SETTINGS,
            allow_redirects=False
        )
        r.raise_for_status()
        self._session_initialized = True

    def _get_super_entry_pronounciations(self, name: str, self_pronounciation: str, components: ComponentsList) -> str:
        pronounciation_parts = []
        for comp in components:
            if comp == SELF_REFERENCE or comp == REPETITION_CHARACTER:
                pronounciation_parts.append(self_pronounciation)
            else:
                comp_entry = self.get_entry(comp.id)
                pronounciation_parts.append(comp_entry.pronounciations[name])
        return " ".join(pronounciation_parts)

    def get_super_entry(self, entry: DictionaryEntry, defn_id: DefinitionId) -> DictionaryEntry:
        try:
            defn = entry.definitions[defn_id]
        except KeyError:
            raise EntryNotFound()
        assert defn.super_entry is not None
        assert defn.components is not None
        new_defn = copy(defn)
        new_defn.super_entry = None
        new_defn.components = [EntryRef(entry.id) if comp == SELF_REFERENCE or comp == REPETITION_CHARACTER else comp for comp in defn.components]
        pronounciations = {name: self._get_super_entry_pronounciations(name, pron, defn.components) for name, pron in entry.pronounciations.items()}
        return DictionaryEntry(
            id=entry.id,
            entry=defn.super_entry,
            pronounciations=pronounciations,
            definitions={defn_id: new_defn},
        )

    @norecurse(getter=lambda _self, entry, defn_id: (entry.id, defn_id))
    def _norecurse_get_super_entry(self, *args, **kwargs):
        return self.get_super_entry(*args, **kwargs)

    def _get_entry(self, id: EntryId) -> DictionaryEntry:
        self._ensure_initialized()
        logger.info(f"Fetching dictionary entry {id}")
        try:
            url = urljoin(urljoin(BASE_URL, "/id/"), str(id))
            r = self._session.get(url)
            if r.status_code == 404:
                raise EntryNotFound(f"Dictionary entry {id} does not exist")
            r.raise_for_status()

            # We are forced to; otherwise, we need to bundle html5lib with Anki.
            soup = BeautifulSoup(r.text, "html.parser")
            entry_tag = _get_single(soup, "div", id="old-content")
            link_tag = _get_single(soup, "link", rel="canonical", href=True)
            real_ref = parse_entry_url(link_tag.attrs["href"])
            if real_ref is None:
                raise RuntimeError("Failed to parse the canonical link")
            data: dict[str, Any] = {
                "id": real_ref.id,
            }
            data.update(_parse_entry_header(entry_tag))
            data.update(_parse_entry_pronounciations(entry_tag))
            data.update(_parse_entry_definitions(entry_tag, real_ref.id, data["entry"]))

            return DictionaryEntry(**data)
        except Exception as e:
            raise RuntimeError(f"Failed to get dictionary entry {id}") from e

    def _cache_entry(self, ref: EntryRef, entry: DictionaryEntry):
        for type, pronounciation in entry.pronounciations.items():
            pronounciation = unicodedata.normalize("NFC", pronounciation)
            try:
                self._cache_db.execute("INSERT INTO pronounciations (pronounciation, type, entry_id, definition_id) VALUES (?, ?, ?, ?)", (pronounciation, type, ref.id, ref.definition))
            except Exception as e:
                logger.warn(f"Failed to add {type} pronounciation {pronounciation} for {ref.id}{f'#{ref.definition}' if ref.definition is not None else ''} into the cache", exc_info=e)

        word = unicodedata.normalize("NFC", entry.entry)
        try:
            self._cache_db.execute("INSERT INTO words (word, entry_id, definition_id) VALUES (?, ?, ?)", (word, ref.id, ref.definition))
        except Exception as e:
            logger.warn(f"Failed to add word {word} for {ref.id}{f'#{ref.definition}' if ref.definition is not None else ''} into the cache", exc_info=e)

    def get_entry(self, id: EntryId) -> DictionaryEntry:
        for real_id, in self._cache_db.execute("SELECT entry_id FROM redirects WHERE id = ?", (id,)):
            return self.get_entry(real_id)
        for raw_entry, in self._cache_db.execute("SELECT data FROM entries WHERE id = ?", (id,)):
            try:
                return DictionaryEntry.from_dict(json.loads(raw_entry))
            except Exception as e:
                logger.warn(f"Failed to fetch entry {id} from the cache", exc_info=e)
                self._cache_db.execute("DELETE FROM entries WHERE id = ?", (id,))

        entry = self._get_entry(id)
        skip_adding = False
        if entry.id != id:
            # Non-canonical ID detected. Check if we have the real record.
            for real_id in self._cache_db.execute("SELECT id FROM entries WHERE id = ?", (entry.id,)):
                skip_adding = True
                break

        if not skip_adding:
            raw_entry = json.dumps(entry.to_dict())
            try:
                self._cache_db.execute("INSERT INTO entries (id, data) VALUES (?, ?)", (entry.id, raw_entry))
            except Exception as e:
                logger.warn(f"Failed to add entry {entry.id} into the cache", exc_info=e)

            self._cache_entry(EntryRef(entry.id), entry)
            for defn_id, defn in entry.definitions.items():
                if defn.super_entry is not None:
                    super_entry = self._norecurse_get_super_entry(entry, defn_id)
                    self._cache_entry(EntryRef(entry.id, defn_id), super_entry)

        if entry.id != id:
            try:
                self._cache_db.execute("INSERT INTO redirects (id, entry_id) VALUES (?, ?)", (id, entry.id))
            except Exception as e:
                logger.warn(f"Failed to add redirect from {id} to {entry.id} into the cache", exc_info=e)

        return entry

    def _get_media_data(self, path: str) -> bytes:
        self._ensure_initialized()
        logger.info(f"Fetching media file {path}")
        try:
            url = urljoin(BASE_URL, path)
            r = self._session.get(url)
            r.raise_for_status()
            hasher = hashlib.sha256()
            hasher.update(r.content)
            return hasher.hexdigest(), r.content
        except Exception as e:
            raise RuntimeError(f"Failed to get media file {path}") from e

    def get_media_data(self, path: str, verify=False) -> bytes:
        has_existing = False
        insert_new = True
        sha256: Optional[str] = None
        data: Optional[bytes] = None

        for existing_sha256, existing_data in self._cache_db.execute("SELECT sha256, data FROM media WHERE path = ?", (path,)):
            has_existing = True
            insert_new = False
            sha256 = existing_sha256
            data = existing_data

        if sha256 is None:
            sha256, data = self._get_media_data(path)

        verified = not verify
        while not verified:
            new_sha256, new_data = self._get_media_data(path)
            verified = sha256 == new_sha256
            insert_new = insert_new or not verified
            sha256, data = new_sha256, new_data

        assert data is not None
        if insert_new:
            try:
                if has_existing:
                    self._cache_db.execute("DELETE FROM media WHERE path = ?", (path,))
                self._cache_db.execute("INSERT INTO media (path, sha256, data) VALUES (?, ?, ?)", (path, sha256, data))
            except Exception as e:
                logger.warn(f"Failed to add media file {path} into the cache", exc_info=e)
        return data

    def lookup_pronounciation(self, pronounciation: str) -> list[EntryId]:
        pronounciation = unicodedata.normalize("NFC", pronounciation.lower())
        # TODO: Implement server-side search.
        return [
            entry_id
            for entry_id,
            in self._cache_db.execute("SELECT entry_id FROM pronounciations WHERE pronounciation = ?", (pronounciation,))
        ]

    def lookup_word(self, word: str, force_serverside=False) -> list[EntryRef]:
        word = unicodedata.normalize("NFC", word.lower())

        if not force_serverside:
            local_ret = [
                EntryRef(entry_id, defn)
                for entry_id, defn
                in self._cache_db.execute("SELECT entry_id, definition_id FROM words WHERE word = ?", (word,))
            ]
            if len(local_ret) > 0:
                return local_ret

        data = {"tmode": 0, "emode": 0, "search": word}
        r = self._session.post(
            urljoin(BASE_URL, "/default.aspx"),
            data=data,
            allow_redirects=False,
        )
        r.raise_for_status()

        if r.status_code != 302:
            return []
        else:
            ref = parse_entry_url(r.headers["Location"])
            if ref is None:
                raise RuntimeError("Unexpected missing Location")
            entry = self.get_entry(ref.id)
            return [EntryRef(entry.id, ref.definition)]

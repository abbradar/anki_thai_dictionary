import os
import logging
import glob
from typing import Any
from dataclasses import dataclass
from functools import cached_property
from gettext import gettext as _
from threading import Lock

from anki.utils import strip_html
from anki.notes import Note
from anki.models import NotetypeId
from anki.collection import Collection
import aqt
from aqt.editor import EditorWebView, Editor
from aqt.qt import QMenu, qconnect
from aqt.operations import QueryOp, CollectionOp, OpChanges

from .thai_language.types import *
from .thai_language.fetch import DictionaryFetcher, EntryNotFound, build_entry_url, parse_entry_url
from .thai_language.note import MediaName, NoteFormatter, WordNote


logger = logging.getLogger(__name__)


class AnkiEntryNotFound(Exception):
    pass


# FIXME: Replace when Anki ships with Python 3.10
# @dataclass(kw_only=True)
@dataclass
class AnkiWordNote(WordNote):
    media_data: dict[MediaName, bytes] = field(default_factory=dict)


@dataclass
class PluginOptions:
    id_field: str = "Id"
    word_field: str = "Word"
    definition_field: str = "Definition"
    extra_field: str = "Extra"
    pronounciation_type: str = "Paiboon"

    @staticmethod
    def from_dict(vals: dict[str, Any]) -> "PluginOptions":
        return PluginOptions(**vals)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


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


def _handle_failure(editor: Editor, e: Exception):
    if isinstance(e, AnkiEntryNotFound):
        aqt.utils.show_warning(
            text=_("Dictionary entry not found"),
            parent=editor.parentWindow,
        )
    else:
        aqt.errors.show_exception(parent=editor.parentWindow, exception=e)
        raise e


class Plugin:
    _config: PluginOptions
    _fetcher_lock: Lock

    def __init__(self):
        raw_cfg = aqt.mw.addonManager.getConfig(__name__)
        if raw_cfg is None:
            self._reset_config()
        else:
            try:
                self._config = PluginOptions.from_dict(raw_cfg)
            except Exception as e:
                aqt.utils.show_warning(f"Failed to load configuration: {e}")
                self._reset_config()
        self._fetcher_lock = Lock()

        aqt.gui_hooks.editor_will_show_context_menu.append(self._on_context_menu)

    @cached_property
    def _fetcher(self):
        user_files = os.path.join(os.path.dirname(__file__), "user_files")
        os.makedirs(user_files, exist_ok=True)
        cache_db_name = f"cache.{DictionaryFetcher.CACHE_VERSION}.db"
        for old_cache_db in glob.glob(os.path.join(user_files, "cache.*.db")):
            if os.path.basename(old_cache_db) != cache_db_name:
                try:
                    os.remove(old_cache_db)
                except OSError as e:
                    logger.warn(f"Failed to remove an old cache database {old_cache_db}", exc_info=e)
        return DictionaryFetcher(cache_database=os.path.join(user_files, cache_db_name))

    def _reset_config(self):
        self._config = PluginOptions()
        aqt.mw.addonManager.writeConfig(__name__, self._config.to_dict())

    def _get_id(self, note: Note) -> str:
        return note[self._config.id_field].strip()

    def _parse_id(self, raw_id: str) -> Optional[EntryRef]:
        strip_id = strip_html(raw_id)

        if raw_id == "":
            return None

        maybe_ref = parse_ref(strip_id)
        if maybe_ref is not None:
            return maybe_ref

        maybe_ref = parse_entry_url(strip_id)
        if maybe_ref is not None:
            return maybe_ref

        maybe_ref = self._fetcher.lookup_word(strip_id)
        if maybe_ref is not None:
            return maybe_ref

        return None

    def _on_context_menu(self, editor_webview: EditorWebView, menu: QMenu):
        editor = editor_webview.editor
        if editor.note is None or self._config.id_field not in editor.note:
            return
        menu.addSection("thai-language.com")

        action = menu.addAction(_("Fill supported fields"))
        qconnect(action.triggered, lambda: self._fetch_and_fill(editor))

        action = menu.addAction(_("Fill this field"))
        action.setEnabled(editor.currentField is not None)
        qconnect(action.triggered, lambda: self._fetch_and_fill_current(editor))

        action = menu.addAction(_("Fill this field in all notes"))
        action.setEnabled(editor.currentField is not None)
        qconnect(action.triggered, lambda: self._fetch_and_fill_current_all(editor))

    def _get_note_by_id(self, col: Collection, raw_id: str) -> Optional[AnkiWordNote]:
        with self._fetcher_lock:
            formatter = NoteFormatter(self._fetcher, pronounciation_type=self._config.pronounciation_type)
            id = self._parse_id(raw_id)
            if id is None:
                return None
            try:
                note = formatter.entry_to_note(id)
                media_data: dict[MediaName, bytes] = {}
                for name, path in note.media.items():
                    if not col.media.have(name):
                        file_data = self._fetcher.get_media_data(path)
                        media_data[name] = file_data
                return AnkiWordNote(**note.__dict__, media_data=media_data)
            except EntryNotFound:
                return None

    def _update_note(self, col: Collection, note: Note, word_note: AnkiWordNote, fields: Optional[set[str]] = None):
        for name, data in word_note.media_data.items():
            new_name = col.media.write_data(name, data)
            assert name == new_name

        note[self._config.id_field] = f'<a href="{build_entry_url(word_note.ref)}">{ref_to_string(word_note.ref)}</a>'
        if self._config.word_field in note and (fields is None or self._config.word_field in fields):
            note[self._config.word_field] = word_note.word
        if self._config.definition_field in note and (fields is None or self._config.definition_field in fields):
            note[self._config.definition_field] = word_note.definition
        if self._config.extra_field in note and (fields is None or self._config.extra_field in fields):
            note[self._config.extra_field] = word_note.extra

    def _get_single_note(self, col: Collection, note: Note) -> AnkiWordNote:
        assert aqt.mw is not None
        mw = aqt.mw

        raw_id = note[self._config.id_field].strip()
        mw.taskman.run_on_main(
            lambda: mw.progress.update(
                label=_("Fetching the dictionary entry"),
                value=0,
                max=2,
            )
        )
        word_note = self._get_note_by_id(col, raw_id)
        if word_note is None:
            raise AnkiEntryNotFound()
        mw.taskman.run_on_main(
            lambda: mw.progress.update(
                label=_("Fetching the media files"),
                value=1,
                max=2,
            )
        )
        return word_note

    def _fill_single_note(self, col: Collection, note: Note, fields: Optional[set[str]] = None) -> OpChanges:
        word_note = self._get_single_note(col, note)
        pos = col.add_custom_undo_entry(_("thai-language.com: Fill note"))
        self._update_note(col, note, word_note, fields)
        col.update_note(note)
        return col.merge_undo_entries(pos)

    def _fill_single_new_note(self, col: Collection, note: Note, fields: Optional[set[str]] = None):
        word_note = self._get_single_note(col, note)
        self._update_note(col, note, word_note, fields)

    def _fill_model_notes(self, col: Collection, model_id: NotetypeId, fields: Optional[set[str]] = None) -> OpChanges:
        assert aqt.mw is not None
        mw = aqt.mw

        mw.taskman.run_on_main(
            lambda: mw.progress.update(
                label=_("Searching for cards"),
            )
        )
        pos = col.add_custom_undo_entry(_("thai-language.com: Fill suitable notes"))
        all_nids = col.models.nids(model_id)
        total = len(all_nids)
        processed_notes = []
        for i, note_id in enumerate(all_nids):
            note = col.get_note(note_id)
            raw_id = note[self._config.id_field].strip()
            mw.taskman.run_on_main(
                lambda: mw.progress.update(
                    label=_("Downloading entry {} of {}").format(i + 1, total),
                    value=2 * i,
                    max=2 * total,
                )
            )
            word_note = self._get_note_by_id(col, raw_id)
            if word_note is not None:
                mw.taskman.run_on_main(
                    lambda: mw.progress.update(
                        label=_("Downloading entry {} of {}").format(i + 1, total),
                        value=2 * i + 1,
                        max=2 * total,
                    )
                )
                self._update_note(col, note, word_note, fields)
                processed_notes.append(note)
        col.update_notes(processed_notes)
        return col.merge_undo_entries(pos)

    def _fetch_and_fill_current(self, editor: Editor):
        if editor.currentField is None:
            return
        assert editor.note is not None
        field = editor.note.keys()[editor.currentField]
        self._fetch_and_fill(editor, set([field]))

    def _fetch_and_fill_current_all(self, editor: Editor):
        if editor.currentField is None:
            return
        assert editor.note is not None
        field = editor.note.keys()[editor.currentField]
        self._fetch_and_fill_all(editor, set([field]))

    def _fetch_and_fill(self, editor: Editor, fields: Optional[set[str]] = None):
        assert editor.note is not None
        note = editor.note
        # Ugh.
        if not editor.addMode:
            cop = CollectionOp(
                parent=editor.parentWindow,
                op=lambda col: self._fill_single_note(col, note, fields),
            )
            cop \
                .failure(lambda e: _handle_failure(editor, e)) \
                .run_in_background()
        else:
            qop = QueryOp(
                parent=editor.parentWindow,
                op=lambda col: self._fill_single_new_note(col, note, fields),
                success=lambda _: editor.loadNoteKeepingFocus(),
            )
            qop \
                .with_progress(_("Filling the dictionary entry")) \
                .failure(lambda e: _handle_failure(editor, e)) \
                .run_in_background()

    def _fetch_and_fill_all(self, editor: Editor, fields: Optional[set[str]] = None):
        if not aqt.utils.askUser(
            text=_("Are you sure you want to perform a mass operation?"),
            parent=editor.parentWindow,
            defaultno=True,
        ):
            return
        assert editor.note is not None
        notetype = editor.note.note_type()
        assert notetype is not None
        model_id: NotetypeId = notetype["id"]
        op = CollectionOp(
            parent=editor.parentWindow,
            op=lambda col: self._fill_model_notes(col, model_id, fields),
        )
        op \
            .failure(lambda e: _handle_failure(editor, e)) \
            .run_in_background()


plugin = Plugin()

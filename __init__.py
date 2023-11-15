import enum
from enum import Enum
import os
import logging
import glob
from typing import Any
from dataclasses import dataclass
from functools import cached_property
from gettext import gettext as _
from threading import Lock

from anki.notes import Note
from anki.models import NotetypeId
from anki.collection import Collection
import aqt
from aqt.editor import EditorWebView, Editor
from aqt.qt import QMenu, qconnect
from aqt.operations import QueryOp, CollectionOp, OpChanges

from .thai_language.types import *
from .thai_language.fetch import DictionaryFetcher, EntryNotFound, build_entry_url
from .thai_language.refs import parse_any_ref, ref_to_string
from .thai_language.note import ClozeNote, MediaName, NoteFormatter, WordNote


logger = logging.getLogger(__name__)


class AnkiEntryNotFound(Exception):
    pass


class NoteType(Enum):
    ENTRY = enum.auto()
    CLOZE = enum.auto()


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
    cloze_ids_field: str = "Ids"
    cloze_text_field: str = "Text"
    cloze_extra_field: str = "Extra"
    pronounciation_type: str = "Paiboon"

    @staticmethod
    def from_dict(vals: dict[str, Any]) -> "PluginOptions":
        return PluginOptions(**vals)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


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

    def _find_note_type(self, note: Note) -> Optional[NoteType]:
        if self._config.id_field in note:
            return NoteType.ENTRY
        elif self._config.cloze_ids_field in note:
            return NoteType.CLOZE
        else:
            return None

    def _get_note_type(self, note: Note) -> NoteType:
        note_type = self._find_note_type(note)
        if note_type is None:
            raise RuntimeError("Unknown note type")
        return note_type

    def _on_context_menu(self, editor_webview: EditorWebView, menu: QMenu):
        editor = editor_webview.editor
        if editor.note is None or self._find_note_type(editor.note) is None:
            return

        menu.addSection("thai-language.com")

        action = menu.addAction(_("Fill supported fields"))
        qconnect(action.triggered, lambda: self._update(editor))

        action = menu.addAction(_("Fill this field"))
        action.setEnabled(editor.currentField is not None)
        qconnect(action.triggered, lambda: self._update_current(editor))

        action = menu.addAction(_("Fill this field in all notes"))
        action.setEnabled(editor.currentField is not None)
        qconnect(action.triggered, lambda: self._update_current_model(editor))

        action = menu.addAction(_("Cache this field"))
        qconnect(action.triggered, lambda: self._fetch(editor))

        action = menu.addAction(_("Cache all notes"))
        qconnect(action.triggered, lambda: self._fetch_model(editor))

        action = menu.addAction(_("Verify the media"))
        qconnect(action.triggered, lambda: self._verify_media(editor))

        action = menu.addAction(_("Verify all the media"))
        qconnect(action.triggered, lambda: self._verify_media_model(editor))

    def _fetch_entry_note(self, col: Collection, note: Note) -> AnkiWordNote:
        raw_id = note[self._config.id_field]

        with self._fetcher_lock:
            formatter = NoteFormatter(self._fetcher, pronounciation_type=self._config.pronounciation_type)
            refs = parse_any_ref(self._fetcher, raw_id)
            if len(refs) == 0:
                raise AnkiEntryNotFound()
            try:
                word_note = formatter.entry_to_note(refs[0])
            except EntryNotFound:
                raise AnkiEntryNotFound()

            media_data: dict[MediaName, bytes] = {}
            for name, path in word_note.media.items():
                if not col.media.have(name):
                    file_data = self._fetcher.get_media_data(path)
                    media_data[name] = file_data
            return AnkiWordNote(**word_note.__dict__, media_data=media_data)

    def _update_entry_note(self, col: Collection, note: Note, *, fields: Optional[set[str]] = None, on_fetch: Optional[Callable[[], None]] = None):
        word_note = self._fetch_entry_note(col, note)

        for name, data in word_note.media_data.items():
            new_name = col.media.write_data(name, data)
            assert name == new_name

        if on_fetch:
            on_fetch()

        note[self._config.id_field] = f'<a href="{build_entry_url(word_note.ref)}">{ref_to_string(word_note.ref)}</a>'
        if self._config.word_field in note and (fields is None or self._config.word_field in fields):
            note[self._config.word_field] = word_note.word
        if self._config.definition_field in note and (fields is None or self._config.definition_field in fields):
            note[self._config.definition_field] = word_note.definition
        if self._config.extra_field in note and (fields is None or self._config.extra_field in fields):
            note[self._config.extra_field] = word_note.extra

    def _fetch_cloze_note(self, col: Collection, note: Note) -> ClozeNote:
        id_cloze = note[self._config.cloze_ids_field].strip()

        with self._fetcher_lock:
            formatter = NoteFormatter(self._fetcher, pronounciation_type=self._config.pronounciation_type)
            return formatter.cloze_to_note(id_cloze)

    def _update_cloze_note(self, col: Collection, note: Note, *, fields: Optional[set[str]] = None, on_fetch: Optional[Callable[[], None]] = None):
        cloze_note = self._fetch_cloze_note(col, note)

        if on_fetch:
            on_fetch()

        note[self._config.cloze_ids_field] = cloze_note.inline_ids
        if self._config.cloze_text_field in note and (fields is None or self._config.cloze_text_field in fields):
            note[self._config.cloze_text_field] = cloze_note.cloze
        if self._config.cloze_extra_field in note and (fields is None or self._config.cloze_extra_field in fields):
            note[self._config.cloze_extra_field] = cloze_note.extra

    def _fetch_note(self, col: Collection, note: Note, type: NoteType):
        if type == NoteType.ENTRY:
            self._fetch_entry_note(col, note)
        elif type == NoteType.CLOZE:
            self._fetch_cloze_note(col, note)
        else:
            raise RuntimeError("Impossible NoteType")

    def _update_note(self, col: Collection, note: Note, type: NoteType, *, fields: Optional[set[str]] = None, on_fetch: Optional[Callable[[], None]] = None):
        if type == NoteType.ENTRY:
            self._update_entry_note(col, note, fields=fields, on_fetch=on_fetch)
        elif type == NoteType.CLOZE:
            self._update_cloze_note(col, note, fields=fields, on_fetch=on_fetch)
        else:
            raise RuntimeError("Impossible NoteType")

    def _fetch_single_note_proc(self, col: Collection, note: Note):
        note_type = self._get_note_type(note)
        self._fetch_note(col, note, note_type)

    def _fetch_model_notes_proc(self, col: Collection, model_id: NotetypeId):
        assert aqt.mw is not None
        mw = aqt.mw

        mw.taskman.run_on_main(
            lambda: mw.progress.update(
                label=_("Searching for model notes"),
            )
        )
        all_nids = col.models.nids(model_id)
        total = len(all_nids)
        note_type = None
        for i, note_id in enumerate(all_nids):
            note = col.get_note(note_id)
            if note_type is None:
                note_type = self._get_note_type(note)
            mw.taskman.run_on_main(
                lambda: mw.progress.update(
                    label=_("Fetching note {} of {}").format(i + 1, total),
                    value=i,
                    max=total,
                )
            )
            try:
                self._fetch_note(col, note, note_type)
            except AnkiEntryNotFound:
                pass

    def _update_single_note_proc(self, col: Collection, note: Note, fields: Optional[set[str]] = None) -> OpChanges:
        assert aqt.mw is not None
        mw = aqt.mw

        mw.taskman.run_on_main(
            lambda: mw.progress.update(
                label=_("Updating the note"),
            )
        )
        pos: Optional[int] = None
        def on_fetch():
            nonlocal pos
            pos = col.add_custom_undo_entry(_("thai-language.com: Update the note"))
        self._update_note(col, note, self._get_note_type(note), fields=fields, on_fetch=on_fetch)
        assert pos is not None
        col.update_note(note)
        return col.merge_undo_entries(pos)

    def _update_single_new_note_proc(self, col: Collection, note: Note, fields: Optional[set[str]] = None):
        self._update_note(col, note, self._get_note_type(note), fields=fields)

    def _update_model_notes_proc(self, col: Collection, model_id: NotetypeId, fields: Optional[set[str]] = None) -> OpChanges:
        assert aqt.mw is not None
        mw = aqt.mw

        mw.taskman.run_on_main(
            lambda: mw.progress.update(
                label=_("Searching for model notes"),
            )
        )
        pos = col.add_custom_undo_entry(_("thai-language.com: Update the model notes"))
        all_nids = col.models.nids(model_id)
        total = len(all_nids)
        processed_notes = []
        note_type = None
        for i, note_id in enumerate(all_nids):
            note = col.get_note(note_id)
            if note_type is None:
                note_type = self._get_note_type(note)
            mw.taskman.run_on_main(
                lambda: mw.progress.update(
                    label=_("Updating note {} of {}").format(i + 1, total),
                    value=i,
                    max=total,
                )
            )
            try:
                self._update_note(col, note, note_type, fields=fields)
                processed_notes.append(note)
            except AnkiEntryNotFound:
                pass
        col.update_notes(processed_notes)
        return col.merge_undo_entries(pos)

    def _verify_entry_media(self, col: Collection, note: Note):
        raw_id = note[self._config.id_field]

        with self._fetcher_lock:
            formatter = NoteFormatter(self._fetcher, pronounciation_type=self._config.pronounciation_type)
            refs = parse_any_ref(self._fetcher, raw_id)
            if len(refs) == 0:
                raise AnkiEntryNotFound()
            try:
                word_note = formatter.entry_to_note(refs[0])
            except EntryNotFound:
                raise AnkiEntryNotFound()

            for name, path in word_note.media.items():
                file_data = self._fetcher.get_media_data(path, verify=True)
                if col.media.have(name):
                    col.media.trash_files([name])
                new_name = col.media.write_data(name, file_data)
                assert name == new_name

    def _verify_media_note(self, col: Collection, note: Note, type: NoteType):
        if type == NoteType.ENTRY:
            self._verify_entry_media(col, note)

    def _verify_media_proc(self, col: Collection, note: Note):
        assert aqt.mw is not None
        mw = aqt.mw
        type = self._get_note_type(note)
        self._verify_media_note(col, note, type)

    def _verify_model_media_proc(self, col: Collection, model_id: NotetypeId):
        assert aqt.mw is not None
        mw = aqt.mw

        mw.taskman.run_on_main(
            lambda: mw.progress.update(
                label=_("Searching for model notes"),
            )
        )
        all_nids = col.models.nids(model_id)
        total = len(all_nids)
        note_type = None
        for i, note_id in enumerate(all_nids):
            note = col.get_note(note_id)
            if note_type is None:
                note_type = self._get_note_type(note)
            mw.taskman.run_on_main(
                lambda: mw.progress.update(
                    label=_("Verifying note {} of {}").format(i + 1, total),
                    value=i,
                    max=total,
                )
            )
            try:
                self._verify_media_note(col, note, note_type)
            except AnkiEntryNotFound:
                pass

    def _fetch(self, editor: Editor):
        assert editor.note is not None
        note = editor.note
        qop = QueryOp(
            parent=editor.parentWindow,
            op=lambda col: self._fetch_single_note_proc(col, note),
            success=lambda _: None,
        )
        qop \
            .with_progress(_("Fetching the note")) \
            .failure(lambda e: _handle_failure(editor, e)) \
            .run_in_background()

    def _fetch_model(self, editor: Editor):
        assert editor.note is not None
        notetype = editor.note.note_type()
        assert notetype is not None
        model_id: NotetypeId = notetype["id"]
        qop = QueryOp(
            parent=editor.parentWindow,
            op=lambda col: self._fetch_model_notes_proc(col, model_id),
            success=lambda _: None,
        )
        qop \
            .with_progress(_("Fetching the model notes")) \
            .failure(lambda e: _handle_failure(editor, e)) \
            .run_in_background()

    def _update(self, editor: Editor, fields: Optional[set[str]] = None):
        assert editor.note is not None
        note = editor.note
        # Ugh.
        if not editor.addMode:
            cop = CollectionOp(
                parent=editor.parentWindow,
                op=lambda col: self._update_single_note_proc(col, note, fields),
            )
            cop \
                .failure(lambda e: _handle_failure(editor, e)) \
                .run_in_background()
        else:
            qop = QueryOp(
                parent=editor.parentWindow,
                op=lambda col: self._update_single_new_note_proc(col, note, fields),
                success=lambda _: editor.loadNoteKeepingFocus(),
            )
            qop \
                .with_progress(_("Updating the note")) \
                .failure(lambda e: _handle_failure(editor, e)) \
                .run_in_background()

    def _update_model(self, editor: Editor, fields: Optional[set[str]] = None):
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
            op=lambda col: self._update_model_notes_proc(col, model_id, fields),
        )
        op \
            .failure(lambda e: _handle_failure(editor, e)) \
            .run_in_background()

    def _update_current(self, editor: Editor):
        if editor.currentField is None:
            return
        assert editor.note is not None
        field = editor.note.keys()[editor.currentField]
        self._update(editor, set([field]))

    def _update_current_model(self, editor: Editor):
        if editor.currentField is None:
            return
        assert editor.note is not None
        field = editor.note.keys()[editor.currentField]
        self._update_model(editor, set([field]))

    def _verify_media(self, editor: Editor):
        assert editor.note is not None
        note = editor.note
        qop = QueryOp(
            parent=editor.parentWindow,
            op=lambda col: self._verify_media_proc(col, note),
            success=lambda _: None,
        )
        qop \
            .with_progress(_("Verifying the note media")) \
            .failure(lambda e: _handle_failure(editor, e)) \
            .run_in_background()

    def _verify_media_model(self, editor: Editor):
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
        qop = QueryOp(
            parent=editor.parentWindow,
            op=lambda col: self._verify_model_media_proc(col, model_id),
            success=lambda _: None,
        )
        qop \
            .with_progress(_("Verifying the note media")) \
            .failure(lambda e: _handle_failure(editor, e)) \
            .run_in_background()


plugin = Plugin()

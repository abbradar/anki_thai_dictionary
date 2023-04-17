from collections.abc import Generator, Iterable
from dataclasses import dataclass
import html
from copy import copy

from .types import *
from .fetch import DictionaryFetcher, EntryNotFound


def escape_quoted_search(s: str) -> str:
    return s \
        .replace('\\', '\\\\') \
        .replace('"', '\\"') \
        .replace('*', '\\*') \
        .replace('_', '\\_')


def join_nonempty_strings(strs: Iterable[str], sep: str = "<br><br>"):
    return sep.join((str for str in strs if str != ""))


MediaPath = str
MediaName = str


@dataclass(kw_only=True)
class WordNote:
    ref: EntryRef
    word: str
    definition: str
    extra: str = ""
    media: dict[MediaName, MediaPath] = field(default_factory=dict)


@dataclass
@dataclass(kw_only=True)
class WordComponent:
    id: EntryId
    definition: DefinitionId
    entry: DictionaryEntry
    level: int = 0


class NoteFormatter:
    _fetcher: DictionaryFetcher
    _pronounciation_type: str
    _media: dict[str, str]

    def __init__(
            self,
            fetcher: DictionaryFetcher,
            *,
            pronounciation_type: Optional[str] = None,
    ):
        if pronounciation_type is None:
            pronounciation_type = "Paiboon"
        self._fetcher = fetcher
        self._pronounciation_type = pronounciation_type
        self._media = {}

    @property
    def pronounciation_type(self):
        return self._pronounciation_type

    @property
    def fetcher(self):
        return self._fetcher

    def use_media(self, path: MediaPath) -> MediaName:
        name = path.replace("/", "_")
        if name in self._media:
            assert self._media[name] == path
        else:
            self._media[name] = path
        return name

    def is_suitable_definition(self, _entry: DictionaryEntry, defn: EntryDefinition):
        if defn.super_entry is not None:
            return False
        if "The English Alphabet" in (c for cat in defn.categories for c in cat):
            return False
        return True

    def suitable_definitions(self, entry: DictionaryEntry) -> list[DefinitionId]:
        defns: list[DefinitionId] = [id for id, defn in entry.definitions.items() if self.is_suitable_definition(entry, defn)]
        if len(defns) == 0:
            raise RuntimeError("No suitable definitions found")
        return defns

    def format_word(self, entry: DictionaryEntry) -> str:
        return html.escape(entry.pronounciations[self.pronounciation_type].replace(" ", "-"))

    def format_word_field(self, entry: DictionaryEntry) -> str:
        word_str = self.format_word(entry)
        if entry.sound_url is not None:
            sound_file = self.use_media(entry.sound_url)
            word_str += f' [sound:{sound_file}]'
        return word_str

    def format_definition_field(self, entry: DictionaryEntry) -> str:
        defn_strs = []
        for id in self.suitable_definitions(entry):
            defn_str = self.format_definition(entry, id)
            # if defn.image_url is not None:
            #     image_file = self.use_media(defn.image_url)
            #     defn_str += f'<img src="{image_file}">'
            defn_strs.append(defn_str)
        return "<br>".join(defn_strs)

    def format_definition(self, entry: DictionaryEntry, defn: Optional[DefinitionId] = None):
        if defn is None:
            defn = entry.first_definition
        return html.escape(entry.definitions[defn].definition)

    def format_component(self, component: WordComponent) -> str:
        component_word = self.format_word(component.entry)
        component_defn = self.format_definition(component.entry, component.definition)
        nbsps = (2 * component.level) * "&nbsp;"
        return f"{nbsps}{component_word}: {component_defn}"

    def build_components(self, entry: DictionaryEntry, *, visited: Optional[set[EntryRef]] = None, level=0) -> Generator[WordComponent, None, None]:
        components = (defn for defn in entry.definitions.values() if defn.components is not None and defn.super_entry is None)
        try:
            comp_defn = next(components)
        except StopIteration:
            return
        assert comp_defn.components is not None
        if visited is None:
            visited = set()
        for rel_component in comp_defn.components:
            if rel_component == SELF_REFERENCE or rel_component.id == entry.id:
                continue
            component = self.fetcher.get_entry(rel_component.id)

            defn = rel_component.definition
            if defn is None:
                defn = component.first_definition
            comp_ref = EntryRef(component.id, defn)

            if comp_ref in visited:
                continue

            if component.definitions[defn].super_entry is not None:
                comp_entry = self._fetcher.get_super_entry(component, defn)
            else:
                comp_entry = component

            yield WordComponent(
                id=component.id,
                definition=defn,
                entry=comp_entry,
                level=level,
            )
            visited.add(comp_ref)
            yield from self.build_components(comp_entry, visited=visited, level=level + 1)

    def format_extra_field(self, entry: DictionaryEntry) -> str:
        components = list(self.build_components(entry))
        try:
            classifier_ref: Optional[EntryRef] = next((defn.classifiers[0] for defn in entry.definitions.values() if defn.classifiers is not None and len(defn.classifiers) > 0))
        except StopIteration:
            classifier_ref = None
        if classifier_ref is None:
            classifier_str = ""
        else:
            classifier_entry = self.fetcher.get_entry(classifier_ref.id)
            classifier_word = self.format_word(classifier_entry)
            classifier_defn = self.format_definition(classifier_entry, classifier_ref.definition)
            classifier_str = f"Classifier: {classifier_word}"
            if classifier_defn.startswith("["):
                classifier_str += f" {classifier_defn}"
            else:
                classifier_str += f" - {classifier_defn}"
        components_str = "<br>".join(map(self.format_component, components))
        extra_str = join_nonempty_strings([classifier_str, components_str])
        return extra_str

    def get_super_entry_pronounciations(self, name: str, self_pronounciation: str, components: list[Union[EntryRef, Literal["self"]]]) -> str:
        pronounciation_parts = []
        for comp in components:
            if comp == SELF_REFERENCE:
                pronounciation_parts.append(self_pronounciation)
            else:
                comp_entry = self.fetcher.get_entry(comp.id)
                pronounciation_parts.append(comp_entry.pronounciations[name])
        return " ".join(pronounciation_parts)

    def entry_to_note(self, ref: EntryRef) -> WordNote:
        entry = self.fetcher.get_entry(ref.id)
        new_ref = EntryRef(entry.id, ref.definition)

        if ref.definition is None:
            return self._entry_to_note(new_ref, entry)
        else:
            # Build a virtual definition.
            try:
                defn = entry.definitions[ref.definition]
            except KeyError:
                raise EntryNotFound()
            if defn.super_entry is None:
                new_entry = copy(entry)
                new_entry.definitions = {id: defn for id, defn in entry.definitions.items() if id == ref.definition}
            else:
                new_entry = self._fetcher.get_super_entry(entry, ref.definition)
            return self._entry_to_note(new_ref, new_entry)


    def _entry_to_note(self, ref: EntryRef, entry: DictionaryEntry) -> WordNote:
        self._media.clear()
        word_str = self.format_word_field(entry)
        definition_str = self.format_definition_field(entry)
        extra_str = self.format_extra_field(entry)
        media = copy(self._media)
        self._media.clear()

        return WordNote(
            ref=ref,
            word=word_str,
            definition=definition_str,
            extra=extra_str,
            media=media,
        )

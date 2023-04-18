from collections.abc import Callable
import dataclasses
from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Union


DEFAULT_DEFINITION: Literal["#"] = "#"


EntryId = int
DefinitionId = Union[str, Literal["#"]]


# We implement (de-)serialization machinery by ourselves; better than bundling marshmallow...
def _adjust_dict(f: Callable[[Any], Any], d: dict[str, Any], name: str):
    d[name] = f(d.get(name))


@dataclass(frozen=True)
class EntryRef:
    id: EntryId
    definition: Optional[DefinitionId] = None

    @staticmethod
    def from_dict(vals: dict[str, Any]) -> "EntryRef":
        return EntryRef(**vals)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


SELF_REFERENCE: Literal["self"] = "self"


def _parse_related_entries(raw: Optional[list[dict[str, Any]]]) -> Optional[list[EntryRef]]:
    if raw:
        return [EntryRef.from_dict(r) for r in raw]
    else:
        return None


ComponentsList = list[Union[EntryRef, Literal["self"]]]

def _parse_components(raw: Optional[list[Union[dict[str, Any], Literal["self"]]]]) -> Optional[ComponentsList]:
    if raw:
        return [SELF_REFERENCE if r == SELF_REFERENCE else EntryRef.from_dict(r) for r in raw]
    else:
        return None


# FIXME: Replace when Anki ships with Python 3.10
# @dataclass(kw_only=True)
@dataclass
class EntryDefinition:
    id: DefinitionId
    definition: str
    classes: list[str]
    super_entry: Optional[str] = None
    is_common: bool = False
    categories: list[list[str]] = field(default_factory=list)
    components: Optional[ComponentsList] = None
    classifiers: Optional[list[EntryRef]] = None
    related: Optional[list[EntryRef]] = None
    synonyms: Optional[list[EntryRef]] = None
    image_url: Optional[str] = None

    @staticmethod
    def from_dict(vals: dict[str, Any]) -> "EntryDefinition":
        nvals = vals.copy()
        _adjust_dict(_parse_components, nvals, "components")
        _adjust_dict(_parse_related_entries, nvals, "classifiers")
        _adjust_dict(_parse_related_entries, nvals, "related")
        _adjust_dict(_parse_related_entries, nvals, "synonyms")
        return EntryDefinition(**nvals)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


# FIXME: Replace when Anki ships with Python 3.10
# @dataclass(kw_only=True)
@dataclass
class DictionaryEntry:
    id: EntryId
    entry: str
    pronounciations: dict[str, str]
    definitions: dict[DefinitionId, EntryDefinition]
    sound_url: Optional[str] = None

    @staticmethod
    def from_dict(vals: dict[str, Any]) -> "DictionaryEntry":
        nvals = vals.copy()
        _adjust_dict(lambda ds: {(d := EntryDefinition.from_dict(rd)).id: d for rd in ds}, nvals, "definitions")
        return DictionaryEntry(**nvals)

    def to_dict(self) -> dict[str, Any]:
        ret = dataclasses.asdict(self)
        _adjust_dict(lambda ds: list(ds.values()), ret, "definitions")
        return ret

    @property
    def first_definition(self):
       return next(iter(self.definitions.keys()))

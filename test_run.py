#!/usr/bin/env python3

from thai_dictionary.types import *
from thai_dictionary.fetch import DictionaryFetcher
from thai_dictionary.note import NoteFormatter

fetcher = DictionaryFetcher()
formatter = NoteFormatter(fetcher)

# note = formatter.entry_to_note(EntryRef(199573))
# print(note)

# note = formatter.entry_to_note(EntryRef(200355))
# print(note)

# note = formatter.entry_to_note(EntryRef(131302))
# print(note)

# note = formatter.entry_to_note(EntryRef(209741))
# print(note)

# note = formatter.entry_to_note(EntryRef(204841, DEFAULT_DEFINITION))
# print(note)

#note = formatter.entry_to_note(EntryRef(199578))
#print(note)

#note = formatter.entry_to_note(EntryRef(131401))
#print(note)

#note = formatter.entry_to_note(EntryRef(131197))
#print(note)

#note = formatter.entry_to_note(EntryRef(135445))
#print(note)

#note = formatter.entry_to_note(EntryRef(139146, DEFAULT_DEFINITION))
#print(note)

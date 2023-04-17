## thai-dictionary

### WARNING

I have tried contacting thai-dictionary.com owners and ask their opinion of publishing this, but have received no reply. While I believe that scrapping dictionary entries for building *personal* decks falls under fair use, I'm not a lawyer.

### Manual

This add-on requires that your model has these fields:
* "Id": required, used for linking notes to their dictionary entries;
* "Word": optional, the word. Currently filled with Paiboon transliteration, but this can be changed by editing the configuration or the source code a bit (see below);
* "Definition": optional, the definition of the word;
* "Extra": optional, for classifiers and components.

The names of the fields and the used transliteration may be changed in the configuration file.

After this add-on has been installed, the following new context menu options appear in the Anki editor:
* "Fill supported fields": Fill all supported fields of the note, replacing the old values;
* "Fill this field": Fill the currently selected field;
* "Fill this field in all notes": FIll the currently selected field *in all notes with the same type*.

"Id" field must be filled before using any of the new options. It supports the following formats:
* Links, including links with anchors: `http://www.thai-language.com/id/131210#def5`;
* Entry IDs: Either `131210`, `131210#5` for definition 5, or `131210##` for the default definition.

### Customization

To change the notes formatting or add/remove details you can edit or derive from the [`NoteFormatter` class](https://github.com/abbradar/anki_thai_dictionary/blob/master/thai_dictionary/note.py). It contains a multitude of methods for formatting various parts of a note, which may be overridden.

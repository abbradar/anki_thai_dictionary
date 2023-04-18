{ nixpkgs ? import <nixpkgs> {} }:

let
  python = (nixpkgs.pythonInterpreters.override {
    pythonPackagesExtensions = [
      (final: prev: {
        anki = final.toPythonModule (nixpkgs.anki.override { python3 = final.python; });
	types-beautifulsoup4 = final.callPackage ./nix/types-beautifulsoup4.nix {};
	types-html5lib = final.callPackage ./nix/types-html5lib.nix {};
      })
    ];
  }).python310;

in python.pkgs.buildPythonPackage {
  name = "anki_thai_language";

  propagatedBuildInputs = with python.pkgs; [
    anki
    types-requests
    types-beautifulsoup4
  ];

  nativeBuildInputs = [ python.pkgs.mypy nixpkgs.sqlite-interactive ];
}

{ lib
, buildPythonPackage
, fetchPypi
, types-html5lib
}:

buildPythonPackage rec {
  pname = "types-beautifulsoup4";
  version = "4.12.0.2";
  format = "setuptools";

  src = fetchPypi {
    inherit pname version;
    hash = "sha256-TWibxYGvaDKrfEfWfenUh164wj9t6k0cnvstKJmcfOk=";
  };

  propagatedBuildInputs = [
    types-html5lib
  ];

  # Module doesn't have tests
  doCheck = false;

  pythonImportsCheck = [
    "bs4-stubs"
  ];

  meta = with lib; {
    description = "Typing stubs for beautifulsoup4";
    homepage = "https://github.com/python/typeshed";
    license = licenses.asl20;
  };
}

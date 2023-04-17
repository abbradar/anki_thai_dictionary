{ lib
, buildPythonPackage
, fetchPypi
}:

buildPythonPackage rec {
  pname = "types-html5lib";
  version = "1.1.11.13";
  format = "setuptools";

  src = fetchPypi {
    inherit pname version;
    hash = "sha256-Klztk0vMe0eAC4jdslQbvfW97X+21CFpPJeCKOH+Yx0=";
  };

  # Module doesn't have tests
  doCheck = false;

  pythonImportsCheck = [
    "html5lib-stubs"
  ];

  meta = with lib; {
    description = "Typing stubs for html5lib";
    homepage = "https://github.com/python/typeshed";
    license = licenses.asl20;
  };
}

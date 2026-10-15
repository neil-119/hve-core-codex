from src.importer import import_rows

def test_import_rows_drops_empty_rows():
    assert import_rows([{}, {"id": 1}]) == [{"id": 1}]

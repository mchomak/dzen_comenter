СДЕЛАНО: Runtime/admin batch controls removed; generation/publication retry settings are bounded and covered by red-green tests.
ФАЙЛЫ: Task-02 changes are present in runtime config, admin form/validation, example JSON, and focused config/admin tests.
РЕШЕНИЯ: Legacy batch JSON is silently ignored by parsing because it is absent from RuntimeSettings; saving writes only the active schema.
ТУПИКИ: Impeccable setup scripts/references are absent from the installed plugin cache; existing stylesheet conventions were read directly.
ДАЛЬШЕ: Run fresh `& .venv\Scripts\python.exe -m pytest -q tests/config tests/admin` and inspect the task-02 diff before final shared commit.

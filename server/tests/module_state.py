"""Put an AI feature module's process-wide state back to what import left.

``ai_services`` and the feature modules beside it (``plantnet``, ``plant_doctor``,
...) hold counters, session maps, in-memory caches and config constants as module
globals. A test file that touches one calls ``restore(module)`` from an autouse
fixture so no test sees what an earlier one left behind.
"""
import copy
import importlib.util

_PLAIN_VALUE = (str, int, float, bool, tuple, frozenset, type(None))
_pristine: dict[str, dict[str, object]] = {}


def _pristine_state(module) -> dict[str, object]:
    """The values the module's data globals have straight after import.

    Taken once from a private second copy of the module, so the copy the
    server imported keeps its identity: reloading that one for every test would
    re-create its classes and functions under the feet of everything that had
    already imported them.
    """
    state = _pristine.get(module.__name__)
    if state is None:
        spec = importlib.util.spec_from_file_location(
            f"_{module.__name__}_pristine", module.__file__,
        )
        twin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(twin)
        state = {
            name: copy.deepcopy(value)
            for name, value in vars(twin).items()
            if not name.startswith("__") and isinstance(value, (dict, *_PLAIN_VALUE))
        }
        _pristine[module.__name__] = state
    return state


def restore(module) -> None:
    """Counters and session maps back at their initial contents, the in-memory
    caches empty, and any constant another test left changed restored."""
    import ai_services

    for name, initial in _pristine_state(module).items():
        live = getattr(module, name)
        if isinstance(initial, dict):
            live.clear()
            live.update(copy.deepcopy(initial))
        elif live != initial:
            setattr(module, name, initial)
    for value in vars(module).values():
        if isinstance(value, ai_services._LRU):
            with value._lock:
                value._d.clear()

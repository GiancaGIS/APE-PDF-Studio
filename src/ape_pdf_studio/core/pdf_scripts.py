"""Remove JavaScript from disposable renderer copies, preserving saved revisions."""


def strip_javascript(writer):
    from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject
    from .errors import PDFSecurityError
    from .localization import tr
    from .security import MAX_NODES
    stack = list(writer._objects)
    seen = set()
    visited = 0
    while stack:
        visited += 1
        if visited > MAX_NODES or len(stack) > MAX_NODES:
            raise PDFSecurityError(tr("struttura troppo complessa per completare il controllo."))
        value = stack.pop()
        if isinstance(value, IndirectObject):
            value = value.get_object()
        if not isinstance(value, (DictionaryObject, ArrayObject)) or id(value) in seen:
            continue
        seen.add(id(value))
        if isinstance(value, DictionaryObject):
            action = value.get('/S')
            if action is not None and action.get_object() == '/JavaScript':
                # Also remove action chains reachable only through a script action.
                value.clear()
            else:
                value.pop('/JS', None)
                value.pop('/JavaScript', None)
                stack.extend(value.values())
        else:
            stack.extend(value)

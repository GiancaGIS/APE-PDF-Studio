"""Strip scripts and disabled URI actions from disposable render copies."""


def strip_disabled_actions(writer):
    from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject
    from .errors import PDFSecurityError
    from .localization import tr
    from .security import MAX_NODES
    from .pdf_links import allowed_url
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
            action = action.get_object() if action is not None else None
            if action == '/JavaScript' or (action == '/URI' and
                    allowed_url(value['/URI'] if '/URI' in value else None) is None):
                # No scripts, local/relative files, or unapproved URL protocols
                # reach PDFium. This also detaches their action chains.
                value.clear()
            else:
                value.pop('/JS', None)
                value.pop('/JavaScript', None)
                stack.extend(value.values())
        else:
            stack.extend(value)

"""Bounded JSON-lines protocol shared by GUI conversion jobs."""
import json

MAX_LINE = 64 * 1024
MAX_TOTAL = 4 * 1024 * 1024
MAX_RESULT_BYTES = 256 * 1024 * 1024


class ProtocolError(ValueError):
    pass


class Messages:
    def __init__(self):
        self.pending = bytearray()
        self.total = 0

    def feed(self, chunk):
        self.total += len(chunk)
        if self.total > MAX_TOTAL:
            raise ProtocolError("Worker output exceeds its budget")
        self.pending.extend(chunk)
        while b"\n" in self.pending:
            line, _, rest = self.pending.partition(b"\n")
            self.pending = bytearray(rest)
            if len(line) > MAX_LINE:
                raise ProtocolError("Worker message too large")
            try:
                value = json.loads(line)
            except (ValueError, RecursionError, UnicodeError):
                continue  # Bounded library diagnostics are not protocol messages.
            if not isinstance(value, dict):
                raise ProtocolError("Invalid worker response")
            if "progress" in value:
                if (set(value)!={'progress','total','label'} or type(value.get("progress")) is not int or type(value.get("total")) is not int or
                        not 0 <= value["progress"] <= value["total"] <= 3000 or
                        not isinstance(value.get("label"), str) or len(value["label"]) > 1024):
                    raise ProtocolError("Invalid worker progress")
            elif "error" in value:
                if set(value)!={'error'} or not isinstance(value["error"], str) or len(value["error"]) > 4096:
                    raise ProtocolError("Invalid worker error")
            elif "result" in value:
                result = value["result"]
                if set(value)!={'result'} or not isinstance(result, dict) or len(result) > 8:
                    raise ProtocolError("Invalid worker result")
                for key, data in result.items():
                    if key == "pages":
                        if type(data) is not int or not 1 <= data <= 3000:
                            raise ProtocolError("Invalid page count")
                    elif key in ("image_pages", "recognized", "skipped", "empty", "reduced_dpi"):
                        if not isinstance(data, list) or len(data) > 3000 or any(type(x) is not int or not 1 <= x <= 3000 for x in data):
                            raise ProtocolError("Invalid page list")
                    else:
                        raise ProtocolError("Unknown result property")
            else:
                raise ProtocolError("Unknown worker message")
            yield value
        if len(self.pending) > MAX_LINE:
            raise ProtocolError("Unterminated worker message too large")

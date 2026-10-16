"""Explicit bounded data types for PDF-worker IPC. No pickle or dynamic imports."""
from dataclasses import fields, is_dataclass
import json
import math
from pathlib import Path
import struct

MAX_PACKET = 32 * 1024 * 1024
HEADER = struct.Struct('<I')


def types():
    from .metadata import MetadataSnapshot
    from .document_info import DocumentInfo, SignatureInfo
    from .pdf_forms import FormField
    from .text_edit import TextTarget
    from .paragraph_edit import ParagraphTarget
    from .security import SecurityReport
    return {cls.__name__: cls for cls in (MetadataSnapshot,DocumentInfo,SignatureInfo,FormField,TextTarget,ParagraphTarget,SecurityReport)}


def encode(value):
    if is_dataclass(value):
        if type(value).__name__ not in types():
            raise ValueError('Unsupported IPC type')
        return {'$type':type(value).__name__, 'fields':{f.name:encode(getattr(value,f.name)) for f in fields(value)}}
    if isinstance(value, Path):
        return {'$path':str(value)}
    if isinstance(value, tuple):
        return {'$tuple':[encode(v) for v in value]}
    if isinstance(value, list):
        return [encode(v) for v in value]
    if isinstance(value, dict):
        return {'$map':[[encode(k),encode(v)] for k,v in value.items()]}
    if value is None or type(value) in (str,int,float,bool):
        return value
    raise ValueError('Unsupported IPC value')


def decode(value, depth=0, budget=None):
    budget = [0] if budget is None else budget
    budget[0] += 1
    if depth>64 or budget[0]>200000:
        raise ValueError('IPC structure exceeds budget')
    child=lambda v:decode(v,depth+1,budget)
    if isinstance(value,list):
        return [child(v) for v in value]
    if isinstance(value,dict):
        if set(value)=={'$type','fields'} and value['$type'] in types():
            cls=types()[value['$type']]
            data=value['fields']
            if not isinstance(data,dict) or set(data)!={f.name for f in fields(cls)}:
                raise ValueError('Invalid IPC record')
            data={k:child(v) for k,v in data.items()}
            from .rpc_validation import record
            record(value['$type'],data)
            return cls(**data)
        if set(value)=={'$path'} and isinstance(value['$path'],str) and len(value['$path'])<32768:
            return Path(value['$path'])
        if set(value)=={'$tuple'} and isinstance(value['$tuple'],list):
            return tuple(child(v) for v in value['$tuple'])
        if set(value)=={'$map'} and isinstance(value['$map'],list):
            result={}
            for pair in value['$map']:
                if not isinstance(pair,list) or len(pair)!=2:
                    raise ValueError('Invalid IPC map')
                key=child(pair[0])
                if type(key) not in (str,int) or key in result:
                    raise ValueError('Invalid IPC key')
                result[key]=child(pair[1])
            return result
        raise ValueError('Unknown IPC schema')
    if type(value) is float and not math.isfinite(value):
        raise ValueError('Non-finite IPC value')
    if type(value) is str and len(value)>4*1024*1024:
        raise ValueError('IPC string too large')
    if value is None or type(value) in (str,int,float,bool):
        return value
    raise ValueError('Unknown IPC primitive')


def dumps(value):
    data=json.dumps(encode(value),ensure_ascii=True,allow_nan=False,separators=(',',':')).encode('ascii')
    if len(data)>MAX_PACKET:
        raise ValueError('IPC packet too large')
    return HEADER.pack(len(data))+data


def read_exact(stream,size):
    result=bytearray()
    while len(result)<size:
        chunk=stream.read(size-len(result))
        if not chunk:
            raise EOFError('PDF worker disconnected')
        result.extend(chunk)
    return bytes(result)


def read(stream):
    size,=HEADER.unpack(read_exact(stream,HEADER.size))
    if not 0<size<=MAX_PACKET:
        raise ValueError('Invalid IPC packet length')
    return decode(json.loads(read_exact(stream,size)))

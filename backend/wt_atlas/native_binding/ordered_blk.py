"""Read the unpacker's ordered BLK text without losing type or duplicates.

This is the human-readable export grammar, not a decoder for packed BLK.
Unknown parameter types retain their raw text; physics loaders accept only
the explicitly supported scalar types. No unsupported type is guessed real.
"""
from pathlib import Path
import re


def _unquote(value):
    # wt_blk a7c864b plaintext serializer surrounds raw string contents with
    # double quotes, or single quotes when the contents contain double quotes.
    # It does not JSON-escape backslashes. Preserve regexes such as \d verbatim.
    if len(value)<2 or value[0] not in ['"',"'"] or value[-1]!=value[0]:
        raise ValueError('Expected a quoted exporter string')
    return value[1:-1]


def parse_ordered_blk(path):
    root={'name':'@root','params':[],'children':[]};stack=[root]
    for number,line in enumerate(Path(path).read_text(encoding='utf8').splitlines(),1):
        line=line.strip()
        if not line or line.startswith('//'):continue
        if line.endswith('{'):
            name=line[:-1].strip();name=_unquote(name) if name.startswith('"') else name
            node={'name':name,'params':[],'children':[]};stack[-1]['children'].append(node);stack.append(node)
        elif line=='}':
            if len(stack)==1:raise ValueError(f'Unmatched closing brace at {number}')
            stack.pop()
        else:
            match=re.fullmatch(r'((?:"(?:\\.|[^"])*")|[^:]+):([A-Za-z0-9_]+)\s*=\s*(.*)',line)
            if not match:raise ValueError(f'Unsupported ordered BLK line {number}: {line[:100]}')
            name,kind,raw=match.groups();name=name.strip();name=_unquote(name) if name.startswith('"') else name
            if kind=='r':value=float(raw)
            elif kind in ['i','i64']:value=int(raw)
            elif kind=='b':
                if raw not in ['true','false','yes','no']:raise ValueError(f'Unknown bool at {number}')
                value=raw in ['true','yes']
            elif kind=='t':value=_unquote(raw)
            elif kind in ['p2','p3','p4','ip2','ip3','ip4','c']:
                value=[int(s.strip()) if kind.startswith('i') or kind=='c' else float(s.strip()) for s in raw.split(',')]
            else:value=raw
            stack[-1]['params'].append({'name':name,'type':kind,'value':value,'line':number,'raw':raw})
    if len(stack)!=1:raise ValueError('Unclosed ordered BLK block')
    return root


def as_json(block):
    result={};repeated=set()
    for name,value in [(p['name'],p['value']) for p in block['params']]+[(c['name'],as_json(c)) for c in block['children']]:
        if name not in result:result[name]=value
        elif name in repeated:result[name].append(value)
        else:result[name]=[result[name],value];repeated.add(name)
    return result

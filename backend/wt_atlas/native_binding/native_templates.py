"""Ordered global part-default expansion (ordinary numeric templates).

Derived from 0x143eb07e0 / 0x143eb0d90 / 0x143eb1430 / 0x143eb18f0.
Typed set/add operations are explicit; no game state is inferred here.
"""
from copy import deepcopy
import re


def set_parameter(parameters,item,overwrite=True):
    item=deepcopy(item)
    if overwrite:
        index=next((i for i,p in enumerate(parameters) if p['name']==item['name']),None)
        if index is not None:parameters[index]=item;return
    parameters.append(item)


def overlay_parameters(parameters,incoming):
    for item in incoming:set_parameter(parameters,item,item['name']!='class')


def expand_numeric_template(value):
    first=value.find('#')
    if first<0:raise ValueError('Template missing opening #')
    last=value.find('#',first+1)
    if last<0:raise ValueError('Template missing closing #')
    # The original strchr(':') is not bounded by the first closing '#'. A
    # later range token can therefore change the current token's start/end
    # and padding width. Preserve that compiled behavior for multiple tokens.
    colon=value.find(':',first+1)
    def integer(text):
        match=re.match(r'\s*([+-]?\d+)',text)
        if not match:raise ValueError('Unsupported non-integer numeric template')
        return int(match[1])
    start,end=(1,integer(value[first+1:])) if colon<0 else (integer(value[first+1:]),integer(value[colon+1:]))
    width=(last if colon<0 else colon)-first-1
    if width<1:raise ValueError('Unsupported empty numeric template width')
    for number in range(start,end+1):
        result=value[:first]+f'{number:0{width}d}'+value[last+1:]
        if '#' in result:yield from expand_numeric_template(result)
        else:yield result


def expand_part_defaults(root):
    base=next((c for c in root['children'] if c['name']=='default'),{'params':[]})['params']
    defaults={}
    for name in ['default_v1','default_v2']:
        values=deepcopy(base)
        for p in next((c for c in root['children'] if c['name']==name),{'params':[]})['params']:set_parameter(values,p)
        defaults[name]=values
    output=[];seen=set();duplicates=[];ranges=[]
    def walk(block,inherited,is_root=False):
        if block['children']:
            start=len(output)
            values=deepcopy(inherited)
            if not is_root:
                values=[{'name':'class','type':'t','value':block['name']}]
                overlay_parameters(values,inherited);overlay_parameters(values,block['params'])
            for child in block['children']:walk(child,values)
            if not is_root:set_parameter(ranges,{'name':block['name'],'type':'ip3','value':[start,len(output)-1,len(output)-start]})
            return
        template=next((p['value'] for p in block['params'] if p['name']=='template' and p['type']=='t'),None)
        names=list(expand_numeric_template(template)) if template is not None else [block['name']]
        for name in names:
            if name in seen:duplicates.append(name);continue
            seen.add(name);values=[{'name':'class','type':'t','value':block['name']}]
            overlay_parameters(values,inherited);overlay_parameters(values,block['params'])
            output.append({'name':name+'_dm','params':values,'source_template_name':block['name']})
    walk(root,base,True)
    return {'defaults':defaults,'class_ranges':ranges,'parts':output,'duplicate_names':duplicates}

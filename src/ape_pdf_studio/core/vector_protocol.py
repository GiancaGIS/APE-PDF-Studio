"""Small bounded drawing protocol. XML is parsed only inside the sandbox."""
from __future__ import annotations

import math
import re

MAX_COMMANDS = 150000
MAX_DRAWS = 20000
KEYS = {'commands','transforms','fill','rule','stroke','width','cap','join','miter',
        'dash','offset','opacity','fill_opacity','stroke_opacity'}
COUNTS = {'M':2,'L':2,'H':1,'V':1,'C':6,'Z':0}


def number(value, bound=1e7):
    if type(value) not in (int,float) or not math.isfinite(value) or abs(value)>bound:
        raise ValueError('Invalid drawing number')
    return value


def color(value):
    if not isinstance(value,str) or not re.fullmatch(r'none|transparent|black|white|#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?',value):
        raise ValueError('Unsupported drawing color')
    return value


def validate(value):
    if not isinstance(value,dict) or set(value)!={'version','layers','size'} or type(value['version'])is not int or value['version']!=1:
        raise ValueError('Invalid drawing envelope')
    if not isinstance(value['size'],list) or len(value['size'])!=2 or any(not 0<number(x,14400) for x in value['size']):raise ValueError('Invalid drawing size')
    layers=value['layers']
    if not isinstance(layers,list) or len(layers)>1024:raise ValueError('Too many drawing layers')
    commands=draws=0
    def geometry(item):
        nonlocal commands
        path=item['commands'];transforms=item['transforms']
        if not isinstance(path,list) or not path or path[0][0]!='M':raise ValueError('Invalid drawing path')
        commands+=len(path)
        if commands>MAX_COMMANDS:raise ValueError('Too many drawing commands')
        for pair in path:
            if not isinstance(pair,list) or len(pair)!=2 or not isinstance(pair[0],str) or pair[0] not in COUNTS or not isinstance(pair[1],list) or len(pair[1])!=COUNTS[pair[0]]:
                raise ValueError('Invalid drawing command')
            for x in pair[1]:number(x)
        if not isinstance(transforms,list) or len(transforms)>64:raise ValueError('Too many transforms')
        for matrix in transforms:
            if not isinstance(matrix,list) or len(matrix)!=6:raise ValueError('Invalid drawing transform')
            for x in matrix:number(x)
        # Bound composed transforms too: individually finite matrices can still
        # overflow when nested, before reaching the native drawing engine.
        composed=[1.,0.,0.,1.,0.,0.]
        for a,b,c,d,e,f in reversed(transforms):
            x,y,z,w,u,v=composed
            composed=[a*x+c*y,b*x+d*y,a*z+c*w,b*z+d*w,a*u+c*v+e,b*u+d*v+f]
            for n in composed:number(n)
        a,b,c,d,e,f=composed
        for command,coords in path:
            pairs=[coords[i:i+2] for i in range(0,len(coords),2)] if command not in ('H','V') else [[coords[0],coords[0]]]
            for x,y in pairs:number(a*x+c*y+e,1e8);number(b*x+d*y+f,1e8)
        if item['rule'] not in ('nonzero','evenodd'):raise ValueError('Invalid fill rule')
    for layer in layers:
        if not isinstance(layer,dict) or set(layer)!={'clips','draws'} or not isinstance(layer['clips'],list) or len(layer['clips'])>64 or not isinstance(layer['draws'],list):raise ValueError('Invalid drawing layer')
        for clip in layer['clips']:
            if not isinstance(clip,dict) or set(clip)!={'commands','transforms','rule'}:raise ValueError('Invalid clip')
            geometry(clip)
        draws+=len(layer['draws'])
        if draws>MAX_DRAWS:raise ValueError('Too many draws')
        for draw in layer['draws']:
            if not isinstance(draw,dict) or set(draw)!=KEYS:raise ValueError('Invalid draw')
            geometry(draw);color(draw['fill']);color(draw['stroke'])
            if draw['cap'] not in ('butt','round','square') or draw['join'] not in ('miter','round','bevel'):raise ValueError('Invalid pen')
            if not 0<=number(draw['width'],10000) or not 0<number(draw['miter'],10000):raise ValueError('Invalid stroke size')
            number(draw['offset'],10000)
            for key in ('opacity','fill_opacity','stroke_opacity'):
                if not 0<=number(draw[key],1)<=1:raise ValueError('Invalid opacity')
            if not isinstance(draw['dash'],list) or len(draw['dash'])>32 or any(not 0<number(x,10000) for x in draw['dash']):raise ValueError('Invalid dash')
            if draw['width']:
                if draw['width']<1e-5:raise ValueError('Stroke too small')
                for x in [*draw['dash'],draw['offset']]:number(x/draw['width'])
    return value


def from_svg(data,layers):
    """Consume the static whitelist output from prepare_svg, in the worker only."""
    import xml.etree.ElementTree as ET
    from .vector_page import SVG, XLINK, matrix, clip_commands
    root=ET.fromstring(data)
    ids={e.get('id'):e for e in root.iter() if e.get('id')}
    style_keys={'fill','fill-rule','fill-opacity','stroke','stroke-width','stroke-linecap',
                'stroke-linejoin','stroke-miterlimit','stroke-dasharray','stroke-dashoffset','stroke-opacity'}
    def visit(element,style,transforms,depth=0):
        if depth>64:raise ValueError('Drawing too deep')
        style={**style,**{k:v for k,v in element.attrib.items() if k in style_keys}}
        transforms=[*transforms,*([matrix(element.get('transform'))] if element.get('transform') else [])]
        if element.tag==f'{{{SVG}}}g':
            for child in element:yield from visit(child,style,transforms,depth+1)
            return
        if element.tag==f'{{{SVG}}}use':
            leaf=ids[element.get(f'{{{XLINK}}}href')[1:]]
            # MuPDF glyph definitions contain geometry only. More complex SVGs
            # retain the full PDF raster renderer instead of approximating style.
            if set(leaf.attrib)-{'id','d'}:raise ValueError('Styled glyph reference')
        elif element.tag==f'{{{SVG}}}path':leaf=element
        else:raise ValueError('Unsupported drawing')
        if not leaf.get('d','').strip():return
        dash=style.get('stroke-dasharray','none')
        yield dict(commands=clip_commands(leaf.get('d','')),transforms=transforms,
            fill=color(style.get('fill','black')),rule=style.get('fill-rule','nonzero'),
            stroke=color(style.get('stroke','none')),width=float(style.get('stroke-width','1')),
            cap=style.get('stroke-linecap','butt'),join=style.get('stroke-linejoin','miter'),
            miter=float(style.get('stroke-miterlimit','4')),
            dash=[] if dash=='none' else [float(x) for x in re.split(r'[ ,]+',dash.strip())],
            offset=float(style.get('stroke-dashoffset','0')),opacity=float(element.get('opacity','1')),
            fill_opacity=float(style.get('fill-opacity','1')),stroke_opacity=float(style.get('stroke-opacity','1')))
    style={k:v for k,v in root.attrib.items() if k in style_keys}
    value={'version':1,'size':[float(root.get('width')),float(root.get('height'))],
           'layers':[{'clips':layer['clips'],'draws':list(visit(ids[layer['id']],style,[]))} for layer in layers]}
    return validate(value)

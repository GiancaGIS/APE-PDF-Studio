"""Validate worker records before they reach Qt or application state."""
from datetime import datetime, timedelta
import math


def text(value,limit=32768):
    if not isinstance(value,str) or len(value)>limit:raise ValueError('Invalid text record')


def integer(value,lower=0,upper=2**32-1):
    if type(value)is not int or not lower<=value<=upper:raise ValueError('Invalid integer record')


def geometry(value,count=4):
    if not isinstance(value,(list,tuple)) or len(value)!=count or any(type(x)not in(int,float) or not math.isfinite(x) or abs(x)>1e7 for x in value):raise ValueError('Invalid geometry record')


def strings(value,limit=10000):
    if not isinstance(value,(tuple,list)) or len(value)>limit:raise ValueError('Invalid text list')
    for v in value:text(v)


def record(name,data):
    if name=='SecurityReport':
        from .security import FINDINGS
        strings(data['findings'],len(FINDINGS))
        if set(data['findings'])-set(FINDINGS) or type(data['memory_limited'])is not bool:raise ValueError('Invalid security report')
    elif name=='SignatureInfo':
        for value in data.values():text(value)
    elif name=='SignatureCertificate':
        for key in ('subject','issuer'):text(data[key],4096)
        serial=data['serial']
        text(serial,128)
        digits=serial.removeprefix('-')
        if not digits or any(c not in '0123456789ABCDEF' for c in digits):
            raise ValueError('Invalid certificate serial number')
        for key in ('valid_from','valid_until'):
            text(data[key],25)
            if len(data[key])!=25 or datetime.fromisoformat(data[key]).utcoffset()!=timedelta(0):
                raise ValueError('Invalid certificate date')
        fingerprint=data['sha256']
        text(fingerprint,64)
        if len(fingerprint)!=64 or any(c not in '0123456789abcdef' for c in fingerprint):
            raise ValueError('Invalid certificate fingerprint')
        if type(data['referenced'])is not bool:raise ValueError('Invalid certificate association')
    elif name=='DocumentInfo':
        from .document_info import SignatureInfo
        if data['pdfa'] is not None:text(data['pdfa'],32)
        if not isinstance(data['signatures'],tuple) or len(data['signatures'])>10000 or any(not isinstance(x,SignatureInfo) for x in data['signatures']):raise ValueError('Invalid signature list')
        strings(data['empty_signature_fields']);strings(data['warnings'])
    elif name=='MetadataSnapshot':
        for key in ('values','info','xmp_values'):
            value=data[key]
            if not isinstance(value,dict) or len(value)>10000:raise ValueError('Invalid metadata map')
            for k,v in value.items():text(k,4096);text(v,1000000)
        text(data['xml'],1000000);strings(data['warnings']);strings(data['conflicts'])
        integer(data['page_count'],1,3000);text(data['version'],32)
        if type(data['editable'])is not bool:raise ValueError('Invalid edit flag')
    elif name=='FormField':
        text(data['name'],4096);text(data['label'],4096)
        if data['kind']not in ('text','checkbox','radio','choice','unsupported'):raise ValueError('Invalid field kind')
        if isinstance(data['value'],str):text(data['value'])
        else:strings(data['value'],10000)
        integer(data['flags']);integer(data['max_length'],0,2**31-1)
        if type(data['readonly'])is not bool:raise ValueError('Invalid field flag')
        options=data['options'];widgets=data['widgets']
        if not isinstance(options,tuple) or len(options)>10000 or not isinstance(widgets,tuple) or len(widgets)>10000:raise ValueError('Invalid field members')
        for pair in options:
            if not isinstance(pair,tuple) or len(pair)!=2:raise ValueError('Invalid field option')
            text(pair[0]);text(pair[1])
        for pair in widgets:
            if not isinstance(pair,tuple) or len(pair)!=2:raise ValueError('Invalid field widget')
            integer(pair[0],0,2999);geometry(pair[1])
        strings(data['widget_states'])
    elif name in ('TextTarget','ParagraphTarget'):
        for key in ('revision','font'):text(data[key],32768)
        text(data['text']);integer(data['page'],0,2999)
        if not isinstance(data['address'],tuple) or len(data['address'])!=3:raise ValueError('Invalid span address')
        for x in data['address']:integer(x,0,1000000)
        geometry(data['bbox']);geometry(data['display_bbox']);geometry(data['origin'],2)
        geometry((data['size'],),1);integer(data['color'],0,0xffffff);integer(data['flags'])
        identity=data['identity']
        if not isinstance(identity,str) or len(identity)!=64 or any(c not in '0123456789abcdef' for c in identity):
            raise ValueError('Invalid text target identity')
        if name=='ParagraphTarget':
            from .text_edit import TextTarget
            from .paragraph_edit import NEW_BLOCK
            members=data['members']
            if (not isinstance(members,tuple) or len(members)>2000 or any(type(m)is not TextTarget for m in members)
                    or (not members) != (data['address']==NEW_BLOCK) or not members and data['text']):
                raise ValueError('Invalid paragraph members')
            if (sum(len(m.text) for m in members)>16000 or any(m.page!=data['page'] or m.revision!=data['revision'] for m in members)
                    or len({m.address for m in members})!=len(members) or not .5<=data['size']<=300):raise ValueError('Inconsistent paragraph members')
            text(data['text'],16000)
            geometry(data['layout_bbox']);geometry(data['display_layout_bbox'])
            for box in (data['bbox'],data['display_bbox'],data['layout_bbox'],data['display_layout_bbox']):
                if box[2]<=box[0] or box[3]<=box[1]:raise ValueError('Invalid paragraph area')
            geometry((data['leading'],data['first_indent'],data['continuation_indent']),3)
            geometry((data['column_limit'],),1)
            if not 0<data['column_limit']<=14400:raise ValueError('Invalid available paragraph width')
            if not 0<data['leading']<=1000 or data['first_indent']<0 or data['continuation_indent']<0:raise ValueError('Invalid paragraph spacing')
            integer(data['alignment'],0,3)
            if type(data['rotation'])is not int or data['rotation']not in (0,90,180,270):raise ValueError('Invalid paragraph rotation')


def annotations(value,*,links=False):
    if not isinstance(value,dict) or len(value)>3000:raise ValueError('Invalid annotations')
    total=0
    for page,rows in value.items():
        integer(page,0,2999)
        if not isinstance(rows,list):raise ValueError('Invalid annotations list')
        total+=len(rows)
        if total>20000:raise ValueError('Too many annotations')
        for row in rows:
            if not isinstance(row,dict) or set(row)!=({'rect','url'} if links else {'rect','index','text'}):raise ValueError('Invalid annotation')
            geometry(row['rect'])
            if links:
                from .pdf_links import allowed_url
                if not allowed_url(row['url']):raise ValueError('Invalid annotation URL')
            else:integer(row['index'],0,1000000);text(row['text'])


def result(method,value):
    if method == 'prepare_signature':
        if not isinstance(value,dict) or set(value)!={'width','height'}:
            raise ValueError('Invalid signature raster')
        integer(value['width'],1,2048);integer(value['height'],1,2048)
    elif method == 'insert_signature':
        if value is not None:raise ValueError('Invalid signature edit response')
    elif method == 'signature_certificates':
        from .document_info import MAX_SIGNATURE_CERTIFICATES, SignatureCertificate
        if (not isinstance(value,tuple) or len(value)>MAX_SIGNATURE_CERTIFICATES
                or any(type(certificate)is not SignatureCertificate for certificate in value)):
            raise ValueError('Invalid certificate list')
    elif method == 'ink':
        if (not isinstance(value,dict) or set(value)!={'rows','truncated'} or
                type(value['truncated']) is not bool or not isinstance(value['rows'],list) or len(value['rows'])>2000):
            raise ValueError('Invalid ink response')
        total=0
        for row in value['rows']:
            if (not isinstance(row,dict) or set(row)-{'page','index','paths','color','width','opacity','smooth'} or
                    not {'page','index','paths','color','width','opacity'}<=set(row) or
                    ('smooth' in row and type(row['smooth'])is not bool)):
                raise ValueError('Invalid ink record')
            integer(row['page'],0,2999);integer(row['index'],0,1000000)
            geometry(row['color'],3);geometry((row['width'],row['opacity']),2)
            if (any(not 0<=c<=1 for c in row['color']) or not .01<=row['width']<=10000 or not 0<=row['opacity']<=1 or
                    not isinstance(row['paths'],list) or not 1<=len(row['paths'])<=200000):
                raise ValueError('Invalid ink style')
            for path in row['paths']:
                if not isinstance(path,list) or not 1<=len(path)<=8000:raise ValueError('Invalid ink path')
                total+=len(path)
                if total>20000:raise ValueError('Too many ink points')
                for point in path:geometry(point,2)
    elif method in ('text_boxes','paragraph_boxes'):
        if not isinstance(value,list) or len(value)>20000:raise ValueError('Invalid text boxes')
        for box in value:
            geometry(box)
            if box[2]<=box[0] or box[3]<=box[1]:raise ValueError('Invalid text box area')
    elif method in ('paragraph_at','new_paragraph_at'):
        from .paragraph_edit import ParagraphTarget
        if type(value)is not ParagraphTarget:raise ValueError('Invalid paragraph target')
    elif method in ('paragraph_info','text_preview'):
        if not isinstance(value,dict) or set(value)!={'bbox','lines','font','fallback','fits','message','runs','raster'}:raise ValueError('Invalid text layout')
        geometry(value['bbox']);integer(value['lines'],0,2000);text(value['font'],4096);text(value['message'],4096)
        if type(value['fits'])is not bool or type(value['fallback'])is not bool:raise ValueError('Invalid paragraph layout flag')
        if value['bbox'][2]<=value['bbox'][0] or value['bbox'][3]<=value['bbox'][1]:raise ValueError('Invalid paragraph layout area')
        runs=value['runs']
        if not isinstance(runs,list) or len(runs)>4000:raise ValueError('Invalid text runs')
        for run in runs:
            if not isinstance(run,tuple) or len(run)!=6:raise ValueError('Invalid text run')
            word, origin, size, color, font, box=run
            text(word,16000);geometry(origin,2);geometry((size,),1);integer(color,0,0xffffff)
            text(font,4096);geometry(box)
            if not .01<=size<=180000 or box[2]<box[0] or box[3]<box[1]:raise ValueError('Invalid text run geometry')
        if sum(len(run[0]) for run in runs)>16000 or not value['fits'] and runs:
            raise ValueError('Inconsistent text layout')
        if value['raster'] is not None:
            raster=value['raster']
            if not value['fits'] or not isinstance(raster,tuple) or len(raster)!=3:raise ValueError('Invalid text preview')
            bounds,width,height=raster
            geometry(bounds);integer(width,1,1024);integer(height,1,1024)
            if bounds[2]<=bounds[0] or bounds[3]<=bounds[1]:raise ValueError('Invalid text preview area')
    elif method=='text_at':
        from .text_edit import TextTarget
        if not isinstance(value,TextTarget):raise ValueError('Invalid text target')
    elif method=='interactions':
        from .pdf_forms import FormField
        if not isinstance(value,tuple) or len(value)!=2 or not isinstance(value[1],tuple) or len(value[1])>10000 or any(not isinstance(v,FormField) for v in value[1]):raise ValueError('Invalid interaction response')
        annotations(value[0],links=True)
    elif method=='notes':annotations(value)
    elif method in ('form_design','validate_form_design'):
        if not isinstance(value,list) or len(value)>10000:raise ValueError('Invalid form design')
        for row in value:
            if not isinstance(row,dict) or set(row)-{'source','widget','page','rect','name','label','kind','multiline','readonly','required','options'}:raise ValueError('Invalid design field')
            for key in ('name','label'):text(row[key],4096)
            integer(row['page'],0,2999);geometry(row['rect'])
            if row['kind']not in ('text','checkbox','combo','list','radio'):raise ValueError('Invalid design kind')
            for key in ('multiline','readonly','required'):
                if key in row and type(row[key])is not bool:raise ValueError('Invalid design flag')
            if 'source' in row:text(row['source'],4096);integer(row['widget'],0,10000)
            options=row.get('options',[])
            if not isinstance(options,list) or len(options)>500:raise ValueError('Invalid design options')
            for pair in options:
                if not isinstance(pair,(list,tuple)) or len(pair)!=2:raise ValueError('Invalid option')
                for part in pair:text(part,4096)
    return value

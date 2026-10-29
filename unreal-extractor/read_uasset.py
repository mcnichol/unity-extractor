#!/usr/bin/env python3
"""Experimental, read-only UE4 .uasset deserializer; Python standard library only.
Supports legacy -7, UE4 serialization versions 518..522, tagged editor assets.
Reads summary tables, imports, export descriptors and generic property tags.
Does NOT decode native mesh buffers, texture source/bulk data, or Blueprint code.
Unsupported property payloads retain offsets, sizes and hex for further analysis.
Usage: python3 read_uasset.py FILE_OR_DIRECTORY --output NEW_DIRECTORY
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys
import zlib

class Reader:
    def __init__(self, data, pos=0): self.data, self.pos = data, pos
    def read(self, size):
        if size < 0 or self.pos + size > len(self.data): raise ValueError('Read outside file at '+str(self.pos))
        value=self.data[self.pos:self.pos+size];self.pos+=size;return value
    def unpack(self, fmt): return struct.unpack('<'+fmt,self.read(struct.calcsize('<'+fmt)))[0]
    def i32(self): return self.unpack('i')
    def string(self):
        size=self.i32()
        if abs(size)>1000000: raise ValueError('Invalid FString length')
        raw=self.read(size if size>=0 else -size*2)
        return raw.decode('utf-8' if size>=0 else 'utf-16-le',errors='strict').rstrip('\0')

def deserialize(path):
    data=path.read_bytes();r=Reader(data)
    if data.startswith(b'version https://git-lfs'):raise ValueError('Git LFS pointer: fetch original bytes first')
    if r.unpack('I')!=0x9e2a83c1:raise ValueError('Not a little-endian Unreal package')
    legacy=r.i32()
    if legacy!=-7:raise ValueError('Only legacy format -7 is supported; found '+str(legacy))
    ue3,ue4,licensee=r.i32(),r.i32(),r.i32()
    if not 518<=ue4<=522 or licensee:raise ValueError('Unsupported serialization version/licensee: '+str((ue4,licensee)))
    count=r.i32()
    if not 0<=count<=10000:raise ValueError('Invalid custom version count')
    custom=[{'guid_bytes':r.read(16).hex(),'version':r.i32()} for _ in range(count)]
    summary={'legacy':legacy,'ue3':ue3,'ue4':ue4,'licensee':licensee,'custom_versions':custom}
    summary['total_header_size']=r.i32();summary['folder']=r.string();flags=r.unpack('I');summary['flags']=hex(flags)
    names_count,names_offset=r.i32(),r.i32()
    if not flags & 0x80000000:summary['localization_id']=r.string()
    summary['gatherable_text_count']=r.i32();summary['gatherable_text_offset']=r.i32()
    export_count,export_offset,import_count,import_offset,depends_offset=[r.i32() for _ in range(5)]
    summary.update(name_count=names_count,name_offset=names_offset,export_count=export_count,export_offset=export_offset,import_count=import_count,import_offset=import_offset,depends_offset=depends_offset)
    for n,o in ((names_count,names_offset),(import_count,import_offset),(export_count,export_offset)):
        if not 0<=n<=1000000 or not 0<=o<=len(data):raise ValueError('Invalid package table')
    names=[];r.pos=names_offset
    for _ in range(names_count):
        names.append(r.string());r.read(4) # two serialized uint16 name hashes
    def fname(reader):
        index,number=reader.i32(),reader.i32()
        if not 0<=index<len(names) or number<0:raise ValueError('Invalid FName at '+str(reader.pos-8))
        return names[index] + ('_'+str(number-1) if number else '')
    imports=[];r.pos=import_offset
    for _ in range(import_count):
        imports.append({'class_package':fname(r),'class_name':fname(r),'outer_index':r.i32(),'object_name':fname(r)})
    exports=[];r.pos=export_offset
    # UE4.21..4.27 versioned, editor export descriptor: 104 bytes.
    for _ in range(export_count):
        start=r.pos
        row=dict(zip(('class_index','super_index','template_index','outer_index'),[r.i32() for i in range(4)]))
        row['object_name']=fname(r);row['object_flags']=hex(r.unpack('I'));row['serial_size']=r.unpack('q');row['serial_offset']=r.unpack('q')
        r.read(104-(r.pos-start));exports.append(row)
    def reference(index,seen=None):
        if index==0:return None
        seen=set() if seen is None else set(seen)
        if index in seen:return '[cycle]'
        seen.add(index)
        table,i=(imports,-index-1) if index<0 else (exports,index-1)
        if not 0<=i<len(table):return '[invalid reference '+str(index)+']'
        item=table[i];outer=reference(item['outer_index'],seen)
        return (outer+'.' if outer else '')+item['object_name']
    for row in imports:row['outer']=reference(row['outer_index'])
    for row in exports:
        row['class']=reference(row['class_index']);row['outer']=reference(row['outer_index'])
    def tags(reader, end, depth=0):
        if depth>8:raise ValueError('Property nesting limit')
        result=[]
        for _ in range(100000):
            name=fname(reader)
            if name=='None':return result
            typ=fname(reader);size=reader.i32();array_index=reader.i32()
            if size<0:raise ValueError('Negative property size')
            tag={'name':name,'type':typ,'size':size,'array_index':array_index}
            if typ=='StructProperty':tag['struct_type']=fname(reader);tag['struct_guid']=reader.read(16).hex()
            elif typ=='BoolProperty':tag['value']=bool(reader.unpack('B'))
            elif typ in ('ByteProperty','EnumProperty'):tag['enum_type']=fname(reader)
            elif typ in ('ArrayProperty','SetProperty'):tag['inner_type']=fname(reader)
            elif typ=='MapProperty':tag['key_type']=fname(reader);tag['value_type']=fname(reader)
            guid_flag=reader.unpack('B')
            if guid_flag not in (0,1):raise ValueError('Invalid property GUID flag')
            if guid_flag:tag['property_guid']=reader.read(16).hex()
            start=reader.pos;stop=start+size
            if stop>end:raise ValueError('Property exceeds export bounds')
            tag['payload_offset']=start
            payload=Reader(reader.data,start)
            try:
                if typ in ('ObjectProperty','InterfaceProperty') and size==4:
                    index=payload.i32();tag['value']={'index':index,'object':reference(index)}
                elif (typ in ('NameProperty','EnumProperty') or (typ=='ByteProperty' and tag.get('enum_type')!='None')) and size==8:tag['value']=fname(payload)
                elif typ=='StrProperty':tag['value']=payload.string()
                elif typ in ('IntProperty','UInt32Property','FloatProperty','DoubleProperty','Int64Property','UInt64Property','ByteProperty'):
                    fmt={'IntProperty':'i','UInt32Property':'I','FloatProperty':'f','DoubleProperty':'d','Int64Property':'q','UInt64Property':'Q','ByteProperty':'B'}[typ]
                    if size!=struct.calcsize(fmt):raise ValueError('Non-scalar payload')
                    tag['value']=payload.unpack(fmt)
                elif typ=='StructProperty' and tag['struct_type']=='Guid' and size==16:
                    tag['value']=payload.read(16).hex()
                elif typ=='StructProperty' and tag['struct_type']=='IntPoint' and size==8:
                    tag['value']=[payload.i32(),payload.i32()]
                elif typ=='StructProperty' and tag['struct_type'] in ('Vector','Vector2D','Rotator','LinearColor'):
                    n={'Vector':3,'Vector2D':2,'Rotator':3,'LinearColor':4}[tag['struct_type']]
                    if size!=n*4:raise ValueError('Unexpected struct size')
                    tag['value']=list(struct.unpack('<'+'f'*n,payload.read(size)))
                elif typ=='ArrayProperty':
                    n=payload.i32();tag['count']=n
                    if not 0<=n<=1000000:raise ValueError('Invalid array count')
                    inner=tag['inner_type']
                    if inner=='ObjectProperty' and size==4+n*4:
                        tag['value']=[reference(payload.i32()) for i in range(n)]
                    elif inner=='NameProperty' and size==4+n*8:tag['value']=[fname(payload) for i in range(n)]
                    elif inner=='StructProperty' and n:
                        # UE4 array inner FPropertyTag, followed by tagged struct elements.
                        inner_name=fname(payload);inner_kind=fname(payload)
                        inner_size=payload.i32();inner_index=payload.i32()
                        if inner_kind!='StructProperty':raise ValueError('Unexpected array inner tag')
                        struct_name=fname(payload);payload.read(16)
                        inner_guid=payload.unpack('B')
                        if inner_guid not in (0,1):raise ValueError('Invalid array inner GUID flag')
                        if inner_guid:payload.read(16)
                        tag['struct_type']=struct_name
                        values=[tags(payload,stop,depth+1) for i in range(n)]
                        if payload.pos!=stop:raise ValueError('Struct array has native or trailing data')
                        tag['value']=values
                elif typ=='StructProperty' and size>=8:
                    values=tags(payload,stop,depth+1)
                    if payload.pos!=stop:raise ValueError('Struct has native or trailing data')
                    tag['value']=values
            except (ValueError,UnicodeError) as error:tag['decode_warning']=str(error)
            if 'value' not in tag:tag['raw_hex']=reader.data[start:stop].hex()
            reader.pos=stop;result.append(tag)
        raise ValueError('Property count limit')
    for row in exports:
        start,size=row['serial_offset'],row['serial_size']
        if start<0 or size<0 or start+size>len(data):
            row['decode_error']='Payload outside .uasset; split .uexp or unsupported layout';continue
        p=Reader(data,start)
        try:
            if flags & 0x2000:raise ValueError('Unversioned properties unsupported')
            row['properties']=tags(p,start+size)
            row['native_tail_offset']=p.pos;row['native_tail_bytes']=start+size-p.pos
        except (ValueError,UnicodeError,struct.error) as error:
            row['decode_error']=str(error);row['stopped_at']=p.pos
    return {'file':str(path),'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),
            'scope':'Partial UE4 deserialization; native class payloads are not decoded',
            'summary':summary,'names':names,'imports':imports,'exports':exports}

def embedded_pngs(data):
    """Find complete PNG streams and verify every chunk CRC; no image conversion."""
    signature=b'\x89PNG\r\n\x1a\n';position=0
    while True:
        start=data.find(signature,position)
        if start<0:return
        position=start+8;cursor=position;dimensions=None
        try:
            while True:
                if cursor+12>len(data):raise ValueError('Truncated PNG')
                size=struct.unpack_from('>I',data,cursor)[0]
                if size>len(data)-cursor-12:raise ValueError('Invalid PNG chunk size')
                kind=data[cursor+4:cursor+8];body=data[cursor+8:cursor+8+size]
                crc=struct.unpack_from('>I',data,cursor+8+size)[0]
                if zlib.crc32(kind+body)&0xffffffff!=crc:raise ValueError('PNG CRC mismatch')
                if dimensions is None:
                    if kind!=b'IHDR' or size!=13:raise ValueError('Missing IHDR')
                    dimensions=struct.unpack('>II',body[:8])
                    if not all(dimensions):raise ValueError('Invalid dimensions')
                cursor+=12+size
                if kind==b'IEND':
                    if size:raise ValueError('Invalid IEND')
                    yield start,cursor,dimensions
                    position=cursor;break
        except (ValueError,struct.error):continue

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path);parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--extract-png',action='store_true',help='Recover validated embedded PNG streams from Texture2D packages')
    args=parser.parse_args()
    if args.output.exists():parser.error('Output directory must be new')
    files=[args.source] if args.source.is_file() else sorted(args.source.rglob('*.uasset'))
    if not files:parser.error('No files found')
    args.output.mkdir(parents=True)
    failures=0;index=[]
    for path in files:
        relative=Path(path.name) if args.source.is_file() else path.relative_to(args.source)
        target=args.output/relative.with_suffix('.json');target.parent.mkdir(parents=True,exist_ok=True)
        try:
            result=deserialize(path)
            if args.extract_png and any(e['class']=='/Script/Engine.Texture2D' for e in result['exports']):
                recovered=[]
                for number,(start,stop,(width,height)) in enumerate(embedded_pngs(path.read_bytes())):
                    image=target.with_name(target.stem+f'.embedded_{number}_{width}x{height}.png')
                    image.write_bytes(path.read_bytes()[start:stop])
                    recovered.append({'file':str(image),'offset':start,'bytes':stop-start,'width':width,'height':height,'chunk_crc_verified':True})
                result['recovered_pngs']=recovered
            print(path.name,':',len(result['names']),'names,',len(result['exports']),'exports')
            status='partial' if any('decode_error' in e for e in result['exports']) else 'tables_and_tags_decoded'
        except (OSError,ValueError,UnicodeError,struct.error) as error:
            result={'file':str(path),'error':str(error)};failures+=1;status='failed';print(path.name,':',error,file=sys.stderr)
        target.write_text(json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf-8')
        index.append({'source':str(path),'report':str(target),'status':status})
    (args.output/'index.json').write_text(json.dumps(index,indent=2)+'\n')
    return 1 if failures else 0
if __name__=='__main__':sys.exit(main())

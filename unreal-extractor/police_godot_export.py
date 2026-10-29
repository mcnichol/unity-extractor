#!/usr/bin/env python3
"""Direct PoliceDepartment UE4 editor assets -> Godot GLB + PNG + TSCN.
Python 3.9+ standard library only; no Unreal installation or pip dependencies.
Keep read_uasset.py and ue4_mesh_description.py next to this script.
Usage: python3 police_godot_export.py PATH/TO/Content --output NEW_FOLDER
"""
import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import struct
import sys
import zlib
from read_uasset import deserialize, embedded_pngs
from ue4_mesh_description import mesh_description

SHADER = '''shader_type spatial;
render_mode diffuse_burley, specular_schlick_ggx;
uniform sampler2D albedo_map : source_color, hint_default_white;
uniform sampler2D rma_map : hint_default_white;
uniform sampler2D normal_map : hint_normal;
uniform sampler2D emission_map : source_color, hint_default_black;
uniform bool has_rma = false;
uniform bool has_normal = false;
uniform bool has_emission = false;
uniform bool flip_normal_y = true;
uniform vec4 tint : source_color = vec4(1.0);
uniform vec3 glow : source_color = vec3(1.0);
uniform vec2 uv_scale = vec2(1.0);
uniform float normal_strength = 1.0;
uniform float emission_strength = 1.0;
void fragment() {
    vec2 uv = UV * uv_scale;
    ALBEDO = texture(albedo_map, uv).rgb * tint.rgb;
    ROUGHNESS = 0.8;
    METALLIC = 0.0;
    if (has_rma) {
        vec3 rma = texture(rma_map, uv).rgb;
        ROUGHNESS = rma.r;
        METALLIC = rma.g;
        AO = rma.b;
    }
    if (has_normal) {
        vec3 n = texture(normal_map, uv).rgb;
        if (flip_normal_y) { n.g = 1.0 - n.g; }
        NORMAL_MAP = n;
        NORMAL_MAP_DEPTH = normal_strength;
    }
    if (has_emission) { EMISSION = texture(emission_map, uv).rgb * glow * emission_strength; }
}
'''

def props(values):
    return {p['name']:p.get('value') for p in values if 'value' in p}

def unpack_compressed(data, offset):
    if offset+32>len(data):raise ValueError('Truncated compressed bulk header')
    tag,chunk,compressed,size=struct.unpack_from('<4q',data,offset)
    if tag!=0x9e2a83c1 or not 0<chunk<=1024**2 or not 0<size<=256*1024**2 or not 0<compressed<=len(data):
        raise ValueError('Unsupported compressed bulk header')
    count=(size+chunk-1)//chunk;p=offset+32
    if p+16*count>len(data):raise ValueError('Truncated chunk table')
    chunks=[struct.unpack_from('<2q',data,p+i*16) for i in range(count)];p+=16*count
    if sum(c for c,u in chunks)!=compressed or sum(u for c,u in chunks)!=size:
        raise ValueError('Bulk size mismatch')
    blocks=[]
    for c,u in chunks:
        if c<=0 or u<=0 or u>chunk or p+c>len(data):raise ValueError('Invalid compressed chunk')
        decoder=zlib.decompressobj();b=decoder.decompress(data[p:p+c],u+1)
        if len(b)!=u or not decoder.eof or decoder.unused_data:raise ValueError('Compressed chunk mismatch')
        blocks.append(b);p+=c
    return b''.join(blocks),p

def find_mesh(data):
    p=0;signature=struct.pack('<q',0x9e2a83c1)
    while True:
        p=data.find(signature,p)
        if p<0:return
        try:
            raw,end=unpack_compressed(data,p)
            mesh=mesh_description(raw)
            yield p,mesh
            p=end
        except (ValueError,AssertionError,struct.error,UnicodeError,IndexError):p+=8

def glb(mesh,name,target):
    va,ia,ga,vmap,pg,triangles=mesh
    positions=va['Position'][0];normals=ia.get('Normal',[None])[0];uvs=ia['TextureCoordinate'][0]
    ids=sorted(vmap);lookup={i:n for n,i in enumerate(ids)}
    def convert(v,scale=1):return (v[1]*scale,v[2]*scale,-v[0]*scale)
    ps=[convert(positions[vmap[i]],.01) for i in ids]
    ns=[convert(normals[i]) for i in ids] if normals else None
    if ns:
        ns=[tuple(x/(math.sqrt(sum(y*y for y in n)) or 1) for x in n) for n in ns]
    ts=[tuple(uvs[i][:2]) for i in ids]
    if not all(math.isfinite(x) for array in (ps,ts,ns or []) for v in array for x in v):raise ValueError('Non-finite geometry')
    groups=defaultdict(list)
    for corners,poly in triangles:
        a,b,c=[lookup[i] for i in corners]
        if ns:
            u=[ps[b][i]-ps[a][i] for i in range(3)];v=[ps[c][i]-ps[a][i] for i in range(3)]
            cross=(u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0])
            n=[ns[a][i]+ns[b][i]+ns[c][i] for i in range(3)]
            if sum(cross[i]*n[i] for i in range(3))<0:b,c=c,b
        else:b,c=c,b
        groups[pg[poly]].extend([a,b,c])
    document={'asset':{'version':'2.0','generator':'PoliceDepartment direct UE4 exporter'},'scene':0,
              'scenes':[{'nodes':[0]}],'nodes':[{'name':'Mesh','mesh':0}],
              'meshes':[{'name':name,'primitives':[]}],'accessors':[],'bufferViews':[],'buffers':[{}]}
    binary=bytearray()
    def accessor(values,fmt,kind,components,target_type,bounds=False):
        while len(binary)%4:binary.append(0)
        start=len(binary);flat=[x for v in values for x in v] if components>1 else values
        binary.extend(struct.pack('<'+fmt*len(flat),*flat))
        view=len(document['bufferViews']);document['bufferViews'].append({'buffer':0,'byteOffset':start,'byteLength':len(binary)-start,'target':target_type})
        acc={'bufferView':view,'componentType':5126 if fmt=='f' else 5125,'count':len(values),'type':kind}
        if bounds:
            acc['min']=[min(v[i] for v in values) for i in range(components)]
            acc['max']=[max(v[i] for v in values) for i in range(components)]
        result=len(document['accessors']);document['accessors'].append(acc);return result
    attrs={'POSITION':accessor(ps,'f','VEC3',3,34962,True),'TEXCOORD_0':accessor(ts,'f','VEC2',2,34962)}
    if ns:attrs['NORMAL']=accessor(ns,'f','VEC3',3,34962)
    for group,indices in sorted(groups.items()):
        document['meshes'][0]['primitives'].append({'attributes':attrs,'indices':accessor(indices,'I','SCALAR',1,34963),'mode':4})
    document['buffers'][0]['byteLength']=len(binary)
    encoded=json.dumps(document,separators=(',',':'),allow_nan=False).encode();encoded+=b' '*((-len(encoded))%4);binary+=b'\0'*((-len(binary))%4)
    payload=struct.pack('<III',0x46546c67,2,12+8+len(encoded)+8+len(binary))+struct.pack('<II',len(encoded),0x4e4f534a)+encoded+struct.pack('<II',len(binary),0x004e4942)+binary
    target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(payload)
    slots=ga.get('ImportedMaterialSlotName',[[]])[0]
    return {'vertices':len(ps),'triangles':len(triangles),'groups':[{'group':g,'slot':slots[g] if g<len(slots) else str(g)} for g in sorted(groups)]}

def quoted(value):return json.dumps(str(value),ensure_ascii=False)
def ref_path(value):
    if isinstance(value,dict):value=value.get('object')
    if isinstance(value,str) and value.startswith('/Game/'):return value.split('.')[0]
    return None

def run(content,output,all_lods=False):
    files=sorted(content.rglob('*.uasset'))
    if not files:raise ValueError('No .uasset files found under Content')
    output.mkdir(parents=True)
    assets=output/'assets/police_department';assets.mkdir(parents=True)
    shader=assets/'materials/pbr_rma.gdshader';shader.parent.mkdir();shader.write_text(SHADER)
    reports=[];cache={};textures={};materials={};material_data={}
    def package(path):return '/Game/'+path.relative_to(content).with_suffix('').as_posix()
    def load(path):
        key=package(path)
        if key not in cache:cache[key]=deserialize(path)
        return cache[key]
    for p in files:
        if '/Textures/' not in p.as_posix():continue
        row={'source':str(p),'kind':'texture'};reports.append(row)
        try:
            d=load(p)
            if not any(e['class']=='/Script/Engine.Texture2D' for e in d['exports']):raise ValueError('Not Texture2D')
            data=p.read_bytes();candidates=list(embedded_pngs(data))
            if not candidates:raise ValueError('No embedded source PNG; native texture decoder required')
            entry=next(e for e in d['exports'] if e['class']=='/Script/Engine.Texture2D');properties=props(entry.get('properties',[]));source=props(properties.get('Source',[]))
            width,height=source.get('SizeX'),source.get('SizeY')
            exact=[c for c in candidates if c[2]==(width,height) and c[0]>entry['serial_offset']]
            source_png=None
            if not exact:
                # Source PNG can itself be stored in a zlib-compressed bulk container.
                position=entry['serial_offset']
                while True:
                    position=data.find(struct.pack('<q',0x9e2a83c1),position)
                    if position<0:break
                    try:
                        raw,bulk_end=unpack_compressed(data,position)
                        matching=[c for c in embedded_pngs(raw) if c[2]==(width,height)]
                        if matching:
                            a,b,dims=matching[-1];source_png=raw[a:b];break
                    except (ValueError,zlib.error,struct.error):pass
                    position+=8
                if source_png is None:raise ValueError('No full-size source PNG; refusing thumbnail substitution')
            else:
                start,end,dims=exact[-1];source_png=data[start:end]
            relative=Path('textures')/p.relative_to(content).with_suffix('.png');target=assets/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(source_png)
            textures[package(p)]={'path':relative.as_posix(),'flip_green':not properties.get('bFlipGreenChannel',False)}
            row.update(status='exported',file=relative.as_posix(),dimensions=dims)
        except Exception as error:row.update(status='failed',error=str(error))
        cache.pop(package(p),None)
    print('Textures:',sum(r.get('status')=='exported' for r in reports),'/',len(reports),flush=True)
    def resolved(key,seen=None):
        if key in material_data:return material_data[key]
        seen=set() if seen is None else set(seen)
        if key in seen:raise ValueError('Material inheritance cycle')
        seen.add(key)
        p=content/(key[len('/Game/'):]+'.uasset')
        d=load(p);values={};parent=None
        for e in d['exports']:
            fields=props(e.get('properties',[]));cls=e['class'] or ''
            if cls=='/Script/Engine.MaterialInstanceConstant':
                parent=ref_path(fields.get('Parent'))
                if parent:values.update(resolved(parent,seen))
                for array in ('TextureParameterValues','ScalarParameterValues','VectorParameterValues'):
                    for item in fields.get(array,[]):
                        f=props(item);name=props(f.get('ParameterInfo',[])).get('Name')
                        if name is not None:values[name]=f.get('ParameterValue')
            elif cls.startswith('/Script/Engine.MaterialExpression') and fields.get('ParameterName'):
                name=fields['ParameterName']
                if 'TextureSampleParameter' in cls:values[name]=fields.get('Texture')
                elif 'Parameter' in cls and 'DefaultValue' in fields:values[name]=fields['DefaultValue']
        material_data[key]=values;return values
    def material(key):
        if key in materials:return materials[key]
        if key=='__default__':
            relative=Path('materials/Unassigned.tres');target=assets/relative
            target.write_text('[gd_resource type="StandardMaterial3D" format=3]\n[resource]\nalbedo_color = Color(0.5, 0.5, 0.5, 1)\nroughness = 1.0\n')
            materials[key]=relative.as_posix();return materials[key]
        values=resolved(key)
        relative=Path('materials')/(key[len('/Game/'):]+'.tres');target=assets/relative;target.parent.mkdir(parents=True,exist_ok=True)
        text=['[gd_resource type="ShaderMaterial" load_steps=6 format=3]',
              '[ext_resource type="Shader" path="res://assets/police_department/materials/pbr_rma.gdshader" id="1"]']
        uniforms=[];mapped={}
        roles={'Base Color':('albedo_map',None),'Channel Packed Texture':('rma_map','has_rma'),'Normal':('normal_map','has_normal'),'Emissive Mask':('emission_map','has_emission')}
        for name,(uniform,toggle) in roles.items():
            ref=ref_path(values.get(name));tex=textures.get(ref)
            if not tex:continue
            identifier=str(len(mapped)+2);mapped[name]=ref
            text.append('[ext_resource type="Texture2D" path='+quoted('res://assets/police_department/'+tex['path'])+' id='+quoted(identifier)+']')
            uniforms.append('shader_parameter/'+uniform+' = ExtResource('+quoted(identifier)+')')
            if toggle:uniforms.append('shader_parameter/'+toggle+' = true')
            if name=='Normal':uniforms.append('shader_parameter/flip_normal_y = '+str(tex['flip_green']).lower())
        tint=values.get('Color Tint',[1,1,1,1]);tint=tint if isinstance(tint,list) and len(tint)==4 else [1,1,1,1]
        uniforms.append('shader_parameter/tint = Color('+', '.join(str(float(x)) for x in tint)+')')
        def scalar(name,default):
            v=values.get(name,default);return float(v) if isinstance(v,(int,float)) and math.isfinite(v) else default
        uniforms.append('shader_parameter/uv_scale = Vector2('+str(scalar('U Tiling',1))+', '+str(scalar('V Tiling',1))+')')
        uniforms.append('shader_parameter/normal_strength = '+str(scalar('Normal Intensity',1)))
        text[0]='[gd_resource type="ShaderMaterial" load_steps='+str(len(mapped)+2)+' format=3]'
        text+=['[resource]','shader = ExtResource("1")']+uniforms
        target.write_text('\n\n'.join(text)+'\n')
        materials[key]=relative.as_posix()
        reports.append({'kind':'material','source':key,'status':'approximated','file':relative.as_posix(),'mapped_textures':mapped,'parameters':values,
                        'limitations':'PBR approximation; custom shader graph, glass, decals, tint masks and parallax are not fully translated.'})
        return materials[key]
    meshes=[p for p in files if '/Meshes/' in p.as_posix()]
    for number,p in enumerate(meshes):
        row={'source':str(p),'kind':'mesh'};reports.append(row)
        try:
            d=load(p);entry=next(e for e in d['exports'] if e['class']=='/Script/Engine.StaticMesh');fields=props(entry.get('properties',[]))
            found=list(find_mesh(p.read_bytes()))
            if not found:raise ValueError('No supported compressed UE4 MeshDescription geometry found')
            slots={}
            for item in fields.get('StaticMaterials',[]):
                f=props(item);slots[f.get('ImportedMaterialSlotName',f.get('MaterialSlotName'))]=ref_path(f.get('MaterialInterface'))
            converted=[]
            for lod,(offset,mesh) in enumerate(found if all_lods else found[:1]):
                suffix='' if lod==0 else '_LOD'+str(lod)
                relative=Path('models')/p.relative_to(content).with_suffix('');relative=relative.with_name(relative.name+suffix).with_suffix('.glb')
                statistics=glb(mesh,p.stem+suffix,assets/relative)
                scene=Path('scenes')/relative.relative_to('models').with_suffix('.tscn');target=assets/scene;target.parent.mkdir(parents=True,exist_ok=True)
                lines=['[gd_scene load_steps=2 format=3]', '[ext_resource type="PackedScene" path='+quoted('res://assets/police_department/'+relative.as_posix())+' id="1"]'];overrides=[]
                for surface,g in enumerate(statistics['groups']):
                    key=slots.get(g['slot']) or '__default__'
                    try:
                        m=material(key);identifier=str(len(overrides)+2)
                        lines.append('[ext_resource type="Material" path='+quoted('res://assets/police_department/'+m)+' id='+quoted(identifier)+']')
                        overrides.append('surface_material_override/'+str(surface)+' = ExtResource('+quoted(identifier)+')')
                    except Exception as error:row.setdefault('material_errors',[]).append(str(error))
                lines[0]='[gd_scene load_steps='+str(len(overrides)+2)+' format=3]'
                lines.append('[node name='+quoted(p.stem+suffix)+' instance=ExtResource("1")]')
                if overrides:lines+=['[node name="Mesh" parent="." index="0"]']+overrides
                target.write_text('\n\n'.join(lines)+'\n')
                converted.append({'glb':relative.as_posix(),'scene':scene.as_posix(),'bulk_offset':offset,**statistics})
            row.update(status='exported',files=converted)
        except Exception as error:row.update(status='failed',error=str(error))
        cache.pop(package(p),None)
        if (number+1)%20==0:print('Meshes:',number+1,'/',len(meshes),flush=True)
    (output/'export_report.json').write_text(json.dumps(reports,indent=2,allow_nan=False)+'\n')
    (output/'project.godot').write_text('config_version=5\n[application]\nconfig/name="PoliceDepartment Godot Export"\n[rendering]\nrenderer/rendering_method="gl_compatibility"\n')
    (output/'README.txt').write_text('Open project.godot in Godot 4, or copy assets/police_department into an existing project at res://assets/police_department.\nDrag .tscn files from scenes/ into your level: these have material assignments. Raw .glb files hold geometry and UVs; use .tscn for textures.\nSource centimeters converted to meters, axes to Y-up. Only recovered imported LOD0 is exported by default; generated reduction LODs need regeneration.\nMaterials are a basic PBR approximation, not an exact Unreal shader translation. Glass, parallax, decals, foliage and tint masks need review.\nNormal green channel is adapted from Unreal texture import metadata. Source PNGs are kept unchanged.\nFailed assets and missing material data are listed in export_report.json. No thumbnail substitution.\n')
    failures=[r for r in reports if r.get('status')=='failed']
    print('DONE:',len(meshes)-sum(r['kind']=='mesh' for r in failures),'meshes;',len(textures),'textures;',len(materials),'materials;',len(failures),'failures',flush=True)
    return failures

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('content',type=Path);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--all-imported-lods',action='store_true')
    args=parser.parse_args()
    if args.output.exists():parser.error('Output must be a new folder')
    if not args.content.is_dir():parser.error('Supply the Content directory')
    try:return 2 if run(args.content.resolve(),args.output.resolve(),args.all_imported_lods) else 0
    except (OSError,ValueError) as e:print('Error:',e,file=sys.stderr);return 1
if __name__=='__main__':sys.exit(main())

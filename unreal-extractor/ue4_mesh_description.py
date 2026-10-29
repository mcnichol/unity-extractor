# UE4 MeshDescription layout adapted from @threenative/raw-unreal 0.1.6.
# Copyright (c) 2026 ThreeNative contributors. MIT; see THIRD_PARTY_LICENSE.txt.
# Reads allocation holes, dense attribute arrays, and trailing triangle containers.
import sys,struct,math
from read_uasset import Reader

def mesh_description(data):
 r=Reader(data)
 def count(n,maximum=10000000):
  if not 0<=n<=maximum:raise ValueError('Invalid count '+str(n))
  return n
 def bits():
  n=count(r.i32());words=[r.unpack('I') for _ in range((n+31)//32)]
  return n,[i for i in range(n) if words[i//32]&(1<<(i%32))]
 def attributes(expected,label=""):
  n=count(r.i32());k=count(r.i32(),1024)
  if n!=expected and label not in ("edge","polygon"):raise ValueError(label+' Attribute count disagrees: actual='+str(n)+' expected='+str(expected)+' offset='+str(r.pos))
  out={}
  types=[('f',4),('f',3),('f',2),('f',1),('i',1),('B',1),(None,1)]
  for _ in range(k):
   name=r.string().rstrip();typ=r.unpack('I');num=count(r.i32());channels=count(r.i32(),128)
   if typ>=len(types) or num!=n:raise ValueError('Invalid attribute shape')
   fmt,c=types[typ];values=[]
   for ch in range(channels):
    if fmt is None:
     size=count(r.i32());assert size==num;v=[r.string() for _ in range(size)]
    else:
     stride=r.i32();size=count(r.i32());assert size==num and stride==struct.calcsize('<'+fmt)*c
     raw=r.read(size*stride);flat=struct.unpack('<'+fmt*(num*c),raw);v=[flat[i*c:(i+1)*c] for i in range(num)]
    values.append(v)
   if fmt is None:r.string()
   else:r.read(4 if typ==5 else struct.calcsize('<'+fmt)*c)
   r.unpack('I');out[name]=values
  return out
 nv,verts=bits();ni,instances=bits();vmap={i:r.i32() for i in instances}
 ne,edges=bits();r.read(len(edges)*8)
 np,polygons=bits();pg={}
 for i in polygons:r.read(count(r.i32())*4);pg[i]=r.i32()
 ng,groups=bits()
 va=attributes(nv,"vertex");ia=attributes(ni,"instance");attributes(ne,"edge");attributes(np,"polygon");ga=attributes(ng,"group")
 nt,tris=bits();triangles=[]
 for i in tris:triangles.append(([r.i32() for _ in range(3)],r.i32()))
 attributes(nt)
 if r.pos!=len(data):raise ValueError('Trailing bytes '+str(len(data)-r.pos))
 assert len(verts)>0 and len(tris)>0
 for i,v in vmap.items():assert v in verts
 for corners,poly in triangles:
  assert poly in pg and pg[poly] in groups and all(i in vmap for i in corners)
 return va,ia,ga,vmap,pg,triangles


"""Original procedural cloud station. Blender 4.5, no addons or external assets.
Run: blender -b -t 8 --python scripts/seasonal/create_scene.py -- --preview
"""
import bpy, math, random, argparse, sys
from pathlib import Path
from mathutils import Vector

argsv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
p = argparse.ArgumentParser()
p.add_argument('--preview', action='store_true')
p.add_argument('--full', action='store_true')
p.add_argument('--season', choices=['spring','summer','autumn','winter'], default='autumn')
p.add_argument('--output', default=str(Path(__file__).resolve().parents[2] / 'work/seasonal-art'))
p.add_argument('--samples', type=int, default=0)
p.add_argument('--subjects', action='store_true', help='Legacy: render transparent subject only; prefer --layers for both layers')
p.add_argument('--layers', action='store_true', help='Also render clean background and transparent subject for real 2.5D composition')
p.add_argument('--all', action='store_true', help='Render all four seasons instead of --season')
p.add_argument('--only', choices=['desktop','mobile'], help='Restrict full rendering to one orientation')
a = p.parse_args(argsv)
out = Path(a.output); out.mkdir(parents=True, exist_ok=True)
random.seed(127)
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
for d in list(bpy.data.materials): bpy.data.materials.remove(d)
scene = bpy.context.scene
scene.render.engine = 'CYCLES'; scene.cycles.device = 'CPU'
scene.cycles.samples = a.samples or (20 if not a.full else 64)
scene.cycles.use_denoising = True
scene.cycles.max_bounces = 6; scene.cycles.diffuse_bounces = 3; scene.cycles.glossy_bounces = 3
scene.render.image_settings.file_format = 'PNG'; scene.render.image_settings.color_mode = 'RGBA'
scene.render.film_transparent = False
scene.view_settings.view_transform = 'AgX'
scene.render.resolution_percentage = 100
scene.world.use_nodes = True
worldbg = scene.world.node_tree.nodes.get('Background')
worldbg.inputs['Color'].default_value = (.34,.45,.59,1); worldbg.inputs['Strength'].default_value = .4

# Named collections remain editable; weather is independent real geometry.
def coll(name):
    c = bpy.data.collections.new(name); scene.collection.children.link(c); return c
SUB = coll('STATION • permanent geometry'); ENV = coll('ATMOSPHERE • mountains and cloud sea')
LEAVES = coll('CANOPY • seasonal leaves'); SNOW = coll('WINTER • independent snow caps')
current = SUB

def move(obj):
    for c in list(obj.users_collection): c.objects.unlink(obj)
    current.objects.link(obj); return obj

def mat(name, color, rough=.7, noise=6, bump=.16, metallic=0):
    m=bpy.data.materials.new(name); m.use_nodes=True
    n=m.node_tree.nodes; l=m.node_tree.links; bs=n.get('Principled BSDF')
    bs.inputs['Base Color'].default_value=(*color,1); bs.inputs['Roughness'].default_value=rough
    bs.inputs['Metallic'].default_value=metallic
    tx=n.new('ShaderNodeTexNoise'); tx.inputs['Scale'].default_value=noise; tx.inputs['Detail'].default_value=3
    ramp=n.new('ShaderNodeValToRGB'); ramp.color_ramp.elements[0].position=.15
    ramp.color_ramp.elements[0].color=(*(c*.55 for c in color),1)
    ramp.color_ramp.elements[1].position=.85; ramp.color_ramp.elements[1].color=(*color,1)
    l.new(tx.outputs['Fac'],ramp.inputs[0]); l.new(ramp.outputs[0],bs.inputs['Base Color'])
    b=n.new('ShaderNodeBump'); b.inputs['Strength'].default_value=bump; b.inputs['Distance'].default_value=.075
    l.new(tx.outputs['Fac'],b.inputs['Height']); l.new(b.outputs[0],bs.inputs['Normal'])
    r=n.new('ShaderNodeMapRange'); r.inputs['To Min'].default_value=max(.1,rough-.15); r.inputs['To Max'].default_value=min(1,rough+.15)
    l.new(tx.outputs['Fac'],r.inputs['Value']); l.new(r.outputs[0],bs.inputs['Roughness'])
    return m
bark=mat('Bark | chestnut ridged grain',(.23,.105,.046),noise=12,bump=.55)
wood=mat('Cedar siding | worn grain',(.46,.235,.105),noise=18,bump=.24)
trim=mat('Honey oak edge beams',(.62,.36,.16),noise=9)
dark=mat('Dark end grain',(.12,.065,.038),noise=13)
rock=mat('Basalt | weathered mineral',(.28,.30,.28),noise=4,bump=.65)
soil=mat('Moss earth',(.23,.28,.09),noise=14,bump=.4)
grass=mat('Meadow blades',(.42,.49,.16),noise=7)
roofm=[mat('Fired clay shingle %02d'%i,(.31+i*.025,.105+i*.015,.047+i*.01),noise=12) for i in range(5)]
leafm=[mat('Seasonal foliage %02d'%i,(.7,.3,.05),noise=3,bump=.13) for i in range(6)]
snowmat=mat('Snow | crystalline soft surface',(.88,.95,1),rough=.84,noise=35,bump=.18)
metal=mat('Forged bronze',(.12,.16,.15),rough=.38,metallic=.7)
bagmat=mat('Traveller canvas | teal',(.06,.24,.25),noise=40,bump=.3)
cloudmat=mat('Cloud pearl',(.70,.79,.85),noise=2,bump=.03)
mountm=[mat('Distant blue ridge %s'%i,(.19+i*.045,.28+i*.045,.34+i*.045),noise=2,bump=.1) for i in range(3)]
glow=bpy.data.materials.new('Window | amber lamp glass'); glow.use_nodes=True
gbs=glow.node_tree.nodes.get('Principled BSDF'); gbs.inputs['Base Color'].default_value=(1,.49,.12,1)
gbs.inputs['Emission Color'].default_value=(1,.29,.055,1); gbs.inputs['Emission Strength'].default_value=3

def material(obj,m): obj.data.materials.append(m); return obj

def cube(name,loc,scale,m,bevel=.04):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc); ob=move(bpy.context.object); ob.name=name
    ob.dimensions=scale; bpy.ops.object.transform_apply(location=False,rotation=False,scale=True)
    material(ob,m)
    if bevel:
        mod=ob.modifiers.new('Rounded handworked edges','BEVEL'); mod.width=bevel; mod.segments=2
        ob.modifiers.new('Weighted normals','WEIGHTED_NORMAL')
    return ob

def ico(name,loc,scale,m,sub=2):
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=sub,radius=1,location=loc)
    ob=move(bpy.context.object); ob.name=name; ob.scale=scale; material(ob,m)
    for v in ob.data.vertices:
        v.co *= random.uniform(.89,1.11)
    for f in ob.data.polygons:f.use_smooth=True
    return ob

def tube(name,points,radii,m,sides=10):
    # Parallel-ish frames produce genuine tapered branch meshes, not straight cylinders.
    vs=[]; fs=[]
    for i,co in enumerate(points):
        co=Vector(co); tang=Vector(points[min(i+1,len(points)-1)])-Vector(points[max(0,i-1)])
        tang.normalize(); u=tang.cross(Vector((0,1,0))).normalized(); v=tang.cross(u).normalized()
        for j in range(sides):
            ang=2*math.pi*j/sides; rr=radii[i]*(1+.1*math.sin(j*3+i))
            vs.append(co+rr*(u*math.cos(ang)+v*math.sin(ang)))
    for i in range(len(points)-1):
        for j in range(sides):
            n=i*sides+j; k=i*sides+(j+1)%sides; fs.append((n,k,k+sides,n+sides))
    fs += [tuple(reversed(range(sides))),tuple((len(points)-1)*sides+j for j in range(sides))]
    mesh=bpy.data.meshes.new(name); mesh.from_pydata(vs,[],fs); mesh.update()
    ob=bpy.data.objects.new(name,mesh); current.objects.link(ob); material(ob,m)
    for f in mesh.polygons:f.use_smooth=True
    return ob

# Floating island: irregular concentric strata and visible hanging rock wedges.
vs=[]; fs=[]; rings=[(3.6,.05),(3.9,-.55),(3.15,-1.5),(2.15,-2.6),(.85,-3.55),(.1,-4.1)]
for r,z in rings:
    for j in range(40):
        t=j*math.tau/40; q=random.uniform(.9,1.1); vs.append((r*math.cos(t)*q,r*.77*math.sin(t)*q,z+random.uniform(-.18,.18)))
for k in range(len(rings)-1):
    for j in range(40):
        n=k*40+j; nxt=k*40+(j+1)%40; fs += [(n,nxt,nxt+40),(n,nxt+40,n+40)]
fs.append(tuple(range(40)))
mesh=bpy.data.meshes.new('Eroded floating geological strata'); mesh.from_pydata(vs,[],fs); mesh.update()
ob=bpy.data.objects.new('Floating island • sculpted basalt',mesh); SUB.objects.link(ob); material(ob,rock)
be=ob.modifiers.new('Weather-softened strata','BEVEL'); be.width=.09; be.segments=2
ico('Mossy turf crown',(0,0,-.08),(3.65,2.82,.40),soil,3)
for i in range(13):
    t=random.random()*math.tau; r=random.uniform(2,3.3)
    ico('Embedded mineral outcrop',(r*math.cos(t),r*.75*math.sin(t),-.65),(random.uniform(.4,.8),.5,random.uniform(.65,1.2)),rock,2)
# Tree silhouette arcs over the cabin.
tube('Ancient twisting trunk',[(-1.65,.7,-.1),(-1.55,.72,1.4),(-1.8,.8,2.7),(-1.65,.9,4),(-1.05,.95,5.2),(-.7,1,6.4),(-1,1.05,7.6)],[.72,.63,.55,.46,.32,.21,.045],bark,16)
for i in range(13):
    t=math.tau*i/13; x=-1.6+math.cos(t)*.35; y=.7+math.sin(t)*.35
    tube('Buttress and hanging root %02d'%i,[(x,y,.9),(x+math.cos(t)*.65,y+math.sin(t)*.6,.15),(math.cos(t)*2.8,math.sin(t)*2,-.15),(math.cos(t)*3.15,math.sin(t)*2.35,-1.4),(math.cos(t)*2.3,math.sin(t)*1.8,-2.9)],[.18,.24,.15,.09,.012],bark,9)
ends=[]
for i in range(12):
    t=i*2.4; z=random.uniform(4.5,6.7); x=-1.3; y=.9
    end=(x+math.cos(t)*random.uniform(2.2,3.4),y+math.sin(t)*random.uniform(1.6,2.8),z+random.uniform(.8,1.7))
    mid=((x+end[0])*.5,(y+end[1])*.5,z+.45)
    tube('Sweeping crown bough %02d'%i,[(x,y,z-.6),mid,end],[.23,.14,.025],bark,10)
    ends.append(end)
    for j in [-1,1]:
        e=(end[0]+j*.7,end[1]+random.uniform(-.6,.6),end[2]+.35)
        tube('Fine fork', [mid,((mid[0]+e[0])*.5,(mid[1]+e[1])*.5,e[2]-.2),e],[.075,.05,.008],bark,7); ends.append(e)
# Leaf cards are plump six-vertex leaf meshes aggregated by material, not sphere blobs.
current=LEAVES
leafverts=[[] for _ in leafm]; leaffaces=[[] for _ in leafm]
for end in ends:
    for i in range(170):
        center=Vector(end)+Vector((random.gauss(0,.65),random.gauss(0,.53),random.gauss(0,.38)))
        axis=Vector((random.uniform(-1,1),random.uniform(-1,1),random.uniform(-.3,.8))).normalized()
        side=axis.cross(Vector((0,0,1))).normalized(); size=random.uniform(.12,.24)
        points=[center-axis*size,center-side*size*.45,center+Vector((0,0,.045)),center+axis*size,center+side*size*.45,center-Vector((0,0,.025))]
        mi=random.randrange(len(leafm)); v=leafverts[mi]; f=leaffaces[mi]; k=len(v); v.extend(points)
        f.extend(tuple(k+j for j in face) for face in [(0,1,2),(1,3,2),(3,4,2),(4,0,2),(1,0,5),(3,1,5),(4,3,5),(0,4,5)])
for i,m in enumerate(leafm):
    me=bpy.data.meshes.new('Individual almond leaves'); me.from_pydata(leafverts[i],[],leaffaces[i]); me.update()
    ob=bpy.data.objects.new('Canopy leaves palette %s'%i,me); current.objects.link(ob); material(ob,m)
current=SNOW
for e in ends[::2]: ico('Snow resting on crown', (e[0],e[1],e[2]+.1),(.78,.57,.18),snowmat,2)
current=SUB
# Cabin: separate thick siding, gables, exposed framing, shingled roof.
cx=.6; cy=-.35
cube('Cabin dark interior',(cx,cy,1.6),(2.65,2.15,2.65),dark)
for z in [ .43+i*.20 for i in range(13) ]:
    cube('Front cedar weatherboard',(cx,-1.46,z),(2.8,.16,.185),wood,.025)
    cube('Side cedar weatherboard',(2.01,cy,z),(.16,2.22,.185),wood,.025)
    cube('Left cedar weatherboard',(-.81,cy,z),(.16,2.22,.185),wood,.025)
for x in [-.87,2.07]:
    for y in [-1.5,.8]:cube('Structural corner post',(x,y,1.6),(.18,.20,2.9),trim)
# Triangular gable filled with real staggered horizontal boards.
for i in range(9):
    z=2.98+i*.15; width=2.8*(1-i/10)
    cube('Gable cedar plank',(cx,-1.46,z),(width,.15,.14),wood,.02)
# Roof halves slope down along X. Each shingle has thick rounded edges and overlap.
angle=math.atan2(1.5,1.72)
for side in [-1,1]:
    slab=cube('Roof timber underlay',(cx+side*.86,cy,3.72),(2.30,2.95,.16),dark)
    slab.rotation_euler[1]=side*angle
    for row in range(7):
        d=.12+row*.265
        for col in range(10):
            yy=cy-1.40+col*.305+(row%2)*.065
            zz=4.48-d*math.tan(angle)
            tile=cube('Overlapping terracotta tile',(cx+side*d,yy,zz),(.39,.33,.105),random.choice(roofm),.045)
            tile.rotation_euler[1]=side*angle
    current=SNOW
    cap=cube('Thick roof snow blanket',(cx+side*.86,cy,3.86),(2.32,2.99,.18),snowmat,.13); cap.rotation_euler[1]=side*angle
    current=SUB
for j in range(11): ico('Ridge cap tile',(cx,cy-1.4+j*.28,4.52),(.17,.19,.13),roofm[2],2)
# Front glowing window, sill, shutters, crossbars; door remains distinct.
cube('Window amber panes',(.05,-1.565,1.98),(1.03,.06,.94),glow)
for x in [-.54,.64]: cube('Window thick vertical jamb',(x,-1.65,1.98),(.12,.18,1.19),trim)
for z in [1.43,2.54]:cube('Window thick lintel',(.05,-1.65,z),(1.29,.18,.13),trim)
cube('Window crossbar horizontal',(.05,-1.69,1.98),(1.12,.10,.06),dark,.01)
cube('Window crossbar vertical',(.05,-1.69,1.98),(.06,.10,1.03),dark,.01)
cube('Deep window sill',(.05,-1.73,1.40),(1.4,.4,.13),trim)
for x in [-.76,.86]:
    for z in [1.61+j*.17 for j in range(5)]:cube('Shutter louver',(x,-1.60,z),(.24,.12,.13),bagmat,.015)
for i in range(5):cube('Entry door plank',(1.22+i*.13,-1.565,1.35),(.12,.12,1.88),trim,.02)
cube('Door lintel',(1.48,-1.65,2.38),(.83,.2,.14),dark)
ico('Brass door knob',(1.70,-1.72,1.35),(.055,.04,.055),metal,2)
# Raised deck, stairs, railings.
for i in range(19):cube('Deck individual plank',(.60,-2.0+i*.11,.32),(3.42,.105,.15),wood,.018)
for k in range(4):cube('Broad entrance step',(1.42,-2.25-k*.27,.28-k*.095),(1.25,.32,.17),trim)
for x in [-1.1,2.3]:
    for y in [-2.0,-.9]:cube('Porch newel post',(x,y,.78),(.12,.12,1.05),trim)
    cube('Porch rounded handrail',(x,-1.45,1.3),(.15,1.28,.13),trim)
    for y in [-1.85,-1.60,-1.35,-1.1]:cube('Porch baluster',(x,y,.88),(.06,.06,.72),trim,.015)
# Chimney and snow mound.
cube('Stone chimney',(1.45,.30,4.2),(.48,.48,1.12),rock)
for z in [3.8,4.03,4.26,4.49]:cube('Chimney mortar course',(1.45,.30,z),(.51,.51,.035),dark,.01)
cube('Chimney crown',(1.45,.30,4.79),(.65,.65,.15),rock)
current=SNOW; ico('Snow chimney crown',(1.45,.3,4.9),(.39,.39,.13),snowmat); current=SUB
# Travel storytelling props: canvas pack, rolled bedroll, buckles and signpost.
cube('Traveller backpack',(-1.65,-1.6,.55),(.58,.37,.78),bagmat,.15)
cube('Backpack front pocket',(-1.65,-1.82,.46),(.42,.14,.34),bagmat,.08)
for x in [-1.84,-1.47]:cube('Leather pack straps',(x,-1.91,.60),(.055,.045,.65),trim,.01)
tube('Rolled wool blanket',[(-2,-1.6,.98),(-1.32,-1.6,.98)],[.16,.16],wood,16)
cube('Wayfinding post',(-2.65,-.9,.85),(.14,.14,1.65),dark)
sg=cube('Arrow sign upper',(-2.65,-.94,1.55),(1.05,.14,.24),trim); sg.rotation_euler[1]=-.09
sg=cube('Arrow sign lower',(-2.65,-.94,1.23),(.84,.14,.22),wood); sg.rotation_euler[1]=.12
# Aged stepping stones and individually curved meadow blades, merged for efficiency.
for i in range(17):
    x=random.uniform(-3.1,3.1); y=random.uniform(-2.25,2.1)
    if -.95<x<2.3 and -2.3<y<1: continue
    ico('Meadow river stone',(x,y,.18),(.22,.17,.12),rock,2)
vs=[]; fs=[]
for i in range(1100):
    x=random.uniform(-3.4,3.4); y=random.uniform(-2.5,2.5)
    if (x/3.4)**2+(y/2.5)**2>1 or (-1.1<x<2.4 and -2.3<y<1):continue
    z=.15; h=random.uniform(.12,.36); w=random.uniform(.018,.035); k=len(vs)
    vs.extend([(x-w,y,z),(x+w,y,z),(x+w*.4+.06,y+.03,z+h*.6),(x+.1,y+.04,z+h)])
    fs.extend([(k,k+1,k+2),(k,k+2,k+3)])
me=bpy.data.meshes.new('Tapered grass blades'); me.from_pydata(vs,[],fs); me.update()
ob=bpy.data.objects.new('Wild meadow grass',me); SUB.objects.link(ob); material(ob,grass)
current=SNOW
ico('Island snow mantle',(0,0,.14),(3.55,2.72,.26),snowmat,3)
for i in range(4):ico('Snow edge drifts',(-2.8+i*1.7,-1.95,.25),(.60,.45,.17),snowmat,2)
# Atmosphere built with opaque soft forms rather than expensive volume marching.
current=ENV
for row in range(3):
    for i in range(14):
        x=-30+i*4.8; y=17+row*10; z=-5.8+row*.35
        # Irregular distant crests have actual layered geometry, deliberately subdued.
        ico('Hazy distant mountain', (x,y,z),(random.uniform(4,7),3.5,random.uniform(2.5,5)),mountm[row],2)
for i in range(65):
    x=random.uniform(-40,40); y=random.uniform(7,40); z=random.uniform(-5.7,-4.6)
    ico('Cloud sea billow',(x,y,z),(random.uniform(2.8,6),random.uniform(1.8,3.5),random.uniform(.35,.8)),cloudmat,2)
# Giant matte background plane prevents a horizon seam and keeps the form zone calm.
sky=mat('Backdrop | mist sky',(.48,.59,.68),rough=1,noise=.12,bump=0)
back=cube('Infinite mist backdrop',(0,65,10),(220,.2,160),sky,0)
current=SUB

def area(name,loc,power,color,size,target):
    bpy.ops.object.light_add(type='AREA',location=loc); ob=bpy.context.object; ob.name=name
    ob.data.energy=power; ob.data.color=color; ob.data.shape='DISK'; ob.data.size=size
    ob.rotation_euler=(Vector(target)-ob.location).to_track_quat('-Z','Y').to_euler(); return ob
key=area('Soft sunset key',(-7,-8,13),1800,(1,.75,.49),8,(0,0,2))
area('Cool skylight fill',(6,-1,10),1000,(.55,.74,1),10,(0,0,3))
area('Canopy rim',(0,7,11),2100,(1,.67,.33),7,(0,0,4))
area('Window warm spill',(.1,-1.9,2),28,(1,.35,.07),1,(0,-4,0))
bpy.ops.object.camera_add(); camera=bpy.context.object; camera.name='CAMERA • composition safe zone'; scene.camera=camera
camera.data.type='ORTHO'; camera.data.lens=50

def composition(kind):
    if kind=='desktop':
        camera.location=(11,-23,12); target=Vector((0,0,2.3)); camera.data.ortho_scale=34
        camera.data.shift_x=.20; camera.data.shift_y=.033
        scene.render.resolution_x=1920; scene.render.resolution_y=1080
    else:
        camera.location=(10,-24,11); target=Vector((0,0,2.2)); camera.data.ortho_scale=30
        camera.data.shift_x=-.043; camera.data.shift_y=-.08
        scene.render.resolution_x=1080; scene.render.resolution_y=1920
    camera.rotation_euler=(target-camera.location).to_track_quat('-Z','Y').to_euler()

palettes={
 'spring':[(.38,.58,.16),(.57,.68,.23),(.72,.77,.32),(.76,.44,.50),(.90,.61,.65),(.63,.72,.29)],
 'summer':[(.16,.37,.08),(.25,.46,.10),(.39,.57,.16),(.49,.65,.22),(.30,.51,.14),(.60,.67,.25)],
 'autumn':[(.68,.18,.035),(.86,.35,.045),(.95,.57,.09),(.68,.28,.055),(.83,.46,.07),(.48,.19,.055)],
 'winter':[(.36,.43,.35)]*6}
def recolor(m,c):
    ramp=next(n for n in m.node_tree.nodes if n.type=='VALTORGB')
    ramp.color_ramp.elements[0].color=(*(x*.55 for x in c),1); ramp.color_ramp.elements[1].color=(*c,1)
def season(s):
    LEAVES.hide_render=s=='winter'; LEAVES.hide_viewport=s=='winter'
    SNOW.hide_render=s!='winter'; SNOW.hide_viewport=s!='winter'
    for m,c in zip(leafm,palettes[s]):recolor(m,c)
    recolor(grass,{'spring':(.47,.61,.20),'summer':(.31,.48,.12),'autumn':(.48,.43,.14),'winter':(.51,.49,.34)}[s])
    recolor(soil,{'spring':(.27,.39,.13),'summer':(.24,.34,.10),'autumn':(.32,.29,.10),'winter':(.42,.43,.37)}[s])
    key.data.color={'spring':(1,.83,.67),'summer':(1,.91,.75),'autumn':(1,.72,.43),'winter':(.73,.85,1)}[s]
    scene['season']=s

def render(filename,layer='poster'):
    # Camera and all lights are in the scene root, never hidden by layer toggles.
    # Restore exact seasonal visibility even when a render raises an exception.
    collections=(ENV,SUB,LEAVES,SNOW)
    previous=[c.hide_render for c in collections]
    previous_alpha=scene.render.film_transparent
    try:
        if layer=='background':
            ENV.hide_render=False
            for c in (SUB,LEAVES,SNOW): c.hide_render=True
        elif layer=='subject':
            ENV.hide_render=True
        scene.render.film_transparent=layer=='subject'
        scene.render.filepath=str(out/filename)
        bpy.ops.render.render(write_still=True)
    finally:
        for c,hidden in zip(collections,previous): c.hide_render=hidden
        scene.render.film_transparent=previous_alpha

def render_layers(prefix):
    if a.layers: render(prefix+'-background.png','background')
    if a.layers or a.subjects: render(prefix+'-subject.png','subject')

season(a.season); composition('desktop')
scene['original_asset_notice']='Original seeded procedural geometry. No downloaded models, textures or reference images.'
scene['reproduce']='Blender 4.5 CPU Cycles; run create_scene.py -- --full; fixed seed 127.'
# Save native editable mother scene before any expensive rendering.
bpy.ops.wm.save_as_mainfile(filepath=str(out/'seasonal-station.blend'))
if not a.full:
    kind=a.only or 'desktop'; composition(kind)
    scene.render.resolution_x=720 if kind=='mobile' else 1280
    scene.render.resolution_y=1280 if kind=='mobile' else 720
    render(a.season+('-mobile-preview.png' if kind=='mobile' else '-preview.png'))
    render_layers(a.season+'-'+kind+'-preview')
else:
    for s in (['spring','summer','autumn','winter'] if a.all else [a.season]):
        season(s)
        for kind in ([a.only] if a.only else ['desktop','mobile']):
            composition(kind); render(s+'-'+kind+'.png')
            render_layers(s+'-'+kind)
    season('autumn'); composition('desktop')
    bpy.ops.wm.save_as_mainfile(filepath=str(out/'seasonal-station.blend'))
print('SEASONAL_SCENE_COMPLETE',str(out),flush=True)

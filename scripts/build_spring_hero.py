"""Build the OmniRoute spring hero scene in Blender.

Run with Blender 4.x:
  blender -b --python scripts/build_spring_hero.py

Outputs:
  front/public/hero/v2/spring-desktop.webp
  front/public/hero/v2/spring-mobile.webp
  front/public/hero/v2/spring-hero.glb

This is an authored procedural base scene. Replace the generated bark/foliage
materials with scanned or hand-authored PBR textures for the final art pass.
"""
import bpy, math, os
from mathutils import Vector

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'front', 'public', 'hero', 'v2'))
TEX = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'front', 'public', 'hero', 'v3', 'textures'))
os.makedirs(ROOT, exist_ok=True)

def clear():
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    for datablocks in (bpy.data.materials, bpy.data.curves, bpy.data.meshes, bpy.data.cameras, bpy.data.lights):
        for block in list(datablocks):
            if block.users == 0: datablocks.remove(block)

def mat(name, color, rough=.8, metallic=0):
    m=bpy.data.materials.new(name); m.diffuse_color=(*color,1); m.use_nodes=True
    bs=m.node_tree.nodes.get('Principled BSDF'); bs.inputs['Base Color'].default_value=(*color,1); bs.inputs['Roughness'].default_value=rough; bs.inputs['Metallic'].default_value=metallic
    return m

def procedural_mat(name, base, detail, scale=4.0, rough=.82):
    m=mat(name, base, rough); n=m.node_tree.nodes; l=m.node_tree.links
    tex=n.new('ShaderNodeTexNoise'); tex.inputs['Scale'].default_value=scale; tex.inputs['Detail'].default_value=5; tex.inputs['Roughness'].default_value=.75
    ramp=n.new('ShaderNodeValToRGB'); ramp.color_ramp.elements[0].color=(*base,1); ramp.color_ramp.elements[1].color=(*detail,1)
    l.new(tex.outputs['Fac'], ramp.inputs['Fac']); l.new(ramp.outputs['Color'], n.get('Principled BSDF').inputs['Base Color'])
    bump=n.new('ShaderNodeBump'); bump.inputs['Strength'].default_value=.22; bump.inputs['Distance'].default_value=.18; l.new(tex.outputs['Fac'], bump.inputs['Height']); l.new(bump.outputs['Normal'], n.get('Principled BSDF').inputs['Normal'])
    return m

def pbr_mat(name, stem, base_fallback, rough=.82):
    """Load a Poly Haven style diffuse/roughness/normal/height set when present."""
    paths={k:os.path.join(TEX, f'{stem}-{k}.jpg') for k in ('diffuse','rough','nor_gl','displacement')}
    if not os.path.exists(paths['diffuse']):
        return procedural_mat(name, base_fallback, tuple(min(1,c*1.8) for c in base_fallback), 8, rough)
    m=bpy.data.materials.new(name); m.use_nodes=True; n=m.node_tree.nodes; l=m.node_tree.links; bs=n.get('Principled BSDF')
    tex=n.new('ShaderNodeTexCoord'); mapping=n.new('ShaderNodeMapping'); l.new(tex.outputs['UV'],mapping.inputs['Vector'])
    img=n.new('ShaderNodeTexImage'); img.image=bpy.data.images.load(paths['diffuse'], check_existing=True); l.new(mapping.outputs['Vector'],img.inputs['Vector']); l.new(img.outputs['Color'],bs.inputs['Base Color'])
    rough_img=n.new('ShaderNodeTexImage'); rough_img.image=bpy.data.images.load(paths['rough'], check_existing=True); rough_img.image.colorspace_settings.name='Non-Color'; l.new(mapping.outputs['Vector'],rough_img.inputs['Vector']); l.new(rough_img.outputs['Color'],bs.inputs['Roughness'])
    normal=n.new('ShaderNodeTexImage'); normal.image=bpy.data.images.load(paths['nor_gl'], check_existing=True); normal.image.colorspace_settings.name='Non-Color'; normal_map=n.new('ShaderNodeNormalMap'); normal_map.inputs['Strength'].default_value=.75; l.new(mapping.outputs['Vector'],normal.inputs['Vector']); l.new(normal.outputs['Color'],normal_map.inputs['Color']); l.new(normal_map.outputs['Normal'],bs.inputs['Normal'])
    height=n.new('ShaderNodeTexImage'); height.image=bpy.data.images.load(paths['displacement'], check_existing=True); height.image.colorspace_settings.name='Non-Color'; bump=n.new('ShaderNodeBump'); bump.inputs['Strength'].default_value=.18; bump.inputs['Distance'].default_value=.08; l.new(mapping.outputs['Vector'],height.inputs['Vector']); l.new(height.outputs['Color'],bump.inputs['Height']); l.new(bump.outputs['Normal'],bs.inputs['Normal'])
    return m

def add_uv(name, loc, scale, material):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, location=loc)
    o=bpy.context.object; o.name=name; o.scale=scale; bpy.ops.object.transform_apply(location=False, rotation=False, scale=True); o.data.materials.append(material)
    for p in o.data.polygons: p.use_smooth=True
    return o

def add_cylinder(name, a, b, radius, material):
    a,b=Vector(a),Vector(b); d=b-a
    bpy.ops.mesh.primitive_cylinder_add(vertices=24, radius=radius, depth=d.length, location=(a+b)/2)
    o=bpy.context.object; o.name=name; o.data.materials.append(material); o.rotation_mode='QUATERNION'; o.rotation_quaternion=d.to_track_quat('Z','Y')
    for p in o.data.polygons: p.use_smooth=True
    return o

def add_cube(name, loc, scale, material, bevel=0.0, rot=(0,0,0)):
    bpy.ops.mesh.primitive_cube_add(location=loc, rotation=rot)
    o=bpy.context.object; o.name=name; o.scale=scale; bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    if bevel:
        mod=o.modifiers.new('Soft worn edges','BEVEL'); mod.width=bevel; mod.segments=3
    o.data.materials.append(material); return o

def add_rock(name, loc, scale, material, rot=(0,0,0)):
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=1, location=loc, rotation=rot)
    o=bpy.context.object; o.name=name; o.scale=scale; bpy.ops.object.transform_apply(location=False, rotation=False, scale=True); o.data.materials.append(material)
    for p in o.data.polygons: p.use_smooth=True
    return o

def add_leaf_cluster(name, loc, scale, material, rot=(0,0,0)):
    # Three overlapping low-poly volumes read as a natural branch cluster.
    for j, off in enumerate(((-.22,0,.03),(.18,.04,.08),(0,-.12,.18))):
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=1, location=(loc[0]+off[0]*scale[0],loc[1]+off[1]*scale[1],loc[2]+off[2]*scale[2]), rotation=rot)
        o=bpy.context.object; o.name=f'{name}_{j}'; o.scale=(scale[0]*(.72 if j else .82),scale[1]*(.72 if j else .85),scale[2]*(.68 if j else .8)); bpy.ops.object.transform_apply(location=False, rotation=False, scale=True); o.data.materials.append(material)
        for p in o.data.polygons: p.use_smooth=True

def add_bark_ridge(name, a, b, radius, material, phase=0.0):
    # Raised vertical strips break up the trunk silhouette and catch the rim light.
    a,b=Vector(a),Vector(b); d=b-a; mid=(a+b)/2
    for k in range(4):
        ang=phase + k*math.tau/4
        off=Vector((math.cos(ang)*radius*.72, math.sin(ang)*radius*.72, 0))
        add_cylinder(f'{name}_{k}',a+off,b+off,radius*.075,material)

def build():
    clear()
    bark=pbr_mat('Bark scanned','bark',(0.025,.008,.003),.94); bark2=pbr_mat('Bark highlight','bark',(0.09,.025,.008),.9); wood=pbr_mat('Cabin wood scanned','wood',(.11,.028,.008),.86); roof=mat('Roof',(.018,.012,.01),.96); grass=pbr_mat('Forest ground scanned','ground',(.025,.09,.012),.92); leaf=procedural_mat('Young leaves',(.06,.22,.025),(.3,.62,.08),9,.74); leaf2=procedural_mat('Leaf warm',(.12,.32,.035),(.52,.78,.12),12,.7); rock=pbr_mat('Island rock scanned','rock',(.012,.022,.025),.98); cloud=procedural_mat('Cloud',(.52,.62,.68),(.9,.94,.95),2.8,.98); glow=mat('Window glow',(1,.22,.035),.24)
    # Floating island and rock underside
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=4, radius=5, location=(1,-1,-2)); island=bpy.context.object; island.name='Floating_Island'; island.scale=(1.65,.85,.42); island.data.materials.append(rock)
    bpy.ops.mesh.primitive_cylinder_add(vertices=64, radius=5.4, depth=.22, location=(1,-1,.12)); deck=bpy.context.object; deck.name='Grass_Cap'; deck.scale=(1.5,.8,1); deck.data.materials.append(grass)
    for i in range(22):
        ang=(i*2.41)%math.tau; rad=4.2+((i*17)%100)/100*1.0
        add_rock(f'Island edge rock {i}',(1+math.cos(ang)*rad*1.42,-1+math.sin(ang)*rad*.76,-.1),(.18+.08*(i%3),.15+.06*(i%2),.24+.1*(i%4)),rock,(0,.3,ang))
    for i in range(12):
        ang=(i*2.7)%math.tau; rad=2.8+((i*19)%100)/100*2.0
        bpy.ops.mesh.primitive_cone_add(vertices=7, radius1=.22+.08*(i%3), radius2=.03, depth=.9+.25*(i%4), location=(1+math.cos(ang)*rad*1.2,-1+math.sin(ang)*rad*.6,-2.2))
        spike=bpy.context.object; spike.name='Island underside stalactite'; spike.data.materials.append(rock); spike.rotation_euler=(math.sin(ang)*.25,math.cos(ang)*.25,ang)
    # hero tree with branch hierarchy
    add_cylinder('Hero trunk',(-5,0,-1),(-4,0,7),.95,bark); add_bark_ridge('Trunk ridge',(-5.02,-.02,-.8),(-4.02,-.02,6.9),.95,bark2,.25)
    add_cylinder('Hero branch',(-4,0,5),(-1,0,8),.42,bark)
    add_cylinder('Hero branch 2',(-4,0,4),(-7,0,7),.36,bark)
    add_cylinder('Hero branch 3',(-4,0,6),(-.5,0,9),.28,bark2)
    for j,(x,y,z,s) in enumerate([(-7,0,7,1.3),(-5.7,0,8,1.45),(-3,0,8.8,1.2),(-1,0,9,1.35),(-.5,0,7.8,1.15),(-3.8,-.2,9.4,1.0)]):
        add_leaf_cluster('Spring canopy', (x,y,z), (s,.82*s,.62*s), leaf if j%2 else leaf2, (0,.2,j*.4))
        for k in range(5):
            a=(k*1.87+j*.6)%math.tau; add_leaf_cluster('Small canopy leaves',(x+math.cos(a)*s*.7,y+math.sin(a)*s*.35,z+(.2 if k%2 else -.15)),(.26,.18,.14),leaf2 if k%3 else leaf)
    for i in range(7):
        x=-4.8+(i%4)*.42; y=.1+(i%2)*.15; top=6.2+(i%3)*.25
        add_cylinder('Hanging root',(x,y,top),(x+math.sin(i)*.18,y-.05,4.4-(i%3)*.18),.025,bark2)
    add_cylinder('Foreground branch',(-9,-4.5,3.2),(-2.5,-2.2,5.1),.16,bark)
    for i in range(8):
        t=i/7; x=-8.6+t*5.5; y=-4.35+t*2.1; z=3.35+t*1.65
        add_leaf_cluster('Foreground leaves',(x,y,z+.18*math.sin(i)),(.34,.24,.2),leaf2 if i%2 else leaf)
    # dense spring grass tufts on the island
    for i in range(75):
        ang=(i*2.399)%(math.tau); rad=.35+((i*37)%100)/100*4.7; x=1+math.cos(ang)*rad*1.45; y=-1+math.sin(ang)*rad*.72; z=.28+((i*17)%9)/120
        for j in range(3+(i%3)):
            aa=ang+(j-1)*.22; h=.25+((i*13+j*7)%11)/38
            add_cylinder('Grass blade',(x+math.cos(aa)*.06*j,y+math.sin(aa)*.06*j,z),(x+math.cos(aa)*(.14+.03*j),y+math.sin(aa)*(.14+.03*j),z+h),.014 if j%2 else .02,leaf if (i+j)%4 else leaf2)
        if i%9==0:
            add_leaf_cluster('Low shrub',(x,y,z+.08),(.24,.18,.17),leaf2)
        if i%13==0:
            flower=mat('Spring flower',(.9,.42,.12),.6); add_uv('Flower',(x+.12,y-.08,z+.18),(.055,.055,.035),flower)
    # cabin body, roof and warm windows
    add_cube('Cabin',(.8,0,1.35),(1.55,.9,1.0),wood,.12)
    # individual facade planks add a readable, hand-built silhouette.
    for i in range(9): add_cube(f'Cabin plank {i}',(-.7+i*.38,-.93,1.35),(.16,.045,.93),wood,.025)
    add_cube('Cabin roof',(.8,0,2.6),(1.95,1.12,.18),roof,.06,(0,math.radians(-4),0)); add_cube('Roof ridge',(.8,0,2.86),(1.65,.08,.08),roof,.03)
    for x in [0.1,1.5]:
        add_cube('Warm window',(x,-.955,1.5),(.33,.035,.38),glow,.025); add_cube('Window mullion',(x,-.99,1.5),(.025,.018,.38),roof,.01); add_cube('Window sill',(x,-.99,1.1),(.42,.05,.04),wood,.01)
    add_cube('Cabin door',(2.0,-.96,.95),(.34,.04,.72),wood,.035); add_cube('Door handle',(2.25,-1.02,.95),(.035,.025,.035),glow,.01)
    add_cube('Cabin porch',(2.0,-1.18,.27),(.62,.35,.08),wood,.03)
    for x in [1.45,2.55]:
        add_cylinder('Porch post',(x,-1.42,.3),(x,-1.42,1.15),.045,roof)
    add_cylinder('Porch rail',(1.45,-1.42,.86),(2.55,-1.42,.86),.035,roof)
    bpy.ops.mesh.primitive_cone_add(vertices=4, radius1=.24, radius2=.18, depth=.7, location=(-.2,.35,3.15), rotation=(0,0,math.radians(45))); chimney=bpy.context.object; chimney.name='Cabin chimney'; chimney.data.materials.append(roof)
    # swing and travel companion silhouette anchor
    add_cylinder('Swing rope L',(2.3,0,6),(2.3,0,2.5),.035,roof); add_cylinder('Swing rope R',(3.1,0,6),(3.1,0,2.5),.035,roof)
    bpy.ops.mesh.primitive_cube_add(location=(2.7,0,2.45), scale=(.5,.18,.06)); bpy.context.object.data.materials.append(wood)
    # small traveler figure reading on the island, kept as a readable silhouette
    skin=mat('Traveler skin',(.35,.16,.08),.9); cloth=mat('Traveler coat',(.06,.12,.16),.9); pack=mat('Traveler pack',(.38,.14,.04),.85)
    add_uv('Traveler head',(3.4,-.25,.92),(.2,.18,.22),skin)
    add_uv('Traveler hair',(3.4,-.3,1.05),(.21,.18,.1),pack)
    bpy.ops.mesh.primitive_cone_add(vertices=16, radius1=.28, radius2=.18, depth=.62, location=(3.4,-.25,.48)); bpy.context.object.data.materials.append(cloth)
    add_cylinder('Traveler leg L',(3.32,-.25,.28),(3.08,-.38,.18),.075,cloth); add_cylinder('Traveler leg R',(3.48,-.25,.28),(3.72,-.38,.18),.075,cloth)
    add_cylinder('Traveler arm L',(3.28,-.28,.62),(3.18,-.45,.48),.045,cloth); add_cylinder('Traveler arm R',(3.52,-.28,.62),(3.62,-.45,.48),.045,cloth)
    add_cube('Traveler open book',(3.4,-.48,.48),(.2,.03,.12),mat('Book paper',(.75,.65,.42),.7),.015,(math.radians(12),0,0))
    add_uv('Traveler backpack',(3.62,-.17,.52),(.12,.08,.24),pack)
    # layered cloud banks: overlapping volumes with cooler shadows create a cloud sea.
    cloud_shadow=mat('Cloud shadow',(.2,.3,.34),1)
    for i,(x,z,s) in enumerate([(-5,4,3.0),(-2,4.3,2.4),(2,5,3.5),(6,4.2,3.0),(9,5.2,2.6)]):
        add_leaf_cluster(f'Cloud bank {i}',(x,4,z),(s,.9,s*.38),cloud,(0,0,i*.17))
        add_uv(f'Cloud shadow {i}',(x,4.3,z-.35),(s*.9,.65,s*.22),cloud_shadow)
    # distant mountain silhouettes for atmospheric depth
    mountain=procedural_mat('Distant mountain',(.03,.08,.11),(.18,.27,.3),3.5,1)
    for i,(x,z,s) in enumerate([(-10,0,5),(-6,1,4),(-1,1,6),(5,1,4),(10,0,6)]):
        bpy.ops.mesh.primitive_cone_add(vertices=12, radius1=s, radius2=0, depth=s*2.3, location=(x,7,z))
        m=bpy.context.object; m.name=f'Distant mountain {i}'; m.rotation_euler[0]=math.radians(90); m.data.materials.append(mountain)
    # lighting and volumetric world
    world=bpy.context.scene.world or bpy.data.worlds.new('Spring World'); bpy.context.scene.world=world; world.use_nodes=True
    wn=world.node_tree.nodes; wl=world.node_tree.links; wn.clear(); wout=wn.new('ShaderNodeOutputWorld'); wbg=wn.new('ShaderNodeBackground'); wbg.inputs['Strength'].default_value=.22
    hdri_path=os.path.join(TEX,'kloppenheim_06.exr')
    if os.path.exists(hdri_path):
        htex=wn.new('ShaderNodeTexEnvironment'); htex.image=bpy.data.images.load(hdri_path, check_existing=True); wl.new(htex.outputs['Color'],wbg.inputs['Color'])
    else:
        wbg.inputs['Color'].default_value=(.035,.08,.11,1)
    wl.new(wbg.outputs['Background'],wout.inputs['Surface'])
    bpy.ops.object.light_add(type='AREA', location=(5,-4,10)); key=bpy.context.object; key.name='Warm spring sun'; key.data.energy=1800; key.data.color=(1,.72,.45); key.data.shape='DISK'; key.data.size=8; key.rotation_euler=(math.radians(25),0,math.radians(145))
    bpy.ops.object.light_add(type='AREA', location=(-5,2,7)); fill=bpy.context.object; fill.data.energy=700; fill.data.color=(.45,.7,1); fill.data.size=10
    bpy.ops.object.light_add(type='POINT', location=(.8,-.75,1.55)); lamp=bpy.context.object; lamp.name='Cabin interior glow'; lamp.data.energy=85; lamp.data.color=(1,.16,.035); lamp.data.shadow_soft_size=1.2
    # volumetric mist volume between the camera and the distant mountains
    fogmat=bpy.data.materials.new('Volumetric mist'); fogmat.use_nodes=True; fogmat.node_tree.nodes.clear()
    out=fogmat.node_tree.nodes.new('ShaderNodeOutputMaterial'); vol=fogmat.node_tree.nodes.new('ShaderNodeVolumePrincipled'); noise=fogmat.node_tree.nodes.new('ShaderNodeTexNoise'); noise.inputs['Scale'].default_value=.28; noise.inputs['Detail'].default_value=4.5; noise.inputs['Roughness'].default_value=.72; ramp=fogmat.node_tree.nodes.new('ShaderNodeValToRGB'); ramp.color_ramp.elements[0].position=.58; ramp.color_ramp.elements[1].position=.78; fogmat.node_tree.links.new(noise.outputs['Fac'],ramp.inputs['Fac']); fogmat.node_tree.links.new(ramp.outputs['Color'],vol.inputs['Density']); vol.inputs['Density'].default_value=.006; vol.inputs['Color'].default_value=(.5,.68,.76,1); fogmat.node_tree.links.new(vol.outputs['Volume'],out.inputs['Volume'])
    bpy.ops.mesh.primitive_cube_add(location=(0,4,4), scale=(16,8,5)); volume=bpy.context.object; volume.name='Cloud volume'; volume.data.materials.append(fogmat)
    # camera
    bpy.ops.object.camera_add(location=(12,-24,11)); cam=bpy.context.object; cam.data.lens=55; cam.data.dof.use_dof=True; cam.data.dof.focus_object=bpy.data.objects.get('Cabin'); cam.data.dof.aperture_fstop=2.4; bpy.context.scene.camera=cam
    direction=Vector((0,0,3.3))-cam.location; cam.rotation_euler=direction.to_track_quat('-Z','Y').to_euler()
    scene=bpy.context.scene
    try:
        scene.view_settings.look='AgX - Medium Low Contrast'
    except Exception:
        pass
    scene.render.engine = 'BLENDER_EEVEE_NEXT' if 'BLENDER_EEVEE_NEXT' in [item.identifier for item in bpy.types.RenderSettings.bl_rna.properties['engine'].enum_items] else 'BLENDER_EEVEE'
    scene.render.resolution_x=2048; scene.render.resolution_y=1152; scene.render.resolution_percentage=100; scene.render.image_settings.file_format='WEBP'; scene.render.image_settings.color_mode='RGB'; scene.render.filepath=os.path.join(ROOT,'spring-desktop.webp')
    scene.render.film_transparent=False; scene.render.image_settings.compression=40
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(ROOT,'spring-hero.blend')); bpy.ops.render.render(write_still=True)
    # mobile crop
    scene.render.resolution_x=900; scene.render.resolution_y=1400; scene.render.filepath=os.path.join(ROOT,'spring-mobile.webp'); bpy.ops.render.render(write_still=True)
    # restore desktop dimensions for GLB export
    scene.render.resolution_x=2048; scene.render.resolution_y=1152
    bpy.ops.export_scene.gltf(filepath=os.path.join(ROOT,'spring-hero.glb'), export_format='GLB', export_materials='EXPORT')

if __name__=='__main__': build()

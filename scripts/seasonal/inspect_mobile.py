import bpy,json
from mathutils import Vector
from bpy_extras.object_utils import world_to_camera_view
from pathlib import Path
s=bpy.context.scene;c=s.camera
c.location=(10,-24,11); c.rotation_euler=(Vector((0,0,2.2))-c.location).to_track_quat('-Z','Y').to_euler()
c.data.ortho_scale=30;c.data.shift_x=-.043;c.data.shift_y=-.08
s.render.resolution_x=1080;s.render.resolution_y=1920
bpy.context.view_layer.update()
points=[]
for o in s.objects:
    if o.type!='MESH' or any('ATMOSPHERE' in col.name or 'WINTER' in col.name for col in o.users_collection):continue
    for p in o.bound_box:
        q=world_to_camera_view(s,c,o.matrix_world @ Vector(p));points.append((q.x,q.y))
r={'left':min(x for x,y in points),'right':max(x for x,y in points),'bottom':min(y for x,y in points),'top':max(y for x,y in points),'note':'Geometric bounds, no visual review'}
(Path(bpy.data.filepath).parent/'mobile-inspection.json').write_text(json.dumps(r,indent=2),encoding='utf-8')
print(json.dumps(r))

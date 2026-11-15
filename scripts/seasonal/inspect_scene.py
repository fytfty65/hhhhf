"""Inspect native scene and projected subject bounds without claiming visual review.
blender -b seasonal-station.blend --python scripts/seasonal/inspect_scene.py
"""
import bpy, json
from pathlib import Path
from bpy_extras.object_utils import world_to_camera_view
scene=bpy.context.scene
camera=scene.camera
bounds=[]
for obj in scene.objects:
    if obj.type!='MESH' or obj.hide_render: continue
    if any('ATMOSPHERE' in c.name or 'WINTER' in c.name for c in obj.users_collection): continue
    for point in obj.bound_box:
        from mathutils import Vector
        co=world_to_camera_view(scene,camera,obj.matrix_world @ Vector(point))
        bounds.append((co.x,co.y))
report={
 'objects':len(scene.objects),
 'mesh_objects':sum(o.type=='MESH' for o in scene.objects),
 'materials':len(bpy.data.materials),
 'vertices':sum(len(o.data.vertices) for o in scene.objects if o.type=='MESH'),
 'engine':scene.render.engine,
 'camera':{'location':list(camera.location),'rotation':list(camera.rotation_euler),'ortho_scale':camera.data.ortho_scale,'shift_x':camera.data.shift_x,'shift_y':camera.data.shift_y},
 'subject_bounds_normalized':{'left':min(x for x,y in bounds),'right':max(x for x,y in bounds),'bottom':min(y for x,y in bounds),'top':max(y for x,y in bounds)},
 'note':'Geometric check only, not image-quality review.'
}
path=Path(bpy.data.filepath).parent/'scene-inspection.json'
path.write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report,indent=2))

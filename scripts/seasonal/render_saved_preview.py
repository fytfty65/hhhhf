import bpy
from pathlib import Path
scene=bpy.context.scene
scene.camera.data.ortho_scale=34
scene.camera.data.shift_x=.20
scene.camera.data.shift_y=.033
scene.render.resolution_x=1280
scene.render.resolution_y=720
scene.cycles.samples=20
out=Path(bpy.data.filepath).parent
bpy.ops.wm.save_as_mainfile(filepath=str(out/'seasonal-station.blend'))
scene.render.filepath=str(out/'autumn-preview.png')
bpy.ops.render.render(write_still=True)


import base64
import os
import tempfile
import threading

import bpy
import requests
from bpy.props import StringProperty, BoolProperty, IntProperty, FloatProperty


class Hunyuan3DProperties(bpy.types.PropertyGroup):
    prompt: StringProperty(
        name="Text Prompt",
        description="Describe what you want to generate",
        default=""
    ) # type: ignore
    api_url: StringProperty(
        name="API URL",
        description="URL of the Text-to-3D API service",
        default="http://localhost:8080"
    ) # type: ignore
    is_processing: BoolProperty(
        name="Processing",
        default=False
    ) # type: ignore
    job_id: StringProperty(
        name="Job ID",
        default=""
    ) # type: ignore
    status_message: StringProperty(
        name="Status Message",
        default=""
    ) # type: ignore
    image_path: StringProperty(
        name="Image",
        description="Select an image to upload",
        subtype='FILE_PATH'
    ) # type: ignore

    octree_resolution: IntProperty(
        name="Octree Resolution",
        description="Octree resolution for the 3D generation",
        default=256,
        min=128,
        max=512,
    )
    num_inference_steps: IntProperty(
        name="Number of Inference Steps",
        description="Number of inference steps for the 3D generation",
        default=20,
        min=20,
        max=50
    )
    guidance_scale: FloatProperty(
        name="Guidance Scale",
        description="Guidance scale for the 3D generation",
        default=5.5,
        min=1.0,
        max=10.0
    )

    texture: BoolProperty(
        name="Generate Texture",
        description="Whether to generate texture for the 3D model",
        default=False
    )


class Hunyuan3DOperator(bpy.types.Operator):
    bl_idname = "object.generate_3d"
    bl_label = "Generate 3D Model"
    bl_description = "Generate a 3D model from text description, an image or a selected mesh"

    job_id = ''
    prompt = ""
    api_url = ""
    image_path = ""
    octree_resolution = 256
    num_inference_steps = 20
    guidance_scale = 5.5
    texture = False  # 新增属性
    selected_mesh_base64 = ""
    selected_mesh = None  # 新增属性，用于存储选中的 mesh

    thread = None
    task_finished = False

    def modal(self, context, event):
        if event.type in {'RIGHTMOUSE', 'ESC'}:
            return {'CANCELLED'}

        if self.task_finished:
            print("Threaded task completed")
            self.task_finished = False
            props = context.scene.gen_3d_props
            props.is_processing = False

        return {'PASS_THROUGH'}

    def invoke(self, context, event):
        # 启动线程
        props = context.scene.gen_3d_props
        self.prompt = props.prompt
        self.api_url = props.api_url
        self.image_path = props.image_path
        self.octree_resolution = props.octree_resolution
        self.num_inference_steps = props.num_inference_steps
        self.guidance_scale = props.guidance_scale
        self.texture = props.texture  # 获取 texture 属性的值

        if self.prompt == "" and self.image_path == "":
            self.report({'WARNING'}, "Please enter some text or select an image first.")
            return {'FINISHED'}

        # 保存选中的 mesh 对象引用
        for obj in context.selected_objects:
            if obj.type == 'MESH':
                self.selected_mesh = obj
                break

        if self.selected_mesh:
            temp_glb_file = tempfile.NamedTemporaryFile(delete=False, suffix=".glb")
            temp_glb_file.close()
            bpy.ops.export_scene.gltf(filepath=temp_glb_file.name, use_selection=True)
            with open(temp_glb_file.name, "rb") as file:
                mesh_data = file.read()
            mesh_b64_str = base64.b64encode(mesh_data).decode()
            os.unlink(temp_glb_file.name)
            self.selected_mesh_base64 = mesh_b64_str

        props.is_processing = True

        # 将相对路径转换为相对于 Blender 文件所在目录的绝对路径
        blend_file_dir = os.path.dirname(bpy.data.filepath)
        self.report({'INFO'}, f"blend_file_dir {blend_file_dir}")
        self.report({'INFO'}, f"image_path {self.image_path}")
        if self.image_path.startswith('//'):
            self.image_path = self.image_path[2:]
            self.image_path = os.path.join(blend_file_dir, self.image_path)

        if self.selected_mesh and self.texture:
            props.status_message = "Texturing Selected Mesh...\n" \
                                   "This may take several minutes depending \n on your GPU power."
        else:
            mesh_type = 'Textured Mesh' if self.texture else 'White Mesh'
            prompt_type = 'Text Prompt' if self.prompt else 'Image'
            props.status_message = f"Generating {mesh_type} with {prompt_type}...\n" \
                                   "This may take several minutes depending \n on your GPU power."

        self.thread = threading.Thread(target=self.generate_model, args=[context])
        self.thread.start()

        wm = context.window_manager
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def generate_model(self, context):
        self.report({'INFO'}, f"Generation Start")
        base_url = self.api_url.rstrip('/')

        try:
            if self.selected_mesh_base64 and self.texture:
                if self.image_path and os.path.exists(self.image_path):
                    self.report({'INFO'}, f"Post Texturing with Image")
                    with open(self.image_path, "rb") as file:
                        image_data = file.read()
                    img_b64_str = base64.b64encode(image_data).decode()
                    response = requests.post(
                        f"{base_url}/generate",
                        json={
                            "mesh": self.selected_mesh_base64,
                            "image": img_b64_str,
                            "octree_resolution": self.octree_resolution,
                            "num_inference_steps": self.num_inference_steps,
                            "guidance_scale": self.guidance_scale,
                            "texture": self.texture 
                        },
                    )
                else:
                    self.report({'INFO'}, f"Post Texturing with Text")
                    response = requests.post(
                        f"{base_url}/generate",
                        json={
                            "mesh": self.selected_mesh_base64,
                            "text": self.prompt,
                            "octree_resolution": self.octree_resolution,
                            "num_inference_steps": self.num_inference_steps,
                            "guidance_scale": self.guidance_scale,
                            "texture": self.texture 
                        },
                    )
            else:
                if self.image_path:
                    if not os.path.exists(self.image_path):
                        self.report({'ERROR'}, f"Image path does not exist {self.image_path}")
                        raise Exception(f'Image path does not exist {self.image_path}')
                    self.report({'INFO'}, f"Post Start Image to 3D")
                    with open(self.image_path, "rb") as file:
                        image_data = file.read()
                    img_b64_str = base64.b64encode(image_data).decode()
                    response = requests.post(
                        f"{base_url}/generate",
                        json={
                            "image": img_b64_str,
                            "octree_resolution": self.octree_resolution,
                            "num_inference_steps": self.num_inference_steps,
                            "guidance_scale": self.guidance_scale,
                            "texture": self.texture  
                        },
                    )
                else:
                    self.report({'INFO'}, f"Post Start Text to 3D")
                    response = requests.post(
                        f"{base_url}/generate",
                        json={
                            "text": self.prompt,
                            "octree_resolution": self.octree_resolution,
                            "num_inference_steps": self.num_inference_steps,
                            "guidance_scale": self.guidance_scale,
                            "texture": self.texture  # 传递 texture 参数
                        },
                    )
            self.report({'INFO'}, f"Post Done")
            self.task_finished = True
            props = context.scene.gen_3d_props
            props.is_processing = False

            if response.status_code != 200:
                self.report({'ERROR'}, f"Generation failed: {response.text}")
                return

            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".glb")
            temp_file.write(response.content)
            temp_file.close()

            def import_handler():
                bpy.ops.import_scene.gltf(filepath=temp_file.name)
                os.unlink(temp_file.name)

                new_obj = bpy.context.selected_objects[0] if bpy.context.selected_objects else None
                if new_obj and self.selected_mesh and self.texture:
                    new_obj.location = self.selected_mesh.location
                    new_obj.rotation_euler = self.selected_mesh.rotation_euler
                    new_obj.scale = self.selected_mesh.scale

                    self.selected_mesh.hide_set(True)
                    self.selected_mesh.hide_render = True

                return None

            bpy.app.timers.register(import_handler)

        except Exception as e:
            self.report({'ERROR'}, f"Error: {str(e)}")

        finally:
            self.task_finished = True
            props = context.scene.gen_3d_props
            props.is_processing = False
            self.selected_mesh_base64 = ""


class Hunyuan3DPanel(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Hunyuan3D-2'
    bl_label = 'Hunyuan3D-2 3D Generator'

    def draw(self, context):
        layout = self.layout
        props = context.scene.gen_3d_props

        layout.prop(props, "api_url")
        layout.prop(props, "prompt")
        layout.prop(props, "image_path")
        layout.prop(props, "octree_resolution")
        layout.prop(props, "num_inference_steps")
        layout.prop(props, "guidance_scale")
        layout.prop(props, "texture")

        row = layout.row()
        row.enabled = not props.is_processing
        row.operator("object.generate_3d")

        if props.is_processing:
            if props.status_message:
                for line in props.status_message.split("\n"):
                    layout.label(text=line)
            else:
                layout.label("Processing...")


classes = (
    Hunyuan3DProperties,
    Hunyuan3DOperator,
    Hunyuan3DPanel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.gen_3d_props = bpy.props.PointerProperty(type=Hunyuan3DProperties)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.gen_3d_props


if __name__ == "__main__":
    register()



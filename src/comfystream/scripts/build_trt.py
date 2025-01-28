#!/usr/bin/env python3

import os
import sys
import time
import argparse

COMFYUI_DIR = "/ComfyUI"
COMFYSTREAM_DIR = "/workspace/"

# Insert it at the front of sys.path so it is found before site-packages
if COMFYUI_DIR not in sys.path:
    sys.path.insert(0, COMFYUI_DIR)
if COMFYSTREAM_DIR not in sys.path:
    sys.path.insert(0, COMFYSTREAM_DIR)

# Path to the comfy/ directory
comfy_dirs = ["/workspace/ComfyUI/","/workspace/ComfyUI/comfy","/workspace/ComfyUI/comfy_extras"]


for comfy_dir in comfy_dirs:
# Path to the __init__.py file
    init_file_path = os.path.join(comfy_dir, "__init__.py")

    # Check if the __init__.py file exists
    if not os.path.exists(init_file_path):
        try:
            # Create the __init__.py file
            with open(init_file_path, "w") as init_file:
                init_file.write("# This file ensures comfy is treated as a package\n")
            print(f"Created __init__.py at {init_file_path}")
        except Exception as e:
            print(f"Error creating __init__.py: {e}")
    else:
        print(f"__init__.py already exists at {init_file_path}")

import comfy
import comfy.model_management


# Import from sibling modules in this package
# Make sure there's an __init__.py in onnx_utils and models so these are importable.
# from ComfyUI.custom_nodes.ComfyUI_TensorRT.models.baseline import TRTModelUtil
from ComfyUI.custom_nodes.ComfyUI_TensorRT.models.supported_models import detect_version_from_model, get_helper_from_model
from ComfyUI.custom_nodes.ComfyUI_TensorRT.onnx_utils.export import export_onnx
from ComfyUI.custom_nodes.ComfyUI_TensorRT.tensorrt_diffusion_model import TRTDiffusionBackbone

def parse_args():
    parser = argparse.ArgumentParser(
        description="Build a static TensorRT engine from a ComfyUI model."
    )
    parser.add_argument(
        "--model",
        type=str,
        required=True,
        help="Path to the .ckpt/.safetensors or ComfyUI model name you want to convert",
    )
    parser.add_argument(
        "--out-engine",
        type=str,
        required=True,
        help="Path to the output .engine file to produce",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1,
        help="Batch size for the exported and built engine (default 1)",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=512,
        help="Width in pixels for the exported model (default 1024)",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=512,
        help="Height in pixels for the exported model (default 1024)",
    )
    parser.add_argument(
        "--context",
        type=int,
        default=1,
        help="Multiplier for context tokens (default 1). If your model is conditional, it usually doubles the batch internally.",
    )
    parser.add_argument(
        "--num-video-frames",
        type=int,
        default=14,
        help="Applies only if the model is 'SVD_img2vid'. Default 14.",
    )
    parser.add_argument(
        "--fp8",
        action="store_true",
        default=False,
        help="If set, attempts to export the ONNX with FP8 transformations (Flux or standard).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable more logging / debug prints."
    )
    return parser.parse_args()


def generate_trt_filename(output_dir, filename_prefix, model_version, batch_size, height, width, is_static=True):
    """Generates a TensorRT engine filename based on model parameters."""
    suffix = "stat" if is_static else "dyn"
    filename = f"static-{filename_prefix}_{model_version}_${suffix}_b-{batch_size}_h-{height}_w-{width}"
    return os.path.join(output_dir, f"{filename}_00001_.engine")

def build_static_trt_engine(
    model_path: str,
    engine_out_path: str,
    batch_size_opt: int = 1,
    width_opt: int = 512,
    height_opt: int = 512,
    context_opt: int = 1,
    num_video_frames: int = 14,
    fp8: bool = False,
    verbose: bool = False
):
    """
    1) Load the model from ComfyUI by path or name
    2) Export to ONNX (static shape)
    3) Build a static TensorRT .engine file
    """

    if verbose:
        print(f"[INFO] Starting build for model: {model_path}")
        print(f"       Output Engine Path: {engine_out_path}")
        print(f"       (batch={batch_size_opt}, H={height_opt}, W={width_opt}, context={context_opt}, "
              f"num_video_frames={num_video_frames}, fp8={fp8})")

    # 1) Load model in GPU:
    comfy.model_management.unload_all_models()
    # load_models_gpu() can accept a list of names/paths. 
    weight_dtype = "fp8_optimizations"
    model_options = {}
    if weight_dtype == "fp8_e4m3fn":
        model_options["dtype"] = torch.float8_e4m3fn
    elif weight_dtype == "fp8_e4m3fn_fast":
        model_options["dtype"] = torch.float8_e4m3fn
        model_options["fp8_optimizations"] = True
    elif weight_dtype == "fp8_e5m2":
        model_options["dtype"] = torch.float8_e5m2

    # loaded_model = comfy.sd.load_diffusion_model(model_path, model_options=model_options)
    # if loaded_model is None:
    #     raise ValueError("Failed to load the UNet model. Check the model file and loading logic.")
    # device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    # loaded_model.model.to(device)
    loaded_model = comfy.sd.load_diffusion_model(model_path, model_options=model_options)
    if loaded_model is None:
        raise ValueError("Failed to load the model. Check the model file and loading logic.")
        
    comfy.model_management.load_models_gpu(
        [loaded_model],
        force_patch_weights=True,
        force_full_load=True
    )

    # 2) Export to ONNX at the desired shape
    # We'll place the ONNX in a temporary folder
    timestamp_str = str(int(time.time()))
    temp_dir = os.path.join(comfy.model_management.get_torch_device().type + "_temp", timestamp_str)
    if not os.path.exists(temp_dir):
        os.makedirs(temp_dir, exist_ok=True)

    onnx_filename = f"model_{timestamp_str}.onnx"
    onnx_path = os.path.join(temp_dir, onnx_filename)

    if verbose:
        print(f"[INFO] Exporting ONNX to: {onnx_path}")

    export_onnx(
        model              = loaded_model,
        path               = onnx_path,
        batch_size         = batch_size_opt,
        height             = height_opt,
        width              = width_opt,
        num_video_frames   = num_video_frames,
        context_multiplier = context_opt,
        fp8                = fp8,
    )

    # 3) Build the static TRT engine
    model_version = detect_version_from_model(loaded_model)
    model_helper  = get_helper_from_model(loaded_model)

    trt_model = TRTDiffusionBackbone(model_helper)

    # Generate the output filename
    filename_prefix = os.path.splitext(os.path.basename(model_path))[0]
    output_trt_engine = generate_trt_filename(engine_out_path, filename_prefix, model_version, batch_size_opt, height_opt, width_opt, is_static=True)

    # We'll define min/opt/max config all the same (i.e. 'static')
    min_config = {
        "batch_size": batch_size_opt,
        "height":     height_opt,
        "width":      width_opt,
        "context_len": context_opt * model_helper.context_len,
    }
    opt_config = dict(min_config)
    max_config = dict(min_config)

    # The tensorrt_diffusion_model build() signature is typically:
    #   build(onnx_path, engine_path, timing_cache_path, opt_config, min_config, max_config)
    # If you have a different signature, adjust accordingly.
    # If you have a separate 'timing_cache.trt', put it next to this script or in your plugin folder:
    timing_cache_path = os.path.join(os.path.dirname(__file__), "timing_cache.trt")

    if verbose:
        print(f"[INFO] Building engine -> {engine_out_path}")

    success = trt_model.build(
        onnx_path         = onnx_path,
        engine_path       = output_trt_engine,
        timing_cache_path = timing_cache_path,
        opt_config        = opt_config,
        min_config        = min_config,
        max_config        = max_config,
    )
    if not success:
        raise RuntimeError("[ERROR] TensorRT engine build failed")

    print(f"[OK] Created TensorRT engine: {engine_out_path}")

    # Clean up
    comfy.model_management.unload_all_models()
    try:
        os.remove(onnx_path)
    except:
        pass
    try:
        os.rmdir(temp_dir)
    except:
        pass


def main():
    args = parse_args()
    build_static_trt_engine(
        model_path       = args.model,
        engine_out_path  = args.out_engine,
        batch_size_opt   = args.batch_size,
        height_opt       = args.height,
        width_opt        = args.width,
        context_opt      = args.context,
        num_video_frames = args.num_video_frames,
        fp8              = args.fp8,
        verbose          = args.verbose
    )


if __name__ == "__main__":
    main()

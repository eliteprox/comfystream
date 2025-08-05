#!/usr/bin/env python3
"""Setup script to download ComfyUI hiddenswitch v0.3.40 as package data"""

import os
import urllib.request
import tarfile
import shutil
from pathlib import Path

def download_comfyui_hiddenswitch():
    """Download ComfyUI hiddenswitch v0.3.40 and prepare as package data"""
    url = "https://github.com/hiddenswitch/ComfyUI/archive/refs/tags/v0.3.40.tar.gz"
    
    # Create package data directory
    package_data_dir = Path("src/comfystream/comfyui_hiddenswitch_v0.3.40")
    package_data_dir.mkdir(parents=True, exist_ok=True)
    
    # Download the tar.gz
    tar_path = package_data_dir / "comfyui_hiddenswitch_v0.3.40.tar.gz"
    print(f"Downloading ComfyUI hiddenswitch v0.3.40 from {url}")
    
    try:
        urllib.request.urlretrieve(url, tar_path)
        print(f"Downloaded to {tar_path}")
        
        # Extract the tar.gz
        with tarfile.open(tar_path, 'r:gz') as tar:
            tar.extractall(package_data_dir)
        
        # Find the extracted directory and move contents
        extracted_dir = None
        for item in package_data_dir.iterdir():
            if item.is_dir() and item.name.startswith("ComfyUI-"):
                extracted_dir = item
                break
        
        if extracted_dir:
            # Move contents to package_data_dir
            for item in extracted_dir.iterdir():
                shutil.move(str(item), str(package_data_dir / item.name))
            
            # Remove the extracted directory
            shutil.rmtree(extracted_dir)
            
            # Remove the tar.gz file
            tar_path.unlink()
            
            print(f"ComfyUI hiddenswitch v0.3.40 prepared in {package_data_dir}")
            return True
        else:
            print("Failed to find extracted ComfyUI directory")
            return False
            
    except Exception as e:
        print(f"Error downloading/extracting ComfyUI: {e}")
        return False

if __name__ == "__main__":
    success = download_comfyui_hiddenswitch()
    if success:
        print("ComfyUI hiddenswitch v0.3.40 package data setup complete!")
    else:
        print("Failed to setup ComfyUI hiddenswitch package data")
        exit(1) 
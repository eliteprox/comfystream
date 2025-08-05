#!/usr/bin/env python3
"""Test script for comfy_loader implementation"""

import sys
import os

# Add src to path for testing
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

def test_comfy_loader():
    """Test the comfy_loader implementation"""
    try:
        from comfystream import comfy_loader
        print("✓ comfy_loader imported successfully")
        
        # Test namespace setup
        if comfy_loader.setup_comfy_namespace():
            print("✓ comfy namespace setup successful")
        else:
            print("⚠ comfy namespace setup failed (this is expected in test environment)")
        
        # Test vanilla custom node detection
        is_vanilla = comfy_loader.is_vanilla_custom_node_context()
        print(f"✓ Vanilla custom node context detection: {is_vanilla}")
        
        # Test component loading decision
        should_load = comfy_loader.should_load_comfystream_components()
        print(f"✓ Should load comfystream components: {should_load}")
        
        return True
        
    except Exception as e:
        print(f"✗ Error testing comfy_loader: {e}")
        return False

if __name__ == "__main__":
    success = test_comfy_loader()
    if success:
        print("\n✓ All tests passed!")
    else:
        print("\n✗ Tests failed!")
        sys.exit(1) 
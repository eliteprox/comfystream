import torch
import numpy as np
import av

class SideData:
    """Container for frame side data attributes."""
    def __init__(self):
        self.input = None
        self.skipped = True
        self.frame_id = None
        self.frame_received_time = None
        self.client_index = -1

class FrameProxy:
    """Proxy class to handle frame data in multiprocessing environment."""
    
    def __init__(self, tensor, width, height, pts=None, time_base=None):
        self.width = width
        self.height = height
        self.pts = pts
        self.time_base = time_base
        self.side_data = SideData()
        self.side_data.input = tensor.clone().cpu()
        self.side_data.skipped = True

    @staticmethod
    def avframe_to_frameproxy(frame):
        """Convert av.VideoFrame to FrameProxy for multiprocessing."""
        frame_np = frame.to_ndarray(format="rgb24").astype(np.float32) / 255.0
        tensor = torch.from_numpy(frame_np).unsqueeze(0)
        
        proxy = FrameProxy(
            tensor=tensor.clone().cpu(),
            width=frame.width,
            height=frame.height,
            pts=getattr(frame, "pts", None),
            time_base=getattr(frame, "time_base", None)
        )
        
        # Copy side data if it exists
        if hasattr(frame, 'side_data'):
            if hasattr(frame.side_data, 'frame_id'):
                proxy.side_data.frame_id = frame.side_data.frame_id
            if hasattr(frame.side_data, 'frame_received_time'):
                proxy.side_data.frame_received_time = frame.side_data.frame_received_time
            if hasattr(frame.side_data, 'client_index'):
                proxy.side_data.client_index = frame.side_data.client_index
        
        return proxy
    
    def to_avframe(self):
        """Convert FrameProxy back to av.VideoFrame."""
        # Handle tensor format - should be [1, H, W, C] from proxy
        tensor = self.side_data.input
        if tensor is None:
            # Create a dummy tensor if none exists
            tensor = torch.zeros(1, self.height, self.width, 3, dtype=torch.float32)
        
        if tensor.dim() == 4:
            tensor = tensor.squeeze(0)  # Remove batch dimension
        
        # Convert to numpy array
        if tensor.dtype in [torch.float32, torch.float64]:
            if tensor.max() <= 1.0:
                # Tensor is in [0, 1] range, convert to [0, 255]
                tensor_np = (tensor * 255.0).clamp(0, 255).to(torch.uint8).cpu().numpy()
            else:
                # Tensor is already in [0, 255] range
                tensor_np = tensor.clamp(0, 255).to(torch.uint8).cpu().numpy()
        else:
            tensor_np = tensor.cpu().numpy()
        
        # Ensure contiguous array
        if not tensor_np.flags.c_contiguous:
            tensor_np = np.ascontiguousarray(tensor_np)
        
        # Create av.VideoFrame
        av_frame = av.VideoFrame.from_ndarray(tensor_np, format="rgb24")
        
        # Restore timing information
        if self.pts is not None:
            av_frame.pts = self.pts
        if self.time_base is not None:
            av_frame.time_base = self.time_base
            
        # Set side_data attributes as dynamic attributes (PyAV allows this)
        # PyAV VideoFrame.side_data is not directly assignable, so we set attributes dynamically
        av_frame.side_data.input = self.side_data.input  # type: ignore
        av_frame.side_data.skipped = self.side_data.skipped  # type: ignore
        av_frame.side_data.frame_id = self.side_data.frame_id  # type: ignore
        av_frame.side_data.frame_received_time = self.side_data.frame_received_time  # type: ignore
        av_frame.side_data.client_index = self.side_data.client_index  # type: ignore
        
        return av_frame 
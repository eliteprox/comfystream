import json
from typing import Any, Dict, List, Optional, Union

from pydantic import Field, model_validator
from runner.live.pipelines.interface import BaseParams

from comfystream.utils import convert_prompt


class ComfyParams(BaseParams):
    """
    Parameters for the ComfyStream runner pipeline.
    """

    prompts: Optional[Union[Dict[str, Any], List[Dict[str, Any]], str]] = Field(
        default=None,
        description="ComfyUI workflow prompts. Can be a dict, a list of dicts, or a JSON string.",
    )

    @model_validator(mode="before")
    @classmethod
    def validate_prompts(cls, data: Any) -> Any:
        if isinstance(data, dict) and "prompts" in data:
            prompts = data["prompts"]

            # Parse JSON string if needed
            if isinstance(prompts, str) and prompts.strip():
                try:
                    prompts = json.loads(prompts)
                except json.JSONDecodeError:
                    # If invalid JSON, remove it so it's ignored or fails validation if required
                    # But here it is Optional, so maybe we set to None
                    prompts = None

            # Handle list - use first valid dict if multiple provided?
            # The original logic in utils_byoc.py took the first valid dict.
            # However, comfystream pipeline supports lists of prompts.
            # Let's keep consistency with utils_byoc logic for now if that was the intent,
            # but usually we want to support whatever the pipeline supports.
            # utils_byoc.py line 40: prompts = next((p for p in prompts if isinstance(p, dict)), None)
            # This suggests it only supports a SINGLE prompt dict for now in the stream params update.
            elif isinstance(prompts, list):
                 prompts = next((p for p in prompts if isinstance(p, dict)), None)

            # Validate prompts using comfystream utils
            if isinstance(prompts, dict):
                try:
                    data["prompts"] = convert_prompt(prompts, return_dict=True)
                except Exception:
                    # If conversion fails, remove it
                    data["prompts"] = None
            else:
                data["prompts"] = None

        return data


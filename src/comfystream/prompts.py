DEFAULT_PROMPT = """
{
  "1": {
    "inputs": {
      "images": [
        "2",
        0
      ]
    },
    "class_type": "SaveTensor",
    "_meta": {
      "title": "SaveTensor"
    }
  },
  "2": {
    "inputs": {},
    "class_type": "LoadTensor",
    "_meta": {
      "title": "LoadTensor"
    }
  }
}
"""

INVERTED_PROMPT = """
{
  "1": {
    "inputs": {
      "images": [
        "3",
        0
      ]
    },
    "class_type": "SaveTensor",
    "_meta": {
      "title": "SaveTensor"
    }
  },
  "2": {
    "inputs": {},
    "class_type": "LoadTensor",
    "_meta": {
      "title": "LoadTensor"
    }
  },
  "3": {
    "inputs": {
      "image": [
        "2",
        0
      ]
    },
    "class_type": "ImageInvert",
    "_meta": {
      "title": "Invert Image"
    }
  }
}
"""
"""Regenerate the language-neutral envelope schema from the producer model."""
import json
from pathlib import Path
from gateway.contracts import Alert

schema = Alert.model_json_schema()
schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
schema["$id"] = "urn:sentinel:alert:1.0"
schema["required"] = list(Alert.model_fields)
Path(__file__).with_name("alert-v1.schema.json").write_text(json.dumps(schema, indent=2) + "\n")

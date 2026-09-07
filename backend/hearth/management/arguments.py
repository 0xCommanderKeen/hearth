"""Bounded validation hints derived from trusted tool schemas, never submitted values."""

from hearth.residents.models import Refused


class InvalidArguments(Refused):
    def __init__(self, error, schema):
        super().__init__("management_invalid_arguments")
        issues = []
        errors = error.errors(include_input=False, include_context=False, include_url=False)
        for detail in errors[:6]:
            node = schema
            path = ""
            for part in detail["loc"][:8]:
                if "$ref" in node:
                    node = schema.get("$defs", {}).get(node["$ref"].rsplit("/", 1)[-1], {})
                if isinstance(part, str) and part in node.get("properties", {}):
                    path += ("." if path else "") + part
                    node = node["properties"][part]
                elif type(part) is int and 0 <= part < 100000 and "items" in node:
                    path += f"[{part}]"
                    node = node["items"]
                else:
                    break
            rule = {
                "too_long": "maxItems",
                "too_short": "minItems",
                "string_too_long": "maxLength",
                "string_too_short": "minLength",
            }.get(detail["type"])
            issue = {"path": path[:128] or "$", "rule": "invalid"}
            if rule in node:
                issue.update(rule=rule, limit=node[rule])
            issues.append(issue)
        self.details = {
            "issues": issues,
            "truncated": len(errors) > 6,
            "retry": "Correct listed fields and retry with a new call ID. No changes were made.",
        }

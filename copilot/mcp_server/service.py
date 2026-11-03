"""Transport-independent read-only service; handlers enforce all boundaries."""
from .contracts import TOOL_SCOPES, ToolRejected, failure, validate_call


class ToolService:
    def __init__(self, reader, gateway, explainer):
        self.reader, self.gateway, self.explainer = reader, gateway, explainer

    def call(self, name, args, scopes):
        try:
            clean = validate_call(name, args)
            if scopes is None or TOOL_SCOPES[name] not in scopes:
                raise ToolRejected("forbidden", "Service token lacks the required tool scope")
            if name == "score_transaction":
                data = self.gateway.score(clean["features"])
            elif name == "list_case_events":
                self._case(clean["case_id"])
                data = {"case_id": clean["case_id"], "events": self.reader.list_case_events(clean["case_id"])}
            elif name == "search_similar_cases":
                self._case(clean["case_id"])
                data = self.reader.search_similar_cases(**clean)
            else:
                case = self._case(clean["case_id"])
                data = self.explainer.explain(case) if name == "explain_score" else case
            return {"ok": True, "data": data}
        except ToolRejected as exc:
            return failure(exc.code, str(exc))
        except Exception:
            # Driver/HTTP exception strings can contain DSNs, tokens and raw evidence.
            return failure("dependency_unavailable", "A required read-only dependency is unavailable")

    def _case(self, case_id):
        case = self.reader.get_case(case_id)
        if case is None:
            raise ToolRejected("case_not_found", "Case was not found")
        return case

from .audit import reset_audit_request_context, set_audit_request_context


class AuditRequestContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = set_audit_request_context(request)
        try:
            return self.get_response(request)
        finally:
            reset_audit_request_context(token)

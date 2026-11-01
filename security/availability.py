"""Transport availability is separate from scanner finding severity."""
import re


def availability(evidence, console_log):
    statuses = evidence['http_status_counts']
    errno99 = len(re.findall(r'connect\(\).*?\(99: Address not available\)', console_log))
    log_502 = len(re.findall(r'"[^"\n]*" 502 \d+\b', console_log))
    return {'observed_active_http_status_counts': statuses,
            'observed_502_responses': statuses.get('502', 0),
            'nginx_access_log_502_responses': log_502,
            'nginx_upstream_address_unavailable_errors': errno99,
            'passed': statuses.get('502', 0) == 0 and log_502 == 0 and errno99 == 0}

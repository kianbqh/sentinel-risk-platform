"""Preserve observed scanner HTTP evidence without changing findings or scan inputs."""
from datetime import datetime, timezone
from collections import Counter
import hashlib
from itertools import chain, islice
import json
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit

SENTINEL = 'thishouldnotexistandhopefullyitwillnot'
CANDIDATES = {'graphql', '/graphql', '\\graphql'}


def _request_parts(message, target):
    header = message.get('requestHeader', '')
    lines = header.splitlines()
    if not lines:
        return None
    parts = lines[0].split(' ', 2)
    if len(parts) != 3:
        return None
    address = urlsplit(urljoin(target, parts[1]))
    expected = urlsplit(target)
    hosts = [line.partition(':')[2].strip() for line in lines[1:] if line.lower().startswith('host:')]
    if (address.scheme, address.hostname, address.port) != (expected.scheme, expected.hostname, expected.port):
        return None
    if hosts and hosts[0].lower() != expected.netloc.lower():
        return None
    return parts[0], address


def marked_inputs(message, target):
    parts = _request_parts(message, target)
    if parts is None or parts[1].path != '/graphql':
        return []
    markers = []

    def inspect(value, path):
        if isinstance(value, dict):
            for key, child in value.items():
                inspect(child, path + '.' + key)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                inspect(child, path + '[' + str(index) + ']')
        elif isinstance(value, str):
            if SENTINEL in value:
                markers.append({'location':path, 'kind':'sentinel', 'payload':SENTINEL})
            elif value in CANDIDATES:
                markers.append({'location':path, 'kind':'candidate', 'payload':value})

    for name, value in parse_qsl(parts[1].query, keep_blank_values=True):
        inspect(value, 'url.' + name)
    try:
        inspect(json.loads(message.get('requestBody', '')), 'body')
    except (ValueError, RecursionError):
        if SENTINEL in message.get('requestBody', ''):
            markers.append({'location':'unparsed-body', 'kind':'sentinel', 'payload':SENTINEL})
    return markers


def _record(message, markers):
    body = message.get('responseBody', '')
    header = message.get('responseHeader', '')
    status = re.match(r'^HTTP/\S+\s+(\d{3})', header)
    return {'message':message, 'matched_inputs':markers,
            'http_status':int(status.group(1)) if status else None,
            'response_has_error_marker':bool(re.search('Exception|Error', body, re.IGNORECASE)),
            'response_body_sha256_before_redaction':hashlib.sha256(body.encode()).hexdigest()}


def capture(zap, target, max_messages=None):
    """Get real alert-linked messages and original active-scan sentinel/candidate traffic."""
    scans = zap.ascan.scans
    ids = []
    for scan in scans:
        for identity in zap.ascan.messages_ids(scan['id']):
            if isinstance(identity, dict):
                identity = identity.get('id', identity.get('messageId'))
            if identity is None:
                raise RuntimeError('Unrecognized active-scan message identity')
            ids.append(str(identity))
    ids = list(dict.fromkeys(ids))
    alerts = []
    high_ids = set()
    for risk in ('3', '2'):
        start = 0
        while True:
            page = zap.alert.alerts(baseurl=target, start=start, count=500, riskid=risk)
            for alert in page:
                detail = zap.alert.alert(alert['id'])
                if not detail.get('messageId'):
                    raise RuntimeError('A high/medium alert has no associated HTTP messageId')
                alerts.append(detail);high_ids.add(str(detail['messageId']))
            if len(page) < 500:
                break
            start += len(page)
    messages = []
    missing = []
    fetched = set()
    selected_ids = iter(ids) if max_messages is None else islice(ids, max_messages)
    order = chain(sorted(high_ids), (identity for identity in selected_ids if identity not in high_ids))
    batches = 0
    statuses = Counter()
    # The completed scan supplies a finite snapshot. Keep only one ID batch and
    # one HTTP response in flight, retaining bodies only for selected evidence.
    while page := tuple(islice(order, 1000)):
        batches += 1
        for identity in page:
            try:
                message = zap.core.message(identity)
                if not isinstance(message, dict) or not message.get('requestHeader'):
                    raise RuntimeError('HTTP message missing')
                if _request_parts(message, target) is None:
                    if identity in high_ids:
                        raise RuntimeError('Alert HTTP message is outside the owned origin')
                    continue
                fetched.add(identity)
                status = re.match(r'^HTTP/\S+\s+(\d{3})', message.get('responseHeader', ''))
                if status is None:
                    raise RuntimeError('HTTP response status missing')
                statuses[status.group(1)] += 1
                markers = marked_inputs(message, target)
                if identity in high_ids or markers:
                    item = _record(message, markers)
                    item.update(message_id=identity, alert_linked=identity in high_ids)
                    messages.append(item)
            except Exception as error:
                missing.append({'message_id':identity, 'error':type(error).__name__ + ': ' + str(error)})
    retention = str(zap.ascan.option_persist_temporary_messages).lower()
    truncated = max_messages is not None and len(ids)>max_messages
    return {'collected_utc':datetime.now(timezone.utc).isoformat(), 'target':target,
            'selection':'Actual HIGH/MEDIUM-linked messages plus original active-scan GraphQL sentinel/candidate messages. No adjacency assumption pairs concurrent requests.',
            'zap_core_version':zap.core.version,
            'installed_addons':[{key:addon.get(key) for key in ('id','version','status')} for addon in zap.autoupdate.installed_addons],
            'temporary_messages_enabled':retention,
            'active_scans':scans, 'active_message_ids_total':len(ids), 'messages_limit':max_messages,
            'capture_bound':'Finite ID snapshot from the completed, time-limited active scan.',
            'message_page_size':1000,'message_batches_processed':batches,
            'history_truncated':truncated, 'messages_fetched':len(fetched),
            'http_status_counts':dict(sorted(statuses.items())),
            'alerts':alerts, 'messages':messages, 'missing_messages':missing,
            'complete':retention=='true' and not missing and not truncated and high_ids<=fetched}


def transport_probes(zap, target, token):
    """Six constructed, read-only probes after the scan; never label these original traffic."""
    url = urlsplit(target)
    query = 'query LimitProbe($alerts_limit:Int!){alerts(limit:$alerts_limit){id}}'
    cases = [('typed-limit-'+str(index), '/graphql', {'query':query,'variables':{'alerts_limit':value},'operationName':'LimitProbe'})
             for index,value in enumerate((1,SENTINEL,'/graphql'))]
    body = {'query':'query BodyProbe { __typename }'}
    cases.extend(('post-url-query-'+str(index), '/graphql'+('?' + urlencode({'query':value}) if value is not None else ''), body)
                 for index,value in enumerate((None,SENTINEL,'/graphql')))
    results = []
    for name, path, payload in cases:
        content = json.dumps(payload)
        request = ('POST '+path+' HTTP/1.1\r\nHost: '+url.netloc+'\r\nContent-Type: application/json\r\nAccept: application/json\r\nAuthorization: Bearer '+token+
                   '\r\nContent-Length: '+str(len(content.encode()))+'\r\n\r\n'+content)
        responses = zap.core.send_request(request, followredirects='false')
        if not isinstance(responses, list) or len(responses)!=1:
            raise RuntimeError('Controlled probe did not return one HTTP response')
        message = {'requestHeader':request.partition('\r\n\r\n')[0]+'\r\n\r\n','requestBody':content, **responses[0]}
        results.append({'name':name,'constructed_probe':True, **_record(message,[])})
    return results

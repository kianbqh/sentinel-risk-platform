import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from security.zap_capture import SENTINEL, capture, marked_inputs, transport_probes

TARGET = 'http://127.0.0.1:13040'


def message(identity, value, host='127.0.0.1:13040'):
    return {'id':str(identity),'requestHeader':'POST /graphql HTTP/1.1\r\nHost: '+host+'\r\nContent-Type: application/json\r\n\r\n',
            'requestBody':json.dumps({'query':'test fixture','variables':{'alerts_limit':value}}),
            'responseHeader':'HTTP/1.1 200 OK\r\n',
            'responseBody':'{"errors":[{"message":"fixture"}]}' if value==SENTINEL else '{"data":{"__typename":"Query"}}'}


class Core:
    version = 'test-core'
    def __init__(self):
        self.messages = {'1':message(1,SENTINEL),'2':message(2,'/graphql'),'3':message(3,1)}
        self.requests = []
    def message(self, identity):
        return self.messages[identity]
    def send_request(self, request, followredirects):
        self.requests.append(request)
        return [{'responseHeader':'HTTP/1.1 200 OK\r\n','responseBody':'{"data":{"__typename":"Query"}}'}]


def scanner():
    return SimpleNamespace(core=Core(),
        ascan=SimpleNamespace(scans=[{'id':'0'}],messages_ids=lambda _:['1',{'messageId':'2'},'3'],option_persist_temporary_messages='true'),
        alert=SimpleNamespace(alerts=lambda **kwargs:[{'id':'a'}] if kwargs['riskid']=='3' else [],
                              alert=lambda _:{'id':'a','messageId':'2','alertRef':'6-5','risk':'High'}),
        autoupdate=SimpleNamespace(installed_addons=[{'id':'ascanrules','version':'test-version','status':'release'}]))


class CaptureTests(unittest.TestCase):
    def test_default_capture_covers_the_observed_85679_message_snapshot(self):
        zap=scanner()
        zap.ascan.messages_ids=lambda _:map(str,range(85679))
        zap.alert.alerts=lambda **_:[]
        zap.core.message=lambda identity:message(identity,1)
        result=capture(zap,TARGET)
        self.assertTrue(result['complete']);self.assertFalse(result['history_truncated'])
        self.assertEqual(result['active_message_ids_total'],85679)
        self.assertEqual(result['messages_fetched'],85679)
        self.assertEqual(result['message_batches_processed'],86)
        self.assertEqual(result['messages'],[])

    def test_original_alert_and_sentinel_bodies_are_preserved(self):
        zap = scanner();result = capture(zap,TARGET)
        self.assertTrue(result['complete'])
        self.assertEqual([row['message_id'] for row in result['messages']],['2','1'])
        self.assertEqual(result['messages'][0]['message'],zap.core.messages['2'])
        self.assertFalse(result['messages'][0]['response_has_error_marker'])
        self.assertTrue(result['messages'][1]['response_has_error_marker'])

    def test_http_status_counts_include_messages_not_selected_for_body_evidence(self):
        zap = scanner()
        zap.core.messages['3']['responseHeader'] = 'HTTP/1.1 502 Bad Gateway\r\n'
        result = capture(zap, TARGET)
        self.assertEqual(result['http_status_counts'], {'200': 2, '502': 1})
        self.assertNotIn('3', [row['message_id'] for row in result['messages']])

    def test_a_missing_alert_message_cannot_claim_complete_evidence(self):
        zap=scanner();del zap.core.messages['2']
        result=capture(zap,TARGET)
        self.assertFalse(result['complete']);self.assertEqual(result['missing_messages'][0]['message_id'],'2')

    def test_an_external_alert_is_rejected_and_history_bounds_are_explicit(self):
        zap=scanner();zap.core.messages['2']=message(2,'/graphql','outside.example')
        self.assertFalse(capture(zap,TARGET)['complete'])
        result=capture(scanner(),TARGET,max_messages=1)
        self.assertTrue(result['history_truncated']);self.assertFalse(result['complete'])

    def test_disabled_temporary_message_storage_is_not_complete(self):
        zap=scanner();zap.ascan.option_persist_temporary_messages='false'
        self.assertFalse(capture(zap,TARGET)['complete'])

    def test_path_alone_is_not_misclassified_as_an_injected_candidate(self):
        self.assertEqual(marked_inputs(message(1,1),TARGET),[])
        row=message(1,1);row['requestHeader']=row['requestHeader'].replace('/graphql HTTP','/graphql?query=%2Fgraphql HTTP')
        self.assertEqual(marked_inputs(row,TARGET)[0]['location'],'url.query')

    def test_constructed_url_probes_keep_the_json_body_identical(self):
        zap=scanner();rows=transport_probes(zap,TARGET,'test-token-not-a-jwt')
        self.assertEqual(len(rows),6)
        self.assertTrue(all(row['constructed_probe'] for row in rows))
        bodies=[request.partition('\r\n\r\n')[2] for request in zap.core.requests[3:]]
        self.assertEqual(len(set(bodies)),1)
        self.assertTrue(all(request.startswith('POST /graphql') for request in zap.core.requests))

    def test_public_captures_remove_tokens_and_local_credentials(self):
        from scripts.scan_console import publish_redacted
        with tempfile.TemporaryDirectory() as directory:
            raw=Path(directory)/'raw';raw.mkdir();public=Path(directory)/'public';public.mkdir()
            current='current-token-placeholder'
            other='.'.join(('eyJ'+'fixtureTestHeader','payloadSegment','signatureSegment'))
            (raw/'capture.json').write_text(current+' '+other+' local-zap-scan-account synthetic-env-secret-value')
            counts=publish_redacted(raw,public,current,['synthetic-env-secret-value'])
            content=(public/'capture.json').read_text()
            self.assertNotIn(current,content);self.assertNotIn(other,content);self.assertNotIn('synthetic-env-secret-value',content)
            self.assertGreater(counts['capture.json'],0)

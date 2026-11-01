import unittest

from security.availability import availability


class AvailabilityTests(unittest.TestCase):
    def test_rejects_unselected_502_and_nginx_ephemeral_port_errors(self):
        self.assertFalse(availability({'http_status_counts': {'200': 100, '502': 1}}, '')['passed'])
        log = 'nginx [crit] connect() to 172.18.0.5:18041 failed (99: Address not available) while connecting to upstream'
        result = availability({'http_status_counts': {'200': 100}}, log)
        self.assertFalse(result['passed'])
        self.assertEqual(result['nginx_upstream_address_unavailable_errors'], 1)
        log = '127.0.0.1 - - [date] "POST /graphql HTTP/1.1" 502 552 "-" "ZAP"'
        result = availability({'http_status_counts': {}}, log)
        self.assertFalse(result['passed'])
        self.assertEqual(result['nginx_access_log_502_responses'], 1)

    def test_expected_graphql_422_does_not_mask_service_availability(self):
        self.assertTrue(availability({'http_status_counts': {'200': 100, '422': 3}}, '')['passed'])

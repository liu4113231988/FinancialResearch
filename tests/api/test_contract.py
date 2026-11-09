"""P3-H：对外 API 有版本路径、版本头与关键响应契约。"""
import json
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import market_research.services.analysis as services
from market_research.api.app import app
from market_research.api.version import API_VERSION, VERSIONED_PREFIX


class ApiVersionTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_health_has_same_contract_on_both_paths_and_version_header(self):
        legacy = self.client.get('/api/health')
        versioned = self.client.get('/api/v1/health')
        self.assertEqual(legacy.status_code, 200)
        self.assertEqual(versioned.status_code, 200)
        self.assertEqual(legacy.json(), versioned.json())
        self.assertEqual(legacy.json()['api_version'], API_VERSION)
        self.assertEqual(legacy.headers['x-api-version'], API_VERSION)
        self.assertEqual(versioned.headers['x-api-version'], API_VERSION)

    def test_versioned_paths_are_documented_in_openapi(self):
        paths = self.client.get('/openapi.json').json()['paths']
        for path in ('/api/analyze', '/api/v1/analyze',
                     '/api/job/{jid}', '/api/v1/job/{jid}',
                     '/api/result/{jid}', '/api/v1/result/{jid}',
                     '/api/predictions/summary', '/api/v1/predictions/summary'):
            self.assertIn(path, paths)

    def test_v1_submission_still_accepts_the_same_body(self):
        response = self.client.post('/api/v1/analyze', json={'code': '1100'})
        self.assertEqual(response.status_code, 400,
                         '非法请求在 v1 路径上同样被校验拒绝')
        with patch.object(services, 'submit',
                          return_value={'ok': True, 'job_id': 'job-v1',
                                        'cached': False, 'merged': False}):
            versioned = self.client.post('/api/v1/analyze', json={'code': '110022'})
            legacy = self.client.post('/api/analyze', json={'code': '110022'})
        self.assertEqual(versioned.status_code, 200)
        self.assertEqual(versioned.json()['job_id'], 'job-v1')
        self.assertEqual(legacy.json()['job_id'], 'job-v1')

    def test_job_status_response_contract(self):
        job = {'id': 'job-1', 'code': '110022', 'models': ['enet_slsqp'],
               'status': 'running', 'stage': '拟合', 'progress': 0.5,
               'detail': ['(1/1)'], 'created': 1.0, 'updated': 2.0,
               'attempts': 1, 'error': None}
        with patch.object(services.JOBS, 'public', return_value=job):
            response = self.client.get('/api/v1/job/job-1')
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), set(job), '契约字段不得悄悄增删')
        self.assertEqual(body['status'], 'running')

    def test_result_envelope_contract(self):
        payload = {'ok': True, 'status': 'partial', 'completeness': {'ok': False},
                   'sections': {'nav': {}}}
        with patch.object(services.JOBS, 'get',
                          return_value={'status': 'done', 'result': payload}):
            response = self.client.get('/api/v1/result/job-1')
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {'ok', 'status', 'completeness', 'result'})
        self.assertEqual(body['status'], 'partial')
        self.assertIn('sections', body['result'])

    def test_ready_version_is_declared(self):
        from market_research.api.routes import meta
        with patch.object(meta, '_storage_check', return_value={'ok': True}), \
             patch.object(meta, '_worker_check', return_value={'ok': True}), \
             patch.object(meta, '_jobs_check', return_value={'ok': True}), \
             patch.object(meta, '_sync_check', return_value={'ok': True}):
            response = self.client.get('/api/v1/ready')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['api_version'], API_VERSION)

    def test_legacy_path_marks_deprecation_and_successor(self):
        env = {'FIL_API_SUNSET': 'Wed, 31 Dec 2026 23:59:59 GMT'}
        with patch.dict(os.environ, env, clear=False):
            legacy = self.client.get('/api/health')
            versioned = self.client.get(VERSIONED_PREFIX + '/health')
        self.assertEqual(legacy.headers['deprecation'], 'true')
        self.assertIn(VERSIONED_PREFIX + '/health', legacy.headers['link'])
        self.assertEqual(legacy.headers['sunset'], env['FIL_API_SUNSET'])
        self.assertNotIn('deprecation', versioned.headers)

    def test_config_view_is_sanitized(self):
        env = {'FIL_API_TOKENS': 'secret-token', 'FIL_MAX_JOBS': '4'}
        with patch.dict(os.environ, env, clear=False):
            response = self.client.get('/api/v1/config')
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['limits']['max_jobs'], 4)
        self.assertIn('environment', body)
        self.assertNotIn('secret-token', json.dumps(body, ensure_ascii=False))
        self.assertNotIn('FIL_API_TOKENS', json.dumps(body, ensure_ascii=False))

    def test_invalid_runtime_settings_are_rejected(self):
        from market_research.settings import Settings
        with patch.dict(os.environ, {'FIL_PORT': '70000'}, clear=False):
            with self.assertRaisesRegex(ValueError, 'FIL_PORT'):
                Settings.load()
        with patch.dict(os.environ, {'FIL_MAX_JOBS': '0'}, clear=False):
            with self.assertRaisesRegex(ValueError, 'max_jobs'):
                Settings.load()


if __name__ == '__main__':
    unittest.main()

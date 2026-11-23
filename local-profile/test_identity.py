"""Run with: python -m unittest test_identity"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class AccountIsolation(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.server = subprocess.Popen([sys.executable, '-u', str(Path(__file__).with_name('server.py')),
                                        '--port', '0', '--data-dir', tmp.name], stdout=subprocess.PIPE,
                                       stderr=subprocess.DEVNULL, text=True)
        self.addCleanup(self.stop_server)
        self.url = self.server.stdout.readline().strip().split()[-1]

    def stop_server(self):
        self.server.terminate()
        self.server.wait(timeout=5)
        self.server.stdout.close()

    def api(self, path, token='', data=None):
        headers = {'Authorization':'Bearer '+token, 'Content-Type':'application/json'}
        request = Request(self.url+'api/'+path, headers=headers,
                          data=None if data is None else json.dumps(data).encode())
        try:
            with urlopen(request,timeout=5) as response: return response.status, json.load(response)
        except HTTPError as error:
            with error: return error.code, json.load(error)

    def test_owner_bound_delete_and_revocation(self):
        def create(name):
            status, body = self.api('register',data={'name':name,'password':'local-test-password'})
            self.assertEqual(status,200)
            return body['token']
        alice, bob = create('alice'), create('bob')
        a, b = self.api('session',alice)[1], self.api('session',bob)[1]
        self.assertNotEqual(a['key'],b['key'])
        self.assertEqual(self.api('session')[0],401)
        self.assertEqual(self.api('login',data={'name':'alice','password':'wrong-password'})[0],401)
        self.assertEqual(self.api('profile/delete',bob,{'generation':a['generation']})[0],409)
        self.assertEqual(self.api('session',alice)[1]['key'],a['key'])
        other_session=self.api('login',data={'name':'alice','password':'local-test-password'})[1]['token']
        self.assertEqual(self.api('profile/delete',alice,{'generation':a['generation']})[0],200)
        self.assertEqual(self.api('session',alice)[1]['id'],a['id'])
        self.assertNotEqual(self.api('session',alice)[1]['generation'],a['generation'])
        self.assertEqual(self.api('session',other_session)[0],401)
        self.assertEqual(self.api('session',bob)[1],b)
        token=self.api('login',data={'name':'alice','password':'local-test-password'})[1]['token']
        self.assertNotEqual(self.api('session',token)[1]['key'],a['key'])
        self.assertEqual(self.api('logout',bob,{})[0],200)
        self.assertEqual(self.api('session',bob)[0],401)

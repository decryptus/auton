"""Published argument metadata never replaces authoritative server validation."""
import unittest
from types import SimpleNamespace

from auton.classes.jobs import JobService, AccessDenied
from auton.classes.parameters import validate_parameters, InvalidParameters
from auton.classes.plugins import AutonEPTSync

SCHEMA = {'version': 1, 'args': [
    {'name': 'count', 'type': 'integer'},
    {'name': 'mode', 'choices': ['quick', 'full'], 'required': False}]}


class ParameterTests(unittest.TestCase):
    def service(self):
        return JobService({'test': SimpleNamespace(users={'alice': True},
            discovery={'parameters': validate_parameters(SCHEMA)})}, {'test': AutonEPTSync('test')})

    def test_catalogue_is_scoped_and_returns_an_independent_schema(self):
        service = self.service()
        self.assertEqual(service.list_endpoints('bob'), [])
        catalogue = service.list_endpoints('alice')
        self.assertEqual(catalogue[0]['parameters'], SCHEMA)
        catalogue[0]['parameters']['args'].clear()
        self.assertEqual(service.list_endpoints('alice')[0]['parameters'], SCHEMA)

    def test_invalid_inputs_are_rejected_before_job_admission(self):
        service = self.service()
        for args in ([], ['NaN'], ['1', 'bad'], ['1', 'quick', 'extra'], ['1.5']):
            with self.subTest(args=args), self.assertRaises(InvalidParameters):
                service.submit('test', 'bad', {'args': args}, 'alice')
        self.assertEqual(service.objs, {})
        with self.assertRaises(AccessDenied):
            service.submit('test', 'bad', {'args': []}, 'bob')
        service.submit('test', 'good', {'args': ['2', 'full']}, 'alice')
        self.assertEqual(len(service.objs), 1)

    def test_invalid_schemas_fail_before_use(self):
        for schema in ({}, {'version': True, 'args': []}, {'version': 1, 'args': [
            {'name': 'optional', 'required': False}, {'name': 'required'}]},
            {'version': 1, 'args': [{'name': 'flag', 'type': 'boolean', 'choices': ['yes']}]},
            {'version': 1, 'args': [{'name': 'count'}, {'name': 'count'}]},
            {'version': 1, 'args': [{'name': 'value', 'private': 'secret'}]}):
            with self.subTest(schema=schema), self.assertRaises(ValueError):
                validate_parameters(schema)

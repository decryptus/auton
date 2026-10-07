"""Offline fixtures; actual OperatorApp rendering, no network or execution."""
from copy import deepcopy
from .textual_tui import OperatorApp
from .monitor import aggregate_snapshots

DEMO_JOB = dict(uid='inventory:demo-001', endpoint='inventory', status='processing', return_code=None)
DEMO_COMPLETE = dict(uid='health:demo-002', endpoint='health', status='complete', return_code=0)
DEMO_FAILED = dict(uid='backup:demo-003', endpoint='backup', status='complete', return_code=1)


class DemoMonitor:
    clients = {'edge-01': None, 'worker-02': None}

    def __init__(self):
        self.result = None
        self.closed = False

    def refresh(self, daemon, job=None):
        if self.closed or self.result is not None:
            return False
        def snapshot(name):
            jobs = [deepcopy(DEMO_JOB), deepcopy(DEMO_COMPLETE)] if name == 'edge-01' else [deepcopy(DEMO_FAILED)]
            data = dict(health={'status': 'ok'}, jobs=jobs, errors={},
                        endpoints=[dict(name='inventory', description='Synthetic inventory operation'),
                                   dict(name='health', description='Synthetic health checks')])
            requested = job[1:] if daemon is None and job and job[0] == name else job if daemon is not None else None
            if requested:
                source = next((j for j in jobs if (j['endpoint'], j['uid']) == tuple(requested)), None)
                if source:
                    data['detail'] = dict(source, stream=['Synthetic output\nReading system inventory...\n3 resources observed.\n'],
                                          errors=['Synthetic diagnostics: no remote command was executed.\n'])
            return data
        data = aggregate_snapshots(list(self.clients), {name: snapshot(name) for name in self.clients}, job) if daemon is None else snapshot(daemon)
        self.result = (daemon, job, data)
        return True

    def poll(self):
        result, self.result = self.result, None
        return result

    def close(self):
        self.closed = True


class DemoExecutionClient:
    """Synthetic adapter: never opens a socket or executes a command."""
    def __init__(self, uris, endpoint, uid, **kwargs):
        self.uri, self.endpoint, self.uid = uris[0], endpoint, uid
        self.output_offset = 0

    def do_run(self):
        failed = 'worker-02' in self.uri and self.endpoint == 'install'
        return dict(uid=self.endpoint + ':' + self.uid, status='complete', return_code=7 if failed else 0,
                    stream=['Synthetic ' + self.endpoint + (' failed: invalid archive.' if failed else ' completed.') + '\n'],
                    errors=[], next_offset=1)


def demo_app(operations=False):
    preparation = None
    if operations:
        from .preparation_model import PreparationModel
        from .session import ExecutionSession
        preparation = PreparationModel({'edge-01': 'http://edge-01.invalid', 'worker-02': 'http://worker-02.invalid'},
            {'staging': ['edge-01', 'worker-02']}, {'deploy-app': {'version': 1, 'steps': [
                {'name': name, 'endpoint': name} for name in ('install', 'restart', 'verify')]}}, {},
            ExecutionSession(), {'client_factory': DemoExecutionClient, 'delay': 0})
    return OperatorApp(DemoMonitor(), demo=True, preparation=preparation)


if __name__ == '__main__':
    app = demo_app(operations=True)
    try:
        app.run()
    finally:
        app.preparation.session.close()

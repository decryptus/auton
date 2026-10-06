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


def demo_app():
    return OperatorApp(DemoMonitor(), demo=True)


if __name__ == '__main__':
    demo_app().run()
